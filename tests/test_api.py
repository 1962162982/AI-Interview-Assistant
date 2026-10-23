"""FastAPI 路由 / 统一响应壳测试（D2 接线版）

覆盖：
- `GET /health` 契约不变；`GET /api/meta/routes` 返回 5 条路由且不含 Key
- 四组业务路由已接线（resume / jd / interview / report），阶段 1 的 501 占位均替换为
  “实装 + 统一响应壳”断言（服务调用用 monkeypatch / fake agent，不依赖 Key 与网络）
- 参数校验失败 → 400+1001；未知路径 → 404+3001；CORS 允许 Vue 开发服务器来源

说明：各接口的完整键级集成断言见 `tests/test_api_integration.py`。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api import deps
from backend.main import app
from backend.services.model_router import MissingAPIKeyError
from backend.services.parser import ParsedResume

client = TestClient(app)

EXPECTED_ROUTES = {
    "resume_diagnosis",
    "jd_matching",
    "interview_agent",
    "evaluation",
    "cross_review",
}


class FakeInterviewAgent:
    """最小 fake：覆盖 interview/report 路由使用到的会话方法。"""

    def create_session(self, resume_text, *, jd_text=None, scene="intern", company="", role="", style=""):
        return {
            "session_id": "sess_test_1",
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
        return {
            "session_id": session_id,
            "scene": "intern",
            "status": "active",
            "turn_count": 1,
            "summary": "",
            "context_usage_pct": 2,
        }


def test_health_contract_unchanged():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_meta_routes_returns_five_routes_without_keys():
    response = client.get("/api/meta/routes")
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert payload["message"] == "ok"
    routes = payload["data"]["routes"]
    assert {item["route"] for item in routes} == EXPECTED_ROUTES
    # 不泄露 Key / Key 环境变量
    for item in routes:
        assert not any("key" in str(key).lower() for key in item)
        assert item["model"]
        assert item["context_window_tokens"] > 0
    models = {item["route"]: item["model"] for item in routes}
    assert models["evaluation"] != models["cross_review"]


# ---------------------------------------------------------------------------
# resume：已接线（解析走本地服务；诊断在 integration 测试中覆盖）
# ---------------------------------------------------------------------------
def test_resume_parse_invalid_file_returns_1002():
    response = client.post(
        "/api/resume/parse",
        files={"file": ("resume.docx", b"fake-docx-bytes", "application/octet-stream")},
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["code"] == 1002
    assert payload["data"] is None


def test_resume_parse_success_with_mocked_parser(monkeypatch):
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
    assert payload["data"]["filename"] == "resume.docx"
    assert payload["data"]["degraded"] is False


def test_resume_structure_wired(monkeypatch):
    import backend.services.resume_structure as resume_structure
    from backend.schemas.resume_structured import ResumeStructured

    def fake_structure(resume_text, *args, **kwargs):
        return resume_structure.ResumeStructureResult(
            data=ResumeStructured(name="张三"), degraded=False
        )

    monkeypatch.setattr(resume_structure, "structure_resume", fake_structure)
    response = client.post("/api/resume/structure", json={"resume_text": "张三"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert set(payload["data"]) == {
        "name",
        "education",
        "experiences",
        "skills",
        "projects",
        "degraded",
    }
    assert payload["data"]["name"] == "张三"
    assert payload["data"]["degraded"] is False


# ---------------------------------------------------------------------------
# interview：已接线（fake agent）
# ---------------------------------------------------------------------------
def test_interview_create_session_wired(monkeypatch):
    fake_agent = FakeInterviewAgent()
    monkeypatch.setattr(deps, "get_interview_agent", lambda: fake_agent)
    response = client.post(
        "/api/interview/sessions",
        json={"resume_text": "张三，计算机专业", "scene": "autumn"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert payload["data"]["session_id"] == "sess_test_1"
    assert payload["data"]["question"]["text"]
    assert payload["data"]["state"]["status"] == "active"


def test_interview_unknown_session_returns_3001(monkeypatch):
    class NotFoundAgent(FakeInterviewAgent):
        def get_session(self, session_id):
            raise ValueError(f"会话不存在: {session_id!r}")

    monkeypatch.setattr(deps, "get_interview_agent", lambda: NotFoundAgent())
    response = client.get("/api/interview/sessions/nope")
    assert response.status_code == 404
    payload = response.json()
    assert payload["code"] == 3001
    assert payload["data"] is None


# ---------------------------------------------------------------------------
# report：已接线（无 Key → 2001；mock 服务 → 200）
# ---------------------------------------------------------------------------
def test_report_generate_without_key_returns_2001(monkeypatch):
    import backend.services.evaluation as evaluation

    def raise_missing_key(*args, **kwargs):
        raise MissingAPIKeyError("evaluation", "EVALUATION_API_KEY", ".env")

    monkeypatch.setattr(evaluation, "generate_report", raise_missing_key)
    response = client.post(
        "/api/report/generate",
        json={"session_id": "sess_test_1", "turns": [{"question": "q", "answer": "a"}]},
    )
    assert response.status_code == 502
    payload = response.json()
    assert payload["code"] == 2001
    assert payload["data"] is None


def test_report_generate_with_mocked_service_returns_200(monkeypatch):
    import backend.services.evaluation as evaluation

    mock_report = {
        "report_id": "rep_test_1",
        "evaluation": {"dimensions": [], "strengths": [], "weaknesses": [], "suggestions": []},
        "cross_review": {"verdicts": [], "overall_verdict": "agree", "summary": ""},
        "markdown": "# 面试报告\n",
    }
    monkeypatch.setattr(evaluation, "generate_report", lambda *args, **kwargs: mock_report)
    response = client.post(
        "/api/report/generate",
        json={"session_id": "sess_test_1", "turns": [{"question": "q", "answer": "a"}]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert payload["data"]["report_id"] == "rep_test_1"
    assert set(payload["data"]) == {"report_id", "evaluation", "cross_review", "markdown"}


# ---------------------------------------------------------------------------
# 通用壳 / 校验 / CORS
# ---------------------------------------------------------------------------
def test_validation_error_uses_unified_shell():
    response = client.post("/api/interview/sessions", json={})  # 缺 resume_text
    assert response.status_code == 400
    payload = response.json()
    assert payload["code"] == 1001
    assert payload["data"] is None
    assert "参数校验失败" in payload["message"]


def test_unknown_path_uses_unified_shell():
    response = client.get("/api/not-exist")
    assert response.status_code == 404
    payload = response.json()
    assert payload["code"] == 3001
    assert payload["data"] is None


def test_invalid_scene_rejected_by_validation():
    response = client.post(
        "/api/interview/sessions",
        json={"resume_text": "x", "scene": "not-a-scene"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == 1001


def test_cors_allows_vue_dev_origin():
    response = client.options(
        "/api/meta/routes",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") in {
        "http://localhost:5173",
        "*",
    }
