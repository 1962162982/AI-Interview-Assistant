#!/usr/bin/env python
"""真实 DeepSeek 联调冒烟：模块 A（诊断 / 结构化）与 cross_review 路由。

前置条件
--------
1. ``.env`` 中 ``RESUME_DIAGNOSIS_API_KEY``（模块 A，deepseek-chat）与
   ``CROSS_REVIEW_API_KEY``（交叉复核，deepseek-chat）为**有效**的 DeepSeek Key；
2. 已安装项目依赖（``.venv``）。

用法（在仓库根目录执行）::

    .\\.venv\\Scripts\\python.exe scripts/smoke_deepseek.py
    .\\.venv\\Scripts\\python.exe scripts/smoke_deepseek.py --only a
    .\\.venv\\Scripts\\python.exe scripts/smoke_deepseek.py --only cross_review

退出码
------
* 0：全部通过（模块 A 与 cross_review 均返回结构化结果，未降级）；
* 2：Key 缺失 / 鉴权失败（401）——需要更换有效 Key 后重跑；
* 1：其他失败（网络、超时、JSON 校验失败等）。

安全：本脚本**不会打印任何 Key**；真实请求全部经 ``model_router`` 按模块路由，
不读取、不落盘任何密钥。
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.schemas.interview_evaluation import DimensionScore, InterviewEvaluation  # noqa: E402
from backend.services.evaluation import DIMENSIONS, cross_review_answer  # noqa: E402
from backend.services.model_router import (  # noqa: E402
    MissingAPIKeyError,
    ModelCallError,
    ModelJSONError,
    ModelRouterError,
)
from backend.services.resume_diagnosis import diagnose_resume  # noqa: E402
from backend.services.resume_structure import structure_resume  # noqa: E402

SAMPLE_RESUME = ROOT / "data" / "resumes" / "resume_intern_01.txt"

#: 交叉复核用的最小面试对话（虚构，不含真人信息）
SAMPLE_CONVERSATION = (
    "面试官：请介绍一个你负责过的后端项目，并说明你解决的关键技术问题。\n"
    "候选人：我做过一个校园二手交易平台，用 FastAPI 写接口，负责订单模块。"
    "项目里遇到过高并发下单导致超卖的问题，我用 Redis 分布式锁加数据库乐观锁解决，"
    "压测时订单接口 P99 从 800ms 降到了 120ms。\n"
    "面试官：为什么同时用分布式锁和乐观锁？\n"
    "候选人：分布式锁挡住同一商品的并发请求，乐观锁作为数据库层兜底，防止锁失效时超卖。"
)


def _configure_stdio() -> None:
    """Windows GBK 控制台容错：保底输出不因个别字符崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


def _classify(exc: BaseException) -> int:
    """把异常分类成退出码：2 = Key 缺失/无效；1 = 其他失败。"""
    if isinstance(exc, MissingAPIKeyError):
        return 2
    if isinstance(exc, ModelCallError):
        message = str(exc)
        if any(token in message for token in ("401", "Authentication", "invalid_api_key", "Unauthorized")):
            return 2
    return 1


def _fake_first_evaluation() -> InterviewEvaluation:
    """构造一份满足校验的“首评”结果，供交叉复核真实调用使用。"""
    evidence = ["候选人提到 Redis 分布式锁", "候选人给出压测数据（P99 800ms→120ms）"]
    return InterviewEvaluation(
        dimensions=[
            DimensionScore(dimension=name, score=7, evidence=list(evidence)) for name in DIMENSIONS
        ],
        strengths=["有具体项目与量化结果"],
        weaknesses=["对锁的取舍原理说明还可以更深入"],
        suggestions=["补充压测方法与监控指标"],
    )


