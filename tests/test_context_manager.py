"""上下文管理器测试（v2 · 阶段 1 实现）

覆盖：
- messages 组装（system prompt 置顶、角色顺序）
- 滑动窗口：超过 N 轮后压缩，旧对话转为摘要
- token 阈值触发（默认 90% 阈值 / 可配置阈值）
- 摘要器可注入 + 失败自动回退规则式
- 关键事实（pinned facts）在压缩后仍保留
- 按路由取上下文窗口（models.yaml 的 context_window_tokens）

不依赖真实 API Key 与网络。
"""
from __future__ import annotations

import pytest

from backend.services.context_manager import ContextManager, default_summarizer


# ------------------------------------------------------------------ #
# 基础组装
# ------------------------------------------------------------------ #
def test_build_messages_with_system_prompt_and_history():
    ctx = ContextManager(system_prompt="你是面试官", window_turns=5)
    ctx.add_user("请自我介绍")
    ctx.add_assistant("我是一名后端开发")
    messages = ctx.build_messages()
    assert messages[0] == {"role": "system", "content": "你是面试官"}
    assert messages[1] == {"role": "user", "content": "请自我介绍"}
    assert messages[2] == {"role": "assistant", "content": "我是一名后端开发"}
    assert ctx.turns == 1


def test_add_messages_rejects_system_role():
    ctx = ContextManager()
    with pytest.raises(ValueError, match="只接受 user/assistant"):
        ctx.add_messages([{"role": "system", "content": "x"}])


def test_empty_message_rejected():
    ctx = ContextManager()
    with pytest.raises(ValueError, match="不能为空"):
        ctx.add_user("   ")


def test_invalid_constructor_params():
    with pytest.raises(ValueError):
        ContextManager(context_window_tokens=0)
    with pytest.raises(ValueError):
        ContextManager(threshold=0)
    with pytest.raises(ValueError):
        ContextManager(window_turns=0)


# ------------------------------------------------------------------ #
# 滑动窗口 + 摘要
# ------------------------------------------------------------------ #
def test_compress_slides_window_and_builds_summary():
    ctx = ContextManager(window_turns=2)  # 保留最近 2 轮 = 4 条消息
    for index in range(4):
        ctx.add_user(f"问题{index}")
        ctx.add_assistant(f"回答{index}")
    assert len(ctx.history) == 8
    assert ctx.compress() is True
    assert len(ctx.history) == 4
    assert ctx.history[0]["role"] == "user"
    assert ctx.summary is not None
    assert "候选人" in ctx.summary
    # 摘要进入 system message（与 system prompt 合并）
    messages = ctx.build_messages()
    assert messages[0]["role"] == "system"
    assert "较早对话摘要" in messages[0]["content"]


def test_compress_noop_when_window_small():
    ctx = ContextManager(window_turns=5)
    ctx.add_user("只有一个问题")
    ctx.add_assistant("一个回答")
    assert ctx.compress() is False
    assert ctx.summary is None


def test_compress_force_small_history_keeps_recent():
    ctx = ContextManager(window_turns=5)
    for index in range(3):
        ctx.add_user(f"问题{index}")
        ctx.add_assistant(f"回答{index}")
    assert ctx.compress(force=True) is True
    assert len(ctx.history) == 3  # 强制压缩只压缩前半段，保留最近对话
    assert "问题0" in (ctx.summary or "")
    assert ctx.history[-1]["content"] == "回答2"


# ------------------------------------------------------------------ #
# token 阈值触发
# ------------------------------------------------------------------ #
def test_should_compress_after_threshold():
    # 极小的窗口 + 默认 90% 阈值：任意消息都会触发
    ctx = ContextManager(context_window_tokens=1, window_turns=1)
    assert ctx.should_compress() is False  # 空历史不触发
    ctx.add_user("请介绍一下项目")
    ctx.add_assistant("项目背景是……")
    ctx.add_user("你的职责是什么")
    ctx.add_assistant("我负责后端接口开发")
    assert ctx.should_compress() is True
    assert ctx.maybe_compress() is True
    assert len(ctx.history) == 2
    assert ctx.summary


