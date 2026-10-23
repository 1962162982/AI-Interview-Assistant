"""路由级集成测试（D2 接线）：四组路由 + 错误码映射

策略：TestClient + monkeypatch 服务函数 / fake InterviewAgent，
**不依赖真实 API Key、不发网络请求**。

断言重点（与 docs/api.md 冻结契约一致）：
- 成功：`code=0`，`data` 键与契约一致；
- 失败：服务异常 → 契约错误码（1001/1002/2001/2002/3001/5000）；
- SSE：`event` 信封（delta / done / error）。
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api import deps
from backend.main import app
from backend.schemas.jd_structured import CapabilityItem, JDStructured
from backend.services import evaluation, jd_matching, resume_diagnosis
from backend.services.evaluation import EvaluationValidationError, ReportNotFoundError
from backend.services.jd_matching import GapItem, JDMatchResult, MatchItem
from backend.services.model_router import ModelCallError, ModelJSONError, MissingAPIKeyError
from backend.services.parser import ParsedResume, ParserError
from backend.services.resume_diagnosis import DiagnosisIssue, DiagnosisReport

client = TestClient(app)


def _mock_report_payload() -> dict[str, Any]:
    return {
        "report_id": "rep_int_1",
        "evaluation": {
            "dimensions": [{"dimension": "相关性", "score": 8, "evidence": ["原句"]}],
            "strengths": ["表达清晰"],
            "weaknesses": ["缺量化"],
            "suggestions": ["补充数据"],
        },
        "cross_review": {"verdicts": [], "overall_verdict": "agree", "summary": "一致"},
        "markdown": "# 面试报告\n\n整体表现良好。\n",
    }


# ===========================================================================
# 模块 A：resume
# ===========================================================================
def test_resume_parse_contract_keys(monkeypatch):
    import backend.services.parser as parser_pkg

    def fake_parse(filename, data=None):
        return ParsedResume(filename=str(filename), kind="docx", text="张三 简历", segments=[])

    monkeypatch.setattr(parser_pkg, "parse_resume", fake_parse)
    response = client.post(
        "/api/resume/parse",
        files={"file": ("resume.docx", b"whatever", "application/octet-stream")},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert set(payload["data"]) == {
        "filename",
        "kind",
        "text",
        "segments",
        "page_count",
        "char_count",
        "degraded",
        "warnings",
    }


def test_resume_parse_failure_maps_to_1002(monkeypatch):
    import backend.services.parser as parser_pkg

    def fake_parse(filename, data=None):
        raise ParserError("不支持的扫描件 PDF")

    monkeypatch.setattr(parser_pkg, "parse_resume", fake_parse)
    response = client.post(
        "/api/resume/parse",
        files={"file": ("resume.pdf", b"xxx", "application/pdf")},
    )
    assert response.status_code == 400
    assert response.json()["code"] == 1002


def test_resume_structure_contract_keys(monkeypatch):
    import backend.services.resume_structure as resume_structure
    from backend.schemas.resume_structured import Education, ResumeStructured

    def fake_structure(resume_text, *args, **kwargs):
        return resume_structure.ResumeStructureResult(
            data=ResumeStructured(
                name="张三",
                education=[
                    Education(
                        school="某大学", major="计算机科学与技术", degree="本科", period="2022–2026"
                    )
                ],
                skills=["Python", "FastAPI"],
            ),
            degraded=False,
        )

    monkeypatch.setattr(resume_structure, "structure_resume", fake_structure)
    response = client.post("/api/resume/structure", json={"resume_text": "张三 简历"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    # ResumeStructured 字段 + degraded 扩展（不破坏既有键）
    assert set(payload["data"]) == {
        "name",
        "education",
        "experiences",
        "skills",
        "projects",
        "degraded",
    }
    assert set(payload["data"]["education"][0]) == {"school", "major", "degree", "period"}
    assert payload["data"]["degraded"] is False


def test_resume_structure_degraded_when_model_json_invalid(monkeypatch):
    """mock router 返回非法 JSON → 规则分段兜底：200 + degraded=True（不返回 2002）。"""
    import backend.services.resume_structure as resume_structure

    class FakeRouter:
        def chat_json(self, route, messages, schema, max_repair=1):
            raise ModelJSONError(route, "deepseek-chat", "{not valid json", "JSONDecodeError")

    monkeypatch.setattr(resume_structure, "get_router", lambda: FakeRouter())
    response = client.post(
        "/api/resume/structure",
        json={"resume_text": "教育经历\n某大学 计算机科学与技术 本科 2022.09–2026.06"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert payload["data"]["degraded"] is True
    assert payload["data"]["name"]  # 规则分段总会给出候选人名（未知时为“未知候选人”）
    assert payload["data"]["education"]


def test_resume_diagnose_contract_keys(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_diagnose(resume_text, jd_text="", *args, **kwargs):
        captured["resume_text"] = resume_text
        captured["jd_text"] = jd_text
        return DiagnosisReport(
            summary="整体不错",
            issues=[
                DiagnosisIssue(
                    check_item="量化表达",
                    priority="high",
                    original_quote="负责优化接口",
                    problem="缺少可量化结果",
                    rewrite="P99 从 800ms 降至 120ms",
                )
            ],
            strengths=["项目与岗位匹配"],
        )

    monkeypatch.setattr(resume_diagnosis, "diagnose_resume", fake_diagnose)
    response = client.post(
        "/api/resume/diagnose",
        json={"resume_text": "张三 简历", "target_role": "后端开发实习生"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert set(payload["data"]) == {"summary", "issues", "strengths"}
    issue = payload["data"]["issues"][0]
    assert set(issue) == {"check_item", "priority", "original_quote", "problem", "rewrite"}
    assert issue["check_item"] == "量化表达"
    # target_role 作为 JD 兜底传入服务
    assert captured["jd_text"] == "后端开发实习生"


def test_resume_diagnose_model_error_maps_to_2001(monkeypatch):
    def boom(*args, **kwargs):
        raise ModelCallError("resume_diagnosis", "deepseek-chat", 3, TimeoutError("timeout"))

    monkeypatch.setattr(resume_diagnosis, "diagnose_resume", boom)
    response = client.post("/api/resume/diagnose", json={"resume_text": "张三"})
    assert response.status_code == 502
    payload = response.json()
    assert payload["code"] == 2001
    assert payload["data"] is None


def test_resume_diagnose_json_error_maps_to_2002(monkeypatch):
    def boom(*args, **kwargs):
        raise ModelJSONError("resume_diagnosis", "deepseek-chat", "{bad json", "ValidationError")

    monkeypatch.setattr(resume_diagnosis, "diagnose_resume", boom)
    response = client.post("/api/resume/diagnose", json={"resume_text": "张三"})
    assert response.status_code == 502
    assert response.json()["code"] == 2002


def test_resume_diagnose_internal_error_maps_to_5000(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("内部炸了")

    monkeypatch.setattr(resume_diagnosis, "diagnose_resume", boom)
    response = client.post("/api/resume/diagnose", json={"resume_text": "张三"})
    assert response.status_code == 500
    payload = response.json()
    assert payload["code"] == 5000
    assert "内部错误" in payload["message"]


# ===========================================================================
# 模块 B：jd
# ===========================================================================
def test_jd_structure_contract_keys(monkeypatch):
    def fake_extract(jd_text, *args, **kwargs):
        return JDStructured(
            title="后端开发实习生",
            responsibilities=["负责接口开发"],
            capabilities=[
                CapabilityItem(capability="Python", level="熟悉", evidence="熟悉 Python")
            ],
        )

    monkeypatch.setattr(jd_matching, "extract_jd_structured", fake_extract)
    response = client.post("/api/jd/structure", json={"jd_text": "岗位：后端开发"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert set(payload["data"]) == {"title", "responsibilities", "capabilities"}
    assert set(payload["data"]["capabilities"][0]) == {"capability", "level", "evidence"}


def test_jd_match_contract_keys(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_match(resume, jd_text=None, *, jd_structured=None, **kwargs):
        captured["resume"] = resume
        captured["jd_text"] = jd_text
        captured["jd_structured"] = jd_structured
        return JDMatchResult(
            score=88,
            summary="整体匹配良好",
            items=[
                MatchItem(
                    capability="Python",
                    status="matched",
                    evidence="校园项目使用 FastAPI",
                    comment="有实践",
                )
            ],
            gaps=[
                GapItem(
                    capability="高并发经验",
                    gap="简历未体现压测",
                    suggestion="补充 QPS / 延迟数据",
                )
            ],
        )

    monkeypatch.setattr(jd_matching, "match_resume_to_jd", fake_match)
    response = client.post(
        "/api/jd/match",
        json={
            "resume": {"name": "张三", "education": [], "experiences": [], "skills": [], "projects": []},
            "jd": {"title": "后端开发", "responsibilities": [], "capabilities": []},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert set(payload["data"]) == {"score", "summary", "items", "gaps"}
    assert payload["data"]["score"] == 88
    assert set(payload["data"]["items"][0]) == {"capability", "status", "evidence", "comment"}
    assert set(payload["data"]["gaps"][0]) == {"capability", "gap", "suggestion"}
    assert captured["jd_structured"] is not None


def test_jd_match_text_resume_returns_200(monkeypatch):
    """T6 文本路径：{resume_text, jd_text} → 服务内部先结构化再匹配。"""
    captured: dict[str, Any] = {}

    def fake_match(
        resume=None, jd_text=None, *, jd_structured=None, resume_text=None, **kwargs
    ):
        captured.update(
            {
                "resume": resume,
                "jd_text": jd_text,
                "jd_structured": jd_structured,
                "resume_text": resume_text,
            }
        )
        return JDMatchResult(
            score=76,
            summary="文本路径匹配完成",
            items=[
                MatchItem(capability="Python", status="matched", evidence="项目使用", comment="有实践")
            ],
            gaps=[],
        )

    monkeypatch.setattr(jd_matching, "match_resume_to_jd", fake_match)
    response = client.post(
        "/api/jd/match",
        json={"resume_text": "张三 简历", "jd_text": "岗位：后端开发"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert set(payload["data"]) == {"score", "summary", "items", "gaps"}
    assert payload["data"]["score"] == 76
    assert captured["resume"] is None
    assert captured["resume_text"] == "张三 简历"
    assert captured["jd_text"] == "岗位：后端开发"


def test_jd_match_text_path_degraded_when_model_json_invalid(monkeypatch):
    """文本路径 + mock router 返回非法 JSON：简历规则分段降级 → 匹配降级 → 200（score=null）。"""
    import backend.services.resume_structure as resume_structure

    class FakeRouter:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def chat_json(self, route, messages, schema, max_repair=1):
            self.calls.append(route)
            raise ModelJSONError(route, "fake-model", "not json", "JSONDecodeError")

    fake_router = FakeRouter()
    monkeypatch.setattr(jd_matching, "get_router", lambda: fake_router)
    monkeypatch.setattr(resume_structure, "get_router", lambda: fake_router)
    response = client.post(
        "/api/jd/match",
        json={
            "resume_text": "教育经历\n某大学 计算机科学与技术 本科 2022.09–2026.06",
            "jd": {"title": "后端开发", "responsibilities": [], "capabilities": []},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert set(payload["data"]) == {"score", "summary", "items", "gaps"}
    assert payload["data"]["score"] is None
    assert "降级" in payload["data"]["summary"]
    # 证明两段都走了路由：简历结构化（A 路由） + 匹配（B 路由）
    assert "resume_diagnosis" in fake_router.calls
    assert "jd_matching" in fake_router.calls


def test_jd_match_missing_inputs_returns_1001():
    response = client.post("/api/jd/match", json={})
    assert response.status_code == 400
    assert response.json()["code"] == 1001


# ===========================================================================
# 模块 C：interview（fake agent）
# ===========================================================================
class FakeAgent:
    """覆盖 interview/report 路由所需会话方法的 fake 实现。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.transcript = [
            {
                "turn": 1,
                "question": "请先做个自我介绍",
                "answer": "我是张三，计算机专业",
                "direction": "项目经历",
                "rule_id": None,
                "follow_up_depth": 0,
                "elapsed_seconds": 3.2,
            }
        ]

    def _require(self, session_id: str) -> None:
        if session_id != "sess_abc":
            raise ValueError(f"会话不存在: {session_id!r}")

    def create_session(self, resume_text, *, jd_text=None, scene="intern", company="", role="", style=""):
        self.calls.append(("create", {"scene": scene, "company": company, "role": role, "style": style}))
        return {
            "session_id": "sess_abc",
            "scene": scene,
            "status": "active",
            "turn_count": 1,
            "question": {
                "question_id": "q1",
                "text": "请先做个自我介绍",
                "direction": "项目经历",
                "turn": 1,
                "rule_id": None,
                "follow_up_depth": 0,
            },
        }

    def get_session(self, session_id):
        self._require(session_id)
        return {
            "session_id": session_id,
            "scene": "intern",
            "status": "active",
            "turn_count": 2,
            "context_usage_pct": 3.5,
            "summary": "候选人做过后端项目",
        }

    def submit_answer(self, session_id, answer):
        self._require(session_id)
        return {
            "session_id": session_id,
            "turn_index": 1,
            "question": {
                "question_id": "q2",
                "text": "为什么选 Redis 做缓存？",
                "direction": "技术基础",
                "turn": 2,
                "rule_id": "D2",
                "follow_up_depth": 1,
            },
            "state": {"turn_count": 2, "context_usage_pct": 5.1},
        }

    def stream_turn(self, session_id, answer):
        self._require(session_id)
        yield {"event": "delta", "text": "为什么"}
        yield {"event": "delta", "text": "选 Redis？"}
        yield {
            "event": "done",
            "question": {"question_id": "q2", "text": "为什么选 Redis？"},
            "state": {"turn_count": 2},
            "usage": {"total_tokens": 120, "context_usage_pct": 5.1},
        }

    def end_session(self, session_id, *args, **kwargs):
        self._require(session_id)
        return {
            "session_id": session_id,
            "transcript": list(self.transcript),
            "state": {"turn_count": 1},
            "report_input": {
                "session_id": session_id,
                "scene": "intern",
                "company": "某互联网公司",
                "role": "后端开发实习生",
                "transcript": list(self.transcript),
                "summary": "",
            },
        }


