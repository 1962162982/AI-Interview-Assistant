"""Prompt 回归评测脚本（v2 · 2026-10-23）

铁律（见 `prompts/README.md`、`说明书.md` §8.3）：
    任何 Prompt 修改 → 先跑本脚本（v_old vs v_new）→ 分数不降才允许 commit；分数下降 → 回滚。

无 Key 可跑（默认 mock 模式，只依赖标准库）：
    python evaluation/run_eval.py                              # 模拟 v_new 更好 → 通过（退出码 0）
    python evaluation/run_eval.py --mock-scenario regress      # 模拟分数下降 → 拦截（退出码 1）
    python evaluation/run_eval.py --json-out eval_report.json  # 额外输出机器可读报告

真实模式（需 Key，当前由 Lead 统一执行；接口契约见 evaluation/README.md §4）：
    python evaluation/run_eval.py --real \
        --old-prompt evaluation/prompt_baselines/scoring_v1.md \
        --new-prompt prompts/scoring.md

退出码（冻结，供 git hook / CI 断言）：
    0 = 通过（分数未下降，允许 commit）
    1 = 回归（分数下降，禁止 commit / 需回滚 Prompt）
    2 = 数据或配置错误（样本非法、Prompt 缺失、真实模式缺依赖/Key 等）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# 路径与常量（与 evaluation/README.md §2/§3 保持一致）
# ---------------------------------------------------------------------------
SCRIPT_PATH = Path(__file__).resolve()
EVAL_DIR = SCRIPT_PATH.parent
REPO_ROOT = EVAL_DIR.parent
DEFAULT_DATA_DIR = EVAL_DIR
DEFAULT_OLD_PROMPT = EVAL_DIR / "prompt_baselines" / "scoring_v1.md"
DEFAULT_NEW_PROMPT = REPO_ROOT / "prompts" / "scoring.md"

SCHEMA_VERSION = 1
TIERS: tuple[str, ...] = ("poor", "medium", "good")
DIMENSIONS: tuple[str, ...] = ("相关性", "结构性", "具体性", "岗位匹配")
# 人工标注分带（冻结）：差 1–4 / 中 5–7 / 优 8–10；中心分用于 MAE
EXPECTED_BANDS: dict[str, tuple[int, int]] = {
    "poor": (1, 4),
    "medium": (5, 7),
    "good": (8, 10),
}
BAND_CENTER: dict[str, float] = {"poor": 2.5, "medium": 6.0, "good": 9.0}
# mock 桩评分噪声幅度（role -> 幅度）；pass 场景下 v_new 更贴近人工分带
MOCK_SPREAD: dict[str, dict[str, float]] = {
    "pass": {"old": 1.8, "new": 0.55},
    "regress": {"old": 0.6, "new": 2.6},
}

EXIT_PASS = 0
EXIT_REGRESSION = 1
EXIT_DATA_ERROR = 2

_EPS = 1e-9


class EvalDataError(RuntimeError):
    """样本 / 配置数据不合法（对应退出码 2）。"""


def _configure_stdio() -> None:
    """Windows GBK 控制台兼容：输出遇到非 GBK 字符时用 ``?`` 替代而不是崩溃。

    - 保留当前编码（GBK 控制台下中文仍正常显示），仅把错误处理改为 ``replace``；
    - 若 ``stdout`` / ``stderr`` 不支持 ``reconfigure``（被捕获 / 重定向包装），静默跳过；
    - 控制台标记已统一为 ASCII（``[PASS]`` / ``[FAIL]``），本函数是额外兜底。
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding=getattr(stream, "encoding", None) or "utf-8", errors="replace")
        except (ValueError, OSError, AttributeError):
            # io.UnsupportedOperation 继承自 OSError；再尝试只改 errors 的降级路径
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError, AttributeError):
                pass


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class ScoredItem:
    """一个样本档位（sample × tier）的评分结果。"""

    sample_id: str
    tier: str
    expected_band: tuple[int, int]
    expected_center: float
    dimension_scores: dict[str, float]
    overall: float


@dataclass
class VersionMetrics:
    """某一 Prompt 版本的评测指标（README §3.2）。"""

    label: str
    n_items: int
    band_hit_rate: float
    mae: float
    mean_score: float
    per_tier_mean: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "n_items": self.n_items,
            "band_hit_rate": round(self.band_hit_rate, 4),
            "mae": round(self.mae, 4),
            "mean_score": round(self.mean_score, 4),
            "per_tier_mean": {k: round(v, 4) for k, v in self.per_tier_mean.items()},
        }


