"""面试评估 Schema（v1）

用途：模块 D 输出校验；字段定义见实行清单：评分维度 / 得分 / 亮点 / 短板 / 建议。
4 个评分维度：相关性 / 结构性 / 具体性 / 岗位匹配（各 1–10 分）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class DimensionScore(BaseModel):
    """单个维度的评分（强制 grounding：先引用原文证据再给分）。"""

    dimension: str = Field(description="评分维度：相关性/结构性/具体性/岗位匹配")
    score: int = Field(ge=1, le=10, description="得分 1–10")
    evidence: list[str] = Field(default_factory=list, description="引用的原文证据")


class InterviewEvaluation(BaseModel):
    """面试评估输出（v1）。"""

    dimensions: list[DimensionScore] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list, description="亮点")
    weaknesses: list[str] = Field(default_factory=list, description="短板")
    suggestions: list[str] = Field(default_factory=list, description="改进建议")
