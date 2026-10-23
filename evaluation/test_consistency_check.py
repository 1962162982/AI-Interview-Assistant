"""`consistency_check.py` mock 测试（不依赖网络 / Key）。"""
from __future__ import annotations

from pathlib import Path

import consistency_check as cc
import run_eval


def test_select_samples_balanced_across_scenes_and_deterministic():
    samples = run_eval.load_qa_samples()
    picked = cc.select_samples(samples, limit=10, seed=0)
    assert len(picked) == 10
    ids = [sample["id"] for sample in picked]
    assert len(ids) == len(set(ids))
    assert {sample["scene"] for sample in picked} == {"intern", "autumn", "grad"}
    again = cc.select_samples(samples, limit=10, seed=0)
    assert [sample["id"] for sample in again] == ids


def test_select_samples_rejects_bad_limit():
    samples = run_eval.load_qa_samples()
    try:
        cc.select_samples(samples, limit=0)
    except cc.ConsistencyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("limit=0 应抛 ConsistencyError")


def test_compute_result_pass_and_fail():
    pass_rows = [
        cc.ConsistencyRow("s1", "intern", 6.3, 6.0, 0.3, 5.0),
        cc.ConsistencyRow("s2", "autumn", 5.4, 6.0, 0.6, 10.0),
    ]
    result = cc.compute_result(pass_rows, threshold=15.0, tier="medium", mode="mock")
    assert result.passed is True
    assert round(result.mean_rel_deviation_pct, 2) == 7.5

    fail_rows = [cc.ConsistencyRow("s1", "intern", 8.0, 6.0, 2.0, 100.0 / 3)]
    result = cc.compute_result(fail_rows, threshold=15.0, tier="medium", mode="mock")
    assert result.passed is False


def test_mock_cli_writes_report(tmp_dir: Path):
    out_path = tmp_dir / "consistency.md"
    assert cc.main(["--mock", "--limit", "10", "--out", str(out_path), "--quiet"]) == 0
    content = out_path.read_text(encoding="utf-8")
    assert "# 评分一致性抽样报告（阶段 4 收尾）" in content
    assert "档位 `medium`" in content
    assert "平均相对偏差" in content
    assert "≤ 15%" in content
    assert "**通过**" in content
    # 10 条样本各一行表格数据
    table_rows = [line for line in content.splitlines() if line.startswith("| qa-")]
    assert len(table_rows) == 10


def test_mock_overall_near_expected_center():
    samples = run_eval.load_qa_samples()
    sample = samples[0]
    value = cc.mock_overall(sample, "medium")
    assert 5.0 <= value <= 7.0
    assert abs(value - 6.0) < 1.0


def test_render_contains_model_and_prompt_version():
    result = cc.run_consistency(live=False, limit=5)
    markdown = cc.render_markdown(result)
    assert "评分 Prompt 版本：v2" in markdown
    assert "mode" not in markdown or "模式：mock" in markdown