@dataclass
class Decision:
    """v_old vs v_new 的回归判定结果（README §3.3）。"""

    passed: bool
    reasons: list[str] = field(default_factory=list)
    tolerance: float = 0.0
    min_hit_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reasons": list(self.reasons),
            "tolerance": self.tolerance,
            "min_hit_rate": self.min_hit_rate,
        }


# ---------------------------------------------------------------------------
# 样本加载与校验
# ---------------------------------------------------------------------------
def _qa_dir(data_dir: str | Path | None) -> Path:
    base = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    nested = base / "golden_qa"
    if nested.is_dir():
        return nested
    return base


def validate_qa_sample(sample: Mapping[str, Any], where: str = "") -> None:
    """校验单条 QA 黄金样本是否满足冻结格式；不合法抛 EvalDataError。"""

    def bad(msg: str) -> None:
        raise EvalDataError(f"{where or sample.get('id', '<unknown>')} 样本非法：{msg}")

    if not isinstance(sample, Mapping):
        bad("顶层必须是 JSON 对象")
    if sample.get("schema_version") != SCHEMA_VERSION:
        bad(f"schema_version 必须为 {SCHEMA_VERSION}，实际 {sample.get('schema_version')!r}")
    for key in ("id", "module", "scene"):
        value = sample.get(key)
        if not isinstance(value, str) or not value.strip():
            bad(f"缺少非空字符串字段 {key}")
    if sample["module"] != "evaluation":
        bad("module 必须为 'evaluation'（QA 评分样本）")
    if sample.get("scene") not in ("intern", "autumn", "grad"):
        bad("scene 必须为 intern / autumn / grad 之一")
    if not isinstance(sample.get("enabled"), bool):
        bad("enabled 必须为布尔值（开关项）")
    tags = sample.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        bad("tags 必须为字符串数组")

    prompt_input = sample.get("prompt_input")
    if not isinstance(prompt_input, Mapping):
        bad("缺少 prompt_input 对象")
    question = prompt_input.get("question")
    if not isinstance(question, str) or not question.strip():
        bad("prompt_input.question 必须为非空字符串")
    for optional in ("jd", "resume_excerpt"):
        if optional in prompt_input and prompt_input[optional] is not None:
            if not isinstance(prompt_input[optional], str):
                bad(f"prompt_input.{optional} 必须为字符串或 null")

    anchors = sample.get("anchors")
    if not isinstance(anchors, Mapping):
        bad("缺少 anchors 对象")
    if set(anchors.keys()) != set(TIERS):
        bad(f"anchors 必须且只能包含 {list(TIERS)}，实际 {sorted(anchors.keys())}")
    for tier in TIERS:
        anchor = anchors[tier]
        if not isinstance(anchor, Mapping):
            bad(f"anchors.{tier} 必须是对象")
        answer = anchor.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            bad(f"anchors.{tier}.answer 必须为非空字符串")
        band = anchor.get("expected_band")
        expected = list(EXPECTED_BANDS[tier])
        if not isinstance(band, list) or len(band) != 2 or not all(
            isinstance(x, int) and not isinstance(x, bool) for x in band
        ):
            bad(f"anchors.{tier}.expected_band 必须为 [int, int]")
        if band != expected:
            bad(f"anchors.{tier}.expected_band 必须为 {expected}（分带已冻结），实际 {band}")
        center = anchor.get("expected_center")
        if not isinstance(center, (int, float)) or isinstance(center, bool):
            bad(f"anchors.{tier}.expected_center 必须为数字")
        if abs(float(center) - BAND_CENTER[tier]) > _EPS:
            bad(f"anchors.{tier}.expected_center 必须为 {BAND_CENTER[tier]}（冻结），实际 {center}")


