"""契约一致性守卫（T7）：docs/api.md ↔ FastAPI 路由 ↔ 前端 API 调用

目标：防止「文档契约 ↔ 代码路由 ↔ 前端调用」三者漂移。
**只读解析**，不修改 `docs/api.md` / `backend/api` / `frontend` 代码；不依赖网络与 Key。

覆盖：
1) docs/api.md 中 `### METHOD /path` 端点 ↔ `backend.main.app` 实际路由：双向集合相等；
2) frontend/src/api/client.js 中出现的 `/api/...` 调用路径 ⊆ 文档端点（允许前端只实现部分）；
3) docs 若声明接口数量则与 `/api` 路由数核对（当前文档未声明数量 → 跳过，见同名测试注释）。

实现说明（FastAPI 0.16x）：`include_router()` 以 `_IncludedRouter` 懒挂载，`app.routes`
不再展开子路由（只剩 /health 与内置文档路由）。因此路由集合以 `app.routes` 为主、
`app.openapi()["paths"]` 为兜底——两者由同一路由表生成，OpenAPI 天然不含 /docs 等内置路径。
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from pathlib import Path

import pytest

from backend.main import app

ROOT = Path(__file__).resolve().parents[1]
DOCS_API = ROOT / "docs" / "api.md"
FRONTEND_CLIENT = ROOT / "frontend" / "src" / "api" / "client.js"

HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})

# FastAPI 内置的文档 / OpenAPI 路径（不属于业务契约，需排除）
INTERNAL_PATHS = frozenset({"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"})

# docs/api.md 中的端点标题：`### GET /api/meta/routes`
_ENDPOINT_HEADING_RE = re.compile(r"^###\s+(?P<method>[A-Z]+)\s+(?P<path>/\S*)", re.MULTILINE)
# 路径参数规范化：{session_id} / {report_id} / {sid} → {param}
_PATH_PARAM_RE = re.compile(r"\{[^}/]+\}")
# docs 中声明的接口数量（如「15 条契约接口」「接口数量：15」）
_DECLARED_COUNT_RE = re.compile(r"(\d+)\s*条(?:契约)?接口|接口(?:总数|数量)[：:]\s*(\d+)")

Endpoint = tuple[str, str]  # (METHOD, /path)


# ---------------------------------------------------------------------------
# 路径与格式化辅助
# ---------------------------------------------------------------------------
def _normalize_path(path: str) -> str:
    """规范化路径参数与格式：{xxx}→{param}、去 query/hash、合并重复斜杠、去尾斜杠。"""
    path = path.strip().split("?")[0].split("#")[0]
    path = _PATH_PARAM_RE.sub("{param}", path)
    path = re.sub(r"/+", "/", path)
    if len(path) > 1:
        path = path.rstrip("/")
    return path


def _format_endpoints(endpoints: Iterable[Endpoint]) -> str:
    lines = [f"  - {method} {path}" for method, path in sorted(endpoints)]
    return "\n".join(lines) if lines else "  （空）"


def _format_paths(paths: Iterable[str]) -> str:
    lines = [f"  - {path}" for path in sorted(paths)]
    return "\n".join(lines) if lines else "  （空）"


# ---------------------------------------------------------------------------
# 1) docs/api.md 解析
# ---------------------------------------------------------------------------
def load_doc_endpoints(text: str | None = None) -> set[Endpoint]:
    """解析 docs/api.md 中的 `### METHOD /path` 端点标题。"""
    content = DOCS_API.read_text(encoding="utf-8") if text is None else text
    endpoints: set[Endpoint] = set()
    for match in _ENDPOINT_HEADING_RE.finditer(content):
        method = match.group("method").upper()
        if method not in HTTP_METHODS:
            continue
        endpoints.add((method, _normalize_path(match.group("path"))))
    return endpoints


# ---------------------------------------------------------------------------
# 2) FastAPI 实际路由解析
# ---------------------------------------------------------------------------
def _iter_direct_routes(routes: Iterable[object]) -> Iterator[Endpoint]:
    """从 app.routes 直接读取已展开的路由（旧版 FastAPI / 顶层路由）。

    `include_in_schema=False` 的路由不算 HTTP 契约（如阶段 3 前端静态托管的
    `/assets/*` 与 SPA 回退 catch-all），需排除，否则会污染契约集合。
    """
    for route in routes:
        if getattr(route, "include_in_schema", True) is False:
            continue
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods or path in INTERNAL_PATHS:
            continue
        for method in methods:
            if str(method).upper() in HTTP_METHODS:
                yield str(method).upper(), _normalize_path(path)


def load_app_endpoints(fastapi_app=app) -> set[Endpoint]:
    """收集应用实际暴露的端点集合（排除 /docs、/redoc、/openapi.json 等内置路径）。"""
    endpoints: set[Endpoint] = set(_iter_direct_routes(fastapi_app.routes))
    # 兜底：FastAPI 0.16x 的 _IncludedRouter 懒挂载使 app.routes 不含子路由
    for path, operations in fastapi_app.openapi().get("paths", {}).items():
        if path in INTERNAL_PATHS:
            continue
        for method in operations:
            if isinstance(method, str) and method.upper() in HTTP_METHODS:
                endpoints.add((method.upper(), _normalize_path(path)))
    return endpoints


# ---------------------------------------------------------------------------
# 3) 前端 client.js 解析
# ---------------------------------------------------------------------------
def _strip_js_comments(code: str) -> str:
    """去掉 JS 行注释与块注释（字符串内的 // 与 /* 不受影响）。"""
    out: list[str] = []
    index, length = 0, len(code)
    quote: str | None = None
    while index < length:
        char = code[index]
        if quote is not None:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(code[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in ("'", '"', "`"):
            quote = char
            out.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < length and code[index + 1] == "/":
            while index < length and code[index] != "\n":
                index += 1
            continue
        if char == "/" and index + 1 < length and code[index + 1] == "*":
            index += 2
            while index + 1 < length and not (code[index] == "*" and code[index + 1] == "/"):
                index += 1
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _iter_js_strings(code: str) -> Iterator[str]:
    """迭代 JS 字符串字面量内容（单引号 / 双引号 / 模板字符串）。"""
    index, length = 0, len(code)
    while index < length:
        char = code[index]
        if char not in ("'", '"', "`"):
            index += 1
            continue
        quote = char
        index += 1
        buffer: list[str] = []
        while index < length:
            char = code[index]
            if char == "\\" and index + 1 < length:
                buffer.append(code[index + 1])
                index += 2
                continue
            if char == quote:
                index += 1
                break
            buffer.append(char)
            index += 1
        yield "".join(buffer)


def load_frontend_paths(text: str | None = None) -> set[str]:
    """解析 client.js 中出现的 `/api/...` 调用路径（模板变量 `${...}` → {param}）。"""
    content = FRONTEND_CLIENT.read_text(encoding="utf-8") if text is None else text
    code = _strip_js_comments(content)
    paths: set[str] = set()
    for literal in _iter_js_strings(code):
        normalized = re.sub(r"\$\{[^}]*\}", "{param}", literal)
        match = re.search(r"/api/(?:[A-Za-z0-9_\-{}/]*)", normalized)
        if not match:
            continue
        paths.add(_normalize_path(match.group(0)))
    return paths


# ---------------------------------------------------------------------------
# 测试：解析器自检
# ---------------------------------------------------------------------------
def test_docs_parser_finds_endpoints():
    """防止解析器静默失效：文档必须能解析出端点，且数量符合冻结契约规模。"""
    endpoints = load_doc_endpoints()
    assert endpoints, "docs/api.md 中未解析到任何 `### METHOD /path` 端点（文档格式或解析器漂移）"
    assert len(endpoints) >= 15, (
        f"docs/api.md 解析出的端点数异常偏少：{len(endpoints)}\n{_format_endpoints(endpoints)}"
    )


def test_app_parser_finds_endpoints():
    """防止 FastAPI 路由读取静默失效（懒挂载 / 版本升级）。"""
    endpoints = load_app_endpoints()
    assert endpoints, "backend.main.app 未解析到任何端点（路由挂载或解析器漂移）"
    assert any(path.startswith("/api") for _, path in endpoints), (
        f"未解析到任何 /api 路由：\n{_format_endpoints(endpoints)}"
    )
    assert not any(path in INTERNAL_PATHS for _, path in endpoints), (
        f"内置文档路径未被排除：\n{_format_endpoints(endpoints & {('GET', p) for p in INTERNAL_PATHS})}"
    )


# ---------------------------------------------------------------------------
# 测试：文档 ↔ 代码 双向集合相等
# ---------------------------------------------------------------------------
def test_doc_and_app_endpoints_are_identical():
    doc_endpoints = load_doc_endpoints()
    app_endpoints = load_app_endpoints()
    missing_in_app = doc_endpoints - app_endpoints
    missing_in_doc = app_endpoints - doc_endpoints
    assert not missing_in_app and not missing_in_doc, (
        "docs/api.md 与 FastAPI 实际路由不一致：\n"
        f"[文档有、代码缺失] {len(missing_in_app)} 条：\n{_format_endpoints(missing_in_app)}\n"
        f"[代码有、文档缺失] {len(missing_in_doc)} 条：\n{_format_endpoints(missing_in_doc)}"
    )
    assert doc_endpoints == app_endpoints


# ---------------------------------------------------------------------------
# 测试：前端调用 ⊆ 文档端点
# ---------------------------------------------------------------------------
def test_frontend_calls_are_documented():
    frontend_paths = load_frontend_paths()
    assert frontend_paths, (
        "未从 frontend/src/api/client.js 解析到任何 `/api/...` 调用路径"
        "（前端实现或解析器需同步更新）"
    )
    documented_paths = {path for _, path in load_doc_endpoints()}
    undocumented = frontend_paths - documented_paths
    assert not undocumented, (
        "前端调用了 docs/api.md 未定义的接口：\n"
        f"{_format_paths(undocumented)}\n"
        f"[前端已调用] {len(frontend_paths)} 条；[文档已定义] {len(documented_paths)} 条"
    )


# ---------------------------------------------------------------------------
# 测试：文档声明的接口数量（未声明则跳过）
# ---------------------------------------------------------------------------
def test_declared_endpoint_count_matches_app_routes():
    """按 T7 任务口径：docs 若声明接口数量，则与 `/api` 路由数核对。

    当前 docs/api.md 未声明「N 条接口」（顶部仅说明 /health 与 /api 前缀约定），
    故按任务要求跳过该子断言，并在本 docstring 与 skip 原因中说明。
    """
    content = DOCS_API.read_text(encoding="utf-8")
    match = _DECLARED_COUNT_RE.search(content)
    if match is None:
        pytest.skip("docs/api.md 未声明接口数量（T7 允许跳过该子断言）")
    declared = int(next(group for group in match.groups() if group))
    api_paths = {path for _, path in load_app_endpoints() if path.startswith("/api")}
    assert declared == len(api_paths), (
        f"docs/api.md 声明 {declared} 条接口，实际 /api 路由 {len(api_paths)} 条：\n"
        f"{_format_paths(api_paths)}"
    )
