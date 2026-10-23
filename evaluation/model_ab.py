"""模型分配 A/B 脚本（阶段 4 收尾 · task-11）

目标：在**同一批 golden_qa 样本（默认 2 条 × 三档）**上比较两种模型配置的评分质量：

- A 现临时混合配置：`get_router()` 读取当前生效配置
  （本机临时模式为 `backend/config/models.deepseek.yaml`：evaluation = `deepseek-v4-pro`）；
- B 全 flash 基线：`evaluation/configs/models_all_flash.yaml`（evaluation = `deepseek-flash`）。

指标沿用回归口径：`band_hit_rate`（越高越好）/ `MAE`（越低越好）/ `mean_score`，
结果写入 `evaluation/reports/model_ab.md`。

用法（Windows 沙箱建议先设 TMP/TEMP=AI-Interview-Assistant\\.tmp）：:

    # 默认 mock：无 Key，验证 A/B 流水线与报告
    python evaluation/model_ab.py

    # 真实调用（受预算约束：本脚本最多 --max-calls 次，默认 18）
    python evaluation/model_ab.py --live --limit 2 --workers 3

退出码：
    0 = A/B 完成（报告已落盘）
    2 = 数据 / 配置 / 模型调用错误

**局限**：两套配置都是同一供应商（DeepSeek 临时模式），不是决策④“跨供应商”混用；
真实调用次数有限（≤18 次），结论仅供决策参考，GLM Key 到位后须重跑。
"""
from __future__ import annotations

import argparse
import hashlib
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

SCRIPT_PATH = Path(__file__).resolve()
EVAL_DIR = SCRIPT_PATH.parent
REPO_ROOT = EVAL_DIR.parent
DEFAULT_OUT = EVAL_DIR / "reports" / "model_ab.md"              # live 真实结果
MOCK_OUT = EVAL_DIR / "reports" / "model_ab_mock.md"            # mock 演示，不覆盖真实结果
FLASH_CONFIG = EVAL_DIR / "configs" / "models_all_flash.yaml"

TIERS = ("poor", "medium", "good")
BAND_CENTER = {"poor": 2.5, "medium": 6.0, "good": 9.0}
DEFAULT_LIMIT = 2          # 2 条样本 × 3 档 × 2 配置 = 12 次真实调用
MAX_LIVE_CALLS = 18        # 真实调用预算（3 条 × 3 档 × 2 配置）
DEFAULT_WORKERS = 3
DEFAULT_SEED = 0
EXIT_OK = 0
EXIT_DATA_ERROR = 2


class AbError(RuntimeError):
    """数据 / 配置 / 调用错误（退出码 2）。"""


@dataclass
class AbConfig:
    """一个参与 A/B 的模型配置。"""

    key: str
    label: str
    config_path: str
    model: str = ""
    temperature: float = 0.1
    router: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "config_path": self.config_path,
            "model": self.model,
            "temperature": self.temperature,
        }


@dataclass
class AbResult:
    """A/B 结果。"""

    configs: list[AbConfig]
    metrics: dict[str, Any]
    sample_ids: list[str]
    n_items: int
    mode: str
    live_calls: int
    generated_at: str = ""
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _import_run_eval():
    if str(EVAL_DIR) not in sys.path:
        sys.path.insert(0, str(EVAL_DIR))
    import run_eval

    return run_eval


def _clip(value: float, low: float = 1.0, high: float = 10.0) -> float:
    return max(low, min(high, value))


def _stable_noise(*parts: str) -> float:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF * 2.0 - 1.0


