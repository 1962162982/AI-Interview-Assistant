"""模型路由器（v2 · 阶段 1 实现）—— ⭐ 核心公共组件

职责（见实行清单阶段 1 / 说明书 6.4）：
- 读取 `backend/config/models.yaml`（唯一事实来源），按模块路由到独立模型 / 温度 / Key / Base URL；
- 统一 `chat()` / `chat_stream()` / `chat_json()` 入口，负责超时、重试、异常兜底；
- DeepSeek 与智谱 GLM 均走 OpenAI 兼容 SDK；
- 业务代码只关心模块名（route），换模型只改 models.yaml。

环境变量约定：
- Key 只从环境变量读取（变量名见 models.yaml 的 `api_key_env`，模板见 .env.example）；
- 启动时按需加载项目根目录 `.env`（仅读取，不覆盖已存在的进程环境变量）；
- Key / Base URL **绝不硬编码，也绝不写入日志或异常信息**。

典型用法::

    from backend.services.model_router import get_router

    router = get_router()
    result = router.chat("resume_diagnosis", messages=[{"role": "user", "content": "..."}])
    print(result.text, result.usage.total_tokens)

    for chunk in router.chat_stream("interview_agent", messages=[...]):
        print(chunk.delta, end="")

    parsed = router.chat_json("jd_matching", messages=[...], schema=JDStructured)
    jd = parsed.value

注意：本模块为**同步阻塞**实现；FastAPI 端点请使用 `def`（线程池）或
`run_in_threadpool`，不要在 async 端点里直接阻塞事件循环。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- #
# 路径与默认值
# ---------------------------------------------------------------- #
# 仓库根目录：AI-Interview-Assistant/
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "backend" / "config" / "models.yaml"
DEFAULT_ENV_PATH = REPO_ROOT / ".env"

# 五条路由为契约的一部分（缺一即配置错误）
REQUIRED_ROUTES: tuple[str, ...] = (
    "resume_diagnosis",
    "jd_matching",
    "interview_agent",
    "evaluation",
    "cross_review",
)

# cross_review 必须与 evaluation 使用不同模型（决策④）
CROSS_REVIEW_EXCLUSIVE_ROUTE = "evaluation"

# 双供应商默认 Base URL（.env 未配置 base_url 时兜底；均为 OpenAI 兼容端点）
DEFAULT_BASE_URLS: tuple[tuple[str, str], ...] = (
    ("deepseek", "https://api.deepseek.com"),
    ("glm", "https://open.bigmodel.cn/api/paas/v4"),
    ("zhipu", "https://open.bigmodel.cn/api/paas/v4"),
)

# 默认上下文窗口（models.yaml 未配置 context_window_tokens 时兜底）
_DEFAULT_CONTEXT_WINDOWS: tuple[tuple[str, int], ...] = (
    ("deepseek", 65536),
    ("glm", 131072),
    ("zhipu", 131072),
)
_FALLBACK_CONTEXT_WINDOW = 8192

# 可重试的 HTTP 状态码（408 超时 / 409 冲突 / 425 过早 / 429 限流 / 5xx）
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429})
_RETRYABLE_EXC_NAMES = frozenset(
    {
        "APITimeoutError",
        "APIConnectionError",
        "RateLimitError",
        "InternalServerError",
        "ServiceUnavailableError",
    }
)


# ---------------------------------------------------------------- #
# 异常体系（业务层按需捕获；异常信息不含 Key）
# ---------------------------------------------------------------- #
class ModelRouterError(RuntimeError):
    """模型路由器异常基类。"""


class RouterConfigError(ModelRouterError):
    """models.yaml 缺失 / 格式错误 / 违反决策④约束。"""


class UnknownRouteError(ModelRouterError):
    """请求了未配置的模块路由。"""

    def __init__(self, route: str, available: Sequence[str]) -> None:
        self.route = route
        self.available = list(available)
        super().__init__(f"未知模型路由 {route!r}；可用路由：{', '.join(self.available)}")


class MissingAPIKeyError(ModelRouterError):
    """模块未配置 API Key（仅报环境变量名与 .env 路径，绝不打印 Key 值）。"""

    def __init__(self, route: str, env_var: str, env_path: str) -> None:
        self.route = route
        self.env_var = env_var
        self.env_path = env_path
        super().__init__(
            f"模块 {route!r} 未配置 API Key：请在 {env_path} 中设置环境变量 "
            f"{env_var}（Key 只走环境变量，禁止写入代码/配置文件）"
        )


class ModelCallError(ModelRouterError):
    """模型调用失败（重试耗尽或不可重试错误）。"""

    def __init__(self, route: str, model: str, attempts: int, last_error: BaseException) -> None:
        self.route = route
        self.model = model
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"模型调用失败（route={route}, model={model}, attempts={attempts}）："
            f"{type(last_error).__name__}: {last_error}"
        )


class ModelJSONError(ModelRouterError):
    """模型输出不是合法 JSON 或不符合 Pydantic Schema（修复重试后仍失败）。"""

    def __init__(self, route: str, model: str, raw_text: str, last_error: str) -> None:
        self.route = route
        self.model = model
        self.raw_text = raw_text
        self.last_error = last_error
        preview = (raw_text or "")[:200]
        super().__init__(
            f"模型输出 JSON 校验失败（route={route}, model={model}）：{last_error}；"
            f"原始输出前 200 字：{preview!r}"
        )


# ---------------------------------------------------------------- #
# 数据结构
# ---------------------------------------------------------------- #
@dataclass(frozen=True)
class RouteConfig:
    """单个模块路由的配置（来自 models.yaml）。"""

    name: str
    model: str
    temperature: float
    api_key_env: str
    base_url_env: str
    timeout: float
    max_retries: int
    stream: bool
    context_window_tokens: int


@dataclass(frozen=True)
class Usage:
    """token 用量（部分供应商 / 流式响应可能为 0）。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class ChatResult:
    """非流式调用结果（统一封装，业务层不直接接触 openai 响应对象）。"""

    route: str
    model: str
    text: str
    finish_reason: str | None = None
    usage: Usage = field(default_factory=Usage)
    raw: Any = field(default=None, repr=False)


