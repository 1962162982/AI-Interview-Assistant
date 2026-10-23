"""evaluation/ 的 pytest 公共配置（模块 D + 评测回归关卡）。

注意：
- 统一把仓库根目录与 evaluation/ 加入 sys.path，使测试可直接
  `import run_eval` / `from backend.services import evaluation`；
- 本仓库运行在受限沙箱中，系统临时目录可能不可写：统一提供工作区内的
  `tmp_dir` fixture（evaluation/ 下创建、测试结束清理），不使用 pytest 默认 tmp_path。
"""
from __future__ import annotations

import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent

for _path in (REPO_ROOT, EVAL_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

# 忽略历史残留的不可读临时目录（受限沙箱创建、当前进程无权限读取/删除）
collect_ignore_glob = ["eval-tmp-*", "pytest-cache-files-*", "probe-*"]


@pytest.fixture()
def tmp_dir() -> Iterator[Path]:
    """在 evaluation/ 下创建临时目录（测试结束后删除）。"""
    path = EVAL_DIR / f"eval-tmp-{uuid4().hex[:8]}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
