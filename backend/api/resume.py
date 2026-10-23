"""路由层（D2/T6 接线）：简历诊断接口（模块 A；契约见 docs/api.md「三」）

- `POST /api/resume/parse`：上传 PDF/DOCX → 纯文本 + 分段（本地解析，不调模型）；
- `POST /api/resume/structure`：简历纯文本 → `ResumeStructured`（T6 `structure_resume`；
  data 为结构化字段 + `degraded` 扩展标记，模型输出非法时降级规则分段）；
- `POST /api/resume/diagnose`：简历纯文本 → 诊断报告（T2 `diagnose_resume`）。

所有服务调用均为**延迟导入**，异常经 `call_service` 统一映射为 docs/api.md 错误码。
"""
from __future__ import annotations

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel, Field

from .common import call_service, ok

router = APIRouter(prefix="/resume", tags=["resume"])


class ResumeStructureRequest(BaseModel):
    """POST /api/resume/structure 请求体。"""

    resume_text: str = Field(min_length=1, description="简历纯文本")


class ResumeDiagnoseRequest(BaseModel):
    """POST /api/resume/diagnose 请求体。"""

    resume_text: str = Field(min_length=1, description="简历纯文本")
    target_role: str | None = Field(default=None, description="目标岗位（可选）")
    jd_text: str | None = Field(
        default=None, description="目标 JD 纯文本（可选；用于“关键词对齐”检查）"
    )


@router.post("/parse")
def parse_resume_file(file: UploadFile = File(..., description="PDF / DOCX 简历文件")):
    """上传简历 → 纯文本 + 分段（data 结构见 docs/api.md）。"""
    from ..services.parser import parse_resume

    data = file.file.read()
    doc = call_service(lambda: parse_resume(file.filename or "resume", data))
    return ok(doc.to_dict())


@router.post("/structure")
def structure_resume(payload: ResumeStructureRequest):
    """简历纯文本 → ResumeStructured（data 为结构化字段 + degraded 扩展）。"""
    from ..services.resume_structure import structure_resume as service

    result = call_service(lambda: service(payload.resume_text))
    data = result.data.model_dump()
    # 契约扩展：degraded=True 表示模型输出非法、已降级为规则分段（不破坏既有键）
    data["degraded"] = bool(result.degraded)
    return ok(data)


@router.post("/diagnose")
def diagnose_resume(payload: ResumeDiagnoseRequest):
    """简历纯文本 → 诊断报告（`data` 严格按契约投影：summary / issues / strengths）。"""
    from ..services.resume_diagnosis import diagnose_resume as service

    # target_role 作为 JD 文本的兜底：无 JD 时至少保留“目标岗位”语义
    jd_text = payload.jd_text or payload.target_role or ""
    report = call_service(lambda: service(payload.resume_text, jd_text))
    payload_dict = report.model_dump()
    return ok(
        {
            "summary": payload_dict.get("summary", ""),
            "issues": payload_dict.get("issues", []),
            "strengths": payload_dict.get("strengths", []),
        }
    )