@dataclass(frozen=True)
class StreamChunk:
    """流式输出的单个增量块。

    - 正常文本块：`delta` 为增量文本，`finish_reason` 为 None；
    - 结束块：`delta` 为空串，`finish_reason` 为 "stop" / "length" 等；
    - `index` 为自 0 开始的块序号。
    """

    delta: str
    index: int
    finish_reason: str | None = None


@dataclass(frozen=True)
class JsonChatResult:
    """`chat_json()` 的返回值：已校验的 Pydantic 实例 + 原始调用结果。"""

    value: Any
    result: ChatResult
    repair_attempts: int = 0


# 客户端工厂签名：(route_config, api_key, base_url) -> OpenAI 兼容客户端
ClientFactory = Callable[[RouteConfig, str, str], Any]


# ---------------------------------------------------------------- #
# .env 加载（仅读取，不覆盖进程环境变量）
# ---------------------------------------------------------------- #
def load_env_file(path: str | Path | None = None) -> bool:
    """加载 `.env`（仅设置尚未存在的环境变量）；返回是否成功加载文件。

    优先使用 python-dotenv；未安装时使用内置极简解析（KEY=VALUE / # 注释 / 引号）。
    **绝不打印文件内容（含 Key）。**
    """
    env_path = Path(path) if path else DEFAULT_ENV_PATH
    if not env_path.is_file():
        return False
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=env_path, override=False)
        return True
    except ImportError:  # pragma: no cover - venv 已安装 python-dotenv
        pass
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
    return True


# ---------------------------------------------------------------- #
# 配置加载与校验
# ---------------------------------------------------------------- #
def _default_context_window(model: str) -> int:
    lowered = model.lower()
    for prefix, window in _DEFAULT_CONTEXT_WINDOWS:
        if lowered.startswith(prefix):
            return window
    return _FALLBACK_CONTEXT_WINDOW


def default_base_url(model: str) -> str:
    """按模型名前缀返回供应商默认 Base URL（找不到返回空串，交给 SDK 默认值）。"""
    lowered = model.lower()
    for prefix, url in DEFAULT_BASE_URLS:
        if lowered.startswith(prefix):
            return url
    return ""


def _as_float(raw: Any, field_name: str, route_name: str, default: float) -> float:
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise RouterConfigError(f"路由 {route_name!r} 的 {field_name} 必须是数字") from exc


