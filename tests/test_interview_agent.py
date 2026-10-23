"""模块 C 测试（v3：纯逻辑状态机 + D1–D5 决策树 + 面试官 Agent mock 测试）

覆盖范围：
- ``InterviewSession`` 状态机：一次一问、追问深度、同方向连续追问、换向保护（D5）；
- D1–D5 决策树判定的各分支（含糊 / 技术名词 / JD 冲突 / 优秀换向 / 换向保护）；
- 每 5 轮滚动摘要触发、token 90% 压缩 / 95% 优雅结束兜底、12 轮上限、结束幂等；
- Prompt 模板变量渲染（system_base + 三场景变体）、三场景参数化；
- 题库种子 ``data/questions/intern_questions.jsonl`` 字段完整性（intern / autumn / grad）；
- ``InterviewAgent`` 引擎（注入 FakeRouter / FakeContext，不依赖真实 Key 与 T1 组件）：
  建会话契约、提交回答、SSE 事件流（delta / done）、换向、LLM 失败兜底、结束与查询。

约束：本文件不调用真实模型、不需要 API Key、不启动 FastAPI。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services import interview_agent as ia

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = PROJECT_ROOT / "prompts" / "interviewer"
QUESTIONS_FILE = PROJECT_ROOT / "data" / "questions" / "intern_questions.jsonl"
REQUIRED_TURN_KEYS = {
    "turn",
    "question",
    "answer",
    "direction",
    "rule_id",
    "follow_up_depth",
}
REQUIRED_QUESTION_KEYS = {
    "question_id",
    "text",
    "direction",
    "follow_up_depth",
    "turn",
    "rule_id",
}


# ---------------------------------------------------------------------------
# 测试替身：FakeContext / FakeRouter / FakeChunk（不碰真实模型与 T1 组件）
# ---------------------------------------------------------------------------


class _FakeContext:
    """最小上下文管理器替身（接口对齐 backend/services/context_manager.py）。"""

    def __init__(self, system_prompt: str, *, usage: int = 1000, window: int = 10000) -> None:
        self._system_prompt = system_prompt
        self._history: list[dict[str, str]] = []
        self._pinned: list[str] = []
        self._usage = usage
        self.context_window_tokens = window
        self.compress_calls = 0

    def add_pinned_fact(self, fact: str) -> None:
        self._pinned.append(fact)

    def add_user(self, text: str) -> None:
        self._history.append({"role": "user", "content": text})

    def add_assistant(self, text: str) -> None:
        self._history.append({"role": "assistant", "content": text})

    def build_messages(self) -> list[dict[str, str]]:
        messages = [{"role": "system", "content": self._system_prompt}]
        messages.extend(dict(item) for item in self._history)
        return messages

    def token_usage(self) -> int:
        return self._usage

    def should_compress(self) -> bool:
        return False

    def compress(self, force: bool = False) -> bool:
        self.compress_calls += 1
        return True

    @property
    def summary(self) -> str | None:
        return None


class _FakeUsage:
    def __init__(self, total: int = 100) -> None:
        self.prompt_tokens = total // 2
        self.completion_tokens = total - total // 2
        self.total_tokens = total


class _FakeChunk:
    def __init__(self, delta: str, index: int, finish_reason: str | None = None) -> None:
        self.delta = delta
        self.index = index
        self.finish_reason = finish_reason


class _FakeRouter:
    """最小模型路由器替身（接口对齐 backend/services/model_router.py）。"""

    def __init__(
        self,
        question: str = "能具体说说这个项目的背景和你负责的部分吗？",
        *,
        fail: bool = False,
        chunks: list[str] | None = None,
    ) -> None:
        self.question = question
        self.fail = fail
        self.chunks = chunks or ["能", "具体", "说说", "吗？"]
        self.calls: list[dict] = []
        self.stream_calls: list[dict] = []

    def chat(self, route: str, messages: list[dict], **kwargs) -> SimpleNamespace:
        self.calls.append({"route": route, "messages": messages, "kwargs": kwargs})
        if self.fail:
            raise RuntimeError("mock LLM unavailable")
        return SimpleNamespace(text=self.question, usage=_FakeUsage())

    def chat_stream(self, route: str, messages: list[dict], **kwargs):
        self.stream_calls.append({"route": route, "messages": messages, "kwargs": kwargs})
        if self.fail:
            raise RuntimeError("mock LLM unavailable")
        for index, piece in enumerate(self.chunks):
            yield _FakeChunk(piece, index)
        yield _FakeChunk("", len(self.chunks), "stop")


def _make_agent(
    *,
    router: _FakeRouter | None = None,
    usage: int = 1000,
    window: int = 10000,
) -> tuple[ia.InterviewAgent, list[_FakeContext]]:
    """构造注入替身的 InterviewAgent（Prompt 与题库使用真实文件）。"""
    created: list[_FakeContext] = []

    def factory(system_prompt: str) -> _FakeContext:
        context = _FakeContext(system_prompt, usage=usage, window=window)
        created.append(context)
        return context

    agent = ia.InterviewAgent(
        router=router or _FakeRouter(),
        context_factory=factory,
        prompts_dir=PROMPTS_DIR,
        question_bank=ia.load_question_bank(QUESTIONS_FILE),
    )
    return agent, created


def _ask_and_answer(
    session: ia.InterviewSession,
    text: str,
    answer: str,
    *,
    direction: str,
    rule_id: ia.RuleId | None = None,
) -> ia.Turn:
    """纯状态机辅助：提一问 + 答一轮。"""
    session.begin_question(text, direction=direction, rule_id=rule_id)
    return session.record_answer(answer)


# ---------------------------------------------------------------------------
# 1. 会话创建与场景范围（三场景）
# ---------------------------------------------------------------------------


def test_supported_scenes_restored() -> None:
    assert ia.SUPPORTED_SCENES == ("intern", "autumn", "grad")
    assert ia.DEFAULT_SCENE == "intern"
    assert set(ia.SCENE_LABELS) == {"intern", "autumn", "grad"}


def test_create_session_defaults() -> None:
    session = ia.InterviewSession.create(company="某某科技", role="后端开发实习生")
    assert session.session_id
    assert session.scene == "intern"
    assert session.status is ia.SessionStatus.ACTIVE
    assert session.turn_count == 0
    assert session.questions_asked == 0
    assert session.current_question is None
    assert session.last_direction is None
    assert session.finish_reason is None
    assert session.snapshot()["status"] == "active"


@pytest.mark.parametrize("scene", ["intern", "autumn", "grad"])
def test_create_session_accepts_all_scenes(scene: str) -> None:
    session = ia.InterviewSession.create(scene=scene)
    assert session.scene == scene


@pytest.mark.parametrize("scene", ["unknown", "", "INTERN"])
def test_create_session_rejects_unknown_scene(scene: str) -> None:
    with pytest.raises(ValueError):
        ia.InterviewSession.create(scene=scene)


# ---------------------------------------------------------------------------
# 2. 状态机：一次一问 / 追问深度 / 连续追问
# ---------------------------------------------------------------------------


def test_begin_question_tracks_depth_and_streak() -> None:
    session = ia.InterviewSession.create()

    q1 = session.begin_question("简历里这个项目你主要负责哪部分？", direction="项目经历")
    assert (q1.turn, q1.follow_up_depth, q1.rule_id) == (1, 0, None)
    assert session.follow_up_depth == 0
    assert session.same_direction_streak == 1
    assert session.direction_counts == {"项目经历": 1}

    session.record_answer("主要负责其中的商品列表接口和缓存部分。")

    q2 = session.begin_question(
        "优化前后的 QPS 大概是多少？", direction="项目经历", rule_id="D1"
    )
    assert q2.turn == 2
    assert q2.follow_up_depth == 1
    assert session.follow_up_depth == 1
    assert session.same_direction_streak == 2


def test_begin_question_rejects_second_question_before_answer() -> None:
    session = ia.InterviewSession.create()
    session.begin_question("第一问？", direction="技术基础")
    with pytest.raises(ValueError, match="一次一问|尚未回答"):
        session.begin_question("第二问？", direction="技术基础")


def test_begin_question_requires_text_and_direction() -> None:
    session = ia.InterviewSession.create()
    with pytest.raises(ValueError):
        session.begin_question("   ", direction="技术基础")
    with pytest.raises(ValueError):
        session.begin_question("问题？", direction="  ")
    with pytest.raises(ValueError):
        session.begin_question("问题？", direction="技术基础", rule_id="D9")  # type: ignore[arg-type]


def test_record_answer_validation() -> None:
    session = ia.InterviewSession.create()
    with pytest.raises(ValueError, match="没有待回答"):
        session.record_answer("没有问题的回答")

    session.begin_question("问题？", direction="技术基础")
    with pytest.raises(ValueError):
        session.record_answer("   ")


# ---------------------------------------------------------------------------
# 3. 换向保护（D5）：同方向最多追问 2 次
# ---------------------------------------------------------------------------


def test_force_switch_after_two_followups() -> None:
    session = ia.InterviewSession.create()
    _ask_and_answer(session, "第一问？", "做过一些相关功能。", direction="项目经历")
    _ask_and_answer(
        session, "追问一？", "具体用了数据库索引。", direction="项目经历", rule_id="D1"
    )
    _ask_and_answer(
        session, "追问二？", "具体是联合索引，字段是 user_id 和 status。",
        direction="项目经历", rule_id="D2",
    )

    assert session.follow_up_depth == ia.FORCE_SWITCH_AFTER_FOLLOWUPS
    assert session.should_force_switch() is True
    assert session.decide_next_rule("就是加了个索引。") == "D5"

    # 引擎若继续同方向追问，代码侧必须拦截
    with pytest.raises(ValueError, match="必须换向"):
        session.begin_question("再追问一次？", direction="项目经历", rule_id="D2")

    # 换到新方向后深度与连续计数重置
    q4 = session.begin_question("换个方向：你最近在学什么？", direction="学习能力", rule_id="D5")
    assert q4.follow_up_depth == 0
    assert session.same_direction_streak == 1


def test_d5_rule_requires_new_direction() -> None:
    session = ia.InterviewSession.create()
    session.begin_question("第一问？", direction="项目经历")
    session.record_answer("做过商品列表。")
    with pytest.raises(ValueError, match="D5"):
        session.begin_question("同一方向换向？", direction="项目经历", rule_id="D5")


# ---------------------------------------------------------------------------
# 4. D1–D5 决策树判定
# ---------------------------------------------------------------------------


def test_detect_answer_signals() -> None:
    vague = ia.detect_answer_signals("就是优化了一下。")
    assert vague.is_vague is True
    assert vague.has_number is False

    detailed = ia.detect_answer_signals(
        "优化前约 80 QPS，加联合索引后到 250 左右，压测 5 分钟取稳定值。"
    )
    assert detailed.has_number is True
    assert detailed.is_vague is False
    assert "索引" in detailed.tech_terms


def test_decide_rule_d1_vague_answer() -> None:
    assert ia.decide_rule("就是优化了一下数据库查询，具体记不清了。") == "D1"
    assert ia.decide_rule("优化了。") == "D1"


def test_decide_rule_d2_tech_term() -> None:
    answer = "我用了 Redis 做缓存，因为热点数据查数据库太慢，缓存之后接口会快很多。"
    assert ia.decide_rule(answer) == "D2"


def test_decide_rule_d3_jd_conflict() -> None:
    answer = "这个方向我确实没有做过，项目里主要是队友负责的，我参与得比较少。"
    # JD 冲突是语义信号，由引擎（JD 关键词 + 否定表述启发式）判定后传入；传入后命中 D3
    assert ia.decide_rule(answer, jd_conflict=True) == "D3"
    # 无冲突信号且无技术名词时，按完整回答处理，落到默认 D4
    assert ia.decide_rule(answer) == "D4"


def test_decide_rule_d4_complete_answer_switch_topic() -> None:
    answer = (
        "当时我们意见不一致，我先把两个方案整理成文档发到群里，"
        "约了十五分钟对齐会，最后按工时和数据评估选了方案二，后面协作挺顺的。"
    )
    assert ia.decide_rule(answer) == "D4"


def test_decide_rule_force_switch_has_highest_priority() -> None:
    # 即使回答含糊 / 有技术名词，同方向追问到上限也必须先换向
    assert (
        ia.decide_rule("就是优化了一下。", follow_up_depth=ia.FORCE_SWITCH_AFTER_FOLLOWUPS)
        == "D5"
    )


def test_decide_next_rule_uses_session_state() -> None:
    session = ia.InterviewSession.create()
    _ask_and_answer(session, "第一问？", "做过相关功能。", direction="项目经历")
    assert session.decide_next_rule("优化了一下。") in {"D1", "D2", "D3", "D4"}
    assert session.decide_next_rule("优化了一下。", jd_conflict=True) in {"D1", "D2", "D3", "D4"}


# ---------------------------------------------------------------------------
# 5. 滚动摘要触发（每 5 轮）与 token 兜底
# ---------------------------------------------------------------------------


def test_summary_triggers_every_five_turns() -> None:
    session = ia.InterviewSession.create()
    for i in range(ia.SUMMARY_EVERY_TURNS):
        _ask_and_answer(
            session,
            f"第 {i + 1} 问？",
            "我主要负责接口开发，做过一些性能优化。",
            direction="项目经历" if i % 2 == 0 else "技术基础",
        )
    assert session.turn_count == 5
    assert session.should_summarize() is True

    session.mark_summarized("候选人做过接口开发与性能优化，基础尚可。")
    assert session.summary_version == 1
    assert session.rolling_summary.startswith("候选人")
    assert session.summary == session.rolling_summary
    assert session.should_summarize() is False

    for i in range(ia.SUMMARY_EVERY_TURNS):
        _ask_and_answer(
            session,
            f"第 {i + 6} 问？",
            "我最近在学消息队列，看文档加写 demo。",
            direction="学习能力" if i % 2 == 0 else "工程实践",
        )
    assert session.should_summarize() is True
    assert session.snapshot()["summary_version"] == 1


def test_key_points_dedup() -> None:
    session = ia.InterviewSession.create()
    session.add_key_point("做过商品列表接口")
    session.add_key_point("做过商品列表接口")
    session.add_key_point("  ")
    assert session.key_points == ["做过商品列表接口"]


def test_token_compression_and_hard_limit() -> None:
    session = ia.InterviewSession.create()
    session.update_token_stats(890, 1000)
    assert session.token_ratio == pytest.approx(0.89)
    assert session.needs_token_compression() is False

    session.update_token_stats(900, 1000)
    assert session.needs_token_compression() is True
    assert session.check_auto_finish() is None

    session.update_token_stats(950, 1000)
    assert session.check_auto_finish() is ia.FinishReason.TOKEN_LIMIT
    assert session.context_usage_pct == pytest.approx(95.0)


# ---------------------------------------------------------------------------
# 6. 结束条件与幂等
# ---------------------------------------------------------------------------


def test_finish_on_max_turns() -> None:
    session = ia.InterviewSession.create()
    for i in range(ia.MAX_TURNS):
        _ask_and_answer(
            session,
            f"第 {i + 1} 问？",
            "我做过一些接口开发和排查工作，能讲清过程。",
            direction="项目经历" if i % 2 == 0 else "技术基础",
        )

    assert session.turn_count == ia.MAX_TURNS
    assert session.check_auto_finish() is ia.FinishReason.MAX_TURNS

    reason = session.finish(ia.FinishReason.MAX_TURNS)
    assert reason is ia.FinishReason.MAX_TURNS
    assert session.status is ia.SessionStatus.FINISHED
    assert session.snapshot()["finish_reason"] == "max_turns"
    assert session.ended_at is not None

    with pytest.raises(ValueError):
        session.begin_question("结束后的提问？", direction="技术基础")
    with pytest.raises(ValueError):
        session.record_answer("结束后的回答")


def test_finish_is_idempotent_and_user_end_priority() -> None:
    session = ia.InterviewSession.create()
    assert session.finish(ia.FinishReason.USER_END) is ia.FinishReason.USER_END
    # 已结束后再次调用不覆盖首次结束原因
    assert session.finish(ia.FinishReason.MAX_TURNS) is ia.FinishReason.USER_END
    assert session.check_auto_finish() is ia.FinishReason.USER_END


# ---------------------------------------------------------------------------
# 7. 序列化：snapshot / transcript（供 API 与模块 D）
# ---------------------------------------------------------------------------


def test_snapshot_and_transcript_serializable() -> None:
    session = ia.InterviewSession.create(company="某某科技")
    _ask_and_answer(session, "第一问？", "做过商品列表接口。", direction="项目经历")
    _ask_and_answer(session, "追问数据？", "优化前 80 QPS，现在 250。", direction="项目经历", rule_id="D1")

    snapshot = session.snapshot()
    transcript = session.transcript()

    assert set(snapshot) >= {
        "session_id",
        "scene",
        "status",
        "turn_count",
        "questions_asked",
        "current_question",
        "follow_up_depth",
        "same_direction_streak",
        "key_points",
        "summary",
        "summary_version",
        "context_usage_pct",
        "finish_reason",
        "token_stats",
    }
    # API 契约：turn_count 表示已提问数；answered_turns 表示已作答轮数
    assert snapshot["turn_count"] == 2
    assert snapshot["answered_turns"] == 2
    assert len(transcript) == 2
    assert all(REQUIRED_TURN_KEYS <= set(turn) for turn in transcript)
    json.dumps({"state": snapshot, "transcript": transcript}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 8. Prompt 模板渲染（角色卡变量 + D1–D5 对齐 + 三场景）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scene", ["intern", "autumn", "grad"])
def test_build_system_prompt_injects_role_card_variables(scene: str) -> None:
    base = (PROMPTS_DIR / "system_base.md").read_text(encoding="utf-8")
    scene_template = (PROMPTS_DIR / f"scene_{scene}.md").read_text(encoding="utf-8")

    prompt = ia.build_system_prompt(
        base,
        scene_template,
        company="某某科技",
        role="后端开发实习生",
        style="轻松、鼓励型",
        scene=scene,
        rule_id="D1",
    )

    assert "{{" not in prompt
    assert "某某科技" in prompt
    assert "后端开发实习生" in prompt
    assert "轻松、鼓励型" in prompt
    assert ia.SCENE_LABELS[scene] in prompt
    assert ia.RULE_INSTRUCTIONS["D1"] in prompt


def test_system_base_contains_all_decision_rules() -> None:
    base = (PROMPTS_DIR / "system_base.md").read_text(encoding="utf-8")
    for rule_id in ia.RULE_IDS:
        assert rule_id in base, f"system_base.md 缺少规则 {rule_id}"
    assert "{{company}}" in base
    assert "{{role}}" in base
    assert "{{style}}" in base
    assert "{{scene}}" in base


@pytest.mark.parametrize("scene", ["intern", "autumn", "grad"])
def test_scene_variants_v2(scene: str) -> None:
    content = (PROMPTS_DIR / f"scene_{scene}.md").read_text(encoding="utf-8")
    assert "prompt_version: v2" in content


def test_render_prompt_template_strict_and_lenient() -> None:
    assert ia.render_prompt_template("你好，{{name}}", {"name": "面试官"}) == "你好，面试官"
    with pytest.raises(KeyError):
        ia.render_prompt_template("你好，{{missing}}", {})
    assert (
        ia.render_prompt_template("你好，{{missing}}", {}, strict=False)
        == "你好，{{missing}}"
    )


def test_build_system_prompt_rejects_unknown_scene() -> None:
    with pytest.raises(ValueError):
        ia.build_system_prompt("base", "scene", scene="unknown")


# ---------------------------------------------------------------------------
# 9. 题库种子（data/questions）格式
# ---------------------------------------------------------------------------


def test_question_seed_file_valid() -> None:
    assert QUESTIONS_FILE.is_file(), f"题库种子缺失: {QUESTIONS_FILE}"
    lines = [
        line for line in QUESTIONS_FILE.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(lines) >= 10, "题库种子至少 10 条"

    ids: set[str] = set()
    scenes: set[str] = set()
    for line in lines:
        item = json.loads(line)
        assert {"id", "scene", "direction", "difficulty", "question", "followups"} <= set(item)
        assert item["scene"] in ia.SUPPORTED_SCENES
        assert item["difficulty"] in {"easy", "medium", "hard"}
        assert item["question"].strip()
        assert isinstance(item["followups"], list) and len(item["followups"]) >= 2
        assert item["id"] not in ids
        ids.add(item["id"])
        scenes.add(item["scene"])
    assert {"intern", "autumn", "grad"} <= scenes, "题库应覆盖三场景标签"


# ---------------------------------------------------------------------------
# 10. 面试官 Agent（引擎）mock 测试 — 契约冻结 v1
# ---------------------------------------------------------------------------


def test_engine_create_session_contract() -> None:
    router = _FakeRouter(question="请介绍一下你的项目经历。")
    agent, contexts = _make_agent(router=router)

    payload = agent.create_session(
        "简历：某大学本科生，做过商品列表接口优化。",
        jd_text="岗位要求：熟悉 Python 与 MySQL。",
        scene="intern",
        company="某某科技",
        role="后端开发实习生",
    )

    # docs/api.md 契约：data = {session_id, question, state}
    assert set(payload) == {"session_id", "question", "state"}
    question = payload["question"]
    assert REQUIRED_QUESTION_KEYS <= set(question)
    assert question["rule_id"] is None
    assert question["text"] == "请介绍一下你的项目经历。"
    assert question["follow_up_depth"] == 0
    assert question["turn"] == 1

    state = payload["state"]
    assert state["scene"] == "intern"
    assert state["turn_index"] == 0
    assert state["asked_questions"] == []  # create 时尚未作答
    assert state["ended"] is False
    assert "context_usage_pct" in state

    # 模型路由按模块名调用，system prompt 已注入场景 + 简历
    assert router.calls[0]["route"] == "interview_agent"
    system_prompt = router.calls[0]["messages"][0]["content"]
    assert "实习" in system_prompt
    assert contexts[0]._system_prompt  # 上下文已初始化
    assert "简历" in contexts[0]._history[0]["content"]


@pytest.mark.parametrize("scene", ["intern", "autumn", "grad"])
def test_engine_create_session_all_scenes(scene: str) -> None:
    agent, _ = _make_agent()
    payload = agent.create_session(
        "简历文本", scene=scene, company=None, role=None, style=None
    )
    assert payload["state"]["scene"] == scene


def test_engine_create_session_validates_input() -> None:
    agent, _ = _make_agent()
    with pytest.raises(ValueError):
        agent.create_session("   ")
    with pytest.raises(ValueError):
        agent.create_session("简历", scene="unknown")


def test_engine_submit_answer_contract() -> None:
    agent, _ = _make_agent()
    created = agent.create_session("简历：做过商品列表接口优化。")
    session_id = created["session_id"]
    first_direction = created["question"]["direction"]

    payload = agent.submit_answer(session_id, "就是优化了一下数据库，加了索引。")

    # docs/api.md 契约：data = {session_id, turn_index, question, state}
    assert set(payload) == {"session_id", "turn_index", "question", "state"}
    assert payload["session_id"] == session_id
    assert payload["turn_index"] == 1
    question = payload["question"]
    assert REQUIRED_QUESTION_KEYS <= set(question)
    assert question["rule_id"] == "D1"  # 回答含糊 → D1 追数据来源
    assert question["direction"] == first_direction  # 追问沿用方向
    assert question["follow_up_depth"] == 1
    state = payload["state"]
    assert state["turn_index"] == 1
    assert len(state["asked_questions"]) == 2
    assert state["rule_id"] == "D1"
    assert state["ended"] is False
    assert "summary" in state and "context_usage_pct" in state


def test_engine_force_switch_after_two_followups() -> None:
    agent, _ = _make_agent()
    created = agent.create_session("简历：做过商品列表接口优化。")
    session_id = created["session_id"]
    first_direction = created["question"]["direction"]

    agent.submit_answer(session_id, "就是优化了一下。")
    agent.submit_answer(session_id, "加了个索引。")
    payload = agent.submit_answer(session_id, "我最近在学 Redis。")

    question = payload["question"]
    assert question["rule_id"] == "D5"
    assert question["direction"] != first_direction
    assert question["follow_up_depth"] == 0


def test_engine_jd_conflict_rule() -> None:
    agent, _ = _make_agent()
    created = agent.create_session(
        "简历：做过商品列表接口优化。",
        jd_text="岗位要求：熟悉 Redis 缓存与 MySQL 索引。",
    )
    payload = agent.submit_answer(
        created["session_id"],
        "这个方向我确实没有做过，Redis 也只是听过，项目里主要是队友负责的，我参与得比较少。",
    )
    assert payload["question"]["rule_id"] == "D3"


def test_engine_stream_turn_contract() -> None:
    router = _FakeRouter(chunks=["能", "具体", "说说", "吗？"])
    agent, _ = _make_agent(router=router)
    created = agent.create_session("简历：做过商品列表接口优化。")
    session_id = created["session_id"]

    events = list(agent.stream_turn(session_id, "我做过接口开发，能讲清过程。"))

    assert events[0]["event"] == "delta"
    deltas = "".join(event["text"] for event in events if event["event"] == "delta")
    assert deltas == "能具体说说吗？"
    done = events[-1]
    assert done["event"] == "done"
    assert done["question"]["text"] == "能具体说说吗？"
    assert done["question"]["rule_id"] == "D1"
    assert {"question", "state", "usage"} <= set(done)
    assert {"prompt_tokens", "completion_tokens", "total_tokens"} <= set(done["usage"])
    json.dumps(events, ensure_ascii=False)


def test_engine_llm_failure_fallback() -> None:
    """模型不可用（无 Key / 401 / 超时）时降级题库兜底，面试不中断。"""
    agent, _ = _make_agent(router=_FakeRouter(fail=True))

    created = agent.create_session("简历：做过商品列表接口优化。")
    assert created["question"]["text"]

    payload = agent.submit_answer(created["session_id"], "就是优化了一下。")
    assert payload["question"]["text"]
    assert payload["question"]["rule_id"] == "D1"

    events = list(agent.stream_turn(created["session_id"], "加了个索引。"))
    done = events[-1]
    assert done["event"] == "done"
    assert done["question"]["text"]


def test_engine_end_session_and_get_session() -> None:
    agent, _ = _make_agent()
    created = agent.create_session("简历：做过商品列表接口优化。")
    session_id = created["session_id"]
    agent.submit_answer(session_id, "就是优化了一下。")
    agent.submit_answer(session_id, "加了个索引。")

    # GET 契约：turn_index / questions_asked / summary / context_usage_pct / ended
    state = agent.get_session(session_id)
    assert {"session_id", "scene", "turn_index", "questions_asked", "summary",
            "context_usage_pct", "ended"} <= set(state)
    assert state["session_id"] == session_id
    assert state["turn_index"] == 2
    assert len(state["questions_asked"]) == 3
    assert state["ended"] is False

    payload = agent.end_session(session_id)
    assert set(payload) == {"session_id", "transcript", "state", "report_input"}
    assert len(payload["transcript"]) == 2
    assert payload["state"]["ended"] is True
    assert payload["state"]["finish_reason"] == "user_end"
    assert {"resume_text", "jd_text", "scene", "turn_count"} <= set(payload["report_input"])
    assert payload["report_input"]["scene"] == "intern"
    assert payload["report_input"]["turn_count"] == 2
    assert len(payload["report_input"]["transcript"]) == 2

    # 幂等：重复结束不报错
    again = agent.end_session(session_id)
    assert again["state"]["finish_reason"] == "user_end"
    with pytest.raises(ValueError):
        agent.submit_answer(session_id, "结束后的回答")


def test_engine_token_limit_finishes_gracefully() -> None:
    agent, _ = _make_agent(usage=9600, window=10000)
    created = agent.create_session("简历：做过商品列表接口优化。")
    payload = agent.submit_answer(created["session_id"], "我做过接口开发，能讲清过程。")

    assert payload["question"] is None
    assert payload["state"]["ended"] is True
    assert payload["state"]["finish_reason"] == "token_limit"


def test_engine_max_turns_finishes() -> None:
    agent, _ = _make_agent()
    created = agent.create_session("简历：做过商品列表接口优化。")
    session_id = created["session_id"]

    payload: dict = {}
    for _ in range(ia.MAX_TURNS):
        payload = agent.submit_answer(session_id, "我做过接口开发，能讲清过程。")

    assert payload["question"] is None
    assert payload["state"]["ended"] is True
    assert payload["state"]["finish_reason"] == "max_turns"
    assert payload["state"]["turn_index"] == ia.MAX_TURNS


def test_engine_unknown_session_raises() -> None:
    agent, _ = _make_agent()
    with pytest.raises(ValueError, match="会话不存在"):
        agent.submit_answer("no-such-session", "回答")
    with pytest.raises(ValueError, match="会话不存在"):
        agent.end_session("no-such-session")
    with pytest.raises(ValueError, match="会话不存在"):
        agent.get_session("no-such-session")


def test_engine_singleton_lifecycle() -> None:
    """进程内单例供 backend/api/interview.py 接线（会话状态跨请求保留）。"""
    ia.reset_interview_agent()
    try:
        first = ia.get_interview_agent()
        assert isinstance(first, ia.InterviewAgent)
        assert ia.get_interview_agent() is first
        ia.reset_interview_agent()
        assert ia.get_interview_agent() is not first
    finally:
        ia.reset_interview_agent()
