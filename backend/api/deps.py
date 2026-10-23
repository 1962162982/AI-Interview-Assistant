"""API 层依赖提供（D2 接线）：进程内服务单例。

- 面试会话状态保存在 `InterviewAgent` 实例内存中，因此 API 层必须复用同一个实例
  （interview 路由与 report 路由共享），否则 `GET /sessions/{sid}` / `end` /
  `report/generate` 找不到会话；
- 单例延迟创建，避免 api 模块 import 时拉起服务实现与 .env 加载；
- 测试可通过 `set_interview_agent()` 注入 fake，或 monkeypatch 本模块函数。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .common import CODE_NOT_FOUND, api_error

_interview_agent: Any = None


def _is_session_not_found(exc: BaseException) -> bool:
    """T3 当前用 `ValueError("会话不存在: ...")` 表示会话不存在；适配层识别后转 3001。

    TODO：T3 若后续提供 `SessionNotFoundError`，优先按类型判断。
    """
    return isinstance(exc, ValueError) and "会话不存在" in str(exc)


def call_agent(agent: Any, func: Callable[[], Any]) -> Any:
    """执行 InterviewAgent 方法：会话不存在 → 404/3001，其余异常交给 call_service 映射。"""
    try:
        return func()
    except ValueError as exc:
        if _is_session_not_found(exc):
            raise api_error(404, CODE_NOT_FOUND, "面试会话不存在或已过期") from exc
        raise


def get_interview_agent() -> Any:
    """获取面试官 Agent 单例（首次调用时创建 InterviewAgent）。"""
    global _interview_agent
    if _interview_agent is None:
        from ..services.interview_agent import InterviewAgent

        _interview_agent = InterviewAgent()
    return _interview_agent


def set_interview_agent(agent: Any) -> None:
    """注入面试官 Agent 实例（测试 / 演示使用）。"""
    global _interview_agent
    _interview_agent = agent


def reset_interview_agent() -> None:
    """重置面试官 Agent 单例（测试使用）。"""
    global _interview_agent
    _interview_agent = None


__all__ = ["get_interview_agent", "reset_interview_agent", "set_interview_agent"]
