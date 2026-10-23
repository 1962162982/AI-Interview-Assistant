"""Token 计数（v2 · 阶段 1 实现）

职责：估算文本 / messages 的 token 总量，供 context_manager 判断是否触发摘要压缩。

策略：
1. 优先 tiktoken（`cl100k_base`）：DeepSeek / 智谱 GLM 均未公开 tokenizer，
   使用 cl100k_base 做近似估算（中文场景偏差可控，仅用于阈值判断）；
2. tiktoken 不可用（未安装 / 首次使用需联网下载编码表失败）时，
   自动降级为**字符级估算**，保证离线单测与演示不中断；
3. 降级只影响阈值判断精度，不影响功能可用性。

调用约定：本项目 messages 为 OpenAI 兼容格式 list[{"role","content"}]，
`content` 可为 str，也可为多段内容 list（按 text 字段累计）。
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

DEFAULT_ENCODING = "cl100k_base"

# 每条消息的固定结构开销（OpenAI chat 格式经验值）
MESSAGE_OVERHEAD_TOKENS = 4
# 整个会话的回复引导开销
REPLY_PRIMING_TOKENS = 3

# CJK 字符区间（汉字 / 扩展 A / 兼容区 / 日文假名）
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x3040, 0x30FF),
)

_ENCODING: Any = None
_ENCODING_TRIED = False


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def _load_encoding() -> Any:
    """懒加载 tiktoken 编码器；失败返回 None（不抛错，避免拖垮主流程）。"""
    global _ENCODING, _ENCODING_TRIED
    if _ENCODING_TRIED:
        return _ENCODING
    _ENCODING_TRIED = True
    try:  # pragma: no cover - 环境相关，离线时走 except 分支
        import tiktoken

        _ENCODING = tiktoken.get_encoding(DEFAULT_ENCODING)
    except Exception:  # noqa: BLE001 - 任何编码器加载失败都降级
        _ENCODING = None
    return _ENCODING


def encoding_available() -> bool:
    """tiktoken 编码器是否可用（不可用时 count_tokens 走字符估算）。"""
    return _load_encoding() is not None


def reset_encoding_cache() -> None:
    """清空编码器缓存（仅供测试模拟“无网降级”场景）。"""
    global _ENCODING, _ENCODING_TRIED
    _ENCODING = None
    _ENCODING_TRIED = False


def estimate_tokens(text: str) -> int:
    """字符级估算（无 tiktoken 时的降级路径）。

    中文按约 1.5 字/token、其他字符按约 4 字符/token 估算。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if _is_cjk(ch))
    others = len(text) - cjk
    return max(1, math.ceil(cjk / 1.5 + others / 4.0))


def count_tokens(text: str, model: str | None = None) -> int:
    """统计一段文本的 token 数。

    Args:
        text: 待统计文本（None 视为空串）。
        model: 目标模型名，当前仅用于未来扩展（DeepSeek / GLM 统一用 cl100k_base 近似）。
    """
    if not text:
        return 0
    encoder = _load_encoding()
    if encoder is None:
        return estimate_tokens(text)
    try:
        return len(encoder.encode(text, disallowed_special=()))
    except Exception:  # noqa: BLE001 - 编码异常同样降级
        return estimate_tokens(text)


def _content_to_text(content: Any) -> str:
    """把 message.content 规整为纯文本（兼容多段内容列表）。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        text = content.get("text")
        return text if isinstance(text, str) else json.dumps(content, ensure_ascii=False)
    if isinstance(content, Sequence):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                elif item.get("type") == "image_url":
                    parts.append("[image]")
        return "\n".join(parts)
    return str(content)


def count_message_tokens(message: Mapping[str, Any], model: str | None = None) -> int:
    """统计单条 chat message 的 token（含固定结构开销）。"""
    total = MESSAGE_OVERHEAD_TOKENS
    total += count_tokens(_content_to_text(message.get("content")), model)
    name = message.get("name")
    if name:
        total += count_tokens(str(name), model)
    tool_calls = message.get("tool_calls")
    if tool_calls:
        total += count_tokens(json.dumps(tool_calls, ensure_ascii=False), model)
    return total


def count_messages_tokens(
    messages: Sequence[Mapping[str, Any]],
    model: str | None = None,
) -> int:
    """统计一组 messages 的总 token（用于上下文窗口判断）。"""
    if not messages:
        return 0
    return sum(count_message_tokens(message, model) for message in messages) + REPLY_PRIMING_TOKENS


def usage_ratio(
    messages: Sequence[Mapping[str, Any]],
    context_window_tokens: int,
    model: str | None = None,
) -> float:
    """消息占用上下文窗口的比例（0.0–1.0+），供 90% 阈值判断。"""
    if context_window_tokens <= 0:
        raise ValueError("context_window_tokens 必须为正整数")
    return count_messages_tokens(messages, model) / float(context_window_tokens)


def truncate_text_to_tokens(text: str, max_tokens: int, model: str | None = None) -> str:
    """按 token 上限截断文本（二分查找，尽量少裁剪字符）。"""
    if max_tokens <= 0 or not text:
        return ""
    if count_tokens(text, model) <= max_tokens:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if count_tokens(text[:mid], model) <= max_tokens:
            low = mid
        else:
            high = mid - 1
    return text[:low]
