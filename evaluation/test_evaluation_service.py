"""模块 D 服务测试（backend/services/evaluation.py）—— 全部使用 FakeRouter，无 Key / 无网络。"""
from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest

from backend.schemas.interview_evaluation import DimensionScore, InterviewEvaluation
from backend.services import evaluation as ev

EVAL_MODEL = "glm-4-plus"
REVIEW_MODEL = "deepseek-chat"


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------
def evaluation_payload(
    scores: tuple[int, int, int, int] = (7, 6, 5, 6),
    evidence_counts: tuple[int, int, int, int] = (2, 2, 2, 2),
    strengths: tuple[str, ...] = ("亮点A",),
    weaknesses: tuple[str, ...] = ("短板A",),
    suggestions: tuple[str, ...] = ("建议A",),
) -> dict[str, Any]:
    dimensions = [
        {
            "dimension": name,
            "score": score,
            "evidence": [f"{name}证据{index + 1}" for index in range(count)],
        }
        for name, score, count in zip(ev.DIMENSIONS, scores, evidence_counts)
    ]
    return {
        "dimensions": dimensions,
        "strengths": list(strengths),
        "weaknesses": list(weaknesses),
        "suggestions": list(suggestions),
    }


def review_payload(
    verdicts: list[dict[str, Any]] | None = None,
    *,
    overall_verdict: str | None = None,
    summary: str = "复核完成",
    risk_flags: list[str] | None = None,
) -> dict[str, Any]:
    if verdicts is None:
        verdicts = [
            {
                "dimension": name,
                "verdict": "agree",
                "suggested_score": None,
                "deviation": "无",
                "reason": f"首评{name}证据可支撑",
            }
            for name in ev.DIMENSIONS
        ]
    if overall_verdict is None:
        overall_verdict = "adjust" if any(item["verdict"] != "agree" for item in verdicts) else "agree"
    return {
        "verdicts": verdicts,
        "overall_verdict": overall_verdict,
        "summary": summary,
        "risk_flags": risk_flags or [],
    }


class FakeRouter:
    """模拟 T1 ModelRouter 的 chat_json / get_route_config 接口。"""

    def __init__(
        self,
        evaluation: dict[str, Any] | None = None,
        review: dict[str, Any] | None = None,
        *,
        models: dict[str, str] | None = None,
        temperatures: dict[str, float] | None = None,
    ) -> None:
        self.evaluation_payload = evaluation
        self.review_payload = review
        self.models = models or {ev.EVALUATION_ROUTE: EVAL_MODEL, ev.CROSS_REVIEW_ROUTE: REVIEW_MODEL}
        self.temperatures = temperatures or {
            ev.EVALUATION_ROUTE: 0.1,
            ev.CROSS_REVIEW_ROUTE: 0.1,
        }
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    def chat_json(self, route: str, messages: list[dict[str, str]], schema: type, *, max_repair: int = 1):
        self.calls.append((route, messages))
        payload = self.evaluation_payload if route == ev.EVALUATION_ROUTE else self.review_payload
        assert payload is not None, f"FakeRouter 未配置 route={route} 的返回"
        return SimpleNamespace(value=schema.model_validate(payload))

    def get_route_config(self, route: str):
        return SimpleNamespace(model=self.models[route], temperature=self.temperatures[route])


@pytest.fixture(autouse=True)
def _clean_report_store():
    ev.clear_reports()
    yield
    ev.clear_reports()


# ---------------------------------------------------------------------------
# 评分：契约 / grounding 规则
# ---------------------------------------------------------------------------
def test_score_answer_returns_four_dimensions_in_fixed_order():
    fake = FakeRouter(evaluation=evaluation_payload())
    result = ev.score_answer(
        {"question": "介绍一下你的项目", "jd": "后端实习", "resume_excerpt": "校园项目"},
        "我优化了接口性能，QPS 提升 3 倍。",
        router=fake,
    )
    assert [item.dimension for item in result.evaluation.dimensions] == list(ev.DIMENSIONS)
    assert result.evaluation.dimensions[0].score == 7
    assert result.prompt_version == "v2"
    assert fake.calls and fake.calls[0][0] == ev.EVALUATION_ROUTE
    user_message = fake.calls[0][1][1]["content"]
    assert "answer" in user_message and "question" in user_message


