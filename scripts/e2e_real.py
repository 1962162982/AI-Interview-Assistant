#!/usr/bin/env python
"""真实端到端验收脚本（阶段 3 系统集成）：按 docs/api.md v1 走通主流程。

覆盖（依赖真实后端已启动，默认 http://127.0.0.1:8000）：
    /health → /api/meta/routes → resume/structure → resume/diagnose
    → jd/structure → jd/match
    → interview/sessions → turns/stream(SSE) → get → end
    →（可选 --with-report）report/generate → get → markdown

前置条件
--------
1. 已启动后端（临时全 DeepSeek 模式或恢复决策④后均可，只要对应 Key 已配置）::

       cd AI-Interview-Assistant
       .\\.venv\\Scripts\\python.exe -m uvicorn backend.main:app --port 8000

2. 后端 `.env` 中已配置所需模型 Key（本脚本不接触 Key）。

用法::

    .\\.venv\\Scripts\\python.exe scripts/e2e_real.py
    .\\.venv\\Scripts\\python.exe scripts/e2e_real.py --with-report
    .\\.venv\\Scripts\\python.exe scripts/e2e_real.py --base-url http://127.0.0.1:8000

退出码：0=全部通过；1=任一步骤失败；2=无法连接后端。

注意：真实模型调用会产生费用/耗时（v4-pro 单次可达数十秒）；默认跳过报告生成，
加上 ``--with-report`` 才会触发评分 + 交叉复核调用。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SAMPLE_RESUME = ROOT / "data" / "resumes" / "resume_intern_01.txt"
SAMPLE_JD = ROOT / "data" / "jds" / "jd_intern_01.txt"


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


def _import_requests():
    try:
        import requests  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给出明确提示
        raise SystemExit("[FAIL] 缺少 requests：请先 pip install -r requirements.txt") from exc
    return requests


class E2E:
    def __init__(self, base_url: str, with_report: bool) -> None:
        import requests  # noqa: PLC0415

        self.requests = requests
        self.base_url = base_url.rstrip("/")
        self.with_report = with_report
        self.passed = 0
        self.failed = 0
        self.session_id: str | None = None
        self.report_id: str | None = None

    # ---------------- 基础工具 ---------------- #
    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _call(self, step: str, method: str, path: str, *, payload=None, timeout=600, **kwargs):
        started = time.time()
        try:
            response = self.requests.request(
                method, self._url(path), json=payload, timeout=timeout, **kwargs
            )
        except Exception as exc:  # noqa: BLE001
            self.failed += 1
            print(f"[FAIL] {step}：请求异常 {type(exc).__name__}: {exc}", flush=True)
            return None
        elapsed = time.time() - started
        try:
            body = response.json()
        except ValueError:
            body = response.text[:300]
        if response.status_code >= 400 or (isinstance(body, dict) and body.get("code") not in (None, 0)):
            self.failed += 1
            code = body.get("code") if isinstance(body, dict) else None
            message = body.get("message") if isinstance(body, dict) else str(body)[:200]
            print(f"[FAIL] {step}：HTTP {response.status_code} code={code} {message} ({elapsed:.1f}s)", flush=True)
            return None
        self.passed += 1
        print(f"[ OK ] {step}：HTTP {response.status_code} ({elapsed:.1f}s)", flush=True)
        return body.get("data") if isinstance(body, dict) and "data" in body else body

    def _check(self, condition: bool, step: str, detail: str = "") -> bool:
        if condition:
            return True
        self.failed += 1
        print(f"[FAIL] {step}：{detail}", flush=True)
        return False

    # ---------------- 各步骤 ---------------- #
    def run(self) -> int:
        print("=" * 72)
        print(f"真实端到端验收：{self.base_url}（with_report={self.with_report}）")
        print("=" * 72)

        try:
            self.requests.get(self._url("/health"), timeout=10)
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] 无法连接后端 {self.base_url}：{exc}")
            return 2

        health = self._call("GET /health", "GET", "/health", timeout=30)
        self._check(isinstance(health, dict) and health.get("status") == "ok", "GET /health", "status != ok")

        meta = self._call("GET /api/meta/routes", "GET", "/api/meta/routes", timeout=30)
        routes = (meta or {}).get("routes") if isinstance(meta, dict) else None
        self._check(isinstance(routes, list) and len(routes) == 5, "GET /api/meta/routes", "应 5 条路由")
        if isinstance(routes, list):
            print("       routes:", [(r.get("route"), r.get("model")) for r in routes], flush=True)

        resume_text = SAMPLE_RESUME.read_text(encoding="utf-8")
        jd_text = SAMPLE_JD.read_text(encoding="utf-8")

        structure = self._call("resume/structure", "POST", "/api/resume/structure", payload={"resume_text": resume_text})
        self._check(isinstance(structure, dict) and bool(structure.get("name")), "resume/structure", "缺少 name")
        if isinstance(structure, dict):
            print(f"       name={structure.get('name')} skills={len(structure.get('skills') or [])} degraded={structure.get('degraded')}", flush=True)

        diagnosis = self._call(
            "resume/diagnose", "POST", "/api/resume/diagnose",
            payload={"resume_text": resume_text, "target_role": "后端开发实习生"},
        )
        issues = (diagnosis or {}).get("issues") if isinstance(diagnosis, dict) else None
        self._check(isinstance(issues, list) and len(issues) >= 1, "resume/diagnose", "issues 为空")
        if isinstance(issues, list):
            print(f"       summary={(diagnosis.get('summary') or '')[:50]} issues={len(issues)}", flush=True)

        jd_structured = self._call("jd/structure", "POST", "/api/jd/structure", payload={"jd_text": jd_text})
        self._check(isinstance(jd_structured, dict) and bool(jd_structured.get("title")), "jd/structure", "缺少 title")

        match = self._call("jd/match", "POST", "/api/jd/match", payload={"resume_text": resume_text, "jd_text": jd_text})
        score = (match or {}).get("score") if isinstance(match, dict) else None
        self._check(isinstance(score, int) and 0 <= score <= 100, "jd/match", f"score 非法：{score!r}")
        if isinstance(match, dict):
            print(f"       score={score} items={len(match.get('items') or [])} gaps={len(match.get('gaps') or [])}", flush=True)

        session = self._call(
            "interview/sessions", "POST", "/api/interview/sessions",
            payload={"resume_text": resume_text, "jd_text": jd_text, "scene": "intern",
                     "company": "某科技公司", "role": "后端开发实习生"},
        )
        self.session_id = (session or {}).get("session_id") if isinstance(session, dict) else None
        self._check(bool(self.session_id), "interview/sessions", "缺少 session_id")

        if self.session_id:
            self._run_sse_turn()

        if self.session_id:
            session_state = self._call(
                "interview/get", "GET", f"/api/interview/sessions/{self.session_id}", timeout=120
            )
            self._check(isinstance(session_state, dict) and session_state.get("session_id") == self.session_id,
                        "interview/get", "session 状态缺失")
            end = self._call(
                "interview/end", "POST", f"/api/interview/sessions/{self.session_id}/end", payload={}
            )
            transcript = (end or {}).get("transcript") if isinstance(end, dict) else None
            self._check(isinstance(transcript, list) and len(transcript) >= 1, "interview/end", "transcript 为空")

        if self.with_report and self.session_id:
            self._run_report()

        print("-" * 72)
        print(f"汇总：passed={self.passed} failed={self.failed}")
        if self.failed:
            print("[FAIL] 真实端到端验收未通过")
            return 1
        print("[PASS] 真实端到端验收通过（接口契约、真实模型链路、SSE 流式均正常）")
        return 0

    def _run_sse_turn(self) -> None:
        assert self.session_id
        answer = (
            "我负责订单接口，先做 Redis 缓存热点数据，并用压测验证效果，"
            "订单查询 P99 从 350ms 降到 80ms。"
        )
        started = time.time()
        events: list[str] = []
        done_payload: dict | None = None
        try:
            with self.requests.post(
                self._url(f"/api/interview/sessions/{self.session_id}/turns/stream"),
                json={"answer": answer},
                stream=True,
                timeout=600,
            ) as response:
                if response.status_code >= 400:
                    self.failed += 1
                    print(f"[FAIL] turns/stream：HTTP {response.status_code}", flush=True)
                    return
                for raw in response.iter_lines(decode_unicode=True):
                    if not raw:
                        continue
                    line = raw.strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    name = event.get("event")
                    events.append(name)
                    if name == "error":
                        self.failed += 1
                        print(f"[FAIL] turns/stream：error 事件 {event}", flush=True)
                        return
                    if name == "done":
                        done_payload = event
            elapsed = time.time() - started
            if done_payload is None:
                self.failed += 1
                print(f"[FAIL] turns/stream：未收到 done 事件（events={events[:8]}）", flush=True)
                return
            self.passed += 1
            question = (done_payload.get("question") or {}).get("text", "")
            print(
                f"[ OK ] turns/stream：HTTP 200 ({elapsed:.1f}s) delta={events.count('delta')} "
                f"rule={((done_payload.get('question') or {}).get('rule_id'))} next={question[:40]}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.failed += 1
            print(f"[FAIL] turns/stream：异常 {type(exc).__name__}: {exc}", flush=True)

    def _run_report(self) -> None:
        assert self.session_id
        report = self._call(
            "report/generate", "POST", "/api/report/generate",
            payload={"session_id": self.session_id}, timeout=900,
        )
        self.report_id = (report or {}).get("report_id") if isinstance(report, dict) else None
        self._check(bool(self.report_id), "report/generate", "缺少 report_id")
        evaluation = (report or {}).get("evaluation") if isinstance(report, dict) else None
        dims = (evaluation or {}).get("dimensions") if isinstance(evaluation, dict) else None
        self._check(isinstance(dims, list) and len(dims) == 4, "report/generate", "应 4 个评分维度")
        self._check(bool((report or {}).get("cross_review")), "report/generate", "缺少 cross_review")
        self._check("markdown" in (report or {}), "report/generate", "缺少 markdown")
        if not self.report_id:
            return
        got = self._call("report/get", "GET", f"/api/report/{self.report_id}", timeout=120)
        self._check(isinstance(got, dict) and got.get("report_id") == self.report_id, "report/get", "报告不存在")
        markdown = self._call(
            "report/markdown", "GET", f"/api/report/{self.report_id}/markdown", timeout=120
        )
        self._check(isinstance(markdown, str) and "#" in markdown, "report/markdown", "Markdown 内容异常")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="真实端到端验收（阶段 3）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="后端地址（默认本机 8000）")
    parser.add_argument("--with-report", action="store_true", help="同时验收报告生成/查询/Markdown（会触发评分+复核调用）")
    args = parser.parse_args(argv)
    _configure_stdio()
    _import_requests()
    if not SAMPLE_RESUME.is_file() or not SAMPLE_JD.is_file():
        print(f"[FAIL] 样例素材缺失：{SAMPLE_RESUME} / {SAMPLE_JD}")
        return 1
    return E2E(args.base_url, args.with_report).run()


if __name__ == "__main__":
    raise SystemExit(main())
