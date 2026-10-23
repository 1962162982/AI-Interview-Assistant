"""模块 D：即时反馈（v2 · 2026-10-23）—— 评分 / 交叉复核 / 面试报告

职责（见实行清单模块 D、说明书 §4.4、evaluation/README.md §4）：

- **实时评分**：单条回答按 `prompts/scoring.md` 做 4 维 rubric 评分（相关性/结构性/具体性/岗位匹配，
  各 1–10 分），强制「先引用原文证据再给分」；证据不足 2 条时该维度不得高于 6 分（代码兜底钳制）；
- **交叉复核**：面试结束由第二次 LLM 调用（`cross_review` 路由，模型与 `evaluation` 不同）复核首评，
  输出逐维 verdict（agree / too_high / too_low）+ 调整建议 + 偏差说明 + 风险标记；
- **面试报告**：聚合每轮评分 → `InterviewEvaluation`（得分 / 证据 / 亮点 / 短板 / 建议）→
  Markdown 报告（可导出），并组装冻结的 API payload（`POST /api/report/generate` 等）。

模型路由约定（与 `backend/services/model_router.py` 对齐）：
- 业务层只调 `get_router()`，不接触具体模型 / Key / Base URL；
- `evaluation` 路由（glm-4-plus，temperature 0.1）评分，`cross_review` 路由（deepseek-chat，0.1）复核，
  两者**必须不同模型**（`check_model_separation()` 硬校验，决策④）；
- 无 Key / 无网络时：模块本身不自动 mock；mock 由 `evaluation/run_eval.py --mock` 提供。

报告 API 契约（docs/api.md v1 冻结）：
- `POST /api/report/generate` `{session_id[, turns]}` → `{report_id, evaluation, cross_review, markdown}`
- `GET  /api/report/{report_id}` → 结构同 generate 的 data（含 markdown）
- `GET  /api/report/{report_id}/markdown` → `text/markdown`
- `cross_review` 数据键固定为 `{agreed, adjustments, deviation_note}`；`report_id` 形如
  `rep_YYYYMMDD_<6 位 hex>`；`evaluation` 严格为 `InterviewEvaluation`。
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from pydantic import BaseModel, Field, ValidationError, field_validator

from backend.schemas.interview_evaluation import DimensionScore, InterviewEvaluation

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "prompts"
DEFAULT_SCORING_PROMPT = PROMPTS_DIR / "scoring.md"
DEFAULT_CROSS_REVIEW_PROMPT = PROMPTS_DIR / "cross_review.md"

DIMENSIONS: tuple[str, ...] = ("相关性", "结构性", "具体性", "岗位匹配")
EVALUATION_ROUTE = "evaluation"
CROSS_REVIEW_ROUTE = "cross_review"
REQUIRED_TEMPERATURE = 0.1
MAX_EVIDENCE_WHEN_INSUFFICIENT = 6
MIN_EVIDENCE_COUNT = 2
MAX_CROSS_REVIEW_DELTA = 2
REPORT_STORE_LIMIT = 200  # 进程内保留最近 N 份报告，防止内存无限增长


# ---------------------------------------------------------------------------
# 异常体系
# ---------------------------------------------------------------------------
class EvaluationError(RuntimeError):
    """模块 D 异常基类。"""


class EvaluationValidationError(EvaluationError):
    """评分结果不符合 InterviewEvaluation / 评分契约。"""


class CrossReviewValidationError(EvaluationError):
    """交叉复核结果不符合复核契约。"""


class ModelSeparationError(EvaluationError):
    """评分与复核对模型配置违约（决策④：必须不同模型）。"""


class ReportNotFoundError(EvaluationError):
    """报告 ID 不存在（API 404 场景）。"""


# ---------------------------------------------------------------------------
# 交叉复核输出契约（无独立 schema 文件，契约内聚在模块 D 内）
# ---------------------------------------------------------------------------
class DimensionReview(BaseModel):
    """单个维度的复核结论。"""

    dimension: str = Field(description="维度名：相关性/结构性/具体性/岗位匹配")
    verdict: str = Field(description="agree / too_high / too_low")
    suggested_score: int | None = Field(default=None, ge=1, le=10, description="建议分；agree 时为空")
    deviation: str = Field(default="", description="偏差说明：无 / 偏高 N 分 / 偏低 N 分")
    reason: str = Field(default="", description="复核依据（引用原文或首评证据）")

    @field_validator("verdict")
    @classmethod
    def _verdict_values(cls, value: str) -> str:
        value = (value or "").strip()
        if value not in {"agree", "too_high", "too_low"}:
            raise ValueError("verdict 必须为 agree / too_high / too_low")
        return value


class CrossReview(BaseModel):
    """一次交叉复核的整体输出（API 的 `cross_review` 字段）。"""

    verdicts: list[DimensionReview] = Field(description="恰好 4 个维度的复核结论")
    overall_verdict: str = Field(default="agree", description="agree / adjust")
    summary: str = Field(default="", description="一句话整体一致性说明")
    risk_flags: list[str] = Field(default_factory=list, description="风险提示（无风险为空数组）")

    @field_validator("overall_verdict")
    @classmethod
    def _overall_values(cls, value: str) -> str:
        value = (value or "").strip() or "agree"
        if value not in {"agree", "adjust"}:
            raise ValueError("overall_verdict 必须为 agree / adjust")
        return value

    @field_validator("risk_flags")
    @classmethod
    def _risk_flags_str(cls, value: list[str]) -> list[str]:
        return [str(item).strip() for item in value or [] if str(item).strip()]


# ---------------------------------------------------------------------------
# 返回结构
# ---------------------------------------------------------------------------
@dataclass
class ScoreResult:
    """单条回答的评分结果。"""

    evaluation: InterviewEvaluation
    adjustments: list[dict[str, Any]] = field(default_factory=list)
    prompt_version: str = ""

    @property
    def overall(self) -> float:
        """4 维均分（保留 2 位小数）。"""
        return overall_score(self.evaluation)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation": self.evaluation.model_dump(),
            "adjustments": list(self.adjustments),
            "overall": self.overall,
            "prompt_version": self.prompt_version,
        }


@dataclass
class CrossReviewResult:
    """一次交叉复核结果（含代码侧纠正记录）。"""

    review: CrossReview
    adjustments: list[dict[str, Any]] = field(default_factory=list)
    prompt_version: str = ""

    @property
    def verdict(self) -> str:
        return self.review.overall_verdict

    def to_dict(self) -> dict[str, Any]:
        return {
            "cross_review": self.review.model_dump(),
            "adjustments": list(self.adjustments),
            "prompt_version": self.prompt_version,
        }


@dataclass
class TurnScore:
    """一轮问答（question + answer）的评分结果。"""

    turn: int
    question: str
    answer: str
    result: ScoreResult


@dataclass
class SessionEvaluation:
    """一场面试的完整评分 + 复核 + 报告。"""

    session_id: str
    evaluation: InterviewEvaluation
    cross_review: CrossReview
    markdown: str
    turn_scores: list[TurnScore] = field(default_factory=list)
    cross_review_adjustments: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Prompt 加载与消息构造
# ---------------------------------------------------------------------------
_PROMPT_VERSION_RE = re.compile(r"prompt_version:\s*(v\d+)")
_VERSION_COMMENT_RE = re.compile(r"^\s*<!--.*?-->\s*\n?", re.DOTALL)


def load_prompt(path: str | Path | None = None) -> tuple[str, str]:
    """读取 Prompt 文件，返回 (正文, 版本号)。缺失版本注释时版本号返回空串。"""
    prompt_path = Path(path) if path else DEFAULT_SCORING_PROMPT
    try:
        text = prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvaluationError(f"读取 Prompt 失败 {prompt_path}: {exc}") from exc
    version_match = _PROMPT_VERSION_RE.search(text)
    version = version_match.group(1) if version_match else ""
    body = _VERSION_COMMENT_RE.sub("", text, count=1).strip()
    if not body:
        raise EvaluationError(f"Prompt 内容为空：{prompt_path}")
    return body, version


def _compact_text(value: Any, limit: int = 1500) -> str:
    """把 str / dict / list 压成适合放进 Prompt 的文本（超长截断）。"""
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(value)
    if len(text) > limit:
        text = text[:limit] + "…（已截断）"
    return text


def build_scoring_messages(
    prompt_input: Mapping[str, Any],
    answer: str,
    prompt_text: str,
) -> list[dict[str, str]]:
    """构造评分调用的 messages（system=评分 Prompt；user=结构化输入）。"""
    question = _compact_text(prompt_input.get("question"), limit=500)
    if not question:
        raise EvaluationError("评分输入缺少 question")
    if not isinstance(answer, str) or not answer.strip():
        raise EvaluationError("评分输入缺少候选人 answer")
    user_payload = {
        "question": question,
        "jd": _compact_text(prompt_input.get("jd")),
        "resume_excerpt": _compact_text(prompt_input.get("resume_excerpt")),
        "answer": answer.strip(),
    }
    return [
        {"role": "system", "content": prompt_text},
        {
            "role": "user",
            "content": "请按 System 中的评分规则对以下输入评分，只输出 JSON：\n"
            + json.dumps(user_payload, ensure_ascii=False, indent=2),
        },
    ]


def build_cross_review_messages(
    conversation: str,
    first_evaluation: InterviewEvaluation,
    prompt_text: str,
    *,
    jd: Any = None,
    resume_excerpt: Any = None,
) -> list[dict[str, str]]:
    """构造交叉复核调用的 messages。"""
    if not conversation.strip():
        raise EvaluationError("交叉复核输入缺少面试对话记录")
    user_payload = {
        "conversation": conversation,
        "first_evaluation": first_evaluation.model_dump(),
        "jd": _compact_text(jd),
        "resume_excerpt": _compact_text(resume_excerpt),
    }
    return [
        {"role": "system", "content": prompt_text},
        {
            "role": "user",
            "content": "请按 System 中的交叉复核规则复核以下首评，只输出 JSON：\n"
            + json.dumps(user_payload, ensure_ascii=False, indent=2),
        },
    ]


# ---------------------------------------------------------------------------
# 模型路由调用（懒加载，便于 mock / 单测注入）
# ---------------------------------------------------------------------------
def _get_router() -> Any:
    """懒加载进程内模型路由单例（首次调用时读取 .env 与 models.yaml）。"""
    from backend.services.model_router import get_router

    return get_router()


def _extract_json_text(text: str) -> Any:
    """从模型文本中提取 JSON（兼容 Markdown 围栏与前后说明）。"""
    if not isinstance(text, str) or not text.strip():
        raise EvaluationValidationError("模型返回内容为空")
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise EvaluationValidationError(f"模型输出 JSON 不合法：{exc}") from exc
    raise EvaluationValidationError("模型输出中未找到 JSON 对象")


def _chat_json(
    router: Any,
    route: str,
    messages: list[dict[str, str]],
    schema: type[BaseModel],
    *,
    max_repair: int = 1,
) -> BaseModel:
    """调用 router.chat_json（优先）或回退到 router.chat + 本地 JSON 校验。"""
    if router is None:
        raise EvaluationError("缺少模型路由实例")
    if hasattr(router, "chat_json"):
        result = router.chat_json(route, messages, schema, max_repair=max_repair)
        value = getattr(result, "value", result)
        if isinstance(value, schema):
            return value
        if isinstance(value, Mapping):
            try:
                return schema.model_validate(value)
            except ValidationError as exc:
                raise EvaluationValidationError(f"路由返回结构校验失败：{exc}") from exc
        raise EvaluationValidationError(f"路由 chat_json 返回类型异常：{type(value)!r}")

    # 回退：仅依赖 router.chat(route, messages, response_format=...) -> ChatResult / str
    if not hasattr(router, "chat"):
        raise EvaluationError("模型路由必须提供 chat_json() 或 chat() 方法")
    last_error: Exception | None = None
    text = ""
    for attempt in range(max_repair + 1):
        try:
            result = router.chat(
                route,
                messages,
                response_format={"type": "json_object"} if attempt == 0 else None,
            )
        except TypeError:
            result = router.chat(route, messages)
        text = result if isinstance(result, str) else str(getattr(result, "text", "") or "")
        try:
            return schema.model_validate(_extract_json_text(text))
        except (EvaluationValidationError, ValidationError) as exc:
            last_error = exc
            if attempt == max_repair:
                break
    raise EvaluationValidationError(
        f"模型输出无法通过 {schema.__name__} 校验：{last_error}；原始输出前 200 字：{text[:200]!r}"
    )


# ---------------------------------------------------------------------------
# 评分结果校验与 grounding 规则
# ---------------------------------------------------------------------------
def overall_score(evaluation: InterviewEvaluation) -> float:
    """4 维均分（保留 2 位小数）。"""
    if not evaluation.dimensions:
        return 0.0
    return round(sum(item.score for item in evaluation.dimensions) / len(evaluation.dimensions), 2)


def validate_evaluation(evaluation: InterviewEvaluation) -> InterviewEvaluation:
    """严格校验评分结果：恰好 4 个维度、名称固定、分数 1–10、证据为字符串数组。

    返回按固定维度顺序归一化后的副本。
    """
    if not isinstance(evaluation, InterviewEvaluation):
        raise EvaluationValidationError("评分结果必须是 InterviewEvaluation 实例")
    if len(evaluation.dimensions) != len(DIMENSIONS):
        raise EvaluationValidationError(
            f"dimensions 必须恰好 {len(DIMENSIONS)} 项，实际 {len(evaluation.dimensions)} 项"
        )
    by_name: dict[str, DimensionScore] = {}
    for item in evaluation.dimensions:
        if item.dimension not in DIMENSIONS:
            raise EvaluationValidationError(
                f"未知评分维度：{item.dimension!r}（应为 {DIMENSIONS}）"
            )
        if item.dimension in by_name:
            raise EvaluationValidationError(f"评分维度重复：{item.dimension}")
        if not isinstance(item.score, int) or isinstance(item.score, bool):
            raise EvaluationValidationError(f"维度 {item.dimension} 的 score 必须是整数")
        if not 1 <= item.score <= 10:
            raise EvaluationValidationError(f"维度 {item.dimension} 的 score 越界：{item.score}")
        evidence = [str(text).strip() for text in item.evidence if str(text).strip()]
        by_name[item.dimension] = DimensionScore(
            dimension=item.dimension, score=item.score, evidence=evidence
        )
    for name in ("strengths", "weaknesses", "suggestions"):
        value = getattr(evaluation, name)
        if not isinstance(value, list) or any(not isinstance(text, str) for text in value):
            raise EvaluationValidationError(f"{name} 必须是字符串数组")
    normalized = InterviewEvaluation(
        dimensions=[by_name[name] for name in DIMENSIONS],
        strengths=[text.strip() for text in evaluation.strengths if text.strip()],
        weaknesses=[text.strip() for text in evaluation.weaknesses if text.strip()],
        suggestions=[text.strip() for text in evaluation.suggestions if text.strip()],
    )
    return normalized


def apply_grounding_rule(
    evaluation: InterviewEvaluation,
) -> tuple[InterviewEvaluation, list[dict[str, Any]]]:
    """执行「证据 <2 条 → 该维度不得高于 6 分」硬规则，返回 (修正后结果, 修正记录)。"""
    fixed = evaluation.model_copy(deep=True)
    adjustments: list[dict[str, Any]] = []
    for item in fixed.dimensions:
        evidence_count = len([text for text in item.evidence if str(text).strip()])
        if evidence_count < MIN_EVIDENCE_COUNT and item.score > MAX_EVIDENCE_WHEN_INSUFFICIENT:
            adjustments.append(
                {
                    "dimension": item.dimension,
                    "from": item.score,
                    "to": MAX_EVIDENCE_WHEN_INSUFFICIENT,
                    "reason": (
                        f"引用证据 {evidence_count} 条 < {MIN_EVIDENCE_COUNT} 条，"
                        f"按 grounding 规则该维度不得高于 {MAX_EVIDENCE_WHEN_INSUFFICIENT} 分"
                    ),
                }
            )
            item.score = MAX_EVIDENCE_WHEN_INSUFFICIENT
    return fixed, adjustments


# ---------------------------------------------------------------------------
# 实时评分
# ---------------------------------------------------------------------------
def score_answer(
    prompt_input: Mapping[str, Any],
    answer: str,
    *,
    router: Any = None,
    prompt_path: str | Path | None = None,
) -> ScoreResult:
    """对单条回答评分（模块 D 实时评分入口；run_eval real 模式复用）。"""
    if router is None:
        router = _get_router()
    prompt_text, version = load_prompt(prompt_path or DEFAULT_SCORING_PROMPT)
    messages = build_scoring_messages(prompt_input, answer, prompt_text)
    raw = _chat_json(router, EVALUATION_ROUTE, messages, InterviewEvaluation, max_repair=1)
    evaluation = validate_evaluation(raw)
    grounded, adjustments = apply_grounding_rule(evaluation)
    return ScoreResult(evaluation=grounded, adjustments=adjustments, prompt_version=version)


def score_turn(
    turn: Mapping[str, Any],
    *,
    jd: Any = None,
    resume_excerpt: Any = None,
    router: Any = None,
) -> ScoreResult:
    """实时评分便捷入口：接受模块 C 的 Turn dict（含 question / answer）。"""
    question = turn.get("question")
    answer = turn.get("answer")
    if not isinstance(question, str) or not question.strip():
        raise EvaluationError("turn 缺少 question")
    if not isinstance(answer, str) or not answer.strip():
        raise EvaluationError("turn 缺少 answer")
    prompt_input = {"question": question, "jd": jd, "resume_excerpt": resume_excerpt}
    return score_answer(prompt_input, answer, router=router)


# ---------------------------------------------------------------------------
# 交叉复核
# ---------------------------------------------------------------------------
def format_conversation(turns: Sequence[Mapping[str, Any]]) -> str:
    """把模块 C 的 Turn 列表格式化为复核 Prompt 可读的对话记录。"""
    lines: list[str] = []
    for index, turn in enumerate(turns, start=1):
        question = str(turn.get("question", "")).strip()
        answer = str(turn.get("answer", "")).strip()
        if not question and not answer:
            continue
        turn_no = turn.get("turn", index)
        lines.append(f"第 {turn_no} 轮 面试官：{question}")
        lines.append(f"候选人：{answer}")
        lines.append("")
    if not lines:
        raise EvaluationError("面试对话记录为空，无法交叉复核")
    return "\n".join(lines).strip()


def enforce_cross_review_rules(
    review: CrossReview,
    first_evaluation: InterviewEvaluation,
) -> tuple[CrossReview, list[dict[str, Any]]]:
    """按复核契约做代码侧纠正：

    - 每维度恰好一次、名称固定、顺序归一；
    - `agree` 时 `suggested_score` 必须为 None；
    - 调整幅度不超过 ±2 分，超出则钳制并记录；
    - `overall_verdict` 与实际 verdict 保持一致；
    - 缺依据的复核补风险标记。
    """
    first_scores = {item.dimension: item.score for item in first_evaluation.dimensions}
    adjustments: list[dict[str, Any]] = []
    by_name: dict[str, DimensionReview] = {}
    for item in review.verdicts:
        if item.dimension not in DIMENSIONS:
            raise CrossReviewValidationError(
                f"未知复核维度：{item.dimension!r}（应为 {DIMENSIONS}）"
            )
        if item.dimension in by_name:
            raise CrossReviewValidationError(f"复核维度重复：{item.dimension}")
        by_name[item.dimension] = item.model_copy(deep=True)
    missing = [name for name in DIMENSIONS if name not in by_name]
    if missing:
        raise CrossReviewValidationError(f"复核结果缺少维度：{missing}")

    normalized: list[DimensionReview] = []
    risk_flags = list(review.risk_flags)
    for name in DIMENSIONS:
        item = by_name[name]
        first_score = first_scores.get(name)
        if item.verdict == "agree":
            if item.suggested_score is not None:
                adjustments.append(
                    {
                        "dimension": name,
                        "kind": "agree_with_score",
                        "detail": f"verdict=agree 但给出 suggested_score={item.suggested_score}，已置空",
                    }
                )
                item.suggested_score = None
            if not item.deviation.strip():
                item.deviation = "无"
        else:
            if item.suggested_score is None:
                adjustments.append(
                    {
                        "dimension": name,
                        "kind": "missing_suggested_score",
                        "detail": f"verdict={item.verdict} 但缺少 suggested_score，已按 agree 处理",
                    }
                )
                item.verdict = "agree"
                item.deviation = "无"
            elif first_score is not None:
                delta = item.suggested_score - first_score
                if abs(delta) > MAX_CROSS_REVIEW_DELTA:
                    clamped = first_score + MAX_CROSS_REVIEW_DELTA * (1 if delta > 0 else -1)
                    clamped = max(1, min(10, clamped))
                    adjustments.append(
                        {
                            "dimension": name,
                            "kind": "delta_clamped",
                            "detail": (
                                f"建议调整 {delta:+d} 分超过 ±{MAX_CROSS_REVIEW_DELTA} 分上限，"
                                f"已钳制为 {clamped} 分"
                            ),
                        }
                    )
                    item.suggested_score = clamped
        if not item.reason.strip():
            risk_flags.append(f"{name}：复核未给出引用依据，建议人工抽查")
            item.reason = "（复核模型未给出依据，建议人工抽查）"
        normalized.append(item)

    expected_overall = "adjust" if any(item.verdict != "agree" for item in normalized) else "agree"
    if review.overall_verdict != expected_overall:
        adjustments.append(
            {
                "dimension": "*",
                "kind": "overall_verdict_fixed",
                "detail": f"overall_verdict={review.overall_verdict} 与逐维结论不一致，已改为 {expected_overall}",
            }
        )
    corrected = CrossReview(
        verdicts=normalized,
        overall_verdict=expected_overall,
        summary=review.summary.strip() or "复核结论：无整体偏差说明。",
        risk_flags=risk_flags,
    )
    return corrected, adjustments


def cross_review_answer(
    conversation: str | Sequence[Mapping[str, Any]],
    first_evaluation: InterviewEvaluation | Mapping[str, Any],
    *,
    jd: Any = None,
    resume_excerpt: Any = None,
    router: Any = None,
    prompt_path: str | Path | None = None,
) -> CrossReviewResult:
    """对首评做交叉复核（必须走 `cross_review` 路由、与评分模型不同）。"""
    if router is None:
        router = _get_router()
    if isinstance(first_evaluation, Mapping):
        try:
            first_evaluation = InterviewEvaluation.model_validate(first_evaluation)
        except ValidationError as exc:
            raise EvaluationValidationError(f"首评结果不合法：{exc}") from exc
    first_evaluation = validate_evaluation(first_evaluation)
    conversation_text = (
        conversation if isinstance(conversation, str) else format_conversation(conversation)
    )
    prompt_text, version = load_prompt(prompt_path or DEFAULT_CROSS_REVIEW_PROMPT)
    messages = build_cross_review_messages(
        conversation_text,
        first_evaluation,
        prompt_text,
        jd=jd,
        resume_excerpt=resume_excerpt,
    )
    raw = _chat_json(router, CROSS_REVIEW_ROUTE, messages, CrossReview, max_repair=1)
    corrected, adjustments = enforce_cross_review_rules(raw, first_evaluation)
    return CrossReviewResult(review=corrected, adjustments=adjustments, prompt_version=version)


# ---------------------------------------------------------------------------
# 模型配置校验（决策④：评分与复核必须不同模型 + 固定低温 0.1）
# ---------------------------------------------------------------------------
def _route_configs(router: Any = None) -> dict[str, Any]:
    """获取路由配置：优先注入的 router，否则读 models.yaml。"""
    if router is not None and hasattr(router, "get_route_config"):
        return {
            EVALUATION_ROUTE: router.get_route_config(EVALUATION_ROUTE),
            CROSS_REVIEW_ROUTE: router.get_route_config(CROSS_REVIEW_ROUTE),
        }
    from backend.services.model_router import load_route_configs

    configs = load_route_configs()
    return {EVALUATION_ROUTE: configs[EVALUATION_ROUTE], CROSS_REVIEW_ROUTE: configs[CROSS_REVIEW_ROUTE]}


def check_model_separation(router: Any = None) -> dict[str, str]:
    """校验评分与复核使用不同模型；返回 {route: model}。违约抛 ModelSeparationError。"""
    configs = _route_configs(router)
    eval_model = str(getattr(configs[EVALUATION_ROUTE], "model", ""))
    review_model = str(getattr(configs[CROSS_REVIEW_ROUTE], "model", ""))
    if not eval_model or not review_model:
        raise ModelSeparationError("模型配置缺少 evaluation / cross_review 的 model 字段")
    if eval_model == review_model:
        raise ModelSeparationError(
            "配置违约（决策④）：cross_review 与 evaluation 必须使用不同模型，"
            f"当前均为 {eval_model}；请修改 backend/config/models.yaml"
        )
    return {EVALUATION_ROUTE: eval_model, CROSS_REVIEW_ROUTE: review_model}


def validate_model_config(router: Any = None) -> dict[str, str]:
    """校验评分 / 复核路由：模型不同 + 温度均为 0.1（评分一致性要求）。"""
    models = check_model_separation(router)
    configs = _route_configs(router)
    for route in (EVALUATION_ROUTE, CROSS_REVIEW_ROUTE):
        temperature = getattr(configs[route], "temperature", None)
        if temperature is None or abs(float(temperature) - REQUIRED_TEMPERATURE) > 1e-9:
            raise EvaluationError(
                f"路由 {route} 的 temperature 必须固定为 {REQUIRED_TEMPERATURE}，"
                f"当前为 {temperature}（见 backend/config/models.yaml）"
            )
    return models


# ---------------------------------------------------------------------------
# 会话聚合与报告
# ---------------------------------------------------------------------------
def aggregate_evaluations(results: Sequence[ScoreResult]) -> InterviewEvaluation:
    """把多轮评分聚合为整场面试的 InterviewEvaluation（各维取均分，四舍五入取整）。

    每个维度的证据从各轮评分中聚合（去重、最多 3 条），供报告的「原文证据」与交叉复核使用。
    """
    if not results:
        raise EvaluationError("没有可聚合的评分结果")
    dimensions: list[DimensionScore] = []
    for name in DIMENSIONS:
        scores: list[int] = []
        evidence: list[str] = []
        for item in results:
            for dimension in item.evaluation.dimensions:
                if dimension.dimension != name:
                    continue
                scores.append(dimension.score)
                for text in dimension.evidence:
                    cleaned = str(text).strip()
                    if cleaned and cleaned not in evidence and len(evidence) < 3:
                        evidence.append(cleaned)
        if not scores:
            raise EvaluationError(f"评分结果缺少维度：{name}")
        mean = sum(scores) / len(scores)
        dimensions.append(
            DimensionScore(dimension=name, score=int(mean + 0.5), evidence=evidence)
        )
    strengths = _merge_texts(item.evaluation.strengths for item in results)
    weaknesses = _merge_texts(item.evaluation.weaknesses for item in results)
    suggestions = _merge_texts(item.evaluation.suggestions for item in results)
    return InterviewEvaluation(
        dimensions=dimensions,
        strengths=strengths,
        weaknesses=weaknesses,
        suggestions=suggestions,
    )


def _merge_texts(groups: Iterable[Sequence[str]], limit: int = 5) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for text in group:
            cleaned = str(text).strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                merged.append(cleaned)
                if len(merged) >= limit:
                    return merged
    return merged


def evaluate_session(
    session_id: str,
    turns: Sequence[Mapping[str, Any]],
    *,
    scene: str = "",
    company: str = "",
    role: str = "",
    jd: Any = None,
    resume: Any = None,
    router: Any = None,
    scoring_prompt_path: str | Path | None = None,
    cross_review_prompt_path: str | Path | None = None,
) -> SessionEvaluation:
    """完整链路：逐轮实时评分 → 聚合 → 交叉复核 → 生成 Markdown 报告。"""
    if not turns:
        raise EvaluationError("面试轮次为空，无法生成报告")
    if router is None:
        router = _get_router()
    resume_excerpt = _compact_text(resume) if not isinstance(resume, str) else resume
    jd_text = _compact_text(jd) if not isinstance(jd, str) else jd

    turn_scores: list[TurnScore] = []
    for index, turn in enumerate(turns, start=1):
        question = str(turn.get("question", "")).strip()
        answer = str(turn.get("answer", "")).strip()
        if not question or not answer:
            raise EvaluationError(f"第 {index} 轮缺少 question / answer")
        result = score_answer(
            {"question": question, "jd": jd_text, "resume_excerpt": resume_excerpt},
            answer,
            router=router,
            prompt_path=scoring_prompt_path,
        )
        turn_scores.append(
            TurnScore(turn=int(turn.get("turn", index) or index), question=question, answer=answer, result=result)
        )

    aggregate = aggregate_evaluations([item.result for item in turn_scores])
    review_result = cross_review_answer(
        [item.__dict__ for item in turn_scores],
        aggregate,
        jd=jd_text,
        resume_excerpt=resume_excerpt,
        router=router,
        prompt_path=cross_review_prompt_path,
    )
    metadata = {
        "scene": scene,
        "company": company,
        "role": role,
        "scoring_prompt_version": turn_scores[0].result.prompt_version if turn_scores else "",
        "cross_review_prompt_version": review_result.prompt_version,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    markdown = render_markdown_report(
        session_id=session_id,
        evaluation=aggregate,
        cross_review=review_result.review,
        turn_scores=turn_scores,
        metadata=metadata,
    )
    return SessionEvaluation(
        session_id=session_id,
        evaluation=aggregate,
        cross_review=review_result.review,
        markdown=markdown,
        turn_scores=turn_scores,
        cross_review_adjustments=review_result.adjustments,
        metadata=metadata,
    )


def _truncate(text: str, limit: int = 90) -> str:
    text = str(text).strip().replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def render_markdown_report(
    *,
    session_id: str,
    evaluation: InterviewEvaluation,
    cross_review: CrossReview,
    turn_scores: Sequence[TurnScore] = (),
    metadata: Mapping[str, Any] | None = None,
) -> str:
    """按说明书 §4.4 的报告结构渲染 Markdown（可导出）。"""
    metadata = dict(metadata or {})
    generated_at = metadata.get("generated_at") or datetime.now().astimezone().isoformat(
        timespec="seconds"
    )
    total = overall_score(evaluation)
    lines = [
        f"# 面试评分报告（{session_id}）",
        "",
        f"- 生成时间：{generated_at}",
    ]
    scene_labels = {"intern": "实习", "autumn": "秋招", "grad": "保研"}
    if metadata.get("scene"):
        lines.append(f"- 场景：{scene_labels.get(str(metadata['scene']), metadata['scene'])}")
    if metadata.get("company") or metadata.get("role"):
        lines.append(f"- 目标：{metadata.get('company', '')} {metadata.get('role', '')}".rstrip())
    if metadata.get("scoring_prompt_version") or metadata.get("cross_review_prompt_version"):
        lines.append(
            "- Prompt 版本：评分 "
            f"{metadata.get('scoring_prompt_version') or '未知'} / 复核 "
            f"{metadata.get('cross_review_prompt_version') or '未知'}"
        )
    lines += [
        "",
        "## 一、总分与维度得分",
        "",
        f"**综合得分：{total:.2f} / 10**（4 维均分）",
        "",
        "| 维度 | 得分 | 原文证据 |",
        "| --- | --- | --- |",
    ]
    for item in evaluation.dimensions:
        evidence = "；".join(_truncate(text) for text in item.evidence) or "（无）"
        lines.append(f"| {item.dimension} | {item.score} | {evidence} |")

    lines += ["", "## 二、亮点", ""]
    if evaluation.strengths:
        lines.extend(f"- {text}" for text in evaluation.strengths)
    else:
        lines.append("- （未提供）")
    lines += ["", "## 三、短板", ""]
    if evaluation.weaknesses:
        lines.extend(f"- {text}" for text in evaluation.weaknesses)
    else:
        lines.append("- （未提供）")
    lines += ["", "## 四、改进建议", ""]
    if evaluation.suggestions:
        lines.extend(f"- {text}" for text in evaluation.suggestions)
    else:
        lines.append("- （未提供）")

    lines += ["", "## 五、交叉复核结论", ""]
    verdict_labels = {"agree": "认可", "too_high": "偏高", "too_low": "偏低"}
    lines.append(
        f"- 整体：{verdict_labels.get(cross_review.overall_verdict, cross_review.overall_verdict)}"
        f" —— {cross_review.summary or '无'}"
    )
    for item in cross_review.verdicts:
        adjust = (
            f"，建议 {item.suggested_score} 分" if item.suggested_score is not None else ""
        )
        lines.append(
            f"- {item.dimension}：{verdict_labels.get(item.verdict, item.verdict)}"
            f"（{item.deviation or '无偏差'}{adjust}）—— {item.reason or '无依据'}"
        )
    if cross_review.risk_flags:
        lines += ["", "**风险提示**", ""]
        lines.extend(f"- {flag}" for flag in cross_review.risk_flags)

    if turn_scores:
        lines += ["", "## 附录：逐题得分", "", "| 轮次 | 题目 | 4 维均分 |", "| --- | --- | --- |"]
        for item in turn_scores:
            lines.append(
                f"| {item.turn} | {_truncate(item.question, 60)} | {item.result.overall:.2f} |"
            )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 报告存储与 API payload（契约冻结，见模块 docstring）
# ---------------------------------------------------------------------------
_REPORT_STORE: dict[str, dict[str, Any]] = {}
_REPORT_LOCK = threading.Lock()


def _new_report_id() -> str:
    """报告 ID 格式（与 docs/api.md 示例一致）：``rep_YYYYMMDD_<6 位 hex>``。"""
    return f"rep_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"


def cross_review_to_api(review: CrossReview) -> dict[str, Any]:
    """把内部复核结论映射为 docs/api.md 冻结的 `cross_review` 数据键。

    契约（docs/api.md §五）::

        {"agreed": bool, "adjustments": [...], "deviation_note": "..."}

    - `agreed`：4 维全部认可时为 True；
    - `adjustments`：仅收集**不认可**维度的调整建议（维度/结论/建议分/偏差/依据）；
    - `deviation_note`：首评偏差说明（复核 summary，附加风险提示）。
    详细逐维复核结论仍保存在服务端 `cross_review_detail`（审计 / 报告 Markdown 使用）。
    """
    adjustments = [
        {
            "dimension": item.dimension,
            "verdict": item.verdict,
            "suggested_score": item.suggested_score,
            "deviation": item.deviation,
            "reason": item.reason,
        }
        for item in review.verdicts
        if item.verdict != "agree"
    ]
    note = (review.summary or "").strip()
    if review.risk_flags:
        risk_text = "；".join(flag for flag in review.risk_flags if flag)
        note = f"{note}；风险提示：{risk_text}".strip("；") if note else f"风险提示：{risk_text}"
    return {
        "agreed": review.overall_verdict == "agree",
        "adjustments": adjustments,
        "deviation_note": note,
    }


def save_report(payload: Mapping[str, Any]) -> dict[str, Any]:
    """保存报告到进程内存储（保留最近 REPORT_STORE_LIMIT 份）。"""
    report = dict(payload)
    report.setdefault("created_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    with _REPORT_LOCK:
        _REPORT_STORE[report["report_id"]] = report
        if len(_REPORT_STORE) > REPORT_STORE_LIMIT:
            for stale_key in list(_REPORT_STORE)[: len(_REPORT_STORE) - REPORT_STORE_LIMIT]:
                _REPORT_STORE.pop(stale_key, None)
    return report


def get_report(report_id: str) -> dict[str, Any]:
    """按 report_id 取报告；不存在抛 ReportNotFoundError（API 层转 404）。"""
    with _REPORT_LOCK:
        report = _REPORT_STORE.get(report_id)
    if report is None:
        raise ReportNotFoundError(f"报告不存在：{report_id}")
    return report


def get_report_markdown(report_id: str) -> str:
    """取报告 Markdown 正文；不存在抛 ReportNotFoundError（API 层转 404）。"""
    return str(get_report(report_id)["markdown"])


def clear_reports() -> None:
    """清空进程内报告存储（测试 / 重启演示用）。"""
    with _REPORT_LOCK:
        _REPORT_STORE.clear()


def generate_report(
    session_id: str,
    turns: Sequence[Mapping[str, Any]],
    *,
    scene: str = "",
    company: str = "",
    role: str = "",
    jd: Any = None,
    resume: Any = None,
    router: Any = None,
    check_config: bool = True,
) -> dict[str, Any]:
    """生成并保存面试报告，返回报告 payload。

    无 Key 时不会自动 mock：真实评分需要可用的 `evaluation` / `cross_review` 路由。
    """
    if check_config:
        validate_model_config(router)
    session = evaluate_session(
        session_id,
        turns,
        scene=scene,
        company=company,
        role=role,
        jd=jd,
        resume=resume,
        router=router,
    )
    report_id = _new_report_id()
    payload = {
        "report_id": report_id,
        "session_id": session_id,
        "scene": scene,
        "company": company,
        "role": role,
        "evaluation": session.evaluation.model_dump(),
        # docs/api.md 冻结数据键：{agreed, adjustments, deviation_note}
        "cross_review": cross_review_to_api(session.cross_review),
        # 服务端内部保留逐维明细（审计 / 报告 Markdown；不进入 API data）
        "cross_review_detail": session.cross_review.model_dump(),
        "cross_review_adjustments": session.cross_review_adjustments,
        "turn_evaluations": [
            {
                "turn": item.turn,
                "question": item.question,
                "overall": item.result.overall,
                "evaluation": item.result.evaluation.model_dump(),
                "grounding_adjustments": item.result.adjustments,
            }
            for item in session.turn_scores
        ],
        "markdown": session.markdown,
        "metadata": session.metadata,
    }
    saved = save_report(payload)
    return to_generate_response(saved)


def generate_report_for_session(
    session: Any,
    *,
    router: Any = None,
    check_config: bool = True,
) -> dict[str, Any]:
    """从模块 C 的 `InterviewSession`（或等价 dict）生成报告，便于 API 层直接调用。

    兼容两种输入：
    - 对象：读取 `session_id / turns / scene / company / role / jd / resume`；
    - dict：同上；若含 `POST /api/interview/.../end` 返回的 `report_input`
      （`{resume_text, jd_text, scene, turn_count}`），自动从其中补齐场景 / 简历 / JD。
    """
    if isinstance(session, Mapping):
        data = dict(session)
    else:
        data = {
            "session_id": getattr(session, "session_id", ""),
            "turns": getattr(session, "turns", []),
            "scene": getattr(session, "scene", ""),
            "company": getattr(session, "company", ""),
            "role": getattr(session, "role", ""),
            "jd": getattr(session, "jd", None),
            "resume": getattr(session, "resume", None),
        }
    report_input = data.get("report_input")
    report_input = dict(report_input) if isinstance(report_input, Mapping) else {}
    turns = data.get("turns") or []
    normalized_turns = [
        turn.to_dict() if hasattr(turn, "to_dict") else dict(turn) for turn in turns
    ]
    return generate_report(
        str(data.get("session_id") or ""),
        normalized_turns,
        scene=str(data.get("scene") or report_input.get("scene") or ""),
        company=str(data.get("company") or ""),
        role=str(data.get("role") or ""),
        jd=data.get("jd", report_input.get("jd_text")),
        resume=data.get("resume", report_input.get("resume_text")),
        router=router,
        check_config=check_config,
    )


def export_markdown(report: Mapping[str, Any] | str, path: str | Path) -> Path:
    """把报告 Markdown 导出到文件（报告可导出；PDF 为加分项，暂不实现）。"""
    markdown = report if isinstance(report, str) else str(report.get("markdown", ""))
    if not markdown.strip():
        raise EvaluationError("报告 Markdown 为空，无法导出")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(markdown, encoding="utf-8")
    return target


def to_generate_response(report: Mapping[str, Any]) -> dict[str, Any]:
    """POST /api/report/generate 的 data 结构（docs/api.md 冻结）。"""
    return {
        "report_id": report["report_id"],
        "evaluation": report["evaluation"],
        "cross_review": report["cross_review"],
        "markdown": report["markdown"],
    }


def to_get_response(report: Mapping[str, Any]) -> dict[str, Any]:
    """GET /api/report/{report_id} 的 data 结构。

    docs/api.md：``按报告 ID 取回完整报告（结构同 /generate 的 data）``，故与 generate 一致（含 markdown）。
    """
    return to_generate_response(report)


def to_markdown_response(report: Mapping[str, Any]) -> str:
    """GET /api/report/{rid}/markdown 的响应体（text/markdown）。"""
    return str(report["markdown"])


__all__ = [
    "CROSS_REVIEW_ROUTE",
    "CrossReview",
    "CrossReviewResult",
    "CrossReviewValidationError",
    "DIMENSIONS",
    "DimensionReview",
    "EVALUATION_ROUTE",
    "EvaluationError",
    "EvaluationValidationError",
    "ModelSeparationError",
    "ReportNotFoundError",
    "ScoreResult",
    "SessionEvaluation",
    "TurnScore",
    "aggregate_evaluations",
    "apply_grounding_rule",
    "build_cross_review_messages",
    "build_scoring_messages",
    "check_model_separation",
    "clear_reports",
    "cross_review_answer",
    "cross_review_to_api",
    "enforce_cross_review_rules",
    "evaluate_session",
    "export_markdown",
    "format_conversation",
    "generate_report",
    "generate_report_for_session",
    "get_report",
    "get_report_markdown",
    "load_prompt",
    "overall_score",
    "render_markdown_report",
    "save_report",
    "score_answer",
    "score_turn",
    "to_generate_response",
    "to_get_response",
    "to_markdown_response",
    "validate_evaluation",
    "validate_model_config",
]