def test_no_compression_below_threshold():
    ctx = ContextManager(context_window_tokens=1_000_000, threshold=0.9)
    ctx.add_user("hi")
    ctx.add_assistant("hello")
    assert ctx.should_compress() is False
    assert ctx.maybe_compress() is False


# ------------------------------------------------------------------ #
# 摘要器注入 / 失败回退
# ------------------------------------------------------------------ #
def test_custom_summarizer_receives_previous_summary():
    calls: list[tuple[int, str | None]] = []

    def fake_summarizer(old_messages, previous_summary):
        calls.append((len(old_messages), previous_summary))
        return f"摘要{len(calls)}"

    ctx = ContextManager(window_turns=1, summarizer=fake_summarizer)
    for index in range(2):
        ctx.add_user(f"问题{index}")
        ctx.add_assistant(f"回答{index}")
    assert ctx.compress() is True
    assert ctx.summary == "摘要1"
    for index in range(2, 4):
        ctx.add_user(f"问题{index}")
        ctx.add_assistant(f"回答{index}")
    assert ctx.compress() is True
    assert ctx.summary == "摘要2"
    assert calls[1][1] == "摘要1"


def test_summarizer_failure_falls_back_to_rule_based():
    def broken_summarizer(old_messages, previous_summary):
        raise RuntimeError("LLM 摘要失败")

    ctx = ContextManager(window_turns=1, summarizer=broken_summarizer)
    ctx.add_user("问题0")
    ctx.add_assistant("回答0")
    ctx.add_user("问题1")
    ctx.add_assistant("回答1")
    assert ctx.compress() is True
    assert ctx.summary is not None
    assert "候选人" in ctx.summary


def test_default_summarizer_keeps_both_roles():
    summary = default_summarizer(
        [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "回答"},
        ]
    )
    assert "候选人" in summary
    assert "面试官" in summary


# ------------------------------------------------------------------ #
# 关键事实与清理
# ------------------------------------------------------------------ #
def test_pinned_facts_survive_compression():
    ctx = ContextManager(system_prompt="面试官", window_turns=1)
    ctx.add_pinned_fact("目标岗位：后端开发实习")
    ctx.add_pinned_fact("已问过：项目经历")
    ctx.add_user("问题0")
    ctx.add_assistant("回答0")
    ctx.add_user("问题1")
    ctx.add_assistant("回答1")
    assert ctx.compress() is True
    messages = ctx.build_messages()
    system_content = messages[0]["content"]
    assert "目标岗位：后端开发实习" in system_content
    assert "已问过：项目经历" in system_content
    # 重复登记去重
    ctx.add_pinned_fact("已问过：项目经历")
    assert ctx.pinned_facts.count("已问过：项目经历") == 1


def test_clear_resets_history_and_summary_but_keeps_system_and_pins():
    ctx = ContextManager(system_prompt="面试官", window_turns=1)
    ctx.add_pinned_fact("岗位 A")
    ctx.add_user("问题0")
    ctx.add_assistant("回答0")
    ctx.add_user("问题1")
    ctx.add_assistant("回答1")
    ctx.compress()
    ctx.clear()
    assert ctx.history == []
    assert ctx.summary is None
    messages = ctx.build_messages()
    assert messages[0]["role"] == "system"
    assert "面试官" in messages[0]["content"]
    assert "岗位 A" in messages[0]["content"]


# ------------------------------------------------------------------ #
# 按路由取窗口（与 models.yaml 单一事实来源对齐）
# ------------------------------------------------------------------ #
def test_from_route_uses_models_yaml_window():
    from pathlib import Path

    from backend.services.model_router import ModelRouter

    config_path = Path(__file__).resolve().parents[1] / "backend" / "config" / "models.yaml"
    router = ModelRouter(config_path, load_env=False)  # 注入实例，避免读取真实 .env
    ctx = ContextManager.from_route("interview_agent", router=router, system_prompt="面试官")
    assert ctx.context_window_tokens == 131072