def run_module_a(results: list[bool]) -> None:
    print("[RUN ] 模块 A · 真实简历诊断（DeepSeek 路由 resume_diagnosis）……")
    if not SAMPLE_RESUME.is_file():
        raise FileNotFoundError(f"样例简历不存在：{SAMPLE_RESUME}")
    resume_text = SAMPLE_RESUME.read_text(encoding="utf-8")
    started = time.time()
    report = diagnose_resume(resume_text, jd_text="后端开发实习生：熟悉 Python / FastAPI，有项目经验")
    elapsed = time.time() - started
    if report.degraded:
        raise ModelJSONError("诊断结果降级为纯文本（模型输出不可解析），视为失败")
    if not report.issues:
        raise ModelJSONError("诊断结果 issues 为空，真实链路验收不通过")
    first = report.issues[0]
    print(
        f"[ OK ] 模块 A 诊断：{elapsed:.1f}s，issues={len(report.issues)}，"
        f"首条=[{first.priority}] {first.check_item}：{first.problem[:40]}"
    )
    print(f"       summary：{(report.summary or '')[:60]}")
    results.append(True)

    print("[RUN ] 模块 A · 真实简历结构化抽取（ResumeStructured）……")
    started = time.time()
    structured = structure_resume(resume_text)
    elapsed = time.time() - started
    if structured.degraded:
        raise ModelJSONError("结构化抽取降级为规则分段，视为失败")
    data = structured.data
    print(
        f"[ OK ] 模块 A 结构化：{elapsed:.1f}s，name={data.name or '（空）'}，"
        f"教育={len(data.education)}，经历={len(data.experiences)}，技能={len(data.skills)}"
    )
    results.append(True)


def run_cross_review(results: list[bool]) -> None:
    print("[RUN ] cross_review · 真实交叉复核（cross_review 路由，与评分不同模型）……")
    started = time.time()
    review = cross_review_answer(
        SAMPLE_CONVERSATION,
        _fake_first_evaluation(),
        jd="后端开发实习生：熟悉 Python / FastAPI，有高并发项目经验",
        resume_excerpt="校园二手交易平台；FastAPI；Redis 分布式锁；P99 800ms→120ms",
    )
    elapsed = time.time() - started
    payload = review.to_dict()
    verdicts = payload.get("verdicts") or []
    if verdicts and len(verdicts) != len(DIMENSIONS):
        raise ModelJSONError(f"交叉复核维度数量异常：{len(verdicts)} != {len(DIMENSIONS)}")
    print(
        f"[ OK ] cross_review：{elapsed:.1f}s，verdict={review.verdict}，"
        f"summary={(payload.get('summary') or '')[:60]}"
    )
    results.append(True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="真实 DeepSeek 联调冒烟（模块 A + cross_review）")
    parser.add_argument(
        "--only",
        choices=("all", "a", "cross_review"),
        default="all",
        help="只跑指定部分（默认 all）",
    )
    args = parser.parse_args(argv)
    _configure_stdio()

    print("=" * 68)
    print("DeepSeek 真实联调冒烟（Key 只从 .env 读取，不会打印）")
    print(f"仓库：{ROOT}")
    print(f"范围：{args.only}")
    print("=" * 68)

    results: list[bool] = []
    exit_code = 0
    for name, runner in (
        ("a", run_module_a),
        ("cross_review", run_cross_review),
    ):
        if args.only not in ("all", name):
            continue
        try:
            runner(results)
        except ModelRouterError as exc:
            code = _classify(exc)
            exit_code = max(exit_code, code)
            label = "Key 缺失或无效（需要更换有效 Key）" if code == 2 else "模型调用失败"
            print(f"[FAIL] {label}：{exc}")
        except Exception as exc:  # noqa: BLE001 - 冒烟脚本需要给出完整失败原因
            exit_code = max(exit_code, 1)
            print(f"[FAIL] 未预期失败：{type(exc).__name__}: {exc}")
            traceback.print_exc(limit=3)

    print("-" * 68)
    if exit_code == 0 and results and all(results):
        print("[PASS] DeepSeek 真实联调通过：模块 A 与 cross_review 均返回结构化结果。")
        return 0
    if exit_code == 2:
        print("[FAIL] 真实联调被阻断：Key 缺失或无效。请更新 .env 后重跑本脚本。")
        return 2
    print("[FAIL] 真实联调未通过，请查看上方失败原因。")
    return exit_code or 1


if __name__ == "__main__":
    raise SystemExit(main())
