"""JD 结构化 Schema（v1）

用途：两段式调用第一步的产物（JD → 能力项 JSON，低温 0.2）。
字段定义见实行清单：岗位 / 职责 / 能力项。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CapabilityItem(BaseModel):
    """单项能力要求。"""

    capability: str = Field(description="能力项名称")
    level: str = Field(default="", description="要求程度：了解/熟悉/精通")
    evidence: str = Field(default="", description="JD 中的原文依据")


class JDStructured(BaseModel):
    """JD 结构化输出（v1）。"""

    title: str = Field(description="岗位名称")
    responsibilities: list[str] = Field(default_factory=list, description="职责列表")
    capabilities: list[CapabilityItem] = Field(default_factory=list, description="能力项列表")