def select_samples(
    samples: Sequence[Mapping[str, Any]],
    *,
    limit: int = DEFAULT_LIMIT,
    seed: int = DEFAULT_SEED,
    sample_ids: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """抽样：优先按显式 sample_ids；否则按场景轮转（intern/autumn/grad 均衡）。"""
    if sample_ids:
        by_id = {sample["id"]: dict(sample) for sample in samples}
        missing = [sample_id for sample_id in sample_ids if sample_id not in by_id]
        if missing:
            raise AbError(f"指定的样本不存在：{missing}")
        return [by_id[sample_id] for sample_id in sample_ids]
    if limit <= 0:
        raise AbError("limit 必须为正整数")
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
# 评分与指标
# ---------------------------------------------------------------------------
def mock_overall(sample: Mapping[str, Any], tier: str, config_key: str) -> float:
    """mock 评分：A（质量档）更贴近人工分带中心，B（flash 基线）偏差更大。"""
    center = BAND_CENTER[tier]
    spread = 0.35 if config_key == "mixed" else 1.45
    return round(_clip(center + _stable_noise(sample["id"], tier, config_key) * spread), 2)


def items_from_scores(
    samples: Sequence[Mapping[str, Any]],
    score_fn,
    *,
    label_prefix: str = "",
) -> list[Any]:
    """构造 run_eval.ScoredItem 列表（复用回归指标口径）。"""
    run_eval = _import_run_eval()
    items: list[Any] = []
    for sample in samples:
        for tier in TIERS:
            anchor = sample["anchors"][tier]
            overall = _clip(float(score_fn(sample, tier)))
            items.append(
                run_eval.ScoredItem(
                    sample_id=f"{label_prefix}{sample['id']}",
                    tier=tier,
                    expected_band=tuple(int(x) for x in anchor["expected_band"]),
                    expected_center=float(anchor["expected_center"]),
                    dimension_scores={dimension: overall for dimension in run_eval.DIMENSIONS},
                    overall=overall,
                )
            )
    return items


def build_live_score_fn(config: AbConfig):
    """构造真实评分函数：调用模块 D 的 score_answer（evaluation 路由）。"""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from backend.services.evaluation import score_answer

    if config.router is None:
        raise AbError(f"配置 {config.key} 缺少 router")

    def score(sample: Mapping[str, Any], tier: str) -> float:
        result = score_answer(
            sample["prompt_input"],
            sample["anchors"][tier]["answer"],
            router=config.router,
        )
        return float(result.overall)

    return score


def build_live_configs() -> list[AbConfig]:
    """构造 A（当前生效配置）与 B（全 flash 基线）两个 router。"""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from backend.services.model_router import ModelRouter, get_router, resolve_config_path

    mixed_router = get_router()
    mixed_cfg = mixed_router.get_route_config("evaluation")
    mixed_path = getattr(mixed_router, "_config_path", None) or resolve_config_path()
    try:
        mixed_display = str(Path(mixed_path).resolve().relative_to(REPO_ROOT))
    except ValueError:  # pragma: no cover
        mixed_display = str(mixed_path)
    flash_router = ModelRouter(config_path=FLASH_CONFIG)
    flash_cfg = flash_router.get_route_config("evaluation")
    return [
        AbConfig(
            key="mixed",
            label="A 现临时混合配置",
            config_path=mixed_display,
            model=mixed_cfg.model,
            temperature=mixed_cfg.temperature,
            router=mixed_router,
        ),
        AbConfig(
            key="flash",
            label="B 全 flash 基线",
            config_path=str(FLASH_CONFIG.relative_to(REPO_ROOT)),
            model=flash_cfg.model,
            temperature=flash_cfg.temperature,
            router=flash_router,
        ),
    ]


def score_config_live(
    config: AbConfig,
    samples: Sequence[Mapping[str, Any]],
    *,
    workers: int = DEFAULT_WORKERS,
) -> tuple[list[Any], int]:
    """并发执行单个配置的全部评分任务；返回 (items, 调用次数)。"""
    score_fn = build_live_score_fn(config)
    tasks = [(sample, tier) for sample in samples for tier in TIERS]

    def run(task: tuple[Mapping[str, Any], str]) -> float:
        sample, tier = task
        return float(score_fn(sample, tier))

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        outcomes = list(pool.map(run, tasks))
    run_eval = _import_run_eval()
    items: list[Any] = []
    cursor = 0
    for sample in samples:
        for tier in TIERS:
            anchor = sample["anchors"][tier]
            overall = _clip(outcomes[cursor])
            cursor += 1
            items.append(
                run_eval.ScoredItem(
                    sample_id=sample["id"],
                    tier=tier,
                    expected_band=tuple(int(x) for x in anchor["expected_band"]),
                    expected_center=float(anchor["expected_center"]),
                    dimension_scores={dimension: overall for dimension in run_eval.DIMENSIONS},
                    overall=overall,
                )
            )
    return items, len(tasks)


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def build_result(
    configs: Sequence[AbConfig],
    metrics: Mapping[str, Any],
    samples: Sequence[Mapping[str, Any]],
    *,
    mode: str,
    live_calls: int,
    notes: Sequence[str] = (),
) -> AbResult:
    return AbResult(
        configs=list(configs),
        metrics=dict(metrics),
        sample_ids=[sample["id"] for sample in samples],
        n_items=len(samples) * len(TIERS),
        mode=mode,
        live_calls=live_calls,
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        notes=list(notes),
    )


def compute_ab_metrics(items_by_config: Mapping[str, Sequence[Any]]) -> dict[str, Any]:
    run_eval = _import_run_eval()
    metrics: dict[str, Any] = {}
    for key, items in items_by_config.items():
        metrics[key] = run_eval.compute_metrics(key, items).to_dict()
    return metrics


def render_markdown(result: AbResult) -> str:
    lines = [
        "# 模型分配 A/B 报告（阶段 4 收尾 · 临时同供应商）",
        "",
        f"- 生成时间：{result.generated_at}",
        f"- 模式：{result.mode}（{'确定性 mock' if result.mode == 'mock' else '真实模型调用'}）",
        f"- 样本：{len(result.sample_ids)} 条 × 三档 = {result.n_items} 条目（{', '.join(result.sample_ids)}）",
        f"- 真实调用次数：{result.live_calls}",
        "",
        "## 配置",
        "",
        "| 配置 | 说明 | evaluation 模型 | temperature | 配置文件 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for config in result.configs:
        lines.append(
            f"| {config.key} | {config.label} | `{config.model or '?'}` | "
            f"{config.temperature} | `{config.config_path}` |"
        )
    lines += [
        "",
        "## 指标对比（evaluation 路由评分 vs 人工分带）",
        "",
        "| 指标 | " + " | ".join(config.label for config in result.configs) + " |",
        "| --- | " + " | ".join("---" for _ in result.configs) + " |",
    ]
    metric_names = [
        ("band_hit_rate", "band_hit_rate（越高越好）"),
        ("mae", "MAE（越低越好）"),
        ("mean_score", "mean_score（观察项）"),
    ]
    for key, title in metric_names:
        values = [
            f"{result.metrics[config.key][key]:.3f}" for config in result.configs
        ]
        lines.append(f"| {title} | " + " | ".join(values) + " |")
    lines += ["", "## 结论", ""]
    keys = [config.key for config in result.configs]
    if len(keys) == 2:
        mixed_key, flash_key = keys
        mixed = result.metrics[mixed_key]
        flash = result.metrics[flash_key]
        hit_delta = mixed["band_hit_rate"] - flash["band_hit_rate"]
        mae_delta = flash["mae"] - mixed["mae"]  # 正数表示 A 更好
        lines.append(
            f"- band_hit_rate 差值（A - B）：{hit_delta:+.3f}；MAE 改善（B - A）：{mae_delta:+.3f}。"
        )
        if hit_delta > 0 and mae_delta > 0:
            lines.append("- A（质量档）在本次子集上两项主指标均优于 B（flash 基线）。")
        elif hit_delta < 0 and mae_delta < 0:
            lines.append("- B（flash 基线）在本次子集上两项主指标均优于 A（质量档）。")
        elif abs(hit_delta) < 1e-9 and abs(mae_delta) > 1e-9:
            better = "A（质量档）" if mae_delta > 0 else "B（flash 基线）"
            lines.append(
                f"- band_hit_rate 相同；MAE 更低的是 {better}——说明档位差异体现在分数偏离度上，"
                "但样本量很小，尚不足以支撑切换决策。"
            )
        else:
            lines.append("- 两种配置指标互有胜负，差异可能落在小样本噪声内。")
        lines.append(
            "- 本报告不做自动模型切换决策：按决策④，换模型属于效果变更，"
            "须在 GLM Key 到位后跑全量评测回归再定。"
        )
    lines.append("")
    if result.notes:
        lines.append("## 说明与局限")
        lines.append("")
        lines.extend(f"- {note}" for note in result.notes)
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_ab(
    *,
    live: bool = False,
    limit: int = DEFAULT_LIMIT,
    seed: int = DEFAULT_SEED,
    workers: int = DEFAULT_WORKERS,
    router_mixed: Any = None,
    router_flash: Any = None,
    sample_ids: Sequence[str] = (),
) -> AbResult:
    run_eval = _import_run_eval()
    all_samples = run_eval.load_qa_samples(EVAL_DIR)
    samples = select_samples(all_samples, limit=limit, seed=seed, sample_ids=sample_ids)

    base_notes = [
        "临时同供应商局限：A/B 两套配置均走 DeepSeek 端点（临时全 DeepSeek 联调模式），"
        "不是决策④跨供应商混用；结论仅用于当前临时配置的模型档位取舍参考。",
        "A/B 仅使用 evaluation 路由；models_all_flash.yaml 中 cross_review 设为 deepseek-v4-pro "
        "仅用于通过决策④「评分与复核必须异模型」强校验，A/B 未调用该路由。",
        "样本量很小（2–3 条 × 三档），单次运行存在方差；不做自动切换，需人工结合全量回归判断。",
    ]
    if live:
        configs = build_live_configs()
        if router_mixed is not None:
            configs[0].router = router_mixed
        if router_flash is not None:
            configs[1].router = router_flash
        planned_calls = len(samples) * len(TIERS) * len(configs)
        if planned_calls > MAX_LIVE_CALLS:
            raise AbError(
                f"计划真实调用 {planned_calls} 次，超过预算 {MAX_LIVE_CALLS} 次；请减小 --limit"
            )
        items_by_config: dict[str, list[Any]] = {}
        live_calls = 0
        for config in configs:
            items, calls = score_config_live(config, samples, workers=workers)
            items_by_config[config.key] = items
            live_calls += calls
        mode = "live"
    else:
        configs = [
            AbConfig("mixed", "A 现临时混合配置（mock）", "mock", "deepseek-v4-pro", 0.1),
            AbConfig("flash", "B 全 flash 基线（mock）", str(FLASH_CONFIG.relative_to(REPO_ROOT)), "deepseek-flash", 0.1),
        ]
        items_by_config = {}
        for config in configs:
            items_by_config[config.key] = items_from_scores(
                samples,
                lambda sample, tier, key=config.key: mock_overall(sample, tier, key),
                label_prefix="",
            )
        live_calls = 0
        mode = "mock"
        base_notes.append("mock 模式：评分为确定性桩，仅验证 A/B 流水线与报告，不代表真实模型效果。")

    metrics = compute_ab_metrics(items_by_config)
    return build_result(
        configs,
        metrics,
        samples,
        mode=mode,
        live_calls=live_calls,
        notes=base_notes,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="模型分配 A/B：现混合配置 vs 全 flash 基线（评分质量指标对比）。",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--mock", action="store_true", help="确定性 mock 评分（默认，无 Key）")
    mode_group.add_argument("--live", action="store_true", help="真实调用 evaluation 路由评分")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help="样本条数（默认 2 = 12 次调用；3 条 = 18 次，达预算上限）",
    )
    parser.add_argument(
        "--sample-ids",
        default="",
        help="显式指定样本 id（逗号分隔），优先于随机抽样；如 qa-intern-001,qa-autumn-001",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="抽样随机种子")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="live 并发数")
    parser.add_argument(
        "--out",
        default=None,
        help="Markdown 报告输出路径（默认 live→reports/model_ab.md；mock→reports/model_ab_mock.md，"
        "避免 mock 覆盖真实报告）",
    )
    parser.add_argument("--quiet", action="store_true", help="不打印报告正文")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sample_ids = tuple(item.strip() for item in args.sample_ids.split(",") if item.strip())
    try:
        result = run_ab(
            live=args.live,
            limit=args.limit,
            seed=args.seed,
            workers=args.workers,
            sample_ids=sample_ids,
        )
    except AbError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return EXIT_DATA_ERROR
    except Exception as exc:  # noqa: BLE001 - live 模式依赖/Key/网络错误统一为配置错误
        print(f"[ERROR] A/B 执行失败（live 模式需要有效 Key）：{exc}", file=sys.stderr)
        return EXIT_DATA_ERROR

    markdown = render_markdown(result)
    out_path = Path(args.out) if args.out else (DEFAULT_OUT if args.live else MOCK_OUT)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")
    if not args.quiet:
        print(markdown)
    print(
        f"[model_ab] mode={result.mode} samples={len(result.sample_ids)} "
        f"items={result.n_items} live_calls={result.live_calls}；报告：{out_path}",
        file=sys.stderr,
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