def _as_int(raw: Any, field_name: str, route_name: str, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise RouterConfigError(f"路由 {route_name!r} 的 {field_name} 必须是整数") from exc


def _build_route_config(name: str, raw: Mapping[str, Any]) -> RouteConfig:
    model = str(raw.get("model", "") or "").strip()
    if not model:
        raise RouterConfigError(f"路由 {name!r} 缺少 model 字段")
    temperature = _as_float(raw.get("temperature"), "temperature", name, 0.3)
    if not 0.0 <= temperature <= 2.0:
        raise RouterConfigError(f"路由 {name!r} 的 temperature 必须在 0–2 之间")
    timeout = _as_float(raw.get("timeout"), "timeout", name, 60.0)
    if timeout <= 0:
        raise RouterConfigError(f"路由 {name!r} 的 timeout 必须大于 0")
    max_retries = _as_int(raw.get("max_retries"), "max_retries", name, 2)
    if max_retries < 0:
        raise RouterConfigError(f"路由 {name!r} 的 max_retries 不能为负")
    context_window = _as_int(
        raw.get("context_window_tokens"),
        "context_window_tokens",
        name,
        _default_context_window(model),
    )
    if context_window <= 0:
        raise RouterConfigError(f"路由 {name!r} 的 context_window_tokens 必须大于 0")
    api_key_env = str(raw.get("api_key_env") or f"{name.upper()}_API_KEY").strip()
    base_url_env = str(raw.get("base_url_env") or f"{name.upper()}_BASE_URL").strip()
    return RouteConfig(
        name=name,
        model=model,
        temperature=temperature,
        api_key_env=api_key_env,
        base_url_env=base_url_env,
        timeout=timeout,
        max_retries=max_retries,
        stream=bool(raw.get("stream", False)),
        context_window_tokens=context_window,
    )


def load_route_configs(config_path: str | Path | None = None) -> dict[str, RouteConfig]:
    """读取并校验 models.yaml，返回 {路由名: RouteConfig}。"""
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.is_file():
        raise RouterConfigError(f"模型配置文件不存在：{path}")
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - venv 已安装 pyyaml
        raise RouterConfigError("缺少 PyYAML 依赖，请执行 pip install -r requirements.txt") from exc
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RouterConfigError(f"读取模型配置文件失败：{path}") from exc
    except Exception as exc:  # yaml.YAMLError
        raise RouterConfigError(f"models.yaml 解析失败：{type(exc).__name__}: {exc}") from exc
    if not isinstance(raw, Mapping) or not raw:
        raise RouterConfigError("models.yaml 顶层必须是路由名到配置的映射")
    missing = [route for route in REQUIRED_ROUTES if route not in raw]
    if missing:
        raise RouterConfigError(f"models.yaml 缺少必需路由：{', '.join(missing)}")
    configs: dict[str, RouteConfig] = {}
    for name, item in raw.items():
        if not isinstance(name, str) or not isinstance(item, Mapping):
            raise RouterConfigError(f"models.yaml 路由 {name!r} 的配置格式不正确")
        configs[name] = _build_route_config(name, item)
    # 决策④约束：评分与复核必须是不同模型
    evaluation = configs[CROSS_REVIEW_EXCLUSIVE_ROUTE]
    cross_review = configs["cross_review"]
    if evaluation.model == cross_review.model:
        raise RouterConfigError(
            "配置违约（决策④）：cross_review 与 evaluation 必须使用不同模型，"
            f"当前均为 {evaluation.model}"
        )
    # 四个功能模块应各自独立模型（决策④；此处仅告警，便于评测前过渡）
    module_models = {
        route: configs[route].model
        for route in ("resume_diagnosis", "jd_matching", "interview_agent", "evaluation")
    }
    if len(set(module_models.values())) < len(module_models):
        logger.warning("决策④提示：四个功能模块未全部使用独立模型：%s", module_models)
    return configs


# ---------------------------------------------------------------- #
# 响应解析 / 重试判定辅助
# ---------------------------------------------------------------- #
def _extract_usage(usage: Any) -> Usage:
    if usage is None:
        return Usage()
    return Usage(
        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
    )


def _status_code_of(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _is_retryable(exc: BaseException) -> bool:
    """超时 / 连接错误 / 限流 / 5xx 可重试；鉴权与参数错误不重试。"""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    status = _status_code_of(exc)
    if status is not None:
        return status in _RETRYABLE_STATUS or status >= 500
    return type(exc).__name__ in _RETRYABLE_EXC_NAMES


def _extract_json(text: str) -> Any:
    """从模型输出中提取 JSON（兼容 Markdown 围栏与前后说明文字）。"""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("模型返回内容为空")
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"响应中的 JSON 不合法：{exc}") from exc
    raise ValueError("响应中未找到 JSON 对象")


def _repair_prompt(schema: type, error: str) -> str:
    try:
        schema_text = json.dumps(schema.model_json_schema(), ensure_ascii=False)[:1500]
    except Exception:  # noqa: BLE001 - 非 Pydantic 类型时降级
        schema_text = "(无法生成 schema 文本，请按字段名与类型输出)"
    return (
        "上一次回复不是合法 JSON 或不符合目标 Schema。错误信息："
        f"{error}\n请严格只输出一个 JSON 对象，必须满足下面的 JSON Schema，"
        "不要输出任何解释、注释或 Markdown 代码围栏：\n"
        f"{schema_text}"
    )


def resolve_config_path(config_path: str | Path | None = None) -> Path:
    """解析路由配置路径：显式参数 > 环境变量 MODELS_CONFIG_PATH > 默认 models.yaml。

    ``MODELS_CONFIG_PATH`` 用于**临时切换供应商 / 模型配置**（例如“全部先走 DeepSeek”
    的联调模式），不修改决策④的默认 ``models.yaml``；相对路径按仓库根目录解析。
    """
    if config_path is not None:
        path = Path(config_path)
    else:
        raw = os.environ.get("MODELS_CONFIG_PATH", "").strip()
        path = Path(raw) if raw else DEFAULT_CONFIG_PATH
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


# ---------------------------------------------------------------- #
# 模型路由器
# ---------------------------------------------------------------- #
class ModelRouter:
    """按模块路由的模型调用统一入口（同步阻塞）。"""

    def __init__(
        self,
        config_path: str | Path | None = None,
        *,
        env_path: str | Path | None = None,
        load_env: bool = True,
        client_factory: ClientFactory | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        backoff_base: float = 0.5,
    ) -> None:
        self._env_path = Path(env_path) if env_path else DEFAULT_ENV_PATH
        if load_env:
            load_env_file(self._env_path)
        self._config_path = resolve_config_path(config_path)
        self._routes = load_route_configs(self._config_path)
        self._client_factory: ClientFactory = client_factory or _default_client_factory
        self._sleep = sleep_fn or time.sleep
        self._backoff_base = max(0.0, float(backoff_base))
        self._clients: dict[str, Any] = {}

    # ---------------- 公开只读配置 ---------------- #
    @property
    def routes(self) -> dict[str, RouteConfig]:
        """全部路由配置（副本，避免外部修改内部状态）。"""
        return dict(self._routes)

    def get_route_config(self, route: str) -> RouteConfig:
        """按模块名取路由配置；不存在时抛 UnknownRouteError。"""
        try:
            return self._routes[route]
        except KeyError:
            raise UnknownRouteError(route, sorted(self._routes)) from None

    # ---------------- 客户端管理 ---------------- #
    def _client_for(self, route: str) -> tuple[RouteConfig, Any]:
        config = self.get_route_config(route)
        api_key = os.environ.get(config.api_key_env, "").strip()
        if not api_key:
            raise MissingAPIKeyError(route, config.api_key_env, str(self._env_path))
        client = self._clients.get(route)
        if client is None:
            base_url = (
                os.environ.get(config.base_url_env, "").strip()
                or default_base_url(config.model)
            )
            client = self._client_factory(config, api_key, base_url)
            self._clients[route] = client
        return config, client

    def clear_client_cache(self) -> None:
        """清空已创建客户端缓存（测试 / 切换 Key 后使用）。"""
        self._clients.clear()

    def close(self) -> None:
        """关闭所有已创建客户端（尽力而为）。"""
        for client in self._clients.values():
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - 关闭失败不影响主流程
                    logger.debug("关闭模型客户端失败", exc_info=True)
        self._clients.clear()

    # ---------------- 请求构造与重试 ---------------- #
    def _backoff_seconds(self, attempt: int) -> float:
        return min(self._backoff_base * (2 ** (attempt - 1)), 8.0)

    def _build_kwargs(
        self,
        config: RouteConfig,
        messages: Sequence[Mapping[str, Any]],
        *,
        temperature: float | None,
        max_tokens: int | None,
        response_format: Mapping[str, Any] | None,
        extra: Mapping[str, Any] | None,
        stream: bool,
    ) -> dict[str, Any]:
        if not messages:
            raise ModelRouterError("messages 不能为空")
        kwargs: dict[str, Any] = {
            "model": config.model,
            "messages": [dict(message) for message in messages],
            "timeout": config.timeout,
        }
        effective_temperature = config.temperature if temperature is None else float(temperature)
        kwargs["temperature"] = effective_temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = int(max_tokens)
        if response_format is not None:
            kwargs["response_format"] = dict(response_format)
        if stream:
            kwargs["stream"] = True
        if extra:
            kwargs.update(dict(extra))
        return kwargs

    def _request_with_retry(self, config: RouteConfig, call: Callable[[], Any]) -> Any:
        """执行调用；超时/连接/限流/5xx 按 max_retries 指数退避重试。"""
        attempts = 0
        last_error: BaseException | None = None
        for attempt in range(1, config.max_retries + 2):
            attempts = attempt
            try:
                return call()
            except Exception as exc:  # noqa: BLE001 - 统一分类后包装
                last_error = exc
                if attempt <= config.max_retries and _is_retryable(exc):
                    delay = self._backoff_seconds(attempt)
                    logger.warning(
                        "模型调用失败，准备重试（route=%s, model=%s, attempt=%s/%s, "
                        "error=%s, delay=%.2fs）",
                        config.name,
                        config.model,
                        attempt,
                        config.max_retries + 1,
                        type(exc).__name__,
                        delay,
                    )
                    self._sleep(delay)
                    continue
                raise ModelCallError(config.name, config.model, attempts, exc) from exc
        raise ModelCallError(config.name, config.model, attempts, last_error or RuntimeError("未知错误"))

    # ---------------- 统一调用入口 ---------------- #
    def chat(
        self,
        route: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: Mapping[str, Any] | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> ChatResult:
        """非流式统一调用。"""
        config, client = self._client_for(route)
        kwargs = self._build_kwargs(
            config,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            extra=extra,
            stream=False,
        )
        response = self._request_with_retry(
            config, lambda: client.chat.completions.create(**kwargs)
        )
        return self._to_chat_result(route, config, response)

    def chat_stream(
        self,
        route: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> Iterator[StreamChunk]:
        """流式统一调用：逐块产出增量文本。

        - 建立连接阶段：按 max_retries 重试（超时/限流/5xx）；
        - 流式传输中途失败：不重放（避免重复输出），抛 ModelCallError。
        """
        config, client = self._client_for(route)
        kwargs = self._build_kwargs(
            config,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=None,
            extra=extra,
            stream=True,
        )
        stream = self._request_with_retry(
            config, lambda: client.chat.completions.create(**kwargs)
        )
        index = 0
        try:
            for chunk in stream:
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                text = getattr(delta, "content", None) if delta is not None else None
                finish_reason = getattr(choice, "finish_reason", None)
                if text:
                    yield StreamChunk(delta=str(text), index=index, finish_reason=None)
                    index += 1
                if finish_reason:
                    yield StreamChunk(delta="", index=index, finish_reason=str(finish_reason))
                    index += 1
        except Exception as exc:  # noqa: BLE001 - 流中途失败统一包装
            raise ModelCallError(config.name, config.model, 1, exc) from exc

    def chat_json(
        self,
        route: str,
        messages: Sequence[Mapping[str, Any]],
        schema: type,
        *,
        max_repair: int = 1,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra: Mapping[str, Any] | None = None,
        use_response_format: bool = True,
    ) -> JsonChatResult:
        """要求模型输出 JSON 并用 Pydantic Schema 校验；失败自动修复重试。

        - 首次带 `response_format={"type": "json_object"}`（供应商不支持时自动去掉重试一次）；
        - JSON 解析 / Schema 校验失败 → 把错误与 Schema 回填给模型重试 `max_repair` 次；
        - 仍失败抛 `ModelJSONError`，模块层可捕获后降级为纯文本输出。
        """
        config = self.get_route_config(route)
        response_format: Mapping[str, Any] | None = (
            {"type": "json_object"} if use_response_format else None
        )
        conversation: list[dict[str, Any]] = [dict(message) for message in messages]
        repair_attempts = 0
        last_error = ""
        raw_text = ""
        for repair in range(max_repair + 1):
            repair_attempts = repair
            try:
                result = self.chat(
                    route,
                    conversation,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                    extra=extra,
                )
            except ModelCallError as exc:
                # 部分供应商不支持 response_format → 去掉该参数再试一次
                if response_format is not None and _status_code_of(exc.last_error) == 400:
                    logger.info("route=%s 不支持 response_format，去掉该参数重试", route)
                    response_format = None
                    result = self.chat(
                        route,
                        conversation,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        response_format=None,
                        extra=extra,
                    )
                else:
                    raise
            raw_text = result.text
            try:
                data = _extract_json(raw_text)
                value = schema.model_validate(data)
                return JsonChatResult(value=value, result=result, repair_attempts=repair)
            except Exception as exc:  # noqa: BLE001 - JSON / 校验错误统一回填
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "模型 JSON 校验失败（route=%s, repair=%s/%s）：%s",
                    route,
                    repair,
                    max_repair,
                    last_error,
                )
                if repair >= max_repair:
                    break
                conversation = conversation + [
                    {"role": "assistant", "content": raw_text},
                    {"role": "user", "content": _repair_prompt(schema, last_error)},
                ]
        raise ModelJSONError(config.name, config.model, raw_text, last_error)

    # ---------------- 内部工具 ---------------- #
    @staticmethod
    def _to_chat_result(route: str, config: RouteConfig, response: Any) -> ChatResult:
        choices = getattr(response, "choices", None) or []
        text = ""
        finish_reason: str | None = None
        if choices:
            choice = choices[0]
            message = getattr(choice, "message", None)
            if message is not None:
                content = getattr(message, "content", None)
                if content is None and isinstance(message, Mapping):
                    content = message.get("content")
                text = str(content or "")
            finish_reason = getattr(choice, "finish_reason", None)
        model = str(getattr(response, "model", "") or config.model)
        return ChatResult(
            route=route,
            model=model,
            text=text,
            finish_reason=str(finish_reason) if finish_reason else None,
            usage=_extract_usage(getattr(response, "usage", None)),
            raw=response,
        )


# ---------------------------------------------------------------- #
# 默认 OpenAI 兼容客户端
# ---------------------------------------------------------------- #
def _default_client_factory(config: RouteConfig, api_key: str, base_url: str) -> Any:
    """创建 OpenAI 兼容客户端；重试与超时由本模块统一控制（关闭 SDK 内部重试）。"""
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - venv 已安装 openai
        raise RouterConfigError(
            "缺少 openai 依赖，请执行 pip install -r requirements.txt"
        ) from exc
    client_kwargs: dict[str, Any] = {
        "api_key": api_key,
        "timeout": config.timeout,
        "max_retries": 0,  # 重试统一由 ModelRouter 负责
    }
    if base_url:
        client_kwargs["base_url"] = base_url
    return OpenAI(**client_kwargs)


# ---------------------------------------------------------------- #
# 进程内单例（业务层统一入口）
# ---------------------------------------------------------------- #
_router_singleton: ModelRouter | None = None


def get_router() -> ModelRouter:
    """获取进程内单例路由器（首次调用时加载 .env 与 models.yaml）。"""
    global _router_singleton
    if _router_singleton is None:
        _router_singleton = ModelRouter()
    return _router_singleton


def reset_router() -> None:
    """重置单例（测试 / 热更新配置时使用）。"""
    global _router_singleton
    if _router_singleton is not None:
        _router_singleton.close()
    _router_singleton = None


__all__ = [
    "ChatResult",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_ENV_PATH",
    "JsonChatResult",
    "MissingAPIKeyError",
    "ModelCallError",
    "ModelJSONError",
    "ModelRouter",
    "ModelRouterError",
    "REQUIRED_ROUTES",
    "RouteConfig",
    "RouterConfigError",
    "StreamChunk",
    "UnknownRouteError",
    "Usage",
    "default_base_url",
    "get_router",
    "load_env_file",
    "load_route_configs",
    "reset_router",
]
