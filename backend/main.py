"""AI 模拟面试与简历诊断助手 · 后端入口（v3 · 阶段 3）

职责：
- 保留骨架期 `GET /health`（契约不变）；
- 挂载 `/api` 下的四组业务路由 + 元信息路由，契约见 docs/api.md（15 条接口不变）；
- CORS（供 Vue 3 开发服务器联调，默认 5173 / 3000，可用 CORS_ORIGINS 覆盖）；
- 统一响应壳与错误处理：HTTPException / 参数校验错误 / 未捕获异常统一为
  `{"code", "message", "data"}`（错误码见 docs/api.md）；
- **前端静态托管（阶段 3 可选）**：`frontend/dist` 存在时，由同一 FastAPI 进程托管
  `/assets/*` 静态资源与 SPA 回退；dist 不存在时行为不变（404，不影响开发与测试）。

启动：`uvicorn backend.main:app --reload`（或 `python -m backend.main`）。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
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


# ------------------------------------------------------------------ #
# 前端静态托管（阶段 3 可选）：frontend/dist 存在时由后端单进程托管
# ------------------------------------------------------------------ #
REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIST_DIR = REPO_ROOT / "frontend" / "dist"

# 静态托管**不接管**的路径前缀（业务 API / 健康检查 / 文档 / 静态资源自身）
STATIC_RESERVED_PREFIXES: tuple[str, ...] = ("/api", "/health", "/assets", "/docs", "/redoc")
STATIC_RESERVED_EXACT: frozenset[str] = frozenset({"/openapi.json"})


def get_frontend_dist_dir() -> Path | None:
    """返回可托管的前端构建目录；不存在返回 None。

    延迟到请求时判断（便于测试 monkeypatch，也允许构建产物出现后无需重启）。
    """
    dist = FRONTEND_DIST_DIR
    return dist if dist.is_dir() else None


def _is_reserved_static_path(path: str) -> bool:
    """判断路径是否属于 API / 健康检查 / 文档等**不得**做 SPA 回退的保留路径。"""
    if path in STATIC_RESERVED_EXACT:
        return True
    return any(path == prefix or path.startswith(prefix + "/") for prefix in STATIC_RESERVED_PREFIXES)


def _resolve_dist_file(dist: Path, relative: str) -> Path | None:
    """安全解析 dist 内文件（拒绝 `..` 越界）；不存在/越界返回 None。"""
    relative = relative.lstrip("/")
    if not relative:
        return None
    try:
        dist_root = dist.resolve()
        candidate = (dist / relative).resolve()
    except OSError:
        return None
    if candidate != dist_root and dist_root not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


def _static_not_found() -> StarletteHTTPException:
    return StarletteHTTPException(status_code=404, detail="Not Found")


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
# 前端静态托管（阶段 3 可选）：仅当 frontend/dist 存在时生效
# ------------------------------------------------------------------ #
@app.get("/assets/{asset_path:path}", include_in_schema=False)
def serve_frontend_asset(asset_path: str):
    """`/assets/*` → `frontend/dist/assets/*`；dist 或文件缺失 → 404。"""
    dist = get_frontend_dist_dir()
    target = _resolve_dist_file(dist, f"assets/{asset_path}") if dist is not None else None
    if target is None:
        raise _static_not_found()
    return FileResponse(target)


@app.get("/{full_path:path}", include_in_schema=False)
def serve_spa(full_path: str):
    """SPA 回退：非保留路径的 GET → `frontend/dist/index.html`；dist 缺失 → 404。"""
    request_path = "/" + full_path
    if _is_reserved_static_path(request_path):
        raise _static_not_found()
    dist = get_frontend_dist_dir()
    if dist is None or not (dist / "index.html").is_file():
        raise _static_not_found()
    target = _resolve_dist_file(dist, full_path)
    if target is not None:
        return FileResponse(target)
    return FileResponse(dist / "index.html", media_type="text/html")


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