def load_qa_samples(
    data_dir: str | Path | None = None,
    include_disabled: bool = False,
) -> list[dict[str, Any]]:
    """加载 `golden_qa/*.jsonl` 中通过校验的样本（默认仅 enabled=True）。"""
    qa_dir = _qa_dir(data_dir)
    if not qa_dir.is_dir():
        raise EvalDataError(f"样本目录不存在：{qa_dir}")
    files = sorted(qa_dir.glob("*.jsonl"))
    if not files:
        raise EvalDataError(f"样本目录中没有 *.jsonl：{qa_dir}")

    enabled_samples: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:  # pragma: no cover - 文件系统异常兜底
            raise EvalDataError(f"读取样本失败 {path}: {exc}") from exc
        for lineno, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                sample = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvalDataError(f"{path.name}:{lineno} JSON 解析失败：{exc}") from exc
            validate_qa_sample(sample, where=f"{path.name}:{lineno}")
            if sample["id"] in seen_ids:
                raise EvalDataError(f"{path.name}:{lineno} id 重复：{sample['id']}")
            seen_ids.add(sample["id"])
            if sample["enabled"] or include_disabled:
                enabled_samples.append(dict(sample))

    if not enabled_samples:
        raise EvalDataError(f"没有可用样本（全部 disabled 或为空）：{qa_dir}")
    return enabled_samples


# ---------------------------------------------------------------------------
# 评分（mock 桩 / real 真实模型）
# ---------------------------------------------------------------------------
def _clip(value: float, low: float = 1.0, high: float = 10.0) -> float:
    return max(low, min(high, value))


def _stable_noise(*parts: str) -> float:
    """确定性伪随机噪声，返回 [-1, 1]；保证同一样本跨进程结果一致。"""
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    unit = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF  # [0, 1]
    return unit * 2.0 - 1.0


def mock_score(
    sample: Mapping[str, Any],
    tier: str,
    role: str,
    scenario: str = "pass",
) -> dict[str, Any]:
    """确定性 mock 评分桩：模拟“旧/新 Prompt 的分数分布”，只验证流水线与阈值逻辑。

    role: "old" / "new"（被模拟的 Prompt 版本）；scenario: "pass" / "regress"。
    返回 {"dimensions": {维度: 分}, "overall": 综合分}。
    """
    if scenario not in MOCK_SPREAD:
        raise EvalDataError(f"未知 mock 场景：{scenario}")
    if role not in ("old", "new"):
        raise EvalDataError(f"未知角色：{role}")
    center = BAND_CENTER[tier]
    spread = MOCK_SPREAD[scenario][role]
    base = _clip(center + _stable_noise(sample["id"], tier, role) * spread)
    dimensions = {
        dim: round(_clip(base + _stable_noise(sample["id"], tier, role, dim) * 0.6), 1)
        for dim in DIMENSIONS
    }
    overall = round(sum(dimensions.values()) / len(dimensions), 2)
    return {"dimensions": dimensions, "overall": overall}


def _score_from_evaluation(evaluation: Mapping[str, Any]) -> dict[str, Any]:
    """把 `InterviewEvaluation` dict 归一化为 {"dimensions", "overall"}。"""
    dims_raw = evaluation.get("dimensions")
    if not isinstance(dims_raw, list) or not dims_raw:
        raise EvalDataError("评分结果缺少 dimensions 数组")
    dimensions: dict[str, float] = {}
    for entry in dims_raw:
        if not isinstance(entry, Mapping):
            raise EvalDataError("dimensions 元素必须是对象")
        name = entry.get("dimension")
        score = entry.get("score")
        if name not in DIMENSIONS:
            raise EvalDataError(f"未知评分维度：{name!r}（应为 {DIMENSIONS}）")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise EvalDataError(f"维度 {name} 的 score 必须为数字")
        dimensions[name] = float(_clip(float(score)))
    missing = [d for d in DIMENSIONS if d not in dimensions]
    if missing:
        raise EvalDataError(f"评分结果缺少维度：{missing}")
    overall = round(sum(dimensions.values()) / len(dimensions), 2)
    return {"dimensions": dimensions, "overall": overall}


def _import_score_answer():
    """懒加载评分服务（mock 模式绝不 import，保证无依赖环境可跑）。"""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from backend.services.evaluation import score_answer  # 延迟导入（real 模式专用）

    return score_answer


def _get_router():
    """懒加载 T1 模型路由单例（契约见 evaluation/README.md §4）。"""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from backend.services.model_router import get_router  # 延迟导入（real 模式专用）

    return get_router()


def real_score(
    sample: Mapping[str, Any],
    tier: str,
    prompt_path: Path,
    router: Any,
    score_answer_fn: Any = None,
) -> dict[str, Any]:
    """真实模式：调用 backend.services.evaluation.score_answer 完成一次评分。"""
    fn = score_answer_fn or _import_score_answer()
    result = fn(
        sample["prompt_input"],
        sample["anchors"][tier]["answer"],
        router=router,
        prompt_path=prompt_path,
    )
    evaluation = getattr(result, "evaluation", result)
    if hasattr(evaluation, "model_dump"):
        evaluation = evaluation.model_dump()
    if not isinstance(evaluation, Mapping):
        raise EvalDataError("score_answer 返回值缺少 evaluation dict")
    return _score_from_evaluation(evaluation)


