"""金标准样本格式与内容校验（schema_version=1，见 evaluation/README.md §2）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
QA_DIR = EVAL_DIR / "golden_qa"
RESUME_DIR = EVAL_DIR / "golden_resumes"

QA_TIERS = ("poor", "medium", "good")
QA_BANDS = {"poor": [1, 4], "medium": [5, 7], "good": [8, 10]}
QA_CENTERS = {"poor": 2.5, "medium": 6.0, "good": 9.0}
RESUME_CATEGORIES = {"量化表达", "动词弱化", "关键词对齐", "结构问题", "错别字格式"}
SEVERITIES = {"high", "medium", "low"}


def _load_jsonl(directory: Path) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    files = sorted(directory.glob("*.jsonl"))
    assert files, f"{directory} 下没有 JSONL 样本"
    for path in files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                samples.append(json.loads(line))
            except json.JSONDecodeError as exc:  # pragma: no cover - 失败时给出定位
                raise AssertionError(f"{path.name}:{lineno} JSON 解析失败：{exc}") from exc
    return samples


def test_qa_samples_count_scenes_and_unique_ids():
    samples = _load_jsonl(QA_DIR)
    assert len(samples) == 10, "起步 QA 样本应为 10 条（实习/秋招/保研三场景）"
    assert {sample["scene"] for sample in samples} == {"intern", "autumn", "grad"}
    ids = [sample["id"] for sample in samples]
    assert len(ids) == len(set(ids)), "样本 id 必须全局唯一"


def test_qa_sample_envelope_fields():
    for sample in _load_jsonl(QA_DIR):
        assert sample["schema_version"] == 1
        assert sample["module"] == "evaluation"
        assert sample["scene"] in {"intern", "autumn", "grad"}
        assert isinstance(sample["enabled"], bool)
        assert isinstance(sample["tags"], list)
        prompt_input = sample["prompt_input"]
        assert isinstance(prompt_input["question"], str) and prompt_input["question"].strip()
        for optional in ("jd", "resume_excerpt"):
            if prompt_input.get(optional) is not None:
                assert isinstance(prompt_input[optional], str)


def test_qa_three_anchor_tiers_and_frozen_bands():
    for sample in _load_jsonl(QA_DIR):
        anchors = sample["anchors"]
        assert set(anchors) == set(QA_TIERS)
        for tier in QA_TIERS:
            anchor = anchors[tier]
            assert isinstance(anchor["answer"], str) and len(anchor["answer"]) >= 10
            assert anchor["expected_band"] == QA_BANDS[tier], f"{sample['id']}:{tier} 分带未冻结"
            assert anchor["expected_center"] == QA_CENTERS[tier]


def test_qa_anchor_answers_are_distinguishable():
    """三档答案在长度上应有可辨识差异（差 < 中 < 优），避免样本本身无法区分档位。"""
    for sample in _load_jsonl(QA_DIR):
        lengths = [len(sample["anchors"][tier]["answer"]) for tier in QA_TIERS]
        assert lengths[0] < lengths[1] < lengths[2], f"{sample['id']} 三档答案长度无区分度：{lengths}"


def test_resume_samples_count_and_annotations():
    samples = _load_jsonl(RESUME_DIR)
    assert len(samples) == 3, "起步简历样本应为 3 份（实习/秋招/保研各 1）"
    ids = [sample["id"] for sample in samples]
    assert len(ids) == len(set(ids))
    for sample in samples:
        assert sample["schema_version"] == 1
        assert sample["module"] == "resume_diagnosis"
        assert sample["scene"] in {"intern", "autumn", "grad"}
        assert isinstance(sample["enabled"], bool)
        prompt_input = sample["prompt_input"]
        assert isinstance(prompt_input["resume_text"], str) and len(prompt_input["resume_text"]) > 100
        assert prompt_input.get("target_role")
        issues = sample["annotations"]["expected_issues"]
        assert len(issues) >= 3, f"{sample['id']} 至少标注 3 条问题"
        for issue in issues:
            assert issue["category"] in RESUME_CATEGORIES
            assert issue["severity"] in SEVERITIES
            assert issue["quote"] in prompt_input["resume_text"], (
                f"{sample['id']} 标注 quote 未在简历原文中出现：{issue['quote']!r}"
            )
