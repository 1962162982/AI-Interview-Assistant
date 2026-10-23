"""路由层（D2 接线）：模拟面试接口（模块 C；契约见 docs/api.md「五」）

服务：`backend.services.interview_agent.InterviewAgent`（T3，进程内单例会话语义）。
- 场景三选一：`intern`（默认）/ `autumn` / `grad`，请求层用 Literal 校验；
- `POST /sessions` → 首问；`POST /turns` → 下一题；`POST /turns/stream` → SSE 流式；
  `GET /sessions/{sid}` → 状态；`POST /sessions/{sid}/end` → transcript + report_input；
- 异常映射：会话不存在 → 3001；参数非法 → 1001；模型调用失败 → 2001；
  SSE 流内错误以 `{"event":"error","code":...,"retryable":...}` 事件返回。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import deps
from .common import call_service, ok, sse_error_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/interview", tags=["interview"])


class CreateSessionRequest(BaseModel):
    """POST /api/interview/sessions 请求体。"""

    resume_text: str = Field(min_length=1, description="简历纯文本")
    jd_text: str | None = Field(default=None, description="目标 JD 纯文本（可选）")
    scene: str = Field(
        default="intern",
        pattern="^(intern|autumn|grad)$",
        description="场景：intern / autumn / grad（默认 intern）",
    )
    company: str | None = Field(default=None, description="公司名（角色卡变量，可选）")
    role: str | None = Field(default=None, description="岗位名（角色卡变量，可选）")
    style: str | None = Field(default=None, description="面试官风格（角色卡变量，可选）")


class TurnRequest(BaseModel):
    """POST /api/interview/sessions/{session_id}/turns 请求体。"""

    answer: str = Field(min_length=1, description="候选人回答")


# SSE 响应头：禁止中间层缓冲，保证逐字输出
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.post("/sessions")
def create_session(payload: CreateSessionRequest):
    """创建面试会话并返回首问（data：session_id / question / state）。"""
    agent = deps.get_interview_agent()
    result = call_service(
        lambda: deps.call_agent(
            agent,
            lambda: agent.create_session(
                payload.resume_text,
                jd_text=payload.jd_text,
                scene=payload.scene,
                company=payload.company or "",
                role=payload.role or "",
                style=payload.style or "",
            ),
        )
    )
    session_id = result["session_id"]
    state = call_service(
        lambda: deps.call_agent(agent, lambda: agent.get_session(session_id))
    )
    return ok(
        {
            "session_id": session_id,
            "question": result.get("question"),
            "state": state,
        }
    )


@router.post("/sessions/{session_id}/turns")
def create_turn(session_id: str, payload: TurnRequest):
    """提交回答 → 下一题（data：session_id / turn_index / question / state）。"""
    agent = deps.get_interview_agent()
    result = call_service(
        lambda: deps.call_agent(agent, lambda: agent.submit_answer(session_id, payload.answer))
    )
    return ok(result)


@router.post("/sessions/{session_id}/turns/stream")
def stream_turn(session_id: str, payload: TurnRequest):
    """提交回答 → 下一题（SSE：delta / done / error 事件）。"""
    agent = deps.get_interview_agent()
    # 先做存在性校验：HTTP 头发出后无法再改 404，需在流开始前抛出
    call_service(lambda: deps.call_agent(agent, lambda: agent.get_session(session_id)))

    def event_stream() -> Iterator[str]:
        try:
            for event in agent.stream_turn(session_id, payload.answer):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001 - 流内错误只能以事件形式返回
            logger.warning("SSE 流内错误：%s: %s", type(exc).__name__, exc)
            payload_event = sse_error_payload(exc)
            yield f"data: {json.dumps(payload_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    """查询面试会话状态（data：会话 snapshot）。"""
    agent = deps.get_interview_agent()
    return ok(call_service(lambda: deps.call_agent(agent, lambda: agent.get_session(session_id))))


@router.post("/sessions/{session_id}/end")
def end_session(session_id: str):
    """结束面试并返回完整对话记录（data：session_id / transcript / state / report_input）。"""
    agent = deps.get_interview_agent()
    return ok(call_service(lambda: deps.call_agent(agent, lambda: agent.end_session(session_id))))
