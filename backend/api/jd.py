"""路由层（D2/T6 接线）：JD 匹配接口（模块 B；契约见 docs/api.md「四」）

- `POST /api/jd/structure`：JD 纯文本 → `JDStructured`（两段式第 1 段）；
- `POST /api/jd/match`：简历（结构化对象或纯文本）+ JD → 对齐评分（两段式完整入口）。
  - 结构化 `resume` 路径：直接进入逐项对齐评分；
  - `resume_text` 文本路径：服务内部先 `resume_structure.structure_resume()` 结构化
    （失败自动规则分段降级），再进入评分。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .common import CODE_INVALID_PARAM, api_error, call_service, ok

router = APIRouter(prefix="/jd", tags=["jd"])


class JDStructureRequest(BaseModel):
    """POST /api/jd/structure 请求体。"""

    jd_text: str = Field(min_length=1, description="JD 纯文本")


class JDMatchRequest(BaseModel):
    """POST /api/jd/match 请求体（结构化对象与纯文本二选一）。"""

    resume: dict[str, Any] | None = Field(default=None, description="ResumeStructured 对象")
    jd: dict[str, Any] | None = Field(default=None, description="JDStructured 对象")
    resume_text: str | None = Field(default=None, description="简历纯文本（未结构化时）")
    jd_text: str | None = Field(default=None, description="JD 纯文本（未结构化时）")


@router.post("/structure")
def structure_jd(payload: JDStructureRequest):
    """JD 纯文本 → JDStructured（data 为 JDStructured 全字段）。"""
    from ..services.jd_matching import extract_jd_structured

    jd = call_service(lambda: extract_jd_structured(payload.jd_text))
    return ok(jd.model_dump())


@router.post("/match")
def match_jd(payload: JDMatchRequest):
    """简历 ↔ JD 逐项对齐评分（data 严格投影：score / summary / items / gaps）。"""
    from ..services.jd_matching import match_resume_to_jd

    has_structured_resume = payload.resume is not None
    has_text_resume = bool((payload.resume_text or "").strip())
    if not has_structured_resume and not has_text_resume:
        raise api_error(400, CODE_INVALID_PARAM, "必须提供 resume（ResumeStructured）或 resume_text")
    if payload.jd is None and not (payload.jd_text or "").strip():
        raise api_error(400, CODE_INVALID_PARAM, "必须提供 jd（JDStructured）或 jd_text")

    # 结构化对象优先；仅传文本时走 T6 文本路径（服务内部先结构化，失败规则分段降级）
    resume_text = payload.resume_text if not has_structured_resume else None
    result = call_service(
        lambda: match_resume_to_jd(
            payload.resume,
            payload.jd_text,
            jd_structured=payload.jd,
            resume_text=resume_text,
        )
    )
    payload_dict = result.model_dump()
    return ok(
        {
            "score": payload_dict.get("score"),
            "summary": payload_dict.get("summary", ""),
            "items": payload_dict.get("items", []),
            "gaps": payload_dict.get("gaps", []),
        }
    )