# ---------------------------------------------------------------------------
# 指标计算与回归判定
# ---------------------------------------------------------------------------
def score_version(
    samples: Sequence[Mapping[str, Any]],
    *,
    label: str,
    role: str,
    mode: str = "mock",
    scenario: str = "pass",
    prompt_path: Path | None = None,
    router: Any = None,
    score_answer_fn: Any = None,
) -> tuple[VersionMetrics, list[ScoredItem]]:
    """对一批样本的 poor/medium/good 三档逐条评分并汇总指标。"""
    items: list[ScoredItem] = []
    for sample in samples:
        for tier in TIERS:
            if mode == "mock":
                scored = mock_score(sample, tier, role=role, scenario=scenario)
            elif mode == "real":
                if prompt_path is None:
                    raise EvalDataError("real 模式缺少 prompt_path")
                scored = real_score(
                    sample, tier, prompt_path, router, score_answer_fn=score_answer_fn
                )
            else:  # pragma: no cover - 仅防御
                raise EvalDataError(f"未知评测模式：{mode}")
            anchor = sample["anchors"][tier]
            band = tuple(int(x) for x in anchor["expected_band"])
            items.append(
                ScoredItem(
                    sample_id=sample["id"],
                    tier=tier,
                    expected_band=band,  # type: ignore[arg-type]
                    expected_center=float(anchor["expected_center"]),
                    dimension_scores=dict(scored["dimensions"]),
                    overall=float(scored["overall"]),
                )
            )
    return compute_metrics(label, items), items


def compute_metrics(label: str, items: Sequence[ScoredItem]) -> VersionMetrics:
    """按 README §3.2 计算版本指标。"""
    if not items:
        raise EvalDataError("没有可统计的评分条目")
    n = len(items)
    hits = sum(1 for item in items if item.expected_band[0] <= item.overall <= item.expected_band[1])
    mae = sum(abs(item.overall - item.expected_center) for item in items) / n
    mean_score = sum(item.overall for item in items) / n
    per_tier_mean: dict[str, float] = {}
    for tier in TIERS:
        tier_scores = [item.overall for item in items if item.tier == tier]
        if tier_scores:
            per_tier_mean[tier] = sum(tier_scores) / len(tier_scores)
    return VersionMetrics(
        label=label,
        n_items=n,
        band_hit_rate=hits / n,
        mae=mae,
        mean_score=mean_score,
        per_tier_mean=per_tier_mean,
    )


def compare_versions(
    old: VersionMetrics,
    new: VersionMetrics,
    *,
    tolerance: float = 0.0,
    min_hit_rate: float = 0.0,
) -> Decision:
    """判定 v_new 是否“分数不降”。两条主指标 + 绝对下限，任一失败即回归。"""
    if tolerance < 0:
        raise EvalDataError("tolerance 不能为负数")
    if not 0.0 <= min_hit_rate <= 1.0:
        raise EvalDataError("min_hit_rate 必须在 [0, 1] 区间")
    reasons: list[str] = []
    if new.band_hit_rate + tolerance + _EPS < old.band_hit_rate:
        reasons.append(
            f"band_hit_rate 下降：{old.band_hit_rate:.3f} → {new.band_hit_rate:.3f}"
            f"（容差 {tolerance}）"
        )
    if new.mae > old.mae + tolerance + _EPS:
        reasons.append(
            f"mae 上升：{old.mae:.3f} → {new.mae:.3f}（容差 {tolerance}）"
        )
    if new.band_hit_rate + _EPS < min_hit_rate:
        reasons.append(
            f"band_hit_rate 低于下限：{new.band_hit_rate:.3f} < {min_hit_rate}"
        )
    return Decision(
        passed=not reasons,
        reasons=reasons,
        tolerance=tolerance,
        min_hit_rate=min_hit_rate,
    )


