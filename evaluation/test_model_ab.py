"""`model_ab.py` mock 测试（不依赖网络 / Key）。"""
from __future__ import annotations

from pathlib import Path

import model_ab as ab
import run_eval


def test_select_samples_scene_balanced_and_deterministic():
    samples = run_eval.load_qa_samples()
    picked = ab.select_samples(samples, limit=2, seed=0)
    assert len(picked) == 2
    assert len({sample["id"] for sample in picked}) == 2
    again = ab.select_samples(samples, limit=2, seed=0)
    assert [sample["id"] for sample in again] == [sample["id"] for sample in picked]


def test_select_samples_respects_explicit_ids():
    samples = run_eval.load_qa_samples()
    picked = ab.select_samples(samples, sample_ids=("qa-intern-001", "qa-autumn-001"))
    assert [sample["id"] for sample in picked] == ["qa-intern-001", "qa-autumn-001"]


def test_select_samples_unknown_id_raises():
    samples = run_eval.load_qa_samples()
    try:
        ab.select_samples(samples, sample_ids=("qa-not-exist",))
    except ab.AbError:
        pass
    else:  # pragma: no cover
        raise AssertionError("未知 sample_id 应抛 AbError")


def test_flash_config_is_valid_and_uses_flash_for_evaluation():
    from backend.services.model_router import load_route_configs

    configs = load_route_configs(ab.FLASH_CONFIG)
    assert set(configs) == {
        "resume_diagnosis",
        "jd_matching",
        "interview_agent",
        "evaluation",
        "cross_review",
    }
    assert configs["evaluation"].model == "deepseek-flash"
    # 决策④强校验：评分与复核必须异模型（flash 配置里 cross_review 为占位）
    assert configs["cross_review"].model != configs["evaluation"].model


def test_mock_metrics_prefer_quality_config():
    result = ab.run_ab(live=False, limit=2)
    assert result.mode == "mock"
    assert result.n_items == 6
    assert set(result.metrics) == {"mixed", "flash"}
    mixed = result.metrics["mixed"]
    flash = result.metrics["flash"]
    assert mixed["band_hit_rate"] >= flash["band_hit_rate"]
    assert mixed["mae"] < flash["mae"]


def test_mock_cli_writes_report(tmp_dir: Path):
    out_path = tmp_dir / "model_ab.md"
    assert ab.main(["--mock", "--out", str(out_path), "--quiet"]) == 0
    content = out_path.read_text(encoding="utf-8")
    assert "# 模型分配 A/B 报告" in content
    assert "band_hit_rate" in content
    assert "MAE" in content
    assert "临时同供应商局限" in content
    assert "cross_review" in content


def test_mock_cli_accepts_sample_ids(tmp_dir: Path):
    out_path = tmp_dir / "model_ab_ids.md"
    assert (
        ab.main(
            [
                "--mock",
                "--sample-ids",
                "qa-intern-001,qa-grad-001",
                "--out",
                str(out_path),
                "--quiet",
            ]
        )
        == 0
    )
    content = out_path.read_text(encoding="utf-8")
    assert "qa-intern-001" in content and "qa-grad-001" in content