def test_evidence_insufficient_caps_score_at_six():
    fake = FakeRouter(evaluation=evaluation_payload(scores=(7, 6, 8, 6), evidence_counts=(2, 2, 1, 2)))
    result = ev.score_answer({"question": "q"}, "回答", router=fake)
    specificity = next(item for item in result.evaluation.dimensions if item.dimension == "具体性")
    assert specificity.score == 6
    assert result.adjustments == [
        {
            "dimension": "具体性",
            "from": 8,
            "to": 6,
            "reason": "引用证据 1 条 < 2 条，按 grounding 规则该维度不得高于 6 分",
        }
    ]
    assert result.overall == round((7 + 6 + 6 + 6) / 4, 2)


def test_evidence_insufficient_but_score_six_is_not_changed():
    raw = InterviewEvaluation.model_validate(evaluation_payload(scores=(6, 5, 4, 6), evidence_counts=(0, 1, 1, 0)))
    fixed, adjustments = ev.apply_grounding_rule(raw)
    assert adjustments == []
    assert [item.score for item in fixed.dimensions] == [6, 5, 4, 6]


def test_validate_evaluation_rejects_wrong_dimension_count():
    payload = evaluation_payload()
    payload["dimensions"] = payload["dimensions"][:3]
    raw = InterviewEvaluation.model_validate(payload)
    with pytest.raises(ev.EvaluationValidationError):
        ev.validate_evaluation(raw)


def test_validate_evaluation_rejects_duplicate_dimension():
    payload = evaluation_payload()
    payload["dimensions"][0]["dimension"] = "结构性"  # 结构性重复、相关性缺失
    raw = InterviewEvaluation.model_validate(payload)
    with pytest.raises(ev.EvaluationValidationError):
        ev.validate_evaluation(raw)


def test_score_turn_accepts_module_c_turn():
    fake = FakeRouter(evaluation=evaluation_payload())
    turn = {"turn": 1, "question": "你的项目难点？", "answer": "难点是并发超卖，我用 Redis 预扣库存解决。"}
    result = ev.score_turn(turn, jd="后端实习", resume_excerpt="校园二手平台", router=fake)
    assert result.evaluation.dimensions
    assert fake.calls[0][0] == ev.EVALUATION_ROUTE


# ---------------------------------------------------------------------------
# 交叉复核：契约纠偏
# ---------------------------------------------------------------------------
def _first_evaluation() -> dict[str, Any]:
    return evaluation_payload(scores=(7, 7, 7, 7), evidence_counts=(2, 2, 2, 2))


def test_cross_review_clamps_adjustment_delta_and_normalizes_agree():
    fake = FakeRouter(
        review=review_payload(
            [
                {
                    "dimension": "相关性",
                    "verdict": "agree",
                    "suggested_score": 8,
                    "deviation": "无",
                    "reason": "证据可支撑",
                },
                {
                    "dimension": "结构性",
                    "verdict": "agree",
                    "suggested_score": None,
                    "deviation": "无",
                    "reason": "证据可支撑",
                },
                {
                    "dimension": "具体性",
                    "verdict": "too_high",
                    "suggested_score": 2,
                    "deviation": "偏高 5 分",
                    "reason": "证据不足",
                },
                {
                    "dimension": "岗位匹配",
                    "verdict": "agree",
                    "suggested_score": None,
                    "deviation": "无",
                    "reason": "证据可支撑",
                },
            ]
        )
    )
    result = ev.cross_review_answer("第 1 轮 面试官：q\n候选人：a", _first_evaluation(), router=fake)
    verdicts = {item.dimension: item for item in result.review.verdicts}
    assert verdicts["相关性"].suggested_score is None  # agree 时置空
    assert verdicts["具体性"].suggested_score == 5  # 7 - 2 分上限
    assert result.review.overall_verdict == "adjust"
    kinds = {item["kind"] for item in result.adjustments}
    assert {"agree_with_score", "delta_clamped"} <= kinds
    assert result.prompt_version == "v2"


def test_cross_review_missing_suggested_score_downgrades_to_agree():
    fake = FakeRouter(
        review=review_payload(
            [
                {
                    "dimension": "相关性",
                    "verdict": "too_low",
                    "suggested_score": None,
                    "deviation": "偏低 1 分",
                    "reason": "依据不足",
                },
                *[
                    {
                        "dimension": name,
                        "verdict": "agree",
                        "suggested_score": None,
                        "deviation": "无",
                        "reason": "证据可支撑",
                    }
                    for name in ev.DIMENSIONS[1:]
                ],
            ]
        )
    )
    result = ev.cross_review_answer("第 1 轮 面试官：q\n候选人：a", _first_evaluation(), router=fake)
    verdicts = {item.dimension: item for item in result.review.verdicts}
    assert verdicts["相关性"].verdict == "agree"
    assert any(item["kind"] == "missing_suggested_score" for item in result.adjustments)
    assert result.review.overall_verdict == "agree"