@pytest.fixture()
def fake_agent(monkeypatch) -> FakeAgent:
    agent = FakeAgent()
    monkeypatch.setattr(deps, "get_interview_agent", lambda: agent)
    return agent


def test_interview_create_session_contract(fake_agent):
    response = client.post(
        "/api/interview/sessions",
        json={"resume_text": "张三 简历", "scene": "autumn", "company": "某公司"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert set(payload["data"]) == {"session_id", "question", "state"}
    assert payload["data"]["session_id"] == "sess_abc"
    assert payload["data"]["question"]["question_id"] == "q1"
    assert fake_agent.calls[0][1]["scene"] == "autumn"


def test_interview_turns_contract(fake_agent):
    response = client.post(
        "/api/interview/sessions/sess_abc/turns", json={"answer": "我做过 FastAPI 项目"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert set(payload["data"]) == {"session_id", "turn_index", "question", "state"}
    assert payload["data"]["turn_index"] == 1
    assert payload["data"]["question"]["rule_id"] == "D2"


def test_interview_get_session_contract(fake_agent):
    response = client.get("/api/interview/sessions/sess_abc")
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert payload["data"]["session_id"] == "sess_abc"
    assert payload["data"]["context_usage_pct"] == pytest.approx(3.5)


def test_interview_end_contract(fake_agent):
    response = client.post("/api/interview/sessions/sess_abc/end")
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert set(payload["data"]) == {"session_id", "transcript", "state", "report_input"}
    turn = payload["data"]["transcript"][0]
    assert {"turn", "question", "answer", "rule_id", "direction", "follow_up_depth"} <= set(turn)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/interview/sessions/unknown"),
        ("post", "/api/interview/sessions/unknown/turns"),
        ("post", "/api/interview/sessions/unknown/end"),
        ("post", "/api/interview/sessions/unknown/turns/stream"),
    ],
)
def test_interview_unknown_session_returns_3001(fake_agent, method, path):
    kwargs = {"json": {"answer": "x"}} if path.endswith(("turns", "stream")) else {}
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 404
    assert response.json()["code"] == 3001


# --------------------------- SSE 流式 ---------------------------
def _parse_sse(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        events.append(json.loads(line[len("data: "):]))
    return events


def test_interview_stream_sse_events(fake_agent):
    response = client.post(
        "/api/interview/sessions/sess_abc/turns/stream", json={"answer": "我做过项目"}
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    events = _parse_sse(response.text)
    assert [event["event"] for event in events] == ["delta", "delta", "done"]
    assert events[0]["text"] == "为什么"
    assert events[-1]["question"]["question_id"] == "q2"
    assert "usage" in events[-1]


def test_interview_stream_error_event(monkeypatch):
    class ErrorStreamAgent(FakeAgent):
        def stream_turn(self, session_id, answer):
            self._require(session_id)
            raise RuntimeError("流式生成异常")
            yield  # pragma: no cover - 让函数保持生成器语义

    monkeypatch.setattr(deps, "get_interview_agent", lambda: ErrorStreamAgent())
    response = client.post(
        "/api/interview/sessions/sess_abc/turns/stream", json={"answer": "x"}
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events[-1]["event"] == "error"
    assert events[-1]["code"] == 5000
    assert events[-1]["retryable"] is False


# ===========================================================================
# 模块 D：report
# ===========================================================================
def test_report_generate_with_turns_contract(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_generate(session_id, turns, *args, **kwargs):
        captured["session_id"] = session_id
        captured["turns"] = turns
        return _mock_report_payload()

    monkeypatch.setattr(evaluation, "generate_report", fake_generate)
    response = client.post(
        "/api/report/generate",
        json={"session_id": "sess_abc", "turns": [{"question": "q", "answer": "a"}]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert set(payload["data"]) == {"report_id", "evaluation", "cross_review", "markdown"}
    assert captured["session_id"] == "sess_abc"
    assert len(captured["turns"]) == 1


def test_report_generate_from_session_without_turns(monkeypatch, fake_agent):
    captured: dict[str, Any] = {}

    def fake_generate_for_session(session, *args, **kwargs):
        captured["session"] = session
        return _mock_report_payload()

    monkeypatch.setattr(evaluation, "generate_report_for_session", fake_generate_for_session)
    response = client.post("/api/report/generate", json={"session_id": "sess_abc"})
    assert response.status_code == 200
    assert response.json()["data"]["report_id"] == "rep_int_1"
    session_payload = captured["session"]
    assert session_payload["session_id"] == "sess_abc"
    assert session_payload["turns"] == fake_agent.transcript
    assert session_payload["scene"] == "intern"
    assert session_payload["company"] == "某互联网公司"


def test_report_generate_missing_key_maps_to_2001(monkeypatch):
    def boom(*args, **kwargs):
        raise MissingAPIKeyError("evaluation", "EVALUATION_API_KEY", ".env")

    monkeypatch.setattr(evaluation, "generate_report", boom)
    response = client.post(
        "/api/report/generate",
        json={"session_id": "sess_abc", "turns": [{"question": "q", "answer": "a"}]},
    )
    assert response.status_code == 502
    assert response.json()["code"] == 2001


def test_report_generate_validation_error_maps_to_2002(monkeypatch):
    def boom(*args, **kwargs):
        raise EvaluationValidationError("恰好 4 个维度校验失败")

    monkeypatch.setattr(evaluation, "generate_report", boom)
    response = client.post(
        "/api/report/generate",
        json={"session_id": "sess_abc", "turns": [{"question": "q", "answer": "a"}]},
    )
    assert response.status_code == 502
    assert response.json()["code"] == 2002


def test_report_get_contract(monkeypatch):
    monkeypatch.setattr(evaluation, "get_report", lambda report_id: _mock_report_payload())
    response = client.get("/api/report/rep_int_1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    # 冻结契约：结构同 /generate 的 data（含 markdown，见 docs/api.md）
    assert set(payload["data"]) == {"report_id", "evaluation", "cross_review", "markdown"}


def test_report_get_not_found_returns_3001(monkeypatch):
    def boom(report_id):
        raise ReportNotFoundError(f"报告不存在：{report_id}")

    monkeypatch.setattr(evaluation, "get_report", boom)
    response = client.get("/api/report/rep_missing")
    assert response.status_code == 404
    assert response.json()["code"] == 3001


def test_report_markdown_returns_raw_text(monkeypatch):
    monkeypatch.setattr(
        evaluation, "get_report_markdown", lambda report_id: "# 面试报告\n\n整体良好。\n"
    )
    response = client.get("/api/report/rep_int_1/markdown")
    assert response.status_code == 200
    assert "text/markdown" in response.headers["content-type"]
    assert response.text == "# 面试报告\n\n整体良好。\n"
    assert "attachment" in response.headers["content-disposition"]


def test_report_markdown_not_found_returns_3001(monkeypatch):
    def boom(report_id):
        raise ReportNotFoundError(f"报告不存在：{report_id}")

    monkeypatch.setattr(evaluation, "get_report_markdown", boom)
    response = client.get("/api/report/rep_missing/markdown")
    assert response.status_code == 404
    payload = response.json()
    assert payload["code"] == 3001
    assert payload["data"] is None
