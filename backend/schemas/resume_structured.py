"""简历结构化 Schema（v1）

用途：LLM 输出校验与解析兜底；模块 B 的能力映射依赖此输出。
字段定义见实行清单：姓名 / 教育 / 经历 / 技能 / 项目。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Education(BaseModel):
    """教育经历。"""

    school: str = Field(description="学校")
    major: str = Field(default="", description="专业")
    degree: str = Field(default="", description="学历/学位")
    period: str = Field(default="", description="起止时间")


class Experience(BaseModel):
    """实习/工作经历。"""

    organization: str = Field(description="公司/组织")
    role: str = Field(description="角色/岗位")
    period: str = Field(default="", description="起止时间")
    highlights: list[str] = Field(default_factory=list, description="经历要点")


class Project(BaseModel):
    """项目经历。"""

    name: str = Field(description="项目名")
    role: str = Field(default="", description="承担角色")
    description: str = Field(default="", description="项目描述")
    highlights: list[str] = Field(default_factory=list, description="项目亮点")


class ResumeStructured(BaseModel):
    """简历结构化输出（v1）。"""

    name: str = Field(description="姓名")
    education: list[Education] = Field(default_factory=list)
    experiences: list[Experience] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list, description="技能关键词")
    projects: list[Project] = Field(default_factory=list)