def test_cross_review_empty_conversation_raises():
    fake = FakeRouter(review=review_payload())
    with pytest.raises(ev.EvaluationError):
        ev.cross_review_answer("   ", _first_evaluation(), router=fake)


def test_cross_review_uses_cross_review_route():
    fake = FakeRouter(review=review_payload())
    ev.cross_review_answer("第 1 轮 面试官：q\n候选人：a", _first_evaluation(), router=fake)
    assert fake.calls[0][0] == ev.CROSS_REVIEW_ROUTE


# ---------------------------------------------------------------------------
# 模型配置（决策④ / 固定低温）
# ---------------------------------------------------------------------------
def test_check_model_separation_passes_for_distinct_models():
    models = ev.check_model_separation(FakeRouter(models={ev.EVALUATION_ROUTE: EVAL_MODEL, ev.CROSS_REVIEW_ROUTE: REVIEW_MODEL}))
    assert models == {ev.EVALUATION_ROUTE: EVAL_MODEL, ev.CROSS_REVIEW_ROUTE: REVIEW_MODEL}


def test_check_model_separation_rejects_same_model():
    fake = FakeRouter(models={ev.EVALUATION_ROUTE: "glm-4-plus", ev.CROSS_REVIEW_ROUTE: "glm-4-plus"})
    with pytest.raises(ev.ModelSeparationError):
        ev.check_model_separation(fake)


def test_validate_model_config_requires_temperature_point_one():
    fake = FakeRouter(
        temperatures={ev.EVALUATION_ROUTE: 0.1, ev.CROSS_REVIEW_ROUTE: 0.7},
    )
    with pytest.raises(ev.EvaluationError):
        ev.validate_model_config(fake)


def test_real_models_yaml_is_valid():
    """从真实 models.yaml 校验：评分/复核异模型 + 温度 0.1。"""
    models = ev.validate_model_config()
    assert models[ev.EVALUATION_ROUTE] != models[ev.CROSS_REVIEW_ROUTE]
    assert models[ev.EVALUATION_ROUTE] == EVAL_MODEL
    assert models[ev.CROSS_REVIEW_ROUTE] == REVIEW_MODEL


# ---------------------------------------------------------------------------
# 聚合 / 报告 / API payload
# ---------------------------------------------------------------------------
def _score_result(scores: tuple[int, int, int, int]) -> ev.ScoreResult:
    raw = InterviewEvaluation.model_validate(
        evaluation_payload(scores=scores, evidence_counts=(2, 2, 2, 2))
    )
    grounded, _ = ev.apply_grounding_rule(raw)
    return ev.ScoreResult(evaluation=grounded)


def test_aggregate_evaluations_means_and_merges_evidence():
    aggregate = ev.aggregate_evaluations([_score_result((7, 7, 7, 7)), _score_result((8, 8, 8, 8))])
    assert [item.score for item in aggregate.dimensions] == [8, 8, 8, 8]  # 7.5 四舍五入
    for item in aggregate.dimensions:
        assert 2 <= len(item.evidence) <= 3


def test_format_conversation_contains_turns():
    text = ev.format_conversation(
        [
            {"turn": 1, "question": "自我介绍", "answer": "我是……"},
            {"turn": 2, "question": "项目难点", "answer": "并发超卖"},
        ]
    )
    assert "第 1 轮 面试官：自我介绍" in text
    assert "候选人：并发超卖" in text


def test_generate_report_full_pipeline(tmp_dir):
    fake = FakeRouter(
        evaluation=evaluation_payload(scores=(8, 7, 6, 7)),
        review=review_payload(),
    )
    turns = [
        {"turn": 1, "question": "介绍一下你的项目", "answer": "我做了校园二手交易平台，负责交易链路。"},
        {"turn": 2, "question": "项目难点是什么", "answer": "并发超卖，我用 Redis 预扣库存 + Lua 解决。"},
    ]
    report = ev.generate_report(
        "sess-demo",
        turns,
        scene="intern",
        company="示例公司",
        role="后端开发实习生",
        router=fake,
    )
    assert set(report) == {"report_id", "evaluation", "cross_review", "markdown"}
    assert re.fullmatch(r"rep_\d{8}_[0-9a-f]{6}", report["report_id"]), report["report_id"]
    assert len(report["evaluation"]["dimensions"]) == 4
    # docs/api.md 冻结数据键：{agreed, adjustments, deviation_note}
    assert set(report["cross_review"]) == {"agreed", "adjustments", "deviation_note"}
    assert report["cross_review"]["agreed"] is True
    assert report["cross_review"]["adjustments"] == []
    assert isinstance(report["cross_review"]["deviation_note"], str)
    markdown = report["markdown"]
    assert "# 面试评分报告（sess-demo）" in markdown
    assert "## 一、总分与维度得分" in markdown
    assert "## 五、交叉复核结论" in markdown
    assert "附录：逐题得分" in markdown
    assert "综合得分" in markdown

    stored = ev.get_report(report["report_id"])
    assert stored["session_id"] == "sess-demo"
    # 服务端保留逐维明细（不进入 API data）
    assert stored["cross_review_detail"]["verdicts"]
    # GET /api/report/{rid} 结构同 generate 的 data（docs/api.md）
    assert ev.to_get_response(stored) == report
    assert ev.to_generate_response(stored) == report
    assert ev.get_report_markdown(report["report_id"]) == markdown

    exported = ev.export_markdown(stored, tmp_dir / "report.md")
    assert exported.is_file()
    assert "面试评分报告" in exported.read_text(encoding="utf-8")


