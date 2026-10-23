"""Prompt v2 契约测试：scoring.md / cross_review.md 的版本、rubric、锚点与输出契约。"""
from __future__ import annotations

import re
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent
SCORING = (REPO_ROOT / "prompts" / "scoring.md").read_text(encoding="utf-8")
CROSS_REVIEW = (REPO_ROOT / "prompts" / "cross_review.md").read_text(encoding="utf-8")
BASELINE_V1 = (EVAL_DIR / "prompt_baselines" / "scoring_v1.md").read_text(encoding="utf-8")

DIMENSIONS = ("相关性", "结构性", "具体性", "岗位匹配")


def test_scoring_prompt_has_v2_version_comment():
    assert re.search(r"prompt_version:\s*v2", SCORING)
    assert re.search(r"changed:\s*2026-\d{2}-\d{2}", SCORING)
    assert "summary:" in SCORING


def test_scoring_prompt_contains_four_dimensions_and_band_semantics():
    for dimension in DIMENSIONS:
        assert dimension in SCORING
    assert "1–10" in SCORING or "1-10" in SCORING
    for marker in ("差档", "中档", "优档", "1–4", "5–7", "8–10"):
        assert marker in SCORING, f"scoring.md 缺少锚定分档标记：{marker}"


def test_scoring_prompt_contains_grounding_hard_rule():
    assert "强制 grounding" in SCORING
    assert "证据" in SCORING and "不得高于 6 分" in SCORING
    assert "编造" in SCORING  # 禁止编造原文证据
    assert "不要输出总分" in SCORING


def test_scoring_prompt_output_matches_interview_evaluation_schema():
    for key in ('"dimensions"', '"strengths"', '"weaknesses"', '"suggestions"'):
        assert key in SCORING, f"scoring.md 输出契约缺少字段：{key}"
    for key in ('"dimension"', '"score"', '"evidence"'):
        assert key in SCORING, f"scoring.md 维度契约缺少字段：{key}"
    assert "json.loads" in SCORING  # 明确可被 json.loads 直接解析


def test_scoring_prompt_has_no_todo_placeholder():
    assert "TODO" not in SCORING


def test_baseline_v1_snapshot_is_frozen_v1():
    assert re.search(r"prompt_version:\s*v1", BASELINE_V1)
    assert "冻结" in BASELINE_V1
    assert "锚定示例" in BASELINE_V1  # 保留 v1 原始内容，用于 v_old 对比


def test_cross_review_prompt_has_v2_contract():
    assert re.search(r"prompt_version:\s*v2", CROSS_REVIEW)
    # 必须与评分不同模型（决策④）
    assert "不同模型" in CROSS_REVIEW or "跨供应商" in CROSS_REVIEW
    # 输出契约字段
    for key in ('"verdicts"', '"overall_verdict"', '"summary"', '"risk_flags"'):
        assert key in CROSS_REVIEW
    for marker in ('"verdict"', '"suggested_score"', '"deviation"', '"reason"'):
        assert marker in CROSS_REVIEW
    # 逐维结论枚举与调整幅度约束
    for verdict in ("agree", "too_high", "too_low"):
        assert verdict in CROSS_REVIEW
    assert "≤2 分" in CROSS_REVIEW
    assert "偏差" in CROSS_REVIEW
    assert "TODO" not in CROSS_REVIEW
