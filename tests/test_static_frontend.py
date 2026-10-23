"""前端静态托管 + CORS 测试（T9 · 阶段 3）

覆盖（**不依赖真实 `frontend/dist`**，用临时目录伪造并 monkeypatch）：
- `frontend/dist` 存在时：`/` 与 SPA 路由回退到 `index.html`；`/assets/*` 返回静态文件；
- dist 内真实文件（如 favicon）直接返回；缺失资产 → 404（不回退 index.html）；
- `/api/*`、`/health`、`/docs`、`/openapi.json` **不被 SPA 劫持**；
- `frontend/dist` 缺失时行为不变：未知路径 404，API / 健康检查仍可用；
- 路径穿越（`..%2F..%2F`）不会逃出 dist 目录；
- CORS：默认放行 5173 / 3000；`CORS_ORIGINS` 可覆盖。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import main as backend_main
from backend.main import app

client = TestClient(app)

INDEX_MARKER = "<title>AI 面试助手（测试 dist）</title>"
INDEX_HTML = f"<!doctype html><html><head>{INDEX_MARKER}</head><body>app</body></html>"
ASSET_JS = "console.log('static asset ok');"


# ---------------------------------------------------------------------------
# fixtures：伪造前端构建产物
# ---------------------------------------------------------------------------
@pytest.fixture()
def fake_dist(tmp_dir: Path, monkeypatch) -> Path:
    dist = tmp_dir / "frontend-dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (assets / "app.js").write_text(ASSET_JS, encoding="utf-8")
    (assets / "app.css").write_text("body{margin:0}", encoding="utf-8")
    (dist / "favicon.ico").write_bytes(b"\x00\x01")
    monkeypatch.setattr(backend_main, "get_frontend_dist_dir", lambda: dist)
    return dist


# ---------------------------------------------------------------------------
# dist 存在：静态资源与 SPA 回退
# ---------------------------------------------------------------------------
def test_root_serves_index_html(fake_dist):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert INDEX_MARKER in response.text


def test_asset_files_are_served(fake_dist):
    js = client.get("/assets/app.js")
    assert js.status_code == 200
    assert ASSET_JS in js.text
    assert "javascript" in js.headers["content-type"].lower()

    css = client.get("/assets/app.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"].lower()


def test_spa_routes_fall_back_to_index(fake_dist):
    for path in ("/interview", "/report/rep_123", "/some/deep/unknown/route"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert INDEX_MARKER in response.text, path


def test_existing_dist_file_is_served_directly(fake_dist):
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.content == b"\x00\x01"


def test_missing_asset_returns_404_without_spa_fallback(fake_dist):
    response = client.get("/assets/not-exist.js")
    assert response.status_code == 404
    assert INDEX_MARKER not in response.text


def test_path_traversal_does_not_escape_dist(fake_dist):
    response = client.get("/assets/..%2F..%2F.env")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 保留路径不被 SPA 劫持
# ---------------------------------------------------------------------------
def test_api_is_not_hijacked(fake_dist):
    # 已实现接口正常返回 JSON 响应壳
    response = client.get("/api/meta/routes")
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    assert INDEX_MARKER not in response.text

    # 未定义的 /api 路径：404 统一壳，而不是 index.html
    unknown = client.get("/api/not-exist")
    assert unknown.status_code == 404
    assert INDEX_MARKER not in unknown.text
    assert unknown.json()["code"] == 3001

    # 非 GET 的 /api 请求也不受影响（不会被 SPA 返回 index.html）
    post_unknown = client.post("/api/not-exist")
    assert post_unknown.status_code in {404, 405}
    assert INDEX_MARKER not in post_unknown.text


def test_health_and_docs_are_not_hijacked(fake_dist):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    assert "paths" in openapi.json()
    assert INDEX_MARKER not in openapi.text


# ---------------------------------------------------------------------------
# dist 缺失：行为不变（404）
# ---------------------------------------------------------------------------
def test_missing_dist_keeps_404_behavior(monkeypatch):
    monkeypatch.setattr(backend_main, "get_frontend_dist_dir", lambda: None)
    for path in ("/", "/assets/app.js", "/some/spa/route"):
        response = client.get(path)
        assert response.status_code == 404, path
        assert INDEX_MARKER not in response.text

    # API 与健康检查不受影响
    assert client.get("/health").status_code == 200
    assert client.get("/api/meta/routes").status_code == 200


# ---------------------------------------------------------------------------
# CORS：默认放行 5173 / 3000，CORS_ORIGINS 可覆盖
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "origin",
    [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ],
)
def test_cors_preflight_allows_dev_origins(origin):
    response = client.options(
        "/api/meta/routes",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") in {origin, "*"}


def test_cors_origins_env_override(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://example.com, https://demo.example.org")
    assert backend_main._cors_origins() == ["http://example.com", "https://demo.example.org"]


def test_cors_origins_default_when_env_missing(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    origins = backend_main._cors_origins()
    assert "http://127.0.0.1:5173" in origins
    assert "http://localhost:3000" in origins
