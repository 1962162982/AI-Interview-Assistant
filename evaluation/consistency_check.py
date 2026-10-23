"""评分一致性抽样脚本（阶段 4 收尾 · task-11）

从 `golden_qa/` 抽样（默认 10 条、`medium` 档），调用 `evaluation` 路由对锚定答案评分，
把模型 4 维均分（overall）与人工标注分带中心 `expected_center` 对比，计算：
- 每条样本的绝对偏差 / 相对偏差（%）；
- 平均相对偏差与最大相对偏差；
- 是否满足「人工与 AI 评分偏差 ≤ 15%」的质量指标。

结果写入 `evaluation/reports/consistency.md`（可用 `--out` 覆盖）。

用法（Windows 沙箱建议先设 TMP/TEMP=AI-Interview-Assistant\\.tmp）：:

    # 默认 mock：无 Key / 无网络，用于 CI 与流程演示
    python evaluation/consistency_check.py

    # 真实调用（当前本机临时 DeepSeek 模式：evaluation=deepseek-v4-pro，slow；可用 --limit 子集）
    python evaluation/consistency_check.py --live --limit 10 --workers 3

退出码：
    0 = 完成且平均相对偏差 ≤ 阈值（默认 15%）
    1 = 完成但平均相对偏差 > 阈值（一致性不达标）
    2 = 数据 / 配置 / 模型调用错误（无法产出结论）

注意：真实模式会产生模型调用费用；本脚本单次运行调用次数 = `--limit`（默认 10）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

SCRIPT_PATH = Path(__file__).resolve()
EVAL_DIR = SCRIPT_PATH.parent
REPO_ROOT = EVAL_DIR.parent
DEFAULT_OUT = EVAL_DIR / "reports" / "consistency.md"          # live 真实结果
MOCK_OUT = EVAL_DIR / "reports" / "consistency_mock.md"        # mock 演示，不覆盖真实结果
DEFAULT_SCORING_PROMPT = REPO_ROOT / "prompts" / "scoring.md"

TIERS = ("poor", "medium", "good")
BAND_CENTER = {"poor": 2.5, "medium": 6.0, "good": 9.0}
DEFAULT_LIMIT = 10
DEFAULT_THRESHOLD = 15.0
DEFAULT_TIER = "medium"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_DATA_ERROR = 2


class ConsistencyError(RuntimeError):
    """数据 / 配置 / 调用错误（退出码 2）。"""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class ConsistencyRow:
    """一条样本的一致性对比结果。"""

    sample_id: str
    scene: str
    model_score: float
    expected_center: float
    abs_deviation: float
    rel_deviation_pct: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "scene": self.scene,
            "model_score": round(self.model_score, 2),
            "expected_center": round(self.expected_center, 2),
            "abs_deviation": round(self.abs_deviation, 2),
            "rel_deviation_pct": round(self.rel_deviation_pct, 2),
        }


@dataclass
class ConsistencyResult:
    """一次一致性抽样的完整结果。"""

    rows: list[ConsistencyRow]
    mean_rel_deviation_pct: float
    max_rel_deviation_pct: float
    threshold: float
    tier: str
    mode: str
    model_info: dict[str, Any] = field(default_factory=dict)
    prompt_version: str = ""
    generated_at: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.mean_rel_deviation_pct <= self.threshold + 1e-9


# ---------------------------------------------------------------------------
# 样本选择
# ---------------------------------------------------------------------------
def _import_run_eval():
    """复用 run_eval 的样本加载/校验逻辑（同目录脚本，兼容从仓库根运行）。"""
    if str(EVAL_DIR) not in sys.path:
        sys.path.insert(0, str(EVAL_DIR))
    import run_eval  # noqa: F401 - 延迟导入（mock 模式不依赖后端）

    return run_eval


def select_samples(
    samples: Sequence[Mapping[str, Any]],
    *,
    limit: int = DEFAULT_LIMIT,
    seed: int = 0,
    tier: str = DEFAULT_TIER,
) -> list[dict[str, Any]]:
    """确定性抽样：按场景轮转（intern/autumn/grad 均衡），同 seed 结果可复现。"""
    if tier not in TIERS:
        raise ConsistencyError(f"未知档位：{tier}（应为 {TIERS}）")
    if limit <= 0:
        raise ConsistencyError("limit 必须为正整数")
    ordered = sorted((dict(sample) for sample in samples), key=lambda item: item["id"])
    if limit >= len(ordered):
        return ordered
    by_scene: dict[str, list[dict[str, Any]]] = {}
    for sample in ordered:
        by_scene.setdefault(str(sample.get("scene", "")), []).append(sample)
    rng = random.Random(seed)
    for pool in by_scene.values():
        rng.shuffle(pool)
    scenes = sorted(by_scene)
    picked: list[dict[str, Any]] = []
    while len(picked) < limit:
        progressed = False
        for scene in scenes:
            pool = by_scene[scene]
            if not pool:
                continue
            picked.append(pool.pop(0))
            progressed = True
            if len(picked) >= limit:
                break
        if not progressed:
            break
    return sorted(picked, key=lambda item: item["id"])


# ---------------------------------------------------------------------------
# 评分（mock / live）
# ---------------------------------------------------------------------------
def _clip(value: float, low: float = 1.0, high: float = 10.0) -> float:
    return max(low, min(high, value))


def _stable_noise(*parts: str) -> float:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF * 2.0 - 1.0


def mock_overall(sample: Mapping[str, Any], tier: str) -> float:
    """mock 评分：围绕人工分带中心 ±0.45，演示一致性抽样的完整流水线。"""
    center = BAND_CENTER[tier]
    return _clip(center + _stable_noise(sample["id"], tier, "consistency") * 0.45)


def read_prompt_version(path: Path | None = None) -> str:
    """从 Prompt 文件头读取版本号（stdlib，仅用于报告展示）。"""
    prompt_path = path or DEFAULT_SCORING_PROMPT
    try:
        text = prompt_path.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - 文件缺失时降级
        return ""
    marker = "prompt_version:"
    index = text.find(marker)
    if index == -1:
        return ""
    return text[index + len(marker) :].split("|")[0].strip()


def build_live_scorer(
    router: Any = None,
    *,
    limit_hint: str = "",
) -> Callable[[Mapping[str, Any], str], float]:
    """构造真实评分函数：调用模块 D 的 score_answer（evaluation 路由，温度由配置固定 0.1）。"""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from backend.services.evaluation import score_answer  # 延迟导入（live 专用）
    from backend.services.model_router import get_router  # 延迟导入（live 专用）

    active_router = router if router is not None else get_router()

    def score(sample: Mapping[str, Any], tier: str) -> float:
        result = score_answer(
            sample["prompt_input"],
            sample["anchors"][tier]["answer"],
            router=active_router,
        )
        return float(result.overall)

    return score


def build_model_info(router: Any = None) -> dict[str, Any]:
    """读取当前 evaluation 路由配置，写入报告（不打印 Key）。"""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from backend.services.model_router import get_router, resolve_config_path  # 延迟导入

    active_router = router if router is not None else get_router()
    config = active_router.get_route_config("evaluation")
    config_path = getattr(active_router, "_config_path", None) or resolve_config_path()
    try:
        config_display = str(Path(config_path).resolve().relative_to(REPO_ROOT))
    except ValueError:  # pragma: no cover - 非仓库内路径
        config_display = str(config_path)
    return {
        "route": "evaluation",
        "model": config.model,
        "temperature": config.temperature,
        "config_path": config_display,
        "base_url": "环境变量（不打印 Key）",
    }


# ---------------------------------------------------------------------------
# 指标计算与报告
# ---------------------------------------------------------------------------
def compute_result(
    rows: Sequence[ConsistencyRow],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    tier: str = DEFAULT_TIER,
    mode: str = "mock",
    model_info: Mapping[str, Any] | None = None,
    prompt_version: str = "",
    notes: Sequence[str] = (),
) -> ConsistencyResult:
    if not rows:
        raise ConsistencyError("没有可计算的一致性样本")
    rel = [row.rel_deviation_pct for row in rows]
    mean_rel = sum(rel) / len(rel)
    return ConsistencyResult(
        rows=list(rows),
        mean_rel_deviation_pct=mean_rel,
        max_rel_deviation_pct=max(rel),
        threshold=threshold,
        tier=tier,
        mode=mode,
        model_info=dict(model_info or {}),
        prompt_version=prompt_version,
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        notes=list(notes),
    )


def evaluate_rows(
    samples: Sequence[Mapping[str, Any]],
    score_fn: Callable[[Mapping[str, Any], str], float],
    *,
    tier: str = DEFAULT_TIER,
) -> list[ConsistencyRow]:
    rows: list[ConsistencyRow] = []
    for sample in samples:
        anchor = sample["anchors"][tier]
        expected = float(anchor["expected_center"])
        predicted = float(score_fn(sample, tier))
        predicted = _clip(predicted)
        abs_dev = abs(predicted - expected)
        rel_dev = abs_dev / expected * 100.0 if expected else 0.0
        rows.append(
            ConsistencyRow(
                sample_id=sample["id"],
                scene=str(sample.get("scene", "")),
                model_score=predicted,
                expected_center=expected,
                abs_deviation=abs_dev,
                rel_deviation_pct=rel_dev,
            )
        )
    return rows


def render_markdown(result: ConsistencyResult) -> str:
    status = "PASS" if result.passed else "FAIL"
    lines = [
        "# 评分一致性抽样报告（阶段 4 收尾）",
        "",
        f"- 生成时间：{result.generated_at}",
        f"- 模式：{result.mode}（{'确定性 mock' if result.mode == 'mock' else '真实模型调用'}）",
        f"- 抽样：{len(result.rows)} 条 golden_qa，档位 `{result.tier}`",
        f"- 评分 Prompt 版本：{result.prompt_version or '未知'}",
    ]
    model = result.model_info
    if model:
        lines.append(
            f"- 评分路由：`{model.get('route', 'evaluation')}` → `{model.get('model', '?')}`"
            f"（temperature {model.get('temperature', '?')}，配置 {model.get('config_path', '?')}）"
        )
    lines += [
        "",
        "## 逐条偏差",
        "",
        "| 样本 ID | 场景 | 模型综合分 | 人工分带中心 | 绝对偏差 | 相对偏差 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in result.rows:
        lines.append(
            f"| {row.sample_id} | {row.scene} | {row.model_score:.2f} | "
            f"{row.expected_center:.2f} | {row.abs_deviation:.2f} | {row.rel_deviation_pct:.1f}% |"
        )
    lines += [
        "",
        "## 汇总与结论",
        "",
        f"- 平均相对偏差：**{result.mean_rel_deviation_pct:.2f}%**（阈值 ≤ {result.threshold:.0f}%）",
        f"- 最大相对偏差：{result.max_rel_deviation_pct:.2f}%",
        f"- 人工与 AI 评分偏差 ≤ {result.threshold:.0f}%：**{'通过' if result.passed else '未通过'}**（{status}）",
        "",
    ]
    if result.notes:
        lines.append("## 说明与局限")
        lines.append("")
        lines.extend(f"- {note}" for note in result.notes)
        lines.append("")
    return "\n".join(lines)


def run_consistency(
    *,
    live: bool = False,
    limit: int = DEFAULT_LIMIT,
    tier: str = DEFAULT_TIER,
    seed: int = 0,
    workers: int = 3,
    threshold: float = DEFAULT_THRESHOLD,
    router: Any = None,
) -> ConsistencyResult:
    """执行一次一致性抽样（live=False 时使用确定性 mock 评分）。"""
    run_eval = _import_run_eval()
    all_samples = run_eval.load_qa_samples(EVAL_DIR)
    samples = select_samples(all_samples, limit=limit, seed=seed, tier=tier)
    prompt_version = read_prompt_version()
    if live:
        score_fn = build_live_scorer(router)
        model_info = build_model_info(router)
        notes = [
            "真实调用：当前为临时全 DeepSeek 模式（evaluation 路由为质量档模型，"
            "非同供应商/跨供应商混用），结论仅代表当前临时配置。",
            f"调用次数 = {len(samples)}（每条样本 1 次评分）；temperature 由 models 配置固定 0.1。",
        ]
        rows: list[ConsistencyRow] = []
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [
                pool.submit(evaluate_rows, [sample], score_fn, tier=tier)
                for sample in samples
            ]
            for future in futures:
                rows.extend(future.result())
        rows.sort(key=lambda row: row.sample_id)
        mode = "live"
    else:
        rows = evaluate_rows(samples, lambda sample, level: mock_overall(sample, level), tier=tier)
        model_info = {"route": "evaluation", "model": "mock", "temperature": 0.1, "config_path": "mock"}
        notes = ["mock 模式：评分为确定性桩，仅验证抽样/指标/报告流水线，不代表真实模型一致性。"]
        mode = "mock"
    return compute_result(
        rows,
        threshold=threshold,
        tier=tier,
        mode=mode,
        model_info=model_info,
        prompt_version=prompt_version,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="评分一致性抽样：evaluation 路由评分 vs 人工分带中心，输出 ≤15% 结论。",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--mock", action="store_true", help="确定性 mock 评分（默认，无 Key）")
    mode_group.add_argument("--live", action="store_true", help="真实调用 evaluation 路由评分")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="抽样条数（默认 10）")
    parser.add_argument(
        "--tier", choices=TIERS, default=DEFAULT_TIER, help="参与对比的锚定档位（默认 medium）"
    )
    parser.add_argument("--seed", type=int, default=0, help="抽样随机种子（默认 0）")
    parser.add_argument("--workers", type=int, default=3, help="live 并发数（默认 3）")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="相对偏差阈值 %%（默认 15）")
    parser.add_argument(
        "--out",
        default=None,
        help="Markdown 报告输出路径（默认 live→reports/consistency.md；mock→reports/consistency_mock.md，"
        "避免 mock 覆盖真实报告）",
    )
    parser.add_argument("--quiet", action="store_true", help="不打印报告正文")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_consistency(
            live=args.live,
            limit=args.limit,
            tier=args.tier,
            seed=args.seed,
            workers=args.workers,
            threshold=args.threshold,
        )
    except (ConsistencyError, FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR
    except Exception as exc:  # noqa: BLE001 - live 模式依赖/Key/网络错误统一为配置错误
        print(f"[ERROR] 一致性抽样执行失败（live 模式需要有效 Key）：{exc}", file=sys.stderr)
        return EXIT_DATA_ERROR

    markdown = render_markdown(result)
    out_path = Path(args.out) if args.out else (DEFAULT_OUT if args.live else MOCK_OUT)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")
    if not args.quiet:
        print(markdown)
    print(
        f"[consistency] mode={result.mode} n={len(result.rows)} "
        f"mean_rel={result.mean_rel_deviation_pct:.2f}% threshold={result.threshold}% "
        f"→ {'PASS' if result.passed else 'FAIL'}；报告：{out_path}",
        file=sys.stderr,
    )
    return EXIT_PASS if result.passed else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
