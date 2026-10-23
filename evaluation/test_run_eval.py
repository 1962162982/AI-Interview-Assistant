"""`evaluation/run_eval.py` 回归关卡测试（无 Key / mock 模式）。

覆盖：样本加载与校验、mock 评分确定性、阈值判定、退出码 0/1/2、报告输出、
mock 模式不依赖 pydantic / backend（可在无依赖环境跑）。
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import run_eval

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent
QA_FILE = EVAL_DIR / "golden_qa" / "qa_intern.jsonl"
SCORING_PROMPT = REPO_ROOT / "prompts" / "scoring.md"


def _first_sample() -> dict:
    line = QA_FILE.read_text(encoding="utf-8").splitlines()[0]
    return json.loads(line)


def _write_jsonl(directory: Path, samples: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "qa_test.jsonl"
    path.write_text(
        "\n".join(json.dumps(sample, ensure_ascii=False) for sample in samples) + "\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# 样本加载与校验
# ---------------------------------------------------------------------------
def test_load_default_samples():
    samples = run_eval.load_qa_samples()
    assert len(samples) == 20  # 阶段 4 第一批：10 → 20
    assert all(sample["module"] == "evaluation" for sample in samples)
    assert all(sample["enabled"] is True for sample in samples)


def test_invalid_band_rejected():
    bad = copy.deepcopy(_first_sample())
    bad["anchors"]["poor"]["expected_band"] = [5, 7]
    with pytest.raises(run_eval.EvalDataError):
        run_eval.validate_qa_sample(bad)


def test_invalid_center_rejected():
    bad = copy.deepcopy(_first_sample())
    bad["anchors"]["good"]["expected_center"] = 7.0
    with pytest.raises(run_eval.EvalDataError):
        run_eval.validate_qa_sample(bad)


def test_disabled_switch(tmp_dir: Path):
    enabled = copy.deepcopy(_first_sample())
    enabled["id"] = "qa-test-enabled"
    disabled = copy.deepcopy(_first_sample())
    disabled["id"] = "qa-test-disabled"
    disabled["enabled"] = False
    _write_jsonl(tmp_dir, [enabled, disabled])

    only_enabled = run_eval.load_qa_samples(tmp_dir)
    assert [sample["id"] for sample in only_enabled] == ["qa-test-enabled"]
    with_disabled = run_eval.load_qa_samples(tmp_dir, include_disabled=True)
    assert len(with_disabled) == 2


def test_duplicate_id_rejected(tmp_dir: Path):
    sample = copy.deepcopy(_first_sample())
    _write_jsonl(tmp_dir, [sample, sample])
    with pytest.raises(run_eval.EvalDataError):
        run_eval.load_qa_samples(tmp_dir)


# ---------------------------------------------------------------------------
# mock 评分与指标
# ---------------------------------------------------------------------------
def test_mock_score_deterministic_and_in_range():
    sample = _first_sample()
    first = run_eval.mock_score(sample, "medium", role="new", scenario="pass")
    second = run_eval.mock_score(sample, "medium", role="new", scenario="pass")
    assert first == second
    assert set(first["dimensions"]) == set(run_eval.DIMENSIONS)
    assert 1.0 <= first["overall"] <= 10.0


def test_compare_versions_strict_drop_is_regression():
    old = run_eval.VersionMetrics(label="v1", n_items=30, band_hit_rate=0.9, mae=0.5, mean_score=6.0)
    new = run_eval.VersionMetrics(label="v2", n_items=30, band_hit_rate=0.7, mae=1.0, mean_score=6.0)
    decision = run_eval.compare_versions(old, new)
    assert decision.passed is False
    assert any("band_hit_rate" in reason for reason in decision.reasons)
    assert any("mae" in reason for reason in decision.reasons)


def test_compare_versions_tolerance_allows_noise():
    old = run_eval.VersionMetrics(label="v1", n_items=30, band_hit_rate=0.9, mae=0.5, mean_score=6.0)
    new = run_eval.VersionMetrics(label="v2", n_items=30, band_hit_rate=0.7, mae=1.0, mean_score=6.0)
    assert run_eval.compare_versions(old, new, tolerance=5.0).passed is True


def test_compare_versions_min_hit_rate_floor():
    old = run_eval.VersionMetrics(label="v1", n_items=30, band_hit_rate=0.6, mae=1.0, mean_score=6.0)
    new = run_eval.VersionMetrics(label="v2", n_items=30, band_hit_rate=0.7, mae=0.5, mean_score=6.0)
    decision = run_eval.compare_versions(old, new, min_hit_rate=0.99)
    assert decision.passed is False
    assert any("低于下限" in reason for reason in decision.reasons)


# ---------------------------------------------------------------------------
# CLI 退出码（0 通过 / 1 回归 / 2 数据错误）
# ---------------------------------------------------------------------------
def test_mock_pass_exit_zero(capsys: pytest.CaptureFixture[str]):
    assert run_eval.main(["--mock"]) == 0
    output = capsys.readouterr().out
    assert "PASS" in output
    assert "[PASS]" in output
    assert "✅" not in output  # 控制台标记统一为 ASCII，避免 GBK 控制台 UnicodeEncodeError
    assert "REGRESSION" not in output


def test_mock_regress_exit_one(capsys: pytest.CaptureFixture[str]):
    assert run_eval.main(["--mock-scenario", "regress"]) == 1
    output = capsys.readouterr().out
    assert "REGRESSION" in output
    assert "[FAIL]" in output
    assert "❌" not in output


def test_runs_under_gbk_console_without_unicode_error(tmp_dir: Path):
    """Windows GBK 控制台兼容：PYTHONIOENCODING=gbk 下默认 mock 运行不崩溃、退出码语义不变。"""
    script = EVAL_DIR / "run_eval.py"
    env = {**os.environ, "PYTHONIOENCODING": "gbk", "PYTHONUTF8": "0"}

    def _run(args: list[str]) -> tuple[int, bytes, bytes]:
        out_file = tmp_dir / "stdout.bin"
        err_file = tmp_dir / "stderr.bin"
        with out_file.open("wb") as stdout, err_file.open("wb") as stderr:
            completed = subprocess.run(
                [sys.executable, str(script), *args],
                cwd=str(REPO_ROOT),
                env=env,
                stdout=stdout,
                stderr=stderr,
                check=False,
            )
        return completed.returncode, out_file.read_bytes(), err_file.read_bytes()

    pass_code, pass_out, pass_err = _run(["--mock"])
    assert pass_code == 0
    assert b"UnicodeEncodeError" not in pass_err
    assert b"[PASS]" in pass_out

    fail_code, fail_out, fail_err = _run(["--mock-scenario", "regress"])
    assert fail_code == 1
    assert b"UnicodeEncodeError" not in fail_err
    assert b"[FAIL]" in fail_out


def test_tolerance_overrides_mock_regression():
    assert run_eval.main(["--mock-scenario", "regress", "--tolerance", "5.0"]) == 0


def test_min_hit_rate_floor_forces_regression():
    assert (
        run_eval.main(
            ["--mock-scenario", "regress", "--tolerance", "5.0", "--min-hit-rate", "0.99"]
        )
        == 1
    )


def test_json_report_written(tmp_dir: Path):
    report_path = tmp_dir / "eval_report.json"
    assert run_eval.main(["--mock", "--quiet", "--json-out", str(report_path)]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mode"] == "mock"
    assert report["decision"]["passed"] is True
    assert report["old"]["n_items"] == 60  # 20 条样本 × 3 档
    assert report["new"]["band_hit_rate"] >= report["old"]["band_hit_rate"]
    assert report["new"]["mae"] <= report["old"]["mae"]


def test_markdown_report_written(tmp_dir: Path):
    report_path = tmp_dir / "eval_report.md"
    assert run_eval.main(["--mock", "--quiet", "--md-out", str(report_path)]) == 0
    content = report_path.read_text(encoding="utf-8")
    assert "# Prompt 回归评测报告" in content
    assert "PASS" in content


def test_empty_data_dir_exit_two(tmp_dir: Path):
    assert run_eval.main(["--data-dir", str(tmp_dir), "--quiet"]) == run_eval.EXIT_DATA_ERROR


def test_invalid_sample_exit_two(tmp_dir: Path):
    bad = copy.deepcopy(_first_sample())
    bad["anchors"]["medium"]["expected_band"] = [1, 4]
    _write_jsonl(tmp_dir / "golden_qa", [bad])
    assert run_eval.main(["--data-dir", str(tmp_dir), "--quiet"]) == run_eval.EXIT_DATA_ERROR


def test_real_mode_missing_prompt_exit_two(tmp_dir: Path):
    missing = tmp_dir / "not-exists-v1.md"
    assert (
        run_eval.main(["--real", "--old-prompt", str(missing), "--quiet"])
        == run_eval.EXIT_DATA_ERROR
    )


def test_real_score_uses_injected_scorer():
    sample = _first_sample()

    def fake_score_answer(prompt_input, answer, *, router=None, prompt_path=None):
        payload = {
            "dimensions": [
                {"dimension": name, "score": 7, "evidence": ["证据1", "证据2"]}
                for name in run_eval.DIMENSIONS
            ],
            "strengths": ["亮点"],
            "weaknesses": ["短板"],
            "suggestions": ["建议"],
        }
        return type("Result", (), {"evaluation": payload})()

    scored = run_eval.real_score(
        sample,
        "poor",
        SCORING_PROMPT,
        router=object(),
        score_answer_fn=fake_score_answer,
    )
    assert scored["overall"] == 7.0
    assert set(scored["dimensions"]) == set(run_eval.DIMENSIONS)


# ---------------------------------------------------------------------------
# mock 模式不依赖 pydantic / backend（无 Key 可跑的核心承诺）
# ---------------------------------------------------------------------------
def test_mock_mode_does_not_import_pydantic_or_backend():
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(EVAL_DIR)!r})\n"
        "import run_eval\n"
        "rc = run_eval.main(['--mock', '--quiet'])\n"
        "assert rc == 0, rc\n"
        "assert 'pydantic' not in sys.modules, 'mock 模式不应 import pydantic'\n"
        "assert 'backend.services.evaluation' not in sys.modules, 'mock 模式不应 import backend'\n"
    )
    env = {**os.environ, "PYTHONUTF8": "1"}
    try:
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError as exc:  # pragma: no cover - 沙箱禁止子进程时跳过
        pytest.skip(f"当前沙箱不允许子进程：{exc}")
    assert completed.returncode == 0, "mock 模式不应依赖 pydantic / backend（详见子进程断言）"
