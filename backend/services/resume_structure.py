"""模块 A 辅助服务（T6 契约缺口）：简历纯文本 → `ResumeStructured`

流程：
1. 渲染 `prompts/resume_structured.md`（抽取规则 + JSON 契约 + few-shot）
2. `get_router().chat_json("resume_diagnosis", messages, schema=ResumeStructured, max_repair=...)`
   —— 复用模块 A 路由（models.yaml 中无独立的 structure 路由）
3. 成功 → `ResumeStructureResult(data=ResumeStructured, degraded=False)`
4. `ModelJSONError`（修复重试后仍非法）→ **规则分段兜底**（标题 + 要点解析）
   → `ResumeStructureResult(data=规则分段结果, degraded=True, raw_text=模型原始输出)`

约束（契约冻结 v1）：
- 禁止自建 OpenAI 客户端，模型调用统一走 `backend.services.model_router.get_router()`；
- 输出 data 为 `backend/schemas/resume_structured.py` 的 `ResumeStructured`；
- degraded 标记在 `ResumeStructureResult` 上，不修改 ResumeStructured Schema。
"""
from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from backend.schemas.resume_structured import Education, Experience, Project, ResumeStructured
from backend.services.model_router import ModelJSONError, get_router

ROUTE = "resume_diagnosis"  # 复用模块 A 路由（决策④：A = deepseek-chat）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = PROJECT_ROOT / "prompts" / "resume_structured.md"

UNKNOWN_NAME = "未知候选人"

# ---------------------------------------------------------------------------
# 规则分段兜底（模型输出不可用时使用；只做标题定位与要点归位，不做改写）
# ---------------------------------------------------------------------------
_HEADING_RE = re.compile(
    r"^(?P<title>"
    r"教育经历|教育背景|教育|学历|"
    r"实习经历|工作经历|实习|工作|实践经历|"
    r"项目经历|项目经验|项目|"
    r"技能清单|专业技能|技能|技能特长|"
    r"获奖情况|荣誉奖项|证书|自我评价|个人评价"
    r")\s*[:：]?$"
)
_HEADING_TO_SECTION = {
    "教育经历": "education",
    "教育背景": "education",
    "教育": "education",
    "学历": "education",
    "实习经历": "experience",
    "工作经历": "experience",
    "实习": "experience",
    "工作": "experience",
    "实践经历": "experience",
    "项目经历": "projects",
    "项目经验": "projects",
    "项目": "projects",
    "技能清单": "skills",
    "专业技能": "skills",
    "技能": "skills",
    "技能特长": "skills",
    "获奖情况": "other",
    "荣誉奖项": "other",
    "证书": "other",
    "自我评价": "other",
    "个人评价": "other",
}
_PERIOD_RE = re.compile(
    r"(20\d{2}[.\-/年]\s?\d{1,2}\s?月?(?:\s*[-–—~至]\s*(?:20\d{2}[.\-/年]\s?\d{1,2}\s?月?|至今))?)"
)
_BULLET_RE = re.compile(r"^[-•·*]\s*|^\d+[.、]\s*")
_ROLE_KEYWORDS = (
    "实习生",
    "工程师",
    "架构师",
    "研究员",
    "负责人",
    "开发者",
    "开发",
    "助理",
    "专员",
    "经理",
    "负责人",
)


class ResumeStructureResult(BaseModel):
    """结构化抽取结果；degraded=True 表示已降级为规则分段。"""

    data: ResumeStructured = Field(description="结构化简历（degraded 时为规则分段结果）")
    degraded: bool = Field(default=False, description="是否因模型输出不可用而降级为规则分段")
    raw_text: str = Field(default="", description="降级时保留的模型原始输出，便于排查")


def load_prompt_template(path: Path = PROMPT_PATH) -> str:
    """读取 Prompt 文件并去掉首行版本注释（HTML 注释不进入模型上下文）。"""
    if not path.is_file():
        raise FileNotFoundError(f"Prompt 文件不存在：{path}")
    text = path.read_text(encoding="utf-8")
    return re.sub(r"^\s*<!--.*?-->\s*", "", text, count=1, flags=re.DOTALL)


def render_prompt(template: str, variables: dict[str, str]) -> str:
    """替换 Prompt 中的 {{placeholder}}；缺少变量时直接报错。"""

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in variables:
            raise KeyError(f"Prompt 占位符未提供变量：{key}")
        return variables[key]

    return re.sub(r"\{\{(\w+)\}\}", _replace, template)


def build_messages(resume_text: str, *, prompt_path: Path = PROMPT_PATH) -> list[dict[str, str]]:
    """构造结构化抽取 messages（单条 user 消息，Prompt 文件为唯一事实来源）。"""
    text = (resume_text or "").strip()
    if not text:
        raise ValueError("简历文本为空，无法结构化抽取")
    prompt = render_prompt(load_prompt_template(prompt_path), {"resume_text": text})
    return [{"role": "user", "content": prompt}]


def _is_bullet(line: str) -> bool:
    return bool(_BULLET_RE.match(line.strip()))


def _strip_bullet(line: str) -> str:
    return _BULLET_RE.sub("", line.strip()).strip()


def _extract_period(line: str) -> str:
    match = _PERIOD_RE.search(line)
    return match.group(1).strip() if match else ""


def _extract_role(line: str) -> str:
    for keyword in _ROLE_KEYWORDS:
        if keyword in line:
            # 取关键词所在片段作为角色（如“后端开发实习生”）
            match = re.search(rf"[\u4e00-\u9fa5A-Za-z/+]{{0,8}}{re.escape(keyword)}[\u4e00-\u9fa5A-Za-z/+]{{0,4}}", line)
            if match:
                return match.group(0).strip()
    return ""