# ---------------------------------------------------------------------------
# 报告渲染
# ---------------------------------------------------------------------------
def build_report(
    *,
    mode: str,
    old: VersionMetrics,
    new: VersionMetrics,
    decision: Decision,
    n_samples: int,
    scenario: str | None = None,
    prompt_old: Path | None = None,
    prompt_new: Path | None = None,
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": mode,
        "mock_scenario": scenario if mode == "mock" else None,
        "n_samples": n_samples,
        "n_items": old.n_items,
        "old": old.to_dict(),
        "new": new.to_dict(),
        "decision": decision.to_dict(),
        "prompt": (
            {"old": str(prompt_old), "new": str(prompt_new)} if mode == "real" else None
        ),
    }


def _metric_row(name: str, old_value: float, new_value: float, fmt: str = "{:.3f}") -> str:
    return f"  {name:<16}{fmt.format(old_value):>14}{fmt.format(new_value):>14}"


def render_console(report: Mapping[str, Any]) -> str:
    old = report["old"]
    new = report["new"]
    decision = report["decision"]
    lines = [
        "== Prompt 回归评测（v_old vs v_new）==",
        f"模式：{report['mode']}"
        + (f"（mock 场景：{report['mock_scenario']}）" if report["mode"] == "mock" else ""),
        f"样本：{report['n_samples']} 条 × 3 档 = {report['n_items']} 条目",
    ]
    if report["mode"] == "mock":
        lines.append("注意：mock 为确定性桩评分，仅验证流水线与阈值逻辑，不代表真实模型效果。")
    lines += [
        "",
        f"  {'指标':<16}{str(old['label']):>14}{str(new['label']):>14}",
        f"  {'-' * 44}",
        _metric_row("band_hit_rate", old["band_hit_rate"], new["band_hit_rate"]),
        _metric_row("mae", old["mae"], new["mae"]),
        _metric_row("mean_score", old["mean_score"], new["mean_score"]),
    ]
    for tier in TIERS:
        old_tier = old["per_tier_mean"].get(tier)
        new_tier = new["per_tier_mean"].get(tier)
        if old_tier is not None and new_tier is not None:
            lines.append(_metric_row(f"  {tier}", old_tier, new_tier))
    lines.append("")
    if decision["passed"]:
        lines.append("判定：[PASS] 分数未下降，允许 commit。")
    else:
        lines.append("判定：[FAIL] REGRESSION：分数下降，禁止 commit / 需回滚 Prompt。")
        for reason in decision["reasons"]:
            lines.append(f"  - {reason}")
    return "\n".join(lines)


def render_markdown(report: Mapping[str, Any]) -> str:
    old = report["old"]
    new = report["new"]
    decision = report["decision"]
    status = "[PASS]" if decision["passed"] else "[FAIL]"
    lines = [
        "# Prompt 回归评测报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 模式：{report['mode']}"
        + (f"（mock 场景：{report['mock_scenario']}）" if report["mode"] == "mock" else ""),
        f"- 样本：{report['n_samples']} 条 × 3 档 = {report['n_items']} 条目",
        f"- 容差：{decision['tolerance']}；band_hit_rate 下限：{decision['min_hit_rate']}",
        "",
        "| 指标 | " + f"{old['label']} | {new['label']} |",
        "| --- | --- | --- |",
        f"| band_hit_rate（越高越好） | {old['band_hit_rate']:.3f} | {new['band_hit_rate']:.3f} |",
        f"| mae（越低越好） | {old['mae']:.3f} | {new['mae']:.3f} |",
        f"| mean_score | {old['mean_score']:.3f} | {new['mean_score']:.3f} |",
    ]
    for tier in TIERS:
        old_tier = old["per_tier_mean"].get(tier, float("nan"))
        new_tier = new["per_tier_mean"].get(tier, float("nan"))
        lines.append(f"| {tier} 均分 | {old_tier:.2f} | {new_tier:.2f} |")
    lines += ["", f"## 判定：{status}", ""]
    if decision["passed"]:
        lines.append("分数未下降，允许 commit。")
    else:
        lines.append("分数下降，禁止 commit / 回滚 Prompt。原因：")
        lines.extend(f"- {reason}" for reason in decision["reasons"])
    if report["mode"] == "mock":
        lines += ["", "> mock 为确定性桩评分，仅用于流水线与阈值逻辑演示，不代表真实模型效果。"]
    return "\n".join(lines)


