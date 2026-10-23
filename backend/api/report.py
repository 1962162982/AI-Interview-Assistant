"""路由层（D2 接线）：反馈报告接口（模块 D；契约见 docs/api.md「五」）

服务：`backend.services.evaluation`（T4）。
- `POST /api/report/generate`：`{session_id, turns?}` → `{report_id, evaluation, cross_review, markdown}`；
  - 传 `turns`：直接评分；
  - 不传：从模块 C 会话取数据（`end_session()` 幂等返回 transcript + report_input）；
- `GET /api/report/{rid}`：`{report_id, evaluation, cross_review}`；
- `GET /api/report/{rid}/markdown`：返回 text/markdown 原文（不套响应壳），错误走统一壳。

异常映射（由 `api.common.call_service` 统一处理）：
`ReportNotFoundError` → 3001；`MissingAPIKeyError` / `ModelCallError` → 2001；
`ModelJSONError` / `EvaluationValidationError` → 2002。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from . import deps
from .common import call_service, ok

router = APIRouter(prefix="/report", tags=["report"])


class GenerateReportRequest(BaseModel):
    """POST /api/report/generate 请求体。"""

    session_id: str = Field(min_length=1, description="面试会话 ID")
    turns: list[dict[str, Any]] | None = Field(
        default=None, description="对话记录（可选；不传则从模块 C 会话取）"
    )


def _session_payload_from_end(ended: dict[str, Any]) -> dict[str, Any]:
    """把 T3 `end_session()` 的 `{transcript, report_input}` 适配为评分服务入参。

    `generate_report_for_session` 需要 `turns` 字段（end 返回的是 `transcript`），
    因此显式映射；`report_input` 若带 `resume_text` / `jd_text` 也一并透传。
    """
    report_input = ended.get("report_input") or {}
    return {
        "session_id": ended.get("session_id") or report_input.get("session_id", ""),
        "turns": ended.get("transcript") or [],
        "scene": report_input.get("scene", ""),
        "company": report_input.get("company", ""),
        "role": report_input.get("role", ""),
        "jd": report_input.get("jd_text"),
        "resume": report_input.get("resume_text"),
    }


@router.post("/generate")
def generate_report(payload: GenerateReportRequest):
    """生成评分报告（4 维度 + 交叉复核 + Markdown）。"""
    from ..services import evaluation

    if payload.turns:
        report = call_service(
            lambda: evaluation.generate_report(payload.session_id, payload.turns)
        )
        return ok(evaluation.to_generate_response(report))

    # 未传对话记录：从模块 C 会话取（end 幂等；会话不存在 → 3001）
    agent = deps.get_interview_agent()
    ended = call_service(
        lambda: deps.call_agent(agent, lambda: agent.end_session(payload.session_id))
    )
    session_payload = _session_payload_from_end(ended)
    report = call_service(lambda: evaluation.generate_report_for_session(session_payload))
    return ok(evaluation.to_generate_response(report))


@router.get("/{report_id}")
def get_report(report_id: str):
    """按 ID 取回报告（data：report_id / evaluation / cross_review）。"""
    from ..services import evaluation

    report = call_service(lambda: evaluation.get_report(report_id))
    return ok(evaluation.to_get_response(report))


@router.get("/{report_id}/markdown")
def get_report_markdown(report_id: str):
    """下载 Markdown 报告（text/markdown 原文；错误走统一响应壳）。"""
    from ..services import evaluation

    markdown = call_service(lambda: evaluation.get_report_markdown(report_id))
    return Response(
        content=markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="report_{report_id}.md"'},
    )
