"""简历解析公共数据结构（阶段 1 实现）

PDF / DOCX 解析器共用的返回值与异常定义，避免解析器之间循环导入。

对外契约（docs/api.md `POST /api/resume/parse`）：
    doc = parse_resume("resume.pdf", data)
    doc.text / doc.segments / doc.degraded / doc.warnings / doc.char_count
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# 简历常见小节标题关键词（用于把纯文本切成“分段”）
HEADING_KEYWORDS: tuple[str, ...] = (
    "教育",
    "工作经历",
    "实习",
    "项目",
    "技能",
    "专业",
    "校园经历",
    "实践经历",
    "科研",
    "论文",
    "获奖",
    "荣誉",
    "证书",
    "自我评价",
    "个人信息",
    "求职意向",
    "研究方向",
    "其他",
)

_PUNCTUATION = "。，；：！？,.;:!?、"
_HEADING_MAX_LEN = 20

# 乱码检测：可打印 ASCII / CJK / 常见符号之外的字符占比
_PRINTABLE_RE = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3000-\u303f\u3040-\u30ff"
    r"\uff00-\uffef\u0020-\u007e\u00a0-\u00ff\u2000-\u206f\n\r\t]"
)


class ParserError(ValueError):
    """简历解析异常基类（API 层应映射为 code=1002）。"""


class UnsupportedResumeFormatError(ParserError):
    """不支持的文件格式。"""


@dataclass
class ResumeSegment:
    """简历文本分段（heading 为空表示无明确小节的正文）。"""

    heading: str
    text: str


@dataclass
class ParsedResume:
    """解析结果（PDF / DOCX / TXT 统一结构）。"""

    filename: str
    kind: str
    text: str
    segments: list[ResumeSegment] = field(default_factory=list)
    page_count: int | None = None
    degraded: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return len(self.text)

    def to_dict(self) -> dict:
        """序列化为 JSON 友好结构（API 层响应 data 字段）。"""
        return {
            "filename": self.filename,
            "kind": self.kind,
            "text": self.text,
            "segments": [
                {"heading": segment.heading, "text": segment.text}
                for segment in self.segments
            ],
            "page_count": self.page_count,
            "char_count": self.char_count,
            "degraded": self.degraded,
            "warnings": list(self.warnings),
        }


def is_heading(line: str) -> bool:
    """判断一行是否像简历小节标题（短、无标点、命中关键词）。"""
    stripped = line.strip()
    if not stripped or len(stripped) > _HEADING_MAX_LEN:
        return False
    if any(ch in _PUNCTUATION for ch in stripped):
        return False
    return any(keyword in stripped for keyword in HEADING_KEYWORDS)


def build_segments(paragraphs: list[str]) -> list[ResumeSegment]:
    """把段落按小节标题分组为分段（标题行本身不计入正文）。"""
    segments: list[ResumeSegment] = []
    current_heading = ""
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        if buffer:
            segments.append(ResumeSegment(heading=current_heading, text="\n".join(buffer)))
            buffer = []

    for paragraph in paragraphs:
        text = paragraph.strip()
        if not text:
            continue
        if is_heading(text):
            flush()
            current_heading = text
            continue
        buffer.append(text)
    flush()
    return segments


def looks_garbled(text: str, threshold: float = 0.05) -> bool:
    """粗略判断提取文本是否大量乱码（控件符 / 替换符占比过高）。"""
    if not text:
        return False
    non_printable = sum(1 for ch in text if not _PRINTABLE_RE.match(ch))
    return non_printable / max(1, len(text)) > threshold


def decode_text(data: bytes) -> str:
    """纯文本解码：优先 UTF-8，失败回退 GBK（Windows 常见）。"""
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_source(source: str | Path | bytes, *, filename: str | None = None) -> tuple[bytes, str]:
    """统一读取输入：路径或 bytes；返回 (原始字节, 文件名)。"""
    if isinstance(source, bytes):
        return source, filename or "resume"
    path = Path(source)
    if not path.is_file():
        raise ParserError(f"文件不存在：{path}")
    if path.stat().st_size == 0:
        raise ParserError(f"文件内容为空：{path.name}")
    try:
        return path.read_bytes(), filename or path.name
    except OSError as exc:
        raise ParserError(f"读取文件失败：{path.name}（{exc}）") from exc


__all__ = [
    "HEADING_KEYWORDS",
    "ParsedResume",
    "ParserError",
    "ResumeSegment",
    "UnsupportedResumeFormatError",
    "build_segments",
    "decode_text",
    "is_heading",
    "looks_garbled",
    "read_source",
]