def _write_output(path_str: str | None, content: str) -> None:
    if not path_str:
        return
    path = Path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prompt 回归评测（v_old vs v_new）：分数下降则非零退出，作为强制回归关卡。",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--mock",
        action="store_true",
        help="使用确定性桩评分（默认；无 Key、无第三方依赖即可运行）",
    )
    mode_group.add_argument(
        "--real", action="store_true", help="调用真实模型评分（需要 Key 与已实现的 model_router）"
    )
    parser.add_argument(
        "--mock-scenario",
        choices=sorted(MOCK_SPREAD),
        default="pass",
        help="mock 场景：pass=新版更好（退出码 0）；regress=分数下降（退出码 1）",
    )
    parser.add_argument(
        "--old-prompt", default=str(DEFAULT_OLD_PROMPT), help="v_old Prompt 文件（real 模式）"
    )
    parser.add_argument(
        "--new-prompt", default=str(DEFAULT_NEW_PROMPT), help="v_new Prompt 文件（real 模式）"
    )
    parser.add_argument("--old-label", default=None, help="v_old 版本名（默认 v1 / 文件名）")
    parser.add_argument("--new-label", default=None, help="v_new 版本名（默认 v2 / 文件名）")
    parser.add_argument("--tolerance", type=float, default=0.0, help="容差（默认 0.0，严格不降）")
    parser.add_argument(
        "--min-hit-rate", type=float, default=0.0, help="band_hit_rate 绝对下限（0–1）"
    )
    parser.add_argument(
        "--data-dir", default=str(DEFAULT_DATA_DIR), help="样本根目录（含 golden_qa/）"
    )
    parser.add_argument("--include-disabled", action="store_true", help="纳入 enabled=false 样本")
    parser.add_argument("--json-out", default=None, help="输出 JSON 报告路径")
    parser.add_argument("--md-out", default=None, help="输出 Markdown 报告路径")
    parser.add_argument("--quiet", action="store_true", help="不打印控制台报告")
    return parser


def _resolve_labels(args: argparse.Namespace, mode: str) -> tuple[str, str]:
    if mode == "mock":
        return args.old_label or "v1", args.new_label or "v2"
    old_prompt = Path(args.old_prompt)
    new_prompt = Path(args.new_prompt)
    return (
        args.old_label or old_prompt.stem,
        args.new_label or new_prompt.stem,
    )


def main(argv: Sequence[str] | None = None) -> int:
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    mode = "real" if args.real else "mock"
    old_label, new_label = _resolve_labels(args, mode)

    try:
        samples = load_qa_samples(args.data_dir, include_disabled=args.include_disabled)

        old_prompt = Path(args.old_prompt)
        new_prompt = Path(args.new_prompt)
        if mode == "real":
            for candidate in (old_prompt, new_prompt):
                if not candidate.is_file():
                    raise EvalDataError(f"Prompt 文件不存在：{candidate}")
            router = _get_router()
            score_answer_fn = _import_score_answer()
            old_metrics, _ = score_version(
                samples,
                label=old_label,
                role="old",
                mode="real",
                prompt_path=old_prompt,
                router=router,
                score_answer_fn=score_answer_fn,
            )
            new_metrics, _ = score_version(
                samples,
                label=new_label,
                role="new",
                mode="real",
                prompt_path=new_prompt,
                router=router,
                score_answer_fn=score_answer_fn,
            )
        else:
            old_metrics, _ = score_version(
                samples, label=old_label, role="old", mode="mock", scenario=args.mock_scenario
            )
            new_metrics, _ = score_version(
                samples, label=new_label, role="new", mode="mock", scenario=args.mock_scenario
            )

        decision = compare_versions(
            old_metrics, new_metrics, tolerance=args.tolerance, min_hit_rate=args.min_hit_rate
        )
        report = build_report(
            mode=mode,
            old=old_metrics,
            new=new_metrics,
            decision=decision,
            n_samples=len(samples),
            scenario=args.mock_scenario if mode == "mock" else None,
            prompt_old=old_prompt if mode == "real" else None,
            prompt_new=new_prompt if mode == "real" else None,
        )
    except EvalDataError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR
    except Exception as exc:  # noqa: BLE001 - 真实模式下依赖/Key/网络异常统一归为配置错误
        print(
            f"[ERROR] 评测执行失败（真实模式需要有效 Key 与 backend.services.model_router.get_router()）：{exc}",
            file=sys.stderr,
        )
        return EXIT_DATA_ERROR

    if not args.quiet:
        print(render_console(report))
    _write_output(args.json_out, json.dumps(report, ensure_ascii=False, indent=2))
    _write_output(args.md_out, render_markdown(report))
    return EXIT_PASS if decision.passed else EXIT_REGRESSION


if __name__ == "__main__":
    raise SystemExit(main())
