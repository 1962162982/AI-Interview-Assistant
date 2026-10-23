"""元信息路由（阶段 1 实现）：模型路由表脱敏查询。

GET /api/meta/routes —— 返回 5 条路由的 model / 温度 / 上下文窗口等摘要，
**不含任何 API Key 或 Key 值**，用于联调自查“模块→模型”分配（决策④）。
"""
from __future__ import annotations

from fastapi import APIRouter

from ..services.model_router import get_router
from .common import ok

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/routes")
def list_model_routes() -> dict:
    """列出 models.yaml 中的全部模型路由（脱敏）。"""
    model_router = get_router()
    routes = [
        {
            "route": config.name,
            "model": config.model,
            "temperature": config.temperature,
            "context_window_tokens": config.context_window_tokens,
            "stream": config.stream,
            "timeout": config.timeout,
            "max_retries": config.max_retries,
        }
        for config in sorted(model_router.routes.values(), key=lambda item: item.name)
    ]
    return ok({"routes": routes})
