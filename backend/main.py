"""AI 模拟面试与简历诊断助手 · 后端入口（v2 · 阶段 1）

职责：
- 保留骨架期 `GET /health`（契约不变）；
- 挂载 `/api` 下的四组业务路由（薄壳占位）+ 元信息路由，契约见 docs/api.md；
- CORS（供 Vue 3 开发服务器联调，默认 5173 / 3000）；
- 统一响应壳与错误处理：HTTPException / 参数校验错误 / 未捕获异常统一为
  `{"code", "message", "data"}`（错误码见 docs/api.md）。

启动：`uvicorn backend.main:app --reload`（或 `python -m backend.main`）。
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api import api_router
from .api.common import CODE_INTERNAL, CODE_INVALID_PARAM, STATUS_DEFAULT_CODE
from .services.model_router import load_env_file

logger = logging.getLogger(__name__)

# 加载项目根目录 .env（仅读取，不覆盖已存在环境变量；Key 不打印、不落日志）
load_env_file()

# Vue 3 / Vite 开发服务器默认端口，可用 CORS_ORIGINS 覆盖（逗号分隔）
DEFAULT_CORS_ORIGINS: tuple[str, ...] = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


def _cors_origins() -> list[str]:
    raw = os.environ.get("CORS_ORIGINS", "").strip()
    if not raw:
        return list(DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app = FastAPI(
    title="AI Interview Assistant",
    version="0.2.0",
    description=(
        "AI 模拟面试与简历诊断助手后端。阶段 1：/health 与 /api/meta/routes 可用，"
        "四组业务路由为 501 薄壳占位；接口契约见 docs/api.md。"
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    """健康检查（骨架期探活用，契约不变）。"""
    return {"status": "ok"}


# ------------------------------------------------------------------ #
# 统一错误响应壳（docs/api.md 错误码表）
# ------------------------------------------------------------------ #
def _error_payload(status_code: int, detail: object) -> dict:
    if isinstance(detail, dict) and "code" in detail:
        return {
            "code": detail.get("code", CODE_INTERNAL),
            "message": str(detail.get("message", "")),
            "data": None,
        }
    return {
        "code": STATUS_DEFAULT_CODE.get(status_code, CODE_INTERNAL),
        "message": str(detail),
        "data": None,
    }


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """把 HTTPException 统一转为响应壳（保留业务码与响应头）。"""
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(exc.status_code, exc.detail),
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """参数校验失败 → 400 + code=1001（不泄露内部堆栈）。"""
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", ()))
        parts.append(f"{location}: {error.get('msg', '参数不合法')}")
    return JSONResponse(
        status_code=400,
        content={
            "code": CODE_INVALID_PARAM,
            "message": "参数校验失败：" + "；".join(parts),
            "data": None,
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底异常 → 500 + code=5000（日志保留堆栈，响应不泄露细节）。"""
    logger.exception("未捕获异常：%s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"code": CODE_INTERNAL, "message": "内部错误，请稍后重试", "data": None},
    )


if __name__ == "__main__":  # 本地演示入口：python -m backend.main
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )
