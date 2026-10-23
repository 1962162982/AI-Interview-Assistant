"""统一上下文管理器（v2 · 阶段 1 实现）—— ⭐ 核心公共组件

职责（见实行清单阶段 1 / 说明书 6.5）：
- 维护 messages 数组（system / user / assistant）；
- 滑动窗口：只保留最近 N 轮完整对话；
- token 监控：估算上下文占用，接近窗口阈值（默认 90%）时触发摘要压缩；
- 摘要器可注入：默认规则式要点提取（不调模型、离线可用），
  模块 C 可注入 LLM 摘要函数（失败时自动回退规则式，保证面试不中断）。

典型用法::

    ctx = ContextManager.from_route("interview_agent", system_prompt=SYSTEM)
    ctx.add_user("请介绍一下你的项目")
    ctx.add_assistant("我做过……")
    resp = router.chat("interview_agent", ctx.build_messages())
    ctx.add_assistant(resp.text)
    if ctx.should_compress():      # 或 ctx.maybe_compress()
        ctx.compress(force=True)

设计边界：本模块只管“消息 + token + 摘要”，不感知面试业务状态
（当前题目 / 追问深度等由模块 C 的会话状态对象维护）。
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ..utils import token_counter

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.9
DEFAULT_WINDOW_TURNS = 10

# 摘要器签名：(被压缩的旧消息, 已有摘要) -> 新摘要文本
SummaryFn = Callable[[Sequence[Mapping[str, Any]], str | None], str]


def _snippet(text: str, limit: int = 120) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def default_summarizer(
    old_messages: Sequence[Mapping[str, Any]],
    previous_summary: str | None = None,
) -> str:
    """规则式兜底摘要：不是简单丢弃，而是保留旧对话的关键要点。

    不调用模型，保证离线 / 模型不可用时也能完成压缩；模块 C 可注入更强的
    LLM 摘要器替换它。摘要只保留最近 12 条要点，避免摘要自身无限膨胀。
    """
    lines: list[str] = []
    if previous_summary:
        lines.extend(line for line in previous_summary.strip().splitlines() if line.strip())
    for message in old_messages:
        role = str(message.get("role", "user"))
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        prefix = "候选人" if role == "user" else "面试官"
        lines.append(f"- {prefix}：{_snippet(content)}")
    return "\n".join(lines[-12:])


class ContextManager:
    """面试 / 长对话的上下文管理器。"""

    def __init__(
        self,
        *,
        system_prompt: str | None = None,
        context_window_tokens: int = 131072,
        threshold: float = DEFAULT_THRESHOLD,
        window_turns: int = DEFAULT_WINDOW_TURNS,
        summarizer: SummaryFn | None = None,
        model: str | None = None,
    ) -> None:
        if context_window_tokens <= 0:
            raise ValueError("context_window_tokens 必须为正整数")
        if not 0.0 < threshold <= 1.0:
            raise ValueError("threshold 必须在 (0, 1] 之间")
        if window_turns <= 0:
            raise ValueError("window_turns 必须为正整数")
        self._system_prompt = system_prompt.strip() if system_prompt else None
        self._context_window_tokens = int(context_window_tokens)
        self._threshold = float(threshold)
        self._window_turns = int(window_turns)
        self._summarizer: SummaryFn = summarizer or default_summarizer
        self._model = model
        self._history: list[dict[str, str]] = []
        self._summary: str | None = None
        self._pinned_facts: list[str] = []

    # ---------------- 构造辅助 ---------------- #
    @classmethod
    def from_route(
        cls,
        route: str,
        *,
        router: Any = None,
        **kwargs: Any,
    ) -> "ContextManager":
        """按模块路由取上下文窗口（models.yaml 的 context_window_tokens）。

        Args:
            router: 可注入 ModelRouter 实例（测试用；默认进程内单例）。
        """
        from .model_router import get_router  # 延迟导入，避免循环依赖

        model_router = router if router is not None else get_router()
        config = model_router.get_route_config(route)
        kwargs.setdefault("context_window_tokens", config.context_window_tokens)
        kwargs.setdefault("model", config.model)
        return cls(**kwargs)

    # ---------------- 只读属性 ---------------- #
    @property
    def context_window_tokens(self) -> int:
        return self._context_window_tokens

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def summary(self) -> str | None:
        return self._summary

    @property
    def pinned_facts(self) -> list[str]:
        return list(self._pinned_facts)

    @property
    def turns(self) -> int:
        """已发生的对话轮数（以用户发言数计）。"""
        return sum(1 for message in self._history if message["role"] == "user")

    @property
    def history(self) -> list[dict[str, str]]:
        """当前滑动窗口内的对话（副本）。"""
        return [dict(message) for message in self._history]

    # ---------------- 消息维护 ---------------- #
    def add_user(self, text: str) -> None:
        self._append("user", text)

    def add_assistant(self, text: str) -> None:
        self._append("assistant", text)

    def add_messages(self, messages: Sequence[Mapping[str, Any]]) -> None:
        for message in messages:
            role = str(message.get("role", "user"))
            if role not in ("user", "assistant"):
                raise ValueError(f"ContextManager 只接受 user/assistant 消息，收到 {role!r}")
            self._append(role, str(message.get("content") or ""))

    def _append(self, role: str, text: Any) -> None:
        content = str(text or "").strip()
        if not content:
            raise ValueError("消息内容不能为空")
        self._history.append({"role": role, "content": content})

    def clear(self) -> None:
        """清空对话与摘要（保留 system prompt 与 pinned facts）。"""
        self._history.clear()
        self._summary = None

    def add_pinned_fact(self, fact: str) -> None:
        """登记压缩时必须保留的关键事实（岗位/简历要点/已问问题等）。"""
        fact = str(fact or "").strip()
        if fact and fact not in self._pinned_facts:
            self._pinned_facts.append(fact)

    # ---------------- 组装与计量 ---------------- #
    def build_messages(self) -> list[dict[str, str]]:
        """组装最终发给模型的 messages：system（含摘要/关键事实）+ 窗口内对话。"""
        system_parts: list[str] = []
        if self._system_prompt:
            system_parts.append(self._system_prompt)
        if self._summary:
            system_parts.append("[较早对话摘要]\n" + self._summary)
        if self._pinned_facts:
            system_parts.append("[关键事实]\n" + "\n".join(f"- {fact}" for fact in self._pinned_facts))
        messages: list[dict[str, str]] = []
        if system_parts:
            messages.append({"role": "system", "content": "\n\n".join(system_parts)})
        messages.extend(dict(message) for message in self._history)
        return messages

    def token_usage(self) -> int:
        """当前上下文估算 token 数。"""
        return token_counter.count_messages_tokens(self.build_messages(), self._model)

    def usage_ratio(self) -> float:
        """当前上下文占窗口比例（0.0–1.0+）。"""
        return self.token_usage() / float(self._context_window_tokens)

    def should_compress(self) -> bool:
        """是否达到摘要压缩阈值（默认 90%）。"""
        return bool(self._history) and self.usage_ratio() >= self._threshold

    # ---------------- 压缩 ---------------- #
    def compress(self, force: bool = False) -> bool:
        """滑动窗口 + 摘要压缩。

        Args:
            force: 为 True 时即使窗口尚未超轮数也会压缩（用于 token 阈值兜底）。

        Returns:
            是否实际发生压缩。
        """
        if not self._history:
            return False
        keep = max(2, self._window_turns * 2)
        if len(self._history) <= keep:
            if not force:
                return False
            keep = max(2, len(self._history) // 2)
        old_messages = self._history[:-keep]
        if not old_messages:
            return False
        try:
            new_summary = self._summarizer(old_messages, self._summary)
        except Exception:  # noqa: BLE001 - 摘要失败不能中断面试，回退规则式
            logger.warning("摘要器执行失败，回退规则式摘要", exc_info=True)
            new_summary = default_summarizer(old_messages, self._summary)
        if new_summary and new_summary.strip():
            self._summary = new_summary.strip()
        self._history = self._history[-keep:]
        logger.info(
            "上下文摘要压缩完成：压缩 %s 条旧消息，保留 %s 条窗口消息，summary 长度 %s",
            len(old_messages),
            len(self._history),
            len(self._summary or ""),
        )
        return True

    def maybe_compress(self) -> bool:
        """达到阈值时自动压缩（一次调用内如需继续压缩，可循环调用）。"""
        if self.should_compress():
            return self.compress(force=True)
        return False