def test_cross_review_maps_to_frozen_api_shape():
    review = ev.CrossReview.model_validate(
        review_payload(
            [
                {
                    "dimension": "相关性",
                    "verdict": "agree",
                    "suggested_score": None,
                    "deviation": "无",
                    "reason": "证据可支撑",
                },
                {
                    "dimension": "结构性",
                    "verdict": "agree",
                    "suggested_score": None,
                    "deviation": "无",
                    "reason": "证据可支撑",
                },
                {
                    "dimension": "具体性",
                    "verdict": "too_high",
                    "suggested_score": 6,
                    "deviation": "偏高 1 分",
                    "reason": "证据只有一条",
                },
                {
                    "dimension": "岗位匹配",
                    "verdict": "agree",
                    "suggested_score": None,
                    "deviation": "无",
                    "reason": "证据可支撑",
                },
            ],
            risk_flags=["具体性：首评 evidence 仅 1 条但给 8 分"],
        )
    )
    api = ev.cross_review_to_api(review)
    assert set(api) == {"agreed", "adjustments", "deviation_note"}
    assert api["agreed"] is False
    assert len(api["adjustments"]) == 1
    adjustment = api["adjustments"][0]
    assert adjustment["dimension"] == "具体性"
    assert adjustment["verdict"] == "too_high"
    assert adjustment["suggested_score"] == 6
    assert "风险提示" in api["deviation_note"]


def test_generate_report_for_session_dict_with_report_input():
    fake = FakeRouter(evaluation=evaluation_payload(), review=review_payload())
    session = {
        "session_id": "sess-end",
        "turns": [{"turn": 1, "question": "介绍项目", "answer": "我做了交易平台。"}],
        # POST /api/interview/sessions/{id}/end 返回的 report_input
        "report_input": {
            "resume_text": "虚构简历文本",
            "jd_text": "后端实习 JD",
            "scene": "autumn",
            "turn_count": 1,
        },
    }
    report = ev.generate_report_for_session(session, router=fake)
    assert report["report_id"].startswith("rep_")
    assert "场景：秋招" in report["markdown"]


def test_generate_report_for_session_object():
    fake = FakeRouter(evaluation=evaluation_payload(), review=review_payload())
    session = SimpleNamespace(
        session_id="sess-obj",
        scene="grad",
        company="某高校",
        role="保研",
        jd=None,
        resume=None,
        turns=[{"turn": 1, "question": "介绍科研经历", "answer": "我做了图神经网络推荐系统。"}],
    )
    report = ev.generate_report_for_session(session, router=fake)
    assert report["report_id"]
    assert report["markdown"].startswith("# 面试评分报告（sess-obj）")


def test_get_missing_report_raises():
    with pytest.raises(ev.ReportNotFoundError):
        ev.get_report("rpt-not-exists")
    with pytest.raises(ev.ReportNotFoundError):
        ev.get_report_markdown("rpt-not-exists")


def test_load_prompt_strips_version_comment():
    body, version = ev.load_prompt(ev.DEFAULT_SCORING_PROMPT)
    assert version == "v2"
    assert body.startswith("# 面试回答评分 Prompt")
    assert "<!-- prompt_version" not in body


def test_all_prompts_loadable():
    scoring_body, scoring_version = ev.load_prompt(ev.DEFAULT_SCORING_PROMPT)
    review_body, review_version = ev.load_prompt(ev.DEFAULT_CROSS_REVIEW_PROMPT)
    assert scoring_version == review_version == "v2"
    assert "岗位匹配" in scoring_body
    assert "suggested_score" in review_body
