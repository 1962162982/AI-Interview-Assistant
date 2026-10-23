"""API 层公共约定（D2 接线 · 统一响应壳 / 错误码映射）

与 docs/api.md「一、通用约定」保持一致：
- 统一响应壳：`{"code": int, "message": str, "data": object|null}`
- 错误码：0 / 1001 / 1002 / 2001 / 2002 / 2003 / 3001 / 5000
- 服务层异常映射（D2）：
  - 解析失败 → 1002；参数 / 数据非法 → 1001；资源不存在 → 3001；
  - 模型调用失败 / 缺 Key → 2001；模型输出 JSON 校验失败 → 2002；
  - 配置违约 / 未预期异常 → 5000。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# /api 统一前缀（docs/api.md）
API_PREFIX = "/api"

# 错误码（与 docs/api.md 表格一一对应）
CODE_OK = 0
CODE_INVALID_PARAM = 1001
CODE_PARSE_FAILED = 1002
CODE_MODEL_FAILED = 2001
CODE_JSON_INVALID = 2002
CODE_NOT_IMPLEMENTED = 2003
CODE_NOT_FOUND = 3001
CODE_INTERNAL = 5000

# HTTP 状态码 → 默认业务码（异常处理器使用）
STATUS_DEFAULT_CODE: dict[int, int] = {
    400: CODE_INVALID_PARAM,
    404: CODE_NOT_FOUND,
    422: CODE_INVALID_PARAM,
    429: CODE_MODEL_FAILED,
    500: CODE_INTERNAL,
    501: CODE_NOT_IMPLEMENTED,
    502: CODE_MODEL_FAILED,
}

# 业务码 → 推荐 HTTP 状态码（服务异常映射使用）
CODE_TO_STATUS: dict[int, int] = {
    CODE_INVALID_PARAM: 400,
    CODE_PARSE_FAILED: 400,
    CODE_MODEL_FAILED: 502,
    CODE_JSON_INVALID: 502,
    CODE_NOT_IMPLEMENTED: 501,
    CODE_NOT_FOUND: 404,
    CODE_INTERNAL: 500,
}

# SSE `error` 事件中可重试的业务码（超时 / 限流 / 模型输出非法）
RETRYABLE_CODES: frozenset[int] = frozenset({CODE_MODEL_FAILED, CODE_JSON_INVALID})


def ok(data: Any = None) -> dict[str, Any]:
    """构造成功响应壳。"""
    return {"code": CODE_OK, "message": "ok", "data": data}


def fail(code: int, message: str, data: Any = None) -> dict[str, Any]:
    """构造业务错误响应壳（一般由异常处理器统一输出）。"""
    return {"code": code, "message": message, "data": data}


def api_error(status_code: int, code: int, message: str) -> HTTPException:
    """构造带业务码的 HTTPException（由 main.py 统一异常处理器转为响应壳）。"""
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def not_implemented(module: str, owner: str) -> HTTPException:
    """尚未实现的服务：501 + code=2003。"""
    return api_error(
        501,
        CODE_NOT_IMPLEMENTED,
        f"{module}尚未实现（由 {owner} 负责）",
    )


# ---------------------------------------------------------------------------
# 服务层异常 → 业务码映射（D2 接线）
# ---------------------------------------------------------------------------
def classify_service_exception(exc: BaseException) -> tuple[int, str]:
    """把服务层异常映射为 `(业务码, 对用户可见消息)`。

    异常类型通过**延迟导入**识别，避免 api 模块加载时拉起全部服务实现。
    """
    # 1) 简历解析（ParserError 是 ValueError 子类，必须优先判断）
    try:
        from ..services.parser import ParserError

        if isinstance(exc, ParserError):
            return CODE_PARSE_FAILED, f"文件解析失败：{exc}"
    except Exception:  # noqa: BLE001 - 映射失败时继续按通用规则处理
        pass

    # 2) 报告不存在
    try:
        from ..services.evaluation import ReportNotFoundError

        if isinstance(exc, ReportNotFoundError):
            return CODE_NOT_FOUND, str(exc)
    except Exception:  # noqa: BLE001
        pass

    # 3) 模型输出 JSON / 业务约定校验失败
    try:
        from ..services.model_router import ModelJSONError

        if isinstance(exc, ModelJSONError):
            return CODE_JSON_INVALID, f"模型输出 JSON 校验失败：{exc}"
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..services.jd_matching import JDStructureExtractionError, PromptOutputError

        if isinstance(exc, (JDStructureExtractionError, PromptOutputError)):
            return CODE_JSON_INVALID, f"模型输出不符合业务约定：{exc}"
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..services.evaluation import (
            CrossReviewValidationError,
            EvaluationValidationError,
        )

        if isinstance(exc, (EvaluationValidationError, CrossReviewValidationError)):
            return CODE_JSON_INVALID, f"模型输出不符合评分契约：{exc}"
    except Exception:  # noqa: BLE001
        pass

    # 4) 模型调用失败 / 缺 Key
    try:
        from ..services.model_router import ModelCallError, MissingAPIKeyError

        if isinstance(exc, (ModelCallError, MissingAPIKeyError)):
            return CODE_MODEL_FAILED, f"模型调用失败：{exc}"
    except Exception:  # noqa: BLE001
        pass

    # 5) 配置违约（决策④ / models.yaml / 路由配置）
    try:
        from ..services.model_router import RouterConfigError, UnknownRouteError

        if isinstance(exc, (RouterConfigError, UnknownRouteError)):
            return CODE_INTERNAL, "服务配置错误，请联系管理员"
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..services.evaluation import ModelSeparationError

        if isinstance(exc, ModelSeparationError):
            return CODE_INTERNAL, "评分与复核模型配置违约，请联系管理员"
    except Exception:  # noqa: BLE001
        pass

    # 6) 参数 / 业务数据非法（含 T3 会话不存在，见 api/interview.py 适配）
    if isinstance(exc, (ValueError, TypeError)):
        return CODE_INVALID_PARAM, str(exc)

    # 7) 未预期异常
    return CODE_INTERNAL, "内部错误，请稍后重试"


def translate_service_exception(exc: BaseException) -> HTTPException:
    """服务层异常 → 带业务码的 HTTPException。"""
    code, message = classify_service_exception(exc)
    return api_error(CODE_TO_STATUS.get(code, 500), code, message)


def call_service(func: Callable[[], Any]) -> Any:
    """执行服务调用：成功返回原值，异常转为统一响应壳（HTTPException 透传）。"""
    try:
        return func()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一映射为业务错误
        code, _ = classify_service_exception(exc)
        if code == CODE_INTERNAL:
            logger.exception("服务调用出现未预期异常：%s", type(exc).__name__)
        else:
            logger.warning("服务调用失败（code=%s, type=%s）：%s", code, type(exc).__name__, exc)
        raise translate_service_exception(exc) from exc


def sse_error_payload(exc: BaseException) -> dict[str, Any]:
    """构造 SSE 流内错误事件（HTTP 头已发出，只能以事件形式报错）。"""
    code, message = classify_service_exception(exc)
    return {
        "event": "error",
        "code": code,
        "message": message,
        "retryable": code in RETRYABLE_CODES,
    }


__all__ = [
    "API_PREFIX",
    "CODE_OK",
    "CODE_INVALID_PARAM",
    "CODE_PARSE_FAILED",
    "CODE_MODEL_FAILED",
    "CODE_JSON_INVALID",
    "CODE_NOT_IMPLEMENTED",
    "CODE_NOT_FOUND",
    "CODE_INTERNAL",
    "CODE_TO_STATUS",
    "RETRYABLE_CODES",
    "STATUS_DEFAULT_CODE",
    "api_error",
    "call_service",
    "classify_service_exception",
    "fail",
    "not_implemented",
    "ok",
    "sse_error_payload",
    "translate_service_exception",
]
