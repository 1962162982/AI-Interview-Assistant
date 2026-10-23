"""API 路由聚合（阶段 1 实现）

`backend/main.py` 只挂载本模块的 `api_router`，即 `/api` 前缀下的四组业务路由
（resume / jd / interview / report）+ 元信息路由（meta）。

阶段 1：四组业务路由均为 501 薄壳占位，契约见 docs/api.md。
"""
from __future__ import annotations

from fastapi import APIRouter

from . import interview, jd, meta, report, resume

api_router = APIRouter(prefix="/api")
api_router.include_router(meta.router)
api_router.include_router(resume.router)
api_router.include_router(jd.router)
api_router.include_router(interview.router)
api_router.include_router(report.router)

__all__ = ["api_router"]
