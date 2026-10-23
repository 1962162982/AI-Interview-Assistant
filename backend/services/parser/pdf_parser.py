"""PDF 简历解析（v2 · 阶段 1 实现）

- 主力：pdfplumber（`page.extract_text()` + `page.extract_tables()` 处理表格）；
- 兜底：pdfplumber 不可用时用 pypdf 提取文本（无表格能力，记 warning）；
- 扫描件 / 乱码：返回 `degraded=True` + `warnings`，API 层据此提示改用粘贴文本
  （契约：`POST /api/resume/parse`，code 仍为 0）。
- 损坏 / 加密 / 非法 PDF：抛 `ParserError`（API 层映射 code=1002）。
"""
from __future__ import annotations

import importlib.util
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

from .base import ParsedResume, ParserError, build_segments, looks_garbled, read_source

# 每页可读字符数低于该值视为“无有效文本”（扫描件常见）
MIN_CHARS_PER_PAGE = 30

# 可注入的提取器（测试 / 替换实现）：data -> (每页文本列表, 表格文本列表)
PdfExtractor = Callable[[bytes], tuple[list[str], list[str]]]


def _pdfplumber_available() -> bool:
    return importlib.util.find_spec("pdfplumber") is not None


def extract_with_pdfplumber(data: bytes) -> tuple[list[str], list[str]]:
    """用 pdfplumber 提取每页文本与表格（失败抛 ParserError）。"""
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - venv 已安装 pdfplumber
        raise ParserError("缺少 pdfplumber 依赖，请执行 pip install -r requirements.txt") from exc
    pages: list[str] = []
    tables: list[str] = []
    try:
        with pdfplumber.open(BytesIO(data)) as pdf:
            for page in pdf.pages:
                pages.append(page.extract_text() or "")
                for table in page.extract_tables() or []:
                    for row in table:
                        cells = [" ".join(str(cell or "").split()) for cell in row]
                        if any(cells):
                            tables.append(" | ".join(cells))
    except ParserError:
        raise
    except Exception as exc:  # noqa: BLE001 - 损坏 / 加密 / 非法 PDF 统一报解析失败
        raise ParserError(f"PDF 解析失败（文件可能损坏或加密）：{exc}") from exc
    return pages, tables


def extract_with_pypdf(data: bytes) -> tuple[list[str], list[str]]:
    """用 pypdf 提取每页文本（兜底通道；不支持表格）。"""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - venv 已安装 pypdf
        raise ParserError("缺少 pypdf 依赖，请执行 pip install -r requirements.txt") from exc
    try:
        reader = PdfReader(BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # noqa: BLE001
        raise ParserError(f"PDF 解析失败（文件可能损坏或加密）：{exc}") from exc
    return pages, []


def parse_pdf(
    source: str | Path | bytes,
    *,
    filename: str | None = None,
    extractor: PdfExtractor | None = None,
) -> ParsedResume:
    """解析 PDF 简历。

    Args:
        source: 文件路径或原始字节。
        filename: 覆盖结果中的文件名（source 为 bytes 时使用）。
        extractor: 自定义提取器（测试注入；默认 pdfplumber → pypdf 兜底）。
    """
    data, name = read_source(source, filename=filename)
    warnings: list[str] = []

    if extractor is not None:
        pages, tables = extractor(data)
    elif _pdfplumber_available():
        pages, tables = extract_with_pdfplumber(data)
    else:
        warnings.append("未安装 pdfplumber，已使用 pypdf 兜底（无法提取表格）")
        pages, tables = extract_with_pypdf(data)

    if not pages:
        raise ParserError("PDF 中没有可解析的页面")

    page_count = len(pages)
    readable_pages = sum(1 for page in pages if len(page.strip()) >= MIN_CHARS_PER_PAGE)
    body_text = "\n".join(page.strip() for page in pages if page.strip()).strip()
    table_text = "\n".join(tables).strip()
    text = "\n".join(part for part in (body_text, table_text) if part).strip()

    degraded = False
    if not text:
        degraded = True
        warnings.append("未提取到可读文本（疑似扫描件 / 图片 PDF），建议改用粘贴文本输入")
    elif readable_pages == 0:
        degraded = True
        warnings.append("各页可读文本过少（疑似扫描件），建议核对内容或改用粘贴文本输入")
    elif readable_pages < page_count:
        warnings.append(f"共 {page_count} 页，其中 {page_count - readable_pages} 页未提取到有效文本")
    if text and looks_garbled(text):
        degraded = True
        warnings.append("提取文本疑似乱码，建议核对或改用粘贴文本输入")

    segments = build_segments([line for line in body_text.splitlines() if line.strip()])
    return ParsedResume(
        filename=name,
        kind="pdf",
        text=text,
        segments=segments,
        page_count=page_count,
        degraded=degraded,
        warnings=warnings,
    )


__all__ = [
    "MIN_CHARS_PER_PAGE",
    "PdfExtractor",
    "extract_with_pdfplumber",
    "extract_with_pypdf",
    "parse_pdf",
]
