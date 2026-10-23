"""pytest 公共 fixture（T1 · 阶段 1）

注意：本仓库可能运行在受限沙箱中：
1. pytest 默认的 `tmp_path` 会在系统临时目录创建目录（可能无写权限）；
2. `tempfile.mkdtemp` 创建的目录在 Windows 沙箱下可能写入被拒。
因此统一提供**工作区内、由 Path.mkdir 创建**的临时目录 fixture，
测试结束自动清理，避免污染仓库。
"""
from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

TESTS_DIR = Path(__file__).resolve().parent

# 忽略历史残留的不可读临时目录（由受限沙箱创建、当前进程无权限读取/删除；
# 空目录不计入 git，仅避免 pytest 收集时报 PermissionError）
collect_ignore_glob = ["t1-tmp-*", "probe-*"]


@pytest.fixture()
def tmp_dir() -> Iterator[Path]:
    """在 tests/ 下创建临时目录（测试结束后删除）。"""
    path = TESTS_DIR / f"t1-tmp-{uuid4().hex[:8]}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
