"""模块 B：JD 匹配（v2 · 契约冻结 v1：统一经 model_router 两段式调用）

两段式调用（禁止一步到位，见实行清单「模块 B JD 匹配」）：
1. `extract_jd_structured(jd_text)` → `JDStructured`（`prompts/jd_structured.md`，低温 0.2）
2. `match_resume_to_jd(resume, jd_structured=...)` → `JDMatchResult`（`prompts/jd_matching.md`，逐项对齐 + 0–100 分 + 差距清单）

输入形态（T6 补齐文本路径）：
- 结构化路径：`resume` 传 `ResumeStructured` / dict / JSON 字符串；
- 文本路径：`/api/jd/match` 允许 `{resume_text, jd_text}`，服务内部先经
  `backend.services.resume_structure.structure_resume()` 结构化，再走第 1/2 段；
  也可直接 `match_resume_to_jd(resume="简历原文", jd_text="JD 原文")`（非 JSON 字符串按纯文本处理）。

模型接入（契约冻结 v1）：
- 两段都走 `from backend.services.model_router import get_router`：
  `router.chat_json("jd_matching", messages, schema=..., max_repair=max_retries)`；
- 禁止自建 OpenAI 客户端；路由器内部完成 JSON 提取、Pydantic 校验与修复重试。

失败兜底（契约：JSON 校验失败重试后降级纯文本）：
- 第 1 段失败 → `extract_jd_structured` 抛 `JDStructureExtractionError`；
  `match_resume_to_jd` 捕获后返回 `degraded=True`（`score=None`，保留 `raw_text`），不中断 API；
- 第 2 段失败（`ModelJSONError` / 语义一致性校验失败）→ 返回 `degraded=True` 的纯文本结果；
- 文本路径下简历结构化失败 → 规则分段兜底后继续匹配，`summary` 追加降级提示。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, Field, field_validator

from backend.schemas.jd_structured import JDStructured
from backend.schemas.resume_structured import ResumeStructured
from backend.services.model_router import ModelJSONError, get_router
from backend.services.resume_structure import structure_resume

ROUTE = "jd_matching"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
JD_PROMPT_PATH = PROJECT_ROOT / "prompts" / "jd_structured.md"
MATCH_PROMPT_PATH = PROJECT_ROOT / "prompts" / "jd_matching.md"

#: 与 prompts/jd_structured.md 约定一致的 level 取值
LEVELS: tuple[str, ...] = ("了解", "熟悉", "精通", "硬性", "加分", "不限")
STATUSES: tuple[str, ...] = ("matched", "partial", "missing")

#: 文本路径下简历结构化降级时的 summary 提示（不改变输出键契约）
RESUME_DEGRADED_NOTE = "（注：简历结构化降级为规则分段）"

_STATUS_ALIASES = {
    "matched": "matched",
    "覆盖": "matched",
    "已覆盖": "matched",
    "满足": "matched",
    "掌握": "matched",
    "partial": "partial",
    "部分": "partial",
    "部分覆盖": "partial",
    "部分满足": "partial",
    "missing": "missing",
    "缺失": "missing",
    "未覆盖": "missing",
    "未体现": "missing",
}


class PromptOutputError(ValueError):
    """模型输出通过了 Schema 校验但不满足业务约定（覆盖 / 差距 / level 等）。"""


class JDStructureExtractionError(RuntimeError):
    """第 1 段（JD 结构化抽取）失败；`raw_text` 保留模型原始输出供降级展示。"""

    def __init__(self, message: str, raw_text: str = "") -> None:
        super().__init__(message)
        self.raw_text = raw_text


class MatchItem(BaseModel):
    """单项能力对齐结果。"""

    capability: str = Field(min_length=1, description="能力项名称（与 JD JSON 完全一致）")
    status: Literal["matched", "partial", "missing"] = Field(description="对齐状态")
    evidence: str = Field(default="", description="来自简历的证据；missing 时为“简历未提及”")
    comment: str = Field(default="", description="判断依据（简短）")

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, value: Any) -> str:
        text = str(value).strip()
        return _STATUS_ALIASES.get(text, text)


class GapItem(BaseModel):
    """差距清单条目：缺什么 + 怎么补。"""

    capability: str = Field(min_length=1, description="对应能力项名称")
    gap: str = Field(min_length=1, description="具体缺什么")
    suggestion: str = Field(min_length=1, description="可执行的补法")


class JDMatchPayload(BaseModel):
    """LLM 输出契约（交给 router.chat_json 校验的 Schema）。"""

    score: int = Field(ge=0, le=100, description="匹配度 0–100")
    summary: str = Field(default="", description="≤80 字结论")
    items: list[MatchItem] = Field(default_factory=list, description="逐项对齐（覆盖全部 JD 能力项）")
    gaps: list[GapItem] = Field(default_factory=list, description="差距清单（partial / missing）")


class JDMatchResult(JDMatchPayload):
    """模块 B 匹配结果；degraded=True 时 score=None、raw_text 保留模型原文。"""

    score: int | None = Field(default=None, ge=0, le=100, description="匹配度 0–100；降级时为 None")
    degraded: bool = Field(default=False, description="是否因模型输出不可解析而降级为纯文本")
    raw_text: str = Field(default="", description="降级时保留的模型原始输出")


# ---------------------------------------------------------------------------
# Prompt 加载与渲染
# ---------------------------------------------------------------------------
def load_prompt_template(path: Path) -> str:
    """读取 Prompt 文件并去掉首行版本注释（HTML 注释不进入模型上下文）。"""
    if not path.is_file():
        raise FileNotFoundError(f"Prompt 文件不存在：{path}")
    text = path.read_text(encoding="utf-8")
    return re.sub(r"^\s*<!--.*?-->\s*", "", text, count=1, flags=re.DOTALL)


def render_prompt(template: str, variables: Mapping[str, str]) -> str:
    """替换 Prompt 中的 {{placeholder}}；缺少变量时直接报错。"""

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in variables:
            raise KeyError(f"Prompt 占位符未提供变量：{key}")
        return variables[key]

    return re.sub(r"\{\{(\w+)\}\}", _replace, template)


def _as_json(value: ResumeStructured | JDStructured | Mapping[str, Any] | str) -> str:
    """统一转成缩进 JSON 字符串；传入 str 时先校验其确为合法 JSON。"""
    if isinstance(value, str):
        parsed = json.loads(value)
        return json.dumps(parsed, ensure_ascii=False, indent=2)
    if isinstance(value, BaseModel):
        return json.dumps(value.model_dump(), ensure_ascii=False, indent=2)
    if isinstance(value, Mapping):
        return json.dumps(dict(value), ensure_ascii=False, indent=2)
    raise TypeError(f"无法序列化为 JSON：{type(value).__name__}")


def build_jd_messages(jd_text: str, *, prompt_path: Path = JD_PROMPT_PATH) -> list[dict[str, str]]:
    """第 1 段 messages：只送 JD 原文，只做抽取。"""
    jd_clean = (jd_text or "").strip()
    if not jd_clean:
        raise ValueError("JD 文本为空，无法结构化抽取")
    prompt = render_prompt(load_prompt_template(prompt_path), {"jd_text": jd_clean})
    return [{"role": "user", "content": prompt}]


def build_match_messages(
    resume: ResumeStructured | Mapping[str, Any] | str,
    jd_structured: JDStructured | Mapping[str, Any] | str,
    *,
    prompt_path: Path = MATCH_PROMPT_PATH,
) -> list[dict[str, str]]:
    """第 2 段 messages：只送两个结构化 JSON，禁止重新抽取 JD。"""
    variables = {
        "resume_json": _as_json(resume),
        "jd_json": _as_json(jd_structured),
    }
    prompt = render_prompt(load_prompt_template(prompt_path), variables)
    return [{"role": "user", "content": prompt}]


# ---------------------------------------------------------------------------
# 业务约定校验
# ---------------------------------------------------------------------------
def _extract_text(result: Any) -> str:
    """从 `JsonChatResult.result`（ChatResult）中尽力取原始文本。"""
    chat_result = getattr(result, "result", None)
    return str(getattr(chat_result, "text", "") or "")


def validate_jd_semantics(jd: JDStructured) -> JDStructured:
    """Schema 之外的业务约定：能力项非空且 level / evidence / capability 合法。"""
    if not jd.capabilities:
        raise PromptOutputError("capabilities 为空：JD 抽取至少应输出 1 条能力项")
    for index, item in enumerate(jd.capabilities):
        if not item.capability.strip():
            raise PromptOutputError(f"capabilities[{index}].capability 为空")
        if item.level not in LEVELS:
            raise PromptOutputError(
                f"capabilities[{index}].level 取值非法：{item.level!r}，必须是 {LEVELS} 之一"
            )
        if not item.evidence.strip():
            raise PromptOutputError(f"capabilities[{index}].evidence 为空：必须引用 JD 原文")
    return jd


def validate_match_payload(payload: JDMatchPayload, jd: JDStructured) -> JDMatchPayload:
    """一致性校验：items 必须逐项覆盖 JD 能力项；gaps 只能来自 partial / missing。"""
    expected = [item.capability.strip() for item in jd.capabilities]
    got = [item.capability.strip() for item in payload.items]
    missing = [name for name in expected if name not in got]
    extra = [name for name in got if name not in expected]
    if missing or extra:
        raise PromptOutputError(f"items 能力项与 JD JSON 不一致：缺失={missing}，多出={extra}")

    status_by_capability = {item.capability.strip(): item.status for item in payload.items}
    for gap in payload.gaps:
        status = status_by_capability.get(gap.capability.strip())
        if status is None:
            raise PromptOutputError(f"gaps 中的能力项 {gap.capability!r} 不在 items 内")
        if status == "matched":
            raise PromptOutputError(f"gaps 中的能力项 {gap.capability!r} 在 items 中为 matched")
    return payload


# ---------------------------------------------------------------------------
# 输入归一化（结构化对象 / JSON 字符串 / 纯文本）
# ---------------------------------------------------------------------------
def _looks_like_json_object(text: str) -> bool:
    stripped = text.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return False
    try:
        return isinstance(json.loads(stripped), dict)
    except json.JSONDecodeError:
        return False


def _coerce_jd_structured(
    jd_structured: JDStructured | Mapping[str, Any] | str,
) -> JDStructured:
    if isinstance(jd_structured, JDStructured):
        return jd_structured
    return JDStructured.model_validate(json.loads(_as_json(jd_structured)))


def _coerce_resume(resume: ResumeStructured | Mapping[str, Any] | str) -> ResumeStructured:
    """结构化路径：ResumeStructured / dict / JSON 字符串（非纯文本）。"""
    if isinstance(resume, ResumeStructured):
        return resume
    if isinstance(resume, Mapping):
        return ResumeStructured.model_validate(dict(resume))
    if isinstance(resume, str):
        return ResumeStructured.model_validate(json.loads(_as_json(resume)))
    raise TypeError(f"无法识别简历输入类型：{type(resume).__name__}")


def _resolve_resume(
    resume: ResumeStructured | Mapping[str, Any] | str | None,
    resume_text: str | None,
    *,
    max_retries: int,
) -> tuple[ResumeStructured, bool]:
    """把入参归一化为 ResumeStructured，返回 (结构化简历, 是否规则分段降级)。"""
    if resume is not None and resume_text is not None:
        raise ValueError("resume 与 resume_text 只能提供其一")
    if resume_text is not None:
        if not resume_text.strip():
            raise ValueError("简历文本为空，无法结构化")
        structured = structure_resume(resume_text, max_retries=max_retries)
        return structured.data, structured.degraded
    if resume is None:
        raise ValueError("必须提供 resume（结构化对象 / JSON 字符串）或 resume_text（纯文本）之一")
    if isinstance(resume, str) and not _looks_like_json_object(resume):
        # 文本路径：非 JSON 字符串按简历原文处理
        structured = structure_resume(resume, max_retries=max_retries)
        return structured.data, structured.degraded
    return _coerce_resume(resume), False


# ---------------------------------------------------------------------------
# 两段式主流程
# ---------------------------------------------------------------------------
def extract_jd_structured(
    jd_text: str,
    *,
    max_retries: int = 2,
    prompt_path: Path = JD_PROMPT_PATH,
) -> JDStructured:
    """两段式第 1 段：JD 原文 → `JDStructured`（温度由 models.yaml 控制为 0.2）。

    Raises:
        ValueError: JD 为空 / max_retries 为负。
        JDStructureExtractionError: JSON 校验重试后仍失败，或业务约定校验失败。
        ModelRouterError: 路由配置 / Key / 模型调用类错误（交由 API 层处理）。
    """
    if max_retries < 0:
        raise ValueError("max_retries 必须 >= 0")
    messages = build_jd_messages(jd_text, prompt_path=prompt_path)

    try:
        result = get_router().chat_json(
            ROUTE,
            messages,
            schema=JDStructured,
            max_repair=max_retries,
        )
    except ModelJSONError as exc:
        raise JDStructureExtractionError(f"JD 结构化抽取失败：{exc}", raw_text=exc.raw_text) from exc

    jd = result.value
    try:
        return validate_jd_semantics(jd)
    except PromptOutputError as exc:
        raise JDStructureExtractionError(str(exc), raw_text=_extract_text(result)) from exc


def _degraded_result(raw_text: str, error: str) -> JDMatchResult:
    return JDMatchResult(
        score=None,
        summary=f"模型输出无法解析为结构化匹配结果，已降级为原文输出（{error[:120]}）",
        items=[],
        gaps=[],
        degraded=True,
        raw_text=raw_text,
    )


def match_resume_to_jd(
    resume: ResumeStructured | Mapping[str, Any] | str | None = None,
    jd_text: str | None = None,
    *,
    jd_structured: JDStructured | Mapping[str, Any] | str | None = None,
    resume_text: str | None = None,
    max_retries: int = 2,
    jd_prompt_path: Path = JD_PROMPT_PATH,
    match_prompt_path: Path = MATCH_PROMPT_PATH,
) -> JDMatchResult:
    """两段式主入口：简历（结构化或纯文本）+ JD → 匹配结果（统一走 model_router）。

    Args:
        resume: `ResumeStructured` 实例 / dict / JSON 字符串；传纯文本时自动识别为文本路径
            （与 `resume_text` 二选一）。
        jd_text: JD 原文；当 `jd_structured` 未提供时用于第 1 段抽取。
        jd_structured: 已抽取的 JD 结构化结果（命中缓存或评测复用时跳过第 1 段）。
        resume_text: 简历纯文本（`/api/jd/match` 的 `{resume_text, jd_text}` 路径）；
            内部先经 `resume_structure.structure_resume()` 结构化，失败则规则分段降级。
        max_retries: 各段 JSON 校验失败后由 router.chat_json 执行的修复重试次数。
        jd_prompt_path / match_prompt_path: 便于测试替换 Prompt 文件。

    Returns:
        `JDMatchResult`；任一段 JSON 校验修复重试后仍失败时返回 degraded=True 的纯文本结果。
        文本路径下简历结构化降级时，`summary` 会追加 `RESUME_DEGRADED_NOTE` 提示。

    Raises:
        ValueError: 既未提供简历输入、又未提供 JD 输入，或参数冲突 / max_retries 为负。
        ModelRouterError: 路由配置 / Key / 模型调用类错误（交由 API 层处理）。
    """
    if max_retries < 0:
        raise ValueError("max_retries 必须 >= 0")

    # ---- 简历输入归一化：结构化对象 / JSON 字符串 → 直接使用；纯文本 → 先结构化 ----
    structured_resume, resume_degraded = _resolve_resume(
        resume, resume_text, max_retries=max_retries
    )

    # ---- 第 1 段：JD 抽取（可跳过） ----
    if jd_structured is None:
        if not (jd_text or "").strip():
            raise ValueError("必须提供 jd_text 或 jd_structured 之一")
        try:
            jd_model = extract_jd_structured(
                jd_text, max_retries=max_retries, prompt_path=jd_prompt_path
            )
        except JDStructureExtractionError as exc:
            return _degraded_result(exc.raw_text, str(exc))
    else:
        jd_model = _coerce_jd_structured(jd_structured)

    # ---- 第 2 段：逐项对齐 + 评分 ----
    messages = build_match_messages(structured_resume, jd_model, prompt_path=match_prompt_path)
    try:
        result = get_router().chat_json(
            ROUTE,
            messages,
            schema=JDMatchPayload,
            max_repair=max_retries,
        )
    except ModelJSONError as exc:
        return _degraded_result(exc.raw_text, str(exc))

    payload: JDMatchPayload = result.value
    try:
        validate_match_payload(payload, jd_model)
    except PromptOutputError as exc:
        return _degraded_result(_extract_text(result), str(exc))

    match_result = JDMatchResult(**payload.model_dump())
    if resume_degraded:
        # 简历结构化失败已用规则分段兜底，匹配仍可执行；仅在 summary 标注降级
        match_result.summary = f"{match_result.summary}{RESUME_DEGRADED_NOTE}"
    return match_result


def match_resume_text_to_jd(
    resume_text: str,
    jd_text: str | None = None,
    *,
    jd_structured: JDStructured | Mapping[str, Any] | str | None = None,
    max_retries: int = 2,
) -> JDMatchResult:
    """文本输入专用入口：`{resume_text, jd_text}` → 内部先结构化再匹配。

    等价于 `match_resume_to_jd(resume_text=resume_text, jd_text=jd_text, ...)`。
    """
    return match_resume_to_jd(
        resume_text=resume_text,
        jd_text=jd_text,
        jd_structured=jd_structured,
        max_retries=max_retries,
    )


# ---------------------------------------------------------------------------
# 展示层辅助（供 api / 前端 / demo 使用）
# ---------------------------------------------------------------------------
def format_match_markdown(result: JDMatchResult) -> str:
    """把匹配结果渲染为 Markdown（得分 + 已覆盖 / 部分覆盖 / 差距清单）。"""
    lines = ["# JD 匹配报告", ""]
    lines.append(f"## 匹配度：{result.score if result.score is not None else '—'} / 100")
    lines.append("")
    lines.append(f"> {result.summary}")
    lines.append("")

    labels = {"matched": "已覆盖", "partial": "部分覆盖", "missing": "未体现"}
    for status in STATUSES:
        items = [item for item in result.items if item.status == status]
        if not items:
            continue
        lines.append(f"## {labels[status]}")
        for item in items:
            evidence = f"（证据：{item.evidence}）" if item.evidence else ""
            lines.append(f"- **{item.capability}**{evidence}")
        lines.append("")

    if result.gaps:
        lines.append("## 差距清单")
        for gap in result.gaps:
            lines.append(f"- **{gap.capability}**：{gap.gap}")
            lines.append(f"  - 建议：{gap.suggestion}")
        lines.append("")

    if result.degraded:
        lines.append("> 注：模型本次输出无法解析为结构化结果，请重试或检查模型配置。")
    return "\n".join(lines).strip() + "\n"


__all__ = [
    "JD_PROMPT_PATH",
    "LEVELS",
    "MATCH_PROMPT_PATH",
    "RESUME_DEGRADED_NOTE",
    "ROUTE",
    "STATUSES",
    "GapItem",
    "JDStructureExtractionError",
    "JDMatchPayload",
    "JDMatchResult",
    "MatchItem",
    "PromptOutputError",
    "build_jd_messages",
    "build_match_messages",
    "extract_jd_structured",
    "format_match_markdown",
    "load_prompt_template",
    "match_resume_text_to_jd",
    "match_resume_to_jd",
    "render_prompt",
    "validate_jd_semantics",
    "validate_match_payload",
]
