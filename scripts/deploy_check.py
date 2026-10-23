#!/usr/bin/env python3
"""部署前自检（阶段 5 · 部署与单进程演示）

检查项：
1. Python 版本（>= 3.10）；
2. 后端依赖是否可导入（fastapi / uvicorn / pydantic / pyyaml / openai / tiktoken /
   python-docx / python-multipart；pdfplumber 缺失时仅告警，pypdf 可兜底）；
3. `frontend/dist/index.html` 是否存在（单进程演示需要，可提前 pnpm build 后拷贝）；
4. 项目根 `.env` 是否存在；
5. 5 组模块 Key 环境变量**是否已配置（只报告有无，绝不打印任何值）**；
6. 模型路由配置（models.yaml / MODELS_CONFIG_PATH 临时配置）是否 5 条路由齐全、
   evaluation 与 cross_review 是否使用不同模型（决策④）；
7. 可选：HTTP 探活 `GET /health`。

退出码：0 = 全部通过（允许 WARN）；1 = 存在 FAIL。

用法：
    python scripts/deploy_check.py
    python scripts/deploy_check.py --health-url http://127.0.0.1:8000/health
    python scripts/deploy_check.py --env-file .tmp/no_key.env      # 调试“无 Key”场景

安全：本脚本**不会读取 / 打印任何 Key 的取值**，只判断“是否非空”。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

KEY_ENVS: tuple[str, ...] = (
    "RESUME_DIAGNOSIS_API_KEY",
    "JD_MATCHING_API_KEY",
    "INTERVIEW_AGENT_API_KEY",
    "EVALUATION_API_KEY",
    "CROSS_REVIEW_API_KEY",
)
BASE_URL_ENVS: tuple[str, ...] = (
    "RESUME_DIAGNOSIS_BASE_URL",
    "JD_MATCHING_BASE_URL",
    "INTERVIEW_AGENT_BASE_URL",
    "EVALUATION_BASE_URL",
    "CROSS_REVIEW_BASE_URL",
)
ROUTES: tuple[str, ...] = (
    "resume_diagnosis",
    "jd_matching",
    "interview_agent",
    "evaluation",
    "cross_review",
)
REQUIRED_MODULES: tuple[tuple[str, str], ...] = (
    ("fastapi", "FastAPI"),
    ("uvicorn", "Uvicorn"),
    ("pydantic", "Pydantic v2"),
    ("yaml", "PyYAML"),
    ("openai", "OpenAI 兼容 SDK"),
    ("tiktoken", "tiktoken"),
    ("docx", "python-docx"),
    ("multipart", "python-multipart"),
)
OPTIONAL_MODULES: tuple[tuple[str, str], ...] = (
    ("pdfplumber", "PDF 解析主力（缺失时 pypdf 兜底）"),
    ("pypdf", "PDF 解析兜底"),
)


class Checker:
    """收集检查结果并统一输出（PASS / WARN / FAIL）。"""

    def __init__(self) -> None:
        self.results: list[tuple[str, str, str]] = []

    def _add(self, level: str, name: str, detail: str = "") -> None:
        self.results.append((level, name, detail))
        suffix = f" — {detail}" if detail else ""
        print(f"[{level}] {name}{suffix}")

    def ok(self, name: str, detail: str = "") -> None:
        self._add("PASS", name, detail)

    def warn(self, name: str, detail: str = "") -> None:
        self._add("WARN", name, detail)

    def fail(self, name: str, detail: str = "") -> None:
        self._add("FAIL", name, detail)

    def summary(self) -> int:
        counts = {level: sum(1 for item in self.results if item[0] == level) for level in ("PASS", "WARN", "FAIL")}
        print()
        print(
            f"自检汇总：PASS={counts['PASS']}  WARN={counts['WARN']}  FAIL={counts['FAIL']}"
        )
        if counts["FAIL"] == 0:
            print("部署自检通过。")
            return 0
        print("部署自检未通过：请先修复上述 FAIL 项。")
        return 1


def read_env_file(path: Path) -> dict[str, str]:
    """解析 .env 为 dict（仅内存使用；调用方不得打印 value）。"""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return values
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def is_configured(name: str, file_env: dict[str, str]) -> bool:
    """环境变量 / .env 中是否非空（只判断有无，不返回取值）。"""
    return bool((file_env.get(name) or "").strip() or (os.environ.get(name) or "").strip())


def resolve_models_config(file_env: dict[str, str]) -> tuple[Path, bool]:
    """返回 (配置路径, 是否使用 MODELS_CONFIG_PATH 临时覆盖)。"""
    raw = (os.environ.get("MODELS_CONFIG_PATH") or file_env.get("MODELS_CONFIG_PATH") or "").strip()
    if not raw:
        return ROOT / "backend" / "config" / "models.yaml", False
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    return path, True


def check_python(checker: Checker) -> None:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 10):
        checker.ok("Python 版本", f"{version}（>= 3.10）")
    else:
        checker.fail("Python 版本", f"{version} 过低，需要 Python >= 3.10")


def check_dependencies(checker: Checker) -> None:
    missing: list[str] = []
    for module, label in REQUIRED_MODULES:
        if importlib.util.find_spec(module) is None:
            missing.append(f"{label}({module})")
    if missing:
        checker.fail("后端依赖", "缺少：" + "、".join(missing) + "；请执行 pip install -r requirements.txt")
    else:
        checker.ok("后端依赖", f"必需依赖齐全（{len(REQUIRED_MODULES)} 个）")
    missing_optional: list[str] = []
    for module, label in OPTIONAL_MODULES:
        if importlib.util.find_spec(module) is None:
            missing_optional.append(f"{label}({module})")
    if missing_optional:
        checker.warn("可选依赖", "缺少：" + "、".join(missing_optional))
    else:
        checker.ok("可选依赖", "PDF 解析依赖齐全")


def check_frontend_dist(checker: Checker) -> None:
    index = ROOT / "frontend" / "dist" / "index.html"
    if index.is_file():
        checker.ok("前端构建产物", str(index.relative_to(ROOT)))
    else:
        checker.fail(
            "前端构建产物",
            "缺少 frontend/dist/index.html；请执行 scripts/run_demo.ps1 自动构建，"
            "或在 frontend/ 执行 pnpm build 后重试（仅 API 部署可忽略）",
        )


def check_env_file(checker: Checker, env_path: Path) -> dict[str, str]:
    file_env = read_env_file(env_path)
    if env_path.is_file():
        checker.ok(".env 文件", str(env_path.relative_to(ROOT)) if env_path.is_relative_to(ROOT) else str(env_path))
    else:
        checker.fail(
            ".env 文件",
            f"未找到 {env_path}；请复制 .env.example 为 .env 后填写（.env 不进库）",
        )
    return file_env


def check_keys(checker: Checker, file_env: dict[str, str]) -> None:
    for name in KEY_ENVS:
        if is_configured(name, file_env):
            checker.ok(f"Key 已配置：{name}", "（不显示值）")
        else:
            checker.fail(
                f"Key 未配置：{name}",
                "对应模块调用会返回 code=2001；请在 .env 中配置（禁止写入代码/文档）",
            )
    unset_base_urls = [name for name in BASE_URL_ENVS if not is_configured(name, file_env)]
    if unset_base_urls:
        checker.warn(
            "Base URL 未显式配置",
            "、".join(unset_base_urls) + "（代码按模型名前缀使用默认供应商地址）",
        )
    else:
        checker.ok("Base URL", "5 组 Base URL 均已配置")


def check_models_config(checker: Checker, file_env: dict[str, str]) -> None:
    config_path, overridden = resolve_models_config(file_env)
    label = str(config_path.relative_to(ROOT)) if config_path.is_relative_to(ROOT) else str(config_path)
    if not config_path.is_file():
        checker.fail("模型路由配置", f"文件不存在：{label}")
        return
    if overridden:
        checker.warn("模型路由配置", f"当前使用 MODELS_CONFIG_PATH 临时配置：{label}")
    else:
        checker.ok("模型路由配置", label)
    try:
        import yaml  # noqa: PLC0415 - 依赖检查已保证可导入
    except ImportError:  # pragma: no cover - 依赖检查已覆盖
        return
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - YAML 解析错误统一报失败
        checker.fail("模型路由配置解析", f"YAML 解析失败：{type(exc).__name__}")
        return
    if not isinstance(raw, dict):
        checker.fail("模型路由配置解析", "顶层不是映射结构")
        return
    missing = [route for route in ROUTES if route not in raw]
    if missing:
        checker.fail("模型路由条目", "缺少：" + "、".join(missing))
        return
    checker.ok("模型路由条目", f"5 条路由齐全（{label}）")
    evaluation = raw.get("evaluation") or {}
    cross_review = raw.get("cross_review") or {}
    eval_model = str(evaluation.get("model", "")) if isinstance(evaluation, dict) else ""
    cross_model = str(cross_review.get("model", "")) if isinstance(cross_review, dict) else ""
    if eval_model and cross_model and eval_model == cross_model:
        checker.fail(
            "决策④校验",
            f"evaluation 与 cross_review 使用同一模型 {eval_model}，违反“评分与复核异模型”",
        )
    else:
        checker.ok("决策④校验", "evaluation 与 cross_review 使用不同模型")


def check_health(checker: Checker, health_url: str | None) -> None:
    if not health_url:
        checker.warn("HTTP 探活", "未指定 --health-url，跳过 /health 探活")
        return
    try:
        with urllib.request.urlopen(health_url, timeout=5) as response:  # noqa: S310 - 本地 URL
            status = getattr(response, "status", response.getcode())
            body = response.read(4096).decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        checker.fail("HTTP 探活", f"{health_url} 不可达：{exc.reason}")
        return
    except OSError as exc:  # 超时等
        checker.fail("HTTP 探活", f"{health_url} 请求失败：{exc}")
        return
    if status != 200:
        checker.fail("HTTP 探活", f"{health_url} 返回 HTTP {status}")
        return
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        checker.warn("HTTP 探活", f"{health_url} 返回 200，但响应不是 JSON")
        return
    if isinstance(payload, dict) and payload.get("status") == "ok":
        checker.ok("HTTP 探活", f"{health_url} → status=ok")
    else:
        checker.warn("HTTP 探活", f"{health_url} 返回 200，但未包含 status=ok")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="部署前自检（不打印任何 Key 值）")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=ROOT / ".env",
        help="指定 .env 路径（默认项目根 .env；调试“无 Key”场景用）",
    )
    parser.add_argument(
        "--health-url",
        default=None,
        help="可选 HTTP 探活地址，如 http://127.0.0.1:8000/health",
    )
    args = parser.parse_args(argv)

    print("=" * 60)
    print("AI 模拟面试与简历诊断助手 · 部署前自检")
    print(f"项目根目录：{ROOT}")
    print("=" * 60)

    checker = Checker()
    check_python(checker)
    check_dependencies(checker)
    check_frontend_dist(checker)
    env_path = args.env_file if args.env_file.is_absolute() else (ROOT / args.env_file)
    file_env = check_env_file(checker, env_path)
    check_keys(checker, file_env)
    check_models_config(checker, file_env)
    check_health(checker, args.health_url)
    return checker.summary()


if __name__ == "__main__":
    sys.exit(main())
