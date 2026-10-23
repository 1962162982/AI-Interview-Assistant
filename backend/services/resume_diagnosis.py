"""模块 A：简历诊断（v2 · 契约冻结 v1：统一经 model_router 调用）

流程：
1. 简历纯文本（解析后）+ 可选目标 JD 文本
2. 渲染 `prompts/resume_diagnosis.md`（5 项检查清单 + few-shot 改写）
3. `get_router().chat_json("resume_diagnosis", messages, schema=DiagnosisPayload)`：
   由路由器按 models.yaml 选模型（deepseek-chat / 温度 0.3）并完成 JSON 提取、
   Pydantic 校验与修复重试（max_repair）。
4. 成功 → `DiagnosisReport`；`ModelJSONError`（修复重试后仍非法）→ 降级纯文本（degraded=True）。

契约（Lead 冻结 v1）：
- 输出 data：`{summary, issues:[{check_item,priority,original_quote,problem,rewrite}], strengths:[]}`
- 禁止自建 OpenAI 客户端；模型调用只走 `backend.services.model_router.get_router()`
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Mapping

from pydantic import BaseModel, Field, field_validator

from backend.services.model_router import ModelJSONError, get_router

ROUTE = "resume_diagnosis"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = PROJECT_ROOT / "prompts" / "resume_diagnosis.md"

#: 与 Prompt 中 5 项检查清单严格一致
CHECK_ITEMS: tuple[str, ...] = ("量化表达", "动词弱化", "关键词对齐", "结构问题", "错别字与格式")
PRIORITIES: tuple[str, ...] = ("high", "medium", "low")

_CHECK_ITEM_ALIASES = {
    "量化": "量化表达",
    "量化表达问题": "量化表达",
    "动词": "动词弱化",
    "弱动词": "动词弱化",
    "关键词": "关键词对齐",
    "关键词匹配": "关键词对齐",
    "结构": "结构问题",
    "结构组织": "结构问题",
    "错别字格式": "错别字与格式",
    "格式问题": "错别字与格式",
    "错别字与格式问题": "错别字与格式",
}

_PRIORITY_ALIASES = {
    "high": "high",
    "medium": "medium",
    "low": "low",
    "高": "high",
    "中": "medium",
    "低": "low",
    "高优先级": "high",
    "中优先级": "medium",
    "低优先级": "low",
}


def _normalize_check_item(value: str) -> str:
    text = re.sub(r"^\s*[0-9]+[.、\s]*", "", str(value)).strip()
    text = _CHECK_ITEM_ALIASES.get(text, text)
    if text not in CHECK_ITEMS:
        raise ValueError(f"check_item 取值非法：{value!r}，必须是 {CHECK_ITEMS} 之一")
    return text


def _normalize_priority(value: str) -> str:
    text = str(value).strip().lower()
    normalized = _PRIORITY_ALIASES.get(text)
    if normalized is None:
        raise ValueError(f"priority 取值非法：{value!r}，必须是 {PRIORITIES} 之一")
    return normalized


class DiagnosisIssue(BaseModel):
    """单条简历问题（强制引用原文 + 必须给改写）。"""

    check_item: str = Field(description="5 项检查清单之一")
    priority: Literal["high", "medium", "low"] = Field(description="优先级：high / medium / low")
    original_quote: str = Field(default="", description="简历原文逐字摘录；结构类问题可为空")
    problem: str = Field(min_length=1, description="问题说明")
    rewrite: str = Field(min_length=1, description="可直接套用的改写句或可执行补法")

    @field_validator("check_item")
    @classmethod
    def _validate_check_item(cls, value: str) -> str:
        return _normalize_check_item(value)

    @field_validator("priority", mode="before")
    @classmethod
    def _validate_priority(cls, value: str) -> str:
        return _normalize_priority(value)


class DiagnosisPayload(BaseModel):
    """LLM 输出契约（交给 router.chat_json 校验的 Schema）。"""

    summary: str = Field(default="", description="一句话总体评价")
    issues: list[DiagnosisIssue] = Field(default_factory=list, description="问题清单（按优先级排序）")
    strengths: list[str] = Field(default_factory=list, description="简历亮点（含依据）")


class DiagnosisReport(DiagnosisPayload):
    """模块 A 诊断报告；degraded=True 时 issues 为空、raw_text 保留模型原文。"""

    degraded: bool = Field(default=False, description="是否因模型输出不可解析而降级为纯文本")
    raw_text: str = Field(default="", description="降级时保留的模型原始输出，便于排查")

    def sort_issues(self) -> "DiagnosisReport":
        """按 high → medium → low 稳定排序（Prompt 已排序，此处兜底）。"""
        order = {priority: index for index, priority in enumerate(PRIORITIES)}
        self.issues.sort(key=lambda item: order.get(item.priority, len(order)))
        return self


# ---------------------------------------------------------------------------
# Prompt 加载与渲染
# ---------------------------------------------------------------------------
def load_prompt_template(path: Path = PROMPT_PATH) -> str:
    """读取 Prompt 文件并去掉首行版本注释（HTML 注释不进入模型上下文）。"""
    if not path.is_file():
        raise FileNotFoundError(f"Prompt 文件不存在：{path}")
    text = path.read_text(encoding="utf-8")
    return re.sub(r"^\s*<!--.*?-->\s*", "", text, count=1, flags=re.DOTALL)


def render_prompt(template: str, variables: Mapping[str, str]) -> str:
    """替换 Prompt 中的 {{placeholder}}；缺少变量时直接报错，避免带占位符调用模型。"""

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in variables:
            raise KeyError(f"Prompt 占位符未提供变量：{key}")
        return variables[key]

    return re.sub(r"\{\{(\w+)\}\}", _replace, template)


def build_messages(
    resume_text: str,
    jd_text: str = "",
    *,
    prompt_path: Path = PROMPT_PATH,
) -> list[dict[str, str]]:
    """构造发给模块 A 模型的 messages（单条 user 消息，Prompt 文件为唯一事实来源）。"""
    resume_clean = (resume_text or "").strip()
    if not resume_clean:
        raise ValueError("简历文本为空，无法诊断")
    variables = {
        "resume_text": resume_clean,
        "jd_text": (jd_text or "").strip() or "（未提供）",
    }
    prompt = render_prompt(load_prompt_template(prompt_path), variables)
    return [{"role": "user", "content": prompt}]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def _degraded_report(raw_text: str, error: str) -> DiagnosisReport:
    return DiagnosisReport(
        summary=f"模型输出无法解析为结构化诊断报告，已降级为原文输出（{error[:120]}）",
        issues=[],
        strengths=[],
        degraded=True,
        raw_text=raw_text,
    )


def diagnose_resume(
    resume_text: str,
    jd_text: str = "",
    *,
    max_retries: int = 2,
    prompt_path: Path = PROMPT_PATH,
) -> DiagnosisReport:
    """模块 A 主入口：简历诊断（模型调用统一走 model_router）。

    Args:
        resume_text: 解析后的简历纯文本（必填）。
        jd_text: 目标 JD 文本；为空时跳过“关键词对齐”检查。
        max_retries: JSON 校验失败后由 router.chat_json 执行的修复重试次数。
        prompt_path: 便于测试替换 Prompt 文件。

    Returns:
        DiagnosisReport；模型输出修复重试后仍不可解析时返回 degraded=True 的降级报告。

    Raises:
        ValueError: 简历文本为空 / max_retries 为负。
        ModelRouterError: 路由配置、Key 或模型调用类错误（交由 API 层统一处理）。
    """
    if max_retries < 0:
        raise ValueError("max_retries 必须 >= 0")
    messages = build_messages(resume_text, jd_text, prompt_path=prompt_path)

    try:
        result = get_router().chat_json(
            ROUTE,
            messages,
            schema=DiagnosisPayload,
            max_repair=max_retries,
        )
    except ModelJSONError as exc:
        # 契约：JSON 校验失败修复重试后仍非法 → 降级纯文本
        return _degraded_report(exc.raw_text, str(exc))

    report = DiagnosisReport(**result.value.model_dump())
    return report.sort_issues()


# ---------------------------------------------------------------------------
# 展示层辅助（供 api / 前端 / demo 使用）
# ---------------------------------------------------------------------------
_PRIORITY_LABELS = {"high": "高", "medium": "中", "low": "低"}


def format_report_markdown(report: DiagnosisReport) -> str:
    """把诊断报告渲染为 Markdown（问题清单 + 优先级 + 原句 → 改写）。"""
    lines = ["# 简历诊断报告", "", "## 总体评价", report.summary or "（无）", ""]
    for priority in PRIORITIES:
        items = [issue for issue in report.issues if issue.priority == priority]
        if not items:
            continue
        lines.append(f"## {_PRIORITY_LABELS[priority]}优先级问题")
        for index, issue in enumerate(items, start=1):
            lines.append(f"### {index}. {issue.check_item}")
            if issue.original_quote:
                lines.append(f"- 原句：{issue.original_quote}")
            lines.append(f"- 问题：{issue.problem}")
            lines.append(f"- 改写：{issue.rewrite}")
            lines.append("")
    if report.strengths:
        lines.append("## 简历亮点")
        lines.extend(f"- {item}" for item in report.strengths)
        lines.append("")
    if report.degraded:
        lines.append("> 注：模型本次输出无法解析为结构化报告，以上为降级提示，请重试或检查模型配置。")
    return "\n".join(lines).strip() + "\n"


__all__ = [
    "CHECK_ITEMS",
    "PRIORITIES",
    "PROMPT_PATH",
    "ROUTE",
    "DiagnosisIssue",
    "DiagnosisPayload",
    "DiagnosisReport",
    "build_messages",
    "diagnose_resume",
    "format_report_markdown",
    "load_prompt_template",
    "render_prompt",
]
