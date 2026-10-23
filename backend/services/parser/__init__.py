"""简历解析（v2 · 阶段 1 实现）：PDF / DOCX / TXT → 纯文本 + 分段。

对外统一入口::

    from backend.services.parser import parse_resume, ParserError

    doc = parse_resume("resume.pdf", data)      # 或 parse_resume(path)
    doc.text; doc.segments; doc.degraded; doc.warnings; doc.char_count; doc.to_dict()

契约见 docs/api.md「三、模块 A」与「6.3 parser」。
"""
from __future__ import annotations

from pathlib import Path

from .base import (
    ParsedResume,
    ParserError,
    ResumeSegment,
    UnsupportedResumeFormatError,
    build_segments,
    decode_text,
    is_heading,
    looks_garbled,
)
from .docx_parser import parse_docx
from .pdf_parser import parse_pdf

SUPPORTED_SUFFIXES: tuple[str, ...] = (".pdf", ".docx", ".txt", ".md")


def parse_resume(filename: str | Path, data: bytes | None = None) -> ParsedResume:
    """按扩展名分派解析器，返回统一解析结果。

    Args:
        filename: 文件名或路径（用于判断格式；已存在且 data 为 None 时直接读取）。
        data: 原始字节（FastAPI UploadFile 内容）；提供后不再读磁盘。
    """
    path = Path(filename)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise UnsupportedResumeFormatError(
            f"不支持的文件格式 {suffix or '(无后缀)'!r}，"
            f"仅支持 {' / '.join(item.lstrip('.').upper() for item in SUPPORTED_SUFFIXES)}"
        )
    if data is None:
        if not path.is_file():
            raise ParserError(f"文件不存在：{path}")
        if path.stat().st_size == 0:
            raise ParserError(f"文件内容为空：{path.name}")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ParserError(f"读取文件失败：{path.name}（{exc}）") from exc
    if not data:
        raise ParserError("文件内容为空")

    if suffix == ".pdf":
        return parse_pdf(data, filename=path.name)
    if suffix == ".docx":
        return parse_docx(data, filename=path.name)

    text = decode_text(data).strip()
    if not text:
        raise ParserError("文本文件内容为空")
    return ParsedResume(
        filename=path.name,
        kind="text",
        text=text,
        segments=build_segments(text.splitlines()),
    )


__all__ = [
    "SUPPORTED_SUFFIXES",
    "ParsedResume",
    "ParserError",
    "ResumeSegment",
    "UnsupportedResumeFormatError",
    "build_segments",
    "decode_text",
    "is_heading",
    "looks_garbled",
    "parse_docx",
    "parse_pdf",
    "parse_resume",
]
