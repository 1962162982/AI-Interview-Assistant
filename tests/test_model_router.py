"""模型路由器测试（v2 · 阶段 1 实现）

覆盖：
- models.yaml 五路由加载与决策④校验（evaluation ≠ cross_review、四模块独立模型）
- 无 Key 时清晰报错（只报环境变量名，不泄露 Key）
- 统一 chat()：按路由取 model / temperature / timeout
- 重试与超时：可重试错误重试、鉴权错误不重试、重试耗尽抛 ModelCallError
- 流式 chat_stream()：增量 chunk + finish_reason
- chat_json()：Markdown 围栏兼容 / Schema 校验 / 修复重试 / 供应商不支持
  response_format 时自动降级
- .env 加载：仅读取、不覆盖既有环境变量

全部使用 fake OpenAI 客户端，**不依赖真实 API Key 与网络**。
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from backend.services import model_router
from backend.services.model_router import (
    DEFAULT_CONFIG_PATH,
    REPO_ROOT,
    REQUIRED_ROUTES,
    ChatResult,
    MissingAPIKeyError,
    ModelCallError,
    ModelJSONError,
    ModelRouter,
    RouterConfigError,
    StreamChunk,
    UnknownRouteError,
    load_env_file,
    load_route_configs,
    resolve_config_path,
)

CONFIG_PATH = Path(__file__).resolve().parents[1] / "backend" / "config" / "models.yaml"
#: 临时“全 DeepSeek”联调配置（通过 MODELS_CONFIG_PATH 启用）
DEEPSEEK_CONFIG_REL = "backend/config/models.deepseek.yaml"
DEEPSEEK_CONFIG_PATH = CONFIG_PATH.parent / "models.deepseek.yaml"


# ------------------------------------------------------------------ #
# fake OpenAI 兼容客户端
# ------------------------------------------------------------------ #
class FakeUsage:
    def __init__(self, prompt_tokens: int = 10, completion_tokens: int = 5) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class FakeDelta:
    def __init__(self, content: str | None) -> None:
        self.content = content


class FakeChoice:
    def __init__(
        self,
        content: str | None = None,
        finish_reason: str | None = "stop",
        delta: str | None = None,
    ) -> None:
        self.message = SimpleNamespace(content=content)
        self.delta = FakeDelta(delta) if delta is not None else None
        self.finish_reason = finish_reason


class FakeCompletion:
    def __init__(
        self, text: str = "ok", finish_reason: str = "stop", model: str = "fake-model"
    ) -> None:
        self.choices = [FakeChoice(content=text, finish_reason=finish_reason)]
        self.usage = FakeUsage()
        self.model = model


class FakeStreamChunk:
    def __init__(self, delta: str | None = None, finish_reason: str | None = None) -> None:
        self.choices = [FakeChoice(content=None, finish_reason=finish_reason, delta=delta)]


class FakeCompletions:
    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("fake 脚本已耗尽，但仍有调用")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeClient:
    def __init__(self, script: list) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(script))
        self.closed = False

    def close(self) -> None:
        self.closed = True


class ApiStatusError(Exception):
    """模拟 openai 的带 status_code 异常（鉴权 / 限流 / 参数错误）。"""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def make_router(scripts: list | None = None, **kwargs) -> tuple[ModelRouter, FakeClient]:
    """构造注入了 fake 客户端的路由器（所有 route 共享同一个 fake）。"""
    client = FakeClient(scripts or [FakeCompletion()])
    router = ModelRouter(
        CONFIG_PATH,
        load_env=False,
        client_factory=lambda config, api_key, base_url: client,
        sleep_fn=lambda _seconds: None,
        **kwargs,
    )
    return router, client


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """清理所有路由 Key/BaseURL 环境变量，避免真实 .env 或系统变量干扰。"""
    for config in load_route_configs(CONFIG_PATH).values():
        monkeypatch.delenv(config.api_key_env, raising=False)
        monkeypatch.delenv(config.base_url_env, raising=False)
    monkeypatch.setenv("RESUME_DIAGNOSIS_API_KEY", "test-key-for-mock")
    monkeypatch.setenv("JD_MATCHING_API_KEY", "test-key-for-mock")
    monkeypatch.setenv("INTERVIEW_AGENT_API_KEY", "test-key-for-mock")
    monkeypatch.setenv("EVALUATION_API_KEY", "test-key-for-mock")
    monkeypatch.setenv("CROSS_REVIEW_API_KEY", "test-key-for-mock")


# ------------------------------------------------------------------ #
# 配置加载与决策④约束
# ------------------------------------------------------------------ #
def test_load_route_configs_contains_required_routes():
    configs = load_route_configs(CONFIG_PATH)
    assert set(REQUIRED_ROUTES).issubset(configs.keys())
    assert configs["resume_diagnosis"].model == "deepseek-chat"
    assert configs["jd_matching"].model == "glm-4-air"
    assert configs["interview_agent"].model == "glm-4-flash"
    assert configs["evaluation"].model == "glm-4-plus"
    assert configs["cross_review"].model == "deepseek-chat"


def test_decision4_evaluation_and_cross_review_use_different_models():
    configs = load_route_configs(CONFIG_PATH)
    assert configs["evaluation"].model != configs["cross_review"].model


def test_decision4_four_modules_have_distinct_models():
    configs = load_route_configs(CONFIG_PATH)
    module_models = [
        configs[route].model
        for route in ("resume_diagnosis", "jd_matching", "interview_agent", "evaluation")
    ]
    assert len(set(module_models)) == len(module_models)


def test_context_window_tokens_configured():
    configs = load_route_configs(CONFIG_PATH)
    for route in REQUIRED_ROUTES:
        assert configs[route].context_window_tokens > 0
    assert configs["resume_diagnosis"].context_window_tokens == 65536
    assert configs["interview_agent"].context_window_tokens == 131072


def test_router_rejects_evaluation_and_cross_review_same_model(tmp_dir):
    text = CONFIG_PATH.read_text(encoding="utf-8").replace(
        "model: glm-4-plus", "model: deepseek-chat"
    )
    bad_config = tmp_dir / "models.yaml"
    bad_config.write_text(text, encoding="utf-8")
    with pytest.raises(RouterConfigError, match="cross_review 与 evaluation"):
        load_route_configs(bad_config)


def test_router_requires_all_routes(tmp_dir):
    good = CONFIG_PATH.read_text(encoding="utf-8")
    missing = good.split("cross_review:")[0]
    bad_config = tmp_dir / "models.yaml"
    bad_config.write_text(missing, encoding="utf-8")
    with pytest.raises(RouterConfigError, match="缺少必需路由"):
        load_route_configs(bad_config)


def test_missing_config_file_raises(tmp_dir):
    with pytest.raises(RouterConfigError, match="不存在"):
        load_route_configs(tmp_dir / "nope.yaml")


# ------------------------------------------------------------------ #
# 路由查询 / Key 缺失
# ------------------------------------------------------------------ #
def test_unknown_route_raises():
    router, _ = make_router()
    with pytest.raises(UnknownRouteError, match="未知模型路由"):
        router.chat("not_a_route", [{"role": "user", "content": "hi"}])


def test_missing_api_key_reports_env_var_only(monkeypatch):
    router, client = make_router()
    monkeypatch.delenv("RESUME_DIAGNOSIS_API_KEY")
    with pytest.raises(MissingAPIKeyError) as excinfo:
        router.chat("resume_diagnosis", [{"role": "user", "content": "hi"}])
    message = str(excinfo.value)
    assert "RESUME_DIAGNOSIS_API_KEY" in message
    assert ".env" in message
    assert client.chat.completions.calls == []


# ------------------------------------------------------------------ #
# chat(): 成功 / 配置生效 / temperature 覆盖
# ------------------------------------------------------------------ #
def test_chat_success_uses_route_config():
    router, client = make_router([FakeCompletion(text="诊断结果")])
    result = router.chat("resume_diagnosis", [{"role": "user", "content": "hi"}])
    assert isinstance(result, ChatResult)
    assert result.route == "resume_diagnosis"
    assert result.text == "诊断结果"
    assert result.finish_reason == "stop"
    assert result.usage.total_tokens == 15
    call = client.chat.completions.calls[0]
    assert call["model"] == "deepseek-chat"
    assert call["temperature"] == 0.3
    assert call["timeout"] == 60
    assert call["messages"] == [{"role": "user", "content": "hi"}]


def test_chat_temperature_override_and_extra():
    router, client = make_router([FakeCompletion(text="ok")])
    router.chat(
        "evaluation",
        [{"role": "user", "content": "score"}],
        temperature=0.5,
        max_tokens=128,
        extra={"stop": ["\n"]},
    )
    call = client.chat.completions.calls[0]
    assert call["temperature"] == 0.5
    assert call["max_tokens"] == 128
    assert call["stop"] == ["\n"]


def test_chat_empty_messages_rejected():
    router, _ = make_router()
    with pytest.raises(model_router.ModelRouterError, match="messages 不能为空"):
        router.chat("resume_diagnosis", [])


# ------------------------------------------------------------------ #
# 重试 / 超时兜底
# ------------------------------------------------------------------ #
def test_chat_retries_on_timeout_then_succeeds():
    router, client = make_router([TimeoutError("timeout"), FakeCompletion(text="重试成功")])
    result = router.chat("resume_diagnosis", [{"role": "user", "content": "hi"}])
    assert result.text == "重试成功"
    assert len(client.chat.completions.calls) == 2


def test_chat_retries_on_rate_limit_then_succeeds():
    router, client = make_router([ApiStatusError(429), FakeCompletion(text="ok")])
    router.chat("resume_diagnosis", [{"role": "user", "content": "hi"}])
    assert len(client.chat.completions.calls) == 2


def test_chat_does_not_retry_auth_error():
    router, client = make_router([ApiStatusError(401), FakeCompletion(text="不应被调用")])
    with pytest.raises(ModelCallError) as excinfo:
        router.chat("resume_diagnosis", [{"role": "user", "content": "hi"}])
    assert len(client.chat.completions.calls) == 1
    assert excinfo.value.attempts == 1


def test_chat_retries_exhausted_raises_model_call_error():
    # resume_diagnosis 的 max_retries=2 → 最多 3 次尝试
    router, client = make_router([TimeoutError("t1"), TimeoutError("t2"), TimeoutError("t3")])
    with pytest.raises(ModelCallError) as excinfo:
        router.chat("resume_diagnosis", [{"role": "user", "content": "hi"}])
    assert excinfo.value.attempts == 3
    assert len(client.chat.completions.calls) == 3


# ------------------------------------------------------------------ #
# 流式输出
# ------------------------------------------------------------------ #
def test_chat_stream_yields_deltas_and_finish_reason():
    # 一次 create(stream=True) 返回可迭代的 chunk 序列（模拟 openai Stream）
    script = [
        [
            FakeStreamChunk(delta="你"),
            FakeStreamChunk(delta="好"),
            FakeStreamChunk(finish_reason="stop"),
        ]
    ]
    router, client = make_router(script)
    chunks = list(router.chat_stream("interview_agent", [{"role": "user", "content": "hi"}]))
    assert isinstance(chunks[0], StreamChunk)
    assert [chunk.delta for chunk in chunks] == ["你", "好", ""]
    assert chunks[-1].finish_reason == "stop"
    call = client.chat.completions.calls[0]
    assert call["model"] == "glm-4-flash"
    assert call["stream"] is True


# ------------------------------------------------------------------ #
# chat_json()
# ------------------------------------------------------------------ #
class Answer(BaseModel):
    answer: str


def test_chat_json_parses_markdown_fence():
    router, client = make_router([FakeCompletion(text='```json\n{"answer": "42"}\n```')])
    parsed = router.chat_json("jd_matching", [{"role": "user", "content": "q"}], Answer)
    assert parsed.value == Answer(answer="42")
    assert parsed.repair_attempts == 0
    call = client.chat.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}


def test_chat_json_repairs_invalid_output():
    router, client = make_router(
        [FakeCompletion(text="抱歉，我无法输出 JSON"), FakeCompletion(text='{"answer": "42"}')]
    )
    parsed = router.chat_json(
        "resume_diagnosis", [{"role": "user", "content": "q"}], Answer, max_repair=1
    )
    assert parsed.value.answer == "42"
    assert parsed.repair_attempts == 1
    calls = client.chat.completions.calls
    assert len(calls) == 2
    # 修复重试应把上一次回答与修复指令追加进 messages
    assert len(calls[1]["messages"]) == len(calls[0]["messages"]) + 2


def test_chat_json_raises_after_repairs():
    router, _ = make_router([FakeCompletion(text="not json"), FakeCompletion(text="still bad")])
    with pytest.raises(ModelJSONError) as excinfo:
        router.chat_json("resume_diagnosis", [{"role": "user", "content": "q"}], Answer)
    assert excinfo.value.raw_text == "still bad"


def test_chat_json_drops_response_format_when_provider_rejects():
    router, client = make_router([ApiStatusError(400), FakeCompletion(text='{"answer": "ok"}')])
    parsed = router.chat_json("evaluation", [{"role": "user", "content": "q"}], Answer)
    assert parsed.value.answer == "ok"
    calls = client.chat.completions.calls
    assert len(calls) == 2
    assert "response_format" not in calls[1]


# ------------------------------------------------------------------ #
# .env 加载（仅读取、不覆盖既有环境变量）
# ------------------------------------------------------------------ #
def test_load_env_file_does_not_override_existing(monkeypatch, tmp_dir):
    env_file = tmp_dir / ".env"
    env_file.write_text(
        "EXISTING_VAR_FOR_TEST=from_file\nNEW_VAR_FOR_TEST=from_file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXISTING_VAR_FOR_TEST", "from_process")
    monkeypatch.delenv("NEW_VAR_FOR_TEST", raising=False)
    assert load_env_file(env_file) is True
    assert os.environ["EXISTING_VAR_FOR_TEST"] == "from_process"
    assert os.environ["NEW_VAR_FOR_TEST"] == "from_file"


def test_load_env_file_missing_returns_false(tmp_dir):
    assert load_env_file(tmp_dir / "no-such.env") is False


# ------------------------------------------------------------------ #
# 配置路径解析（MODELS_CONFIG_PATH：临时切换供应商 / 模型配置）
# ------------------------------------------------------------------ #
def test_resolve_config_path_default_when_no_param_and_no_env(monkeypatch):
    """无显式参数、无环境变量 → 默认 backend/config/models.yaml。"""
    monkeypatch.delenv("MODELS_CONFIG_PATH", raising=False)
    assert resolve_config_path() == DEFAULT_CONFIG_PATH
    assert resolve_config_path(None) == DEFAULT_CONFIG_PATH


def test_resolve_config_path_relative_env_is_resolved_from_repo_root(monkeypatch):
    """环境变量为相对路径 → 按仓库根目录解析。"""
    monkeypatch.setenv("MODELS_CONFIG_PATH", DEEPSEEK_CONFIG_REL)
    resolved = resolve_config_path()
    assert resolved == REPO_ROOT / DEEPSEEK_CONFIG_REL
    assert resolved == DEEPSEEK_CONFIG_PATH
    assert resolved.is_absolute()
    assert resolved.is_file(), f"临时配置不存在：{resolved}"


def test_resolve_config_path_explicit_param_overrides_env(monkeypatch):
    """显式参数优先于 MODELS_CONFIG_PATH（绝对 / 相对路径都支持）。"""
    monkeypatch.setenv("MODELS_CONFIG_PATH", DEEPSEEK_CONFIG_REL)
    assert resolve_config_path(CONFIG_PATH) == CONFIG_PATH
    assert resolve_config_path("backend/config/models.yaml") == CONFIG_PATH


def test_resolve_config_path_absolute_env_used_as_is(monkeypatch):
    """环境变量为绝对路径 → 原样使用。"""
    monkeypatch.setenv("MODELS_CONFIG_PATH", str(DEEPSEEK_CONFIG_PATH))
    assert resolve_config_path() == DEEPSEEK_CONFIG_PATH


def test_resolve_config_path_blank_env_falls_back_to_default(monkeypatch):
    """环境变量为空串 / 空白 → 回退默认配置。"""
    monkeypatch.setenv("MODELS_CONFIG_PATH", "   ")
    assert resolve_config_path() == DEFAULT_CONFIG_PATH


def test_model_router_loads_temporary_deepseek_config_from_env(monkeypatch):
    """env + load_env=False → ModelRouter 加载临时“全 DeepSeek”配置的 5 条路由。"""
    monkeypatch.setenv("MODELS_CONFIG_PATH", DEEPSEEK_CONFIG_REL)
    router = ModelRouter(load_env=False)
    models = {route: config.model for route, config in router.routes.items()}
    assert set(models) == set(REQUIRED_ROUTES)
    assert models["resume_diagnosis"] == "deepseek-v4-pro"
    assert models["jd_matching"] == "deepseek-flash"
    assert models["interview_agent"] == "deepseek-flash"
    assert models["evaluation"] == "deepseek-v4-pro"
    assert models["cross_review"] == "deepseek-flash"
    # 决策④原则（评分与复核异模型）在临时配置中仍成立
    assert models["evaluation"] != models["cross_review"]
    # 上下文窗口取自临时配置（1M）
    assert router.get_route_config("interview_agent").context_window_tokens == 1048576


def test_model_router_explicit_config_ignores_env(monkeypatch):
    """显式 config_path 优先于 MODELS_CONFIG_PATH：显式传默认配置时不受 env 影响。"""
    monkeypatch.setenv("MODELS_CONFIG_PATH", DEEPSEEK_CONFIG_REL)
    router = ModelRouter(CONFIG_PATH, load_env=False)
    assert router.get_route_config("resume_diagnosis").model == "deepseek-chat"
    assert router.get_route_config("jd_matching").model == "glm-4-air"