def _split_sections(text: str) -> dict[str, list[str]]:
    """按标题把简历切成 {section: [行]}；标题前的行归入 header。"""
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            current = _HEADING_TO_SECTION.get(heading.group("title"), "other")
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _extract_name(header_lines: list[str]) -> str:
    for line in header_lines[:6]:
        compact = re.sub(r"\s+", "", line)
        if not compact:
            continue
        if any(token in compact for token in ("简历", "求职", "意向", "电话", "手机", "邮箱", "@", "http")):
            continue
        if re.fullmatch(r"[\u4e00-\u9fa5·]{2,5}", compact):
            return compact
        if re.fullmatch(r"[A-Za-z][A-Za-z .]{1,30}", line.strip()):
            return line.strip()
    return UNKNOWN_NAME


def _parse_education(lines: list[str]) -> list[Education]:
    items: list[Education] = []
    for line in lines:
        if _is_bullet(line) or not any(token in line for token in ("大学", "学院", "学校")):
            continue
        parts = [part.strip() for part in re.split(r"[\s　]+", line) if part.strip()]
        school = parts[0] if parts else line
        degree = next((d for d in ("博士", "硕士", "研究生", "本科", "大专") if d in line), "")
        major = ""
        for part in parts[1:]:
            if any(token in part for token in ("专业", "工程", "科学", "技术", "管理", "学")) and not any(
                token in part for token in ("大学", "学院", "本科", "硕士", "博士")
            ):
                major = part
                break
        items.append(Education(school=school, major=major, degree=degree, period=_extract_period(line)))
    return items


def _parse_experiences(lines: list[str]) -> list[Experience]:
    items: list[Experience] = []
    current: Experience | None = None
    for line in lines:
        if _is_bullet(line):
            if current is not None:
                current.highlights.append(_strip_bullet(line))
            continue
        current = Experience(
            organization=next(
                (token for token in re.split(r"[\s　]+", line) if token.strip()), line
            ),
            role=_extract_role(line),
            period=_extract_period(line),
            highlights=[],
        )
        items.append(current)
    return items


def _parse_projects(lines: list[str]) -> list[Project]:
    items: list[Project] = []
    current: Project | None = None
    for line in lines:
        if _is_bullet(line):
            if current is not None:
                current.highlights.append(_strip_bullet(line))
            continue
        current = Project(
            name=line,
            role=_extract_role(line),
            description="",
            highlights=[],
        )
        items.append(current)
    return items


def _parse_skills(lines: list[str]) -> list[str]:
    tokens: list[str] = []
    for line in lines:
        cleaned = _strip_bullet(line)
        cleaned = re.sub(r"^[^：:]{1,10}[：:]", "", cleaned)  # 去掉“编程语言：”等前缀
        for token in re.split(r"[、,，;；/|]", cleaned):
            token = token.strip().strip("。.）)（(")
            if 1 < len(token) <= 20 and token not in tokens:
                tokens.append(token)
    return tokens


def segment_resume_text(resume_text: str) -> ResumeStructured:
    """规则分段兜底：把纯文本按标题切成教育 / 经历 / 项目 / 技能。"""
    text = (resume_text or "").strip()
    if not text:
        raise ValueError("简历文本为空，无法分段")
    sections = _split_sections(text)
    return ResumeStructured(
        name=_extract_name(sections.get("header", [])),
        education=_parse_education(sections.get("education", [])),
        experiences=_parse_experiences(sections.get("experience", [])),
        skills=_parse_skills(sections.get("skills", [])),
        projects=_parse_projects(sections.get("projects", [])),
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def structure_resume(
    resume_text: str,
    *,
    max_retries: int = 2,
    prompt_path: Path = PROMPT_PATH,
) -> ResumeStructureResult:
    """简历纯文本 → 结构化结果（模型优先，失败降级规则分段）。

    Args:
        resume_text: 简历纯文本（必填）。
        max_retries: JSON 校验失败后由 router.chat_json 执行的修复重试次数。
        prompt_path: 便于测试替换 Prompt 文件。

    Returns:
        `ResumeStructureResult`：`data` 为 ResumeStructured；`degraded=True` 时表示
        模型输出修复重试后仍不可解析，`data` 来自规则分段、`raw_text` 保留模型原文。

    Raises:
        ValueError: 简历文本为空 / max_retries 为负。
        ModelRouterError: 路由配置、Key 或模型调用类错误（交由 API 层统一处理）。
    """
    if max_retries < 0:
        raise ValueError("max_retries 必须 >= 0")
    messages = build_messages(resume_text, prompt_path=prompt_path)

    try:
        result = get_router().chat_json(
            ROUTE,
            messages,
            schema=ResumeStructured,
            max_repair=max_retries,
        )
    except ModelJSONError as exc:
        # 契约：JSON 校验失败重试后降级为规则分段 + degraded 标记
        return ResumeStructureResult(
            data=segment_resume_text(resume_text),
            degraded=True,
            raw_text=exc.raw_text,
        )

    return ResumeStructureResult(data=result.value)


def to_structured(result: ResumeStructureResult) -> ResumeStructured:
    """便利函数：取结构化数据（忽略 degraded 标记，供下游直接使用）。"""
    return result.data


__all__ = [
    "PROMPT_PATH",
    "ROUTE",
    "UNKNOWN_NAME",
    "ResumeStructureResult",
    "build_messages",
    "load_prompt_template",
    "render_prompt",
    "segment_resume_text",
    "structure_resume",
    "to_structured",
]
