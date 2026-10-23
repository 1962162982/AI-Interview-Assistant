"""模块 C：模拟面试（v3 · 面试官 Agent + 会话状态机 + 追问决策逻辑）

职责（对应实行清单「模块 C 模拟面试」）：
- 面试官 Agent：基于简历文本 + JD 文本生成首问（``prompts/interviewer/``）；
- 会话状态对象：当前题目 / 已问列表 / 追问深度 / 候选人关键回答摘要 / 场景 / 计时；
- 多轮追问引擎：代码侧按 D1–D5 决策树判定 rule_id 并注入 Prompt，模型只负责措辞；
- 每 5 轮滚动摘要接入 ``context_manager``；token ≥90% 压缩、≥95% 优雅结束，保证长面试不中断；
- 流式输出（SSE 事件：``delta`` / ``done``），契约见 ``docs/api.md``（契约冻结 v1）；
- 场景三选一：``intern``（默认）/ ``autumn`` / ``grad``，加载 system_base + 对应 scene 变体。

分层设计：
- 本文件顶层只依赖标准库；``model_router`` / ``context_manager`` 延迟导入或通过构造参数注入，
  无 API Key / 无 Web 依赖时也能跑纯逻辑单测与 mock 引擎测试；
- 模型不可用（无 Key / 401 / 超时）时自动降级为题库种子出题，保证离线演示不中断。

接口契约对齐（契约冻结 v1）：
- ``create_session`` → ``{session_id, scene, status, turn_count, question}``（首问 rule_id=null）；
- ``submit_answer`` → ``{session_id, turn_index, question, state}``；
- ``stream_turn`` → SSE 事件 ``{"event":"delta","text":...}`` / ``{"event":"done","question":...,"state":...,"usage":...}``；
- ``end_session`` → ``{session_id, transcript, state, report_input}``；
- ``get_session`` → 会话状态（含 turn_count / questions_asked / summary / context_usage_pct）。
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Literal, Protocol

logger = logging.getLogger(__name__)

__all__ = [
    "SCENE_INTERN",
    "SCENE_AUTUMN",
    "SCENE_GRAD",
    "DEFAULT_SCENE",
    "SUPPORTED_SCENES",
    "SCENE_LABELS",
    "DEFAULT_STYLES",
    "DIRECTIONS_BY_SCENE",
    "MAX_TURNS",
    "MAX_FOLLOWUP_DEPTH",
    "FORCE_SWITCH_AFTER_FOLLOWUPS",
    "SUMMARY_EVERY_TURNS",
    "TOKEN_COMPRESS_RATIO",
    "TOKEN_HARD_LIMIT_RATIO",
    "RULE_IDS",
    "RULE_INSTRUCTIONS",
    "DEFAULT_PROMPTS_DIR",
    "DEFAULT_QUESTIONS_PATH",
    "RuleId",
    "SessionStatus",
    "FinishReason",
    "Question",
    "Turn",
    "InterviewSession",
    "AnswerSignals",
    "detect_answer_signals",
    "decide_rule",
    "render_prompt_template",
    "build_system_prompt",
    "load_question_bank",
    "ChatStreamProtocol",
    "ContextManagerProtocol",
    "InterviewAgent",
    "get_interview_agent",
    "reset_interview_agent",
]

# ---------------------------------------------------------------------------
# 常量（与实行清单「模块 C」/ 说明书 4.3 对齐）
# ---------------------------------------------------------------------------

SCENE_INTERN = "intern"
SCENE_AUTUMN = "autumn"
SCENE_GRAD = "grad"
DEFAULT_SCENE = SCENE_INTERN
SUPPORTED_SCENES: tuple[str, ...] = (SCENE_INTERN, SCENE_AUTUMN, SCENE_GRAD)
SCENE_LABELS: dict[str, str] = {
    SCENE_INTERN: "实习",
    SCENE_AUTUMN: "秋招",
    SCENE_GRAD: "保研",
}
DEFAULT_COMPANY = "某互联网公司"
DEFAULT_ROLES: dict[str, str] = {
    SCENE_INTERN: "后端开发实习生",
    SCENE_AUTUMN: "后端开发工程师（校招）",
    SCENE_GRAD: "硕士研究生（科研方向）",
}
DEFAULT_STYLES: dict[str, str] = {
    SCENE_INTERN: "轻松、鼓励型",
    SCENE_AUTUMN: "正式、专业、适度压力",
    SCENE_GRAD: "学术、严谨、关注思维过程",
}

#: 各场景的考察方向（决定换向时选哪个方向）
DIRECTIONS_BY_SCENE: dict[str, tuple[str, ...]] = {
    SCENE_INTERN: (
        "项目经历",
        "技术基础",
        "学习能力",
        "工程实践",
        "问题排查",
        "团队协作",
        "实习动机",
        "职业规划",
    ),
    SCENE_AUTUMN: (
        "项目经历",
        "技术基础",
        "工程实践",
        "问题排查",
        "岗位匹配",
        "团队协作",
        "职业规划",
    ),
    SCENE_GRAD: (
        "科研经历",
        "专业基础",
        "研究兴趣",
        "项目经历",
        "学习能力",
        "团队协作",
    ),
}

MAX_TURNS = 12                    # 一场面试默认最多提问 12 次
MAX_FOLLOWUP_DEPTH = 3            # 追问深度状态上限（防御性）
FORCE_SWITCH_AFTER_FOLLOWUPS = 2  # 同一方向追问 ≥2 次 → 强制换向（D5）
SUMMARY_EVERY_TURNS = 5           # 每 5 轮触发一次滚动摘要
TOKEN_COMPRESS_RATIO = 0.90       # token 占用 ≥90% 窗口 → 触发摘要压缩
TOKEN_HARD_LIMIT_RATIO = 0.95     # 压缩后仍 ≥95% → 优雅结束，保证面试不中断

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPTS_DIR = REPO_ROOT / "prompts" / "interviewer"
DEFAULT_QUESTIONS_PATH = REPO_ROOT / "data" / "questions" / "intern_questions.jsonl"

RuleId = Literal["D1", "D2", "D3", "D4", "D5"]
RULE_IDS: tuple[RuleId, ...] = ("D1", "D2", "D3", "D4", "D5")

#: 注入 Prompt 的规则执行说明（措辞与 system_base.md 决策树的动作列一致）
RULE_INSTRUCTIONS: dict[RuleId, str] = {
    "D1": "追问数据来源与具体做法：只挑一个点，问清优化前基线、具体做了什么、效果如何衡量",
    "D2": "深挖一层原理：为什么选 X 不选 Y、底层机制是什么、条件变化时会怎样",
    "D3": "温和质疑并给台阶（“我理解可能是……，能具体说说吗？”），不要连续逼问",
    "D4": "结束当前方向，切换到尚未考察的能力维度提问",
    "D5": "不再追问，换到一个全新的方向（从简历 / JD 未覆盖处选）",
}


class SessionStatus(str, Enum):
    """会话状态。"""

    ACTIVE = "active"
    FINISHED = "finished"


class FinishReason(str, Enum):
    """面试结束原因（优先级见 ``InterviewSession.check_auto_finish``）。"""

    USER_END = "user_end"
    MAX_TURNS = "max_turns"
    TOKEN_LIMIT = "token_limit"


# ---------------------------------------------------------------------------
# 追问决策树：代码侧判定（D1–D5）
# ---------------------------------------------------------------------------

_VAGUE_MARKERS: tuple[str, ...] = (
    "大概",
    "差不多",
    "应该是",
    "好像",
    "可能吧",
    "就是",
    "优化了一下",
    "改了一下",
    "提升了不少",
    "没统计",
    "记不清",
    "忘了",
    "不太确定",
)

_NEGATION_MARKERS: tuple[str, ...] = (
    "没做过",
    "没有做过",
    "没接触",
    "没接触过",
    "没有接触",
    "没参与",
    "没有参与",
    "没学过",
    "没有学过",
    "没写过",
    "不了解",
    "不会",
    "没有相关经验",
    "只是听说过",
)

_HAS_NUMBER_RE = re.compile(r"\d")
_NUMBER_UNITS: tuple[str, ...] = ("倍", "%", "毫秒", "QPS", "TPS")

#: 常见技术名词（出现但未解释原理时命中 D2；JD 冲突启发式也复用它）
TECH_TERMS: tuple[str, ...] = (
    "Redis",
    "MySQL",
    "PostgreSQL",
    "MongoDB",
    "FastAPI",
    "Django",
    "Flask",
    "Spring",
    "Docker",
    "Kubernetes",
    "K8s",
    "Nginx",
    "Kafka",
    "RabbitMQ",
    "JMeter",
    "tiktoken",
    "Git",
    "Linux",
    "Java",
    "Python",
    "Go",
    "C++",
    "Vue",
    "React",
    "SQL",
    "TCP",
    "UDP",
    "HTTP",
    "HTTPS",
    "TLS",
    "JVM",
    "GC",
    "ORM",
    "索引",
    "事务",
    "缓存",
    "消息队列",
    "微服务",
    "分布式",
    "并发",
    "多线程",
    "哈希",
    "二叉树",
    "动态规划",
    "正则",
)

_VAGUE_MIN_CHARS = 30  # 过短回答直接视为含糊


@dataclass(frozen=True)
class AnswerSignals:
    """对候选人回答的轻量信号抽取结果（不调用模型，便于单测）。"""

    length: int
    has_number: bool
    has_vague_marker: bool
    tech_terms: tuple[str, ...]

    @property
    def is_vague(self) -> bool:
        """回答是否含糊：过短，或只有空泛表述且没有任何量化信息。"""
        return self.length < _VAGUE_MIN_CHARS or (
            self.has_vague_marker and not self.has_number
        )


def detect_answer_signals(
    answer: str,
    *,
    extra_tech_terms: Iterable[str] = (),
) -> AnswerSignals:
    """抽取回答中的量化 / 空泛 / 技术名词信号（纯规则，无模型调用）。"""
    text = (answer or "").strip()
    tech_terms = tuple(
        term
        for term in TECH_TERMS + tuple(extra_tech_terms)
        if term and term.lower() in text.lower()
    )
    return AnswerSignals(
        length=len(text),
        has_number=bool(_HAS_NUMBER_RE.search(text))
        or any(unit in text for unit in _NUMBER_UNITS),
        has_vague_marker=any(marker in text for marker in _VAGUE_MARKERS),
        tech_terms=tech_terms,
    )


def decide_rule(
    answer: str,
    *,
    follow_up_depth: int = 0,
    jd_conflict: bool = False,
    extra_tech_terms: Iterable[str] = (),
) -> RuleId:
    """按 D1–D5 决策树判定本轮应使用的规则。

    判定顺序（与 ``system_base.md`` 一致）：
    1. 同方向追问已达 ``FORCE_SWITCH_AFTER_FOLLOWUPS`` 次 → D5（换向保护，优先级最高）；
    2. 回答含糊 → D1；
    3. 回答与 JD 能力要求冲突（由调用方传入语义信号）→ D3（压力测试优先于深挖）；
    4. 回答含技术名词 → D2；
    5. 其余（回答完整优秀）→ D4。

    ``jd_conflict`` 需要语义判断，由引擎侧启发式 / 模型信号提供；本函数保持纯规则、可单测。
    """
    if follow_up_depth >= FORCE_SWITCH_AFTER_FOLLOWUPS:
        return "D5"
    signals = detect_answer_signals(answer, extra_tech_terms=extra_tech_terms)
    if signals.is_vague:
        return "D1"
    if jd_conflict:
        return "D3"
    if signals.tech_terms:
        return "D2"
    return "D4"


# ---------------------------------------------------------------------------
# Prompt 模板渲染（角色卡变量注入）
# ---------------------------------------------------------------------------

_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def render_prompt_template(
    template: str,
    variables: dict[str, Any],
    *,
    strict: bool = True,
) -> str:
    """用 ``variables`` 替换模板中的 ``{{var}}`` 占位符（一次替换，不递归）。"""

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            if strict:
                raise KeyError(f"Prompt 模板变量缺失: {name}")
            return match.group(0)
        return str(variables[name])

    return _TEMPLATE_VAR_RE.sub(_replace, template)


def build_system_prompt(
    base_template: str,
    scene_template: str = "",
    *,
    company: str = DEFAULT_COMPANY,
    role: str = DEFAULT_ROLES[SCENE_INTERN],
    style: str = DEFAULT_STYLES[SCENE_INTERN],
    scene: str = DEFAULT_SCENE,
    rule_id: RuleId | None = None,
) -> str:
    """组装模块 C 的 system prompt（base + 场景变体 + 角色卡变量）。

    ``rule_id`` 为 None 表示首问或未指定规则：Prompt 中提示“自行按决策树判定”。
    追问轮由引擎传入 D1–D5，模型只负责按规则措辞（规则判定在代码侧）。
    """
    if scene not in SUPPORTED_SCENES:
        raise ValueError(f"仅支持场景 {SUPPORTED_SCENES}，收到: {scene!r}")

    variables: dict[str, str] = {
        "company": company,
        "role": role,
        "style": style,
        "scene": SCENE_LABELS.get(scene, scene),
        "rule_id": rule_id or "（自行按决策树判定）",
        "rule_instruction": RULE_INSTRUCTIONS[rule_id]
        if rule_id
        else "（未指定规则：按决策树顺序自行判定）",
    }
    rendered_base = render_prompt_template(base_template, variables)
    rendered_scene = (
        render_prompt_template(scene_template, variables) if scene_template.strip() else ""
    )
    return rendered_base + ("\n\n" + rendered_scene if rendered_scene else "")


# ---------------------------------------------------------------------------
# 会话状态对象
# ---------------------------------------------------------------------------


@dataclass
class Question:
    """一道已提出的问题。"""

    id: str
    text: str
    direction: str
    turn: int
    rule_id: RuleId | None = None
    follow_up_depth: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.id,
            "text": self.text,
            "direction": self.direction,
            "turn": self.turn,
            "rule_id": self.rule_id,
            "follow_up_depth": self.follow_up_depth,
        }


@dataclass
class Turn:
    """一轮完整问答（供模块 D 评分使用，字段与契约 transcript 对齐）。"""

    turn: int
    question: str
    answer: str
    direction: str
    rule_id: RuleId | None
    follow_up_depth: int
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "question": self.question,
            "answer": self.answer,
            "direction": self.direction,
            "rule_id": self.rule_id,
            "follow_up_depth": self.follow_up_depth,
            "elapsed_seconds": self.elapsed_seconds,
        }


@dataclass
class InterviewSession:
    """面试会话状态（模块 C 的唯一状态事实来源）。

    字段对应实行清单「面试会话状态对象」：当前题目 / 已问列表 / 追问深度 /
    候选人关键回答摘要，另加场景、计时、token 与结束条件。
    """

    session_id: str
    scene: str = DEFAULT_SCENE
    company: str = ""
    role: str = ""
    style: str = ""
    resume_text: str = ""
    jd_text: str = ""
    status: SessionStatus = SessionStatus.ACTIVE
    turn_count: int = 0  # 已作答轮数
    current_question: Question | None = None
    asked_questions: list[Question] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    follow_up_depth: int = 0
    same_direction_streak: int = 0
    direction_counts: dict[str, int] = field(default_factory=dict)
    key_points: list[str] = field(default_factory=list)
    rolling_summary: str = ""
    summary_version: int = 0
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    finish_reason: FinishReason | None = None
    token_used: int = 0
    token_window: int = 0
    _summarized_until_turn: int = 0

    # -- 构造 --------------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        session_id: str | None = None,
        scene: str = DEFAULT_SCENE,
        company: str = "",
        role: str = "",
        style: str = "",
        resume_text: str = "",
        jd_text: str = "",
    ) -> "InterviewSession":
        """创建一场面试会话；scene 三选一，默认 ``intern``。"""
        if scene not in SUPPORTED_SCENES:
            raise ValueError(f"仅支持场景 {SUPPORTED_SCENES}，收到: {scene!r}")
        return cls(
            session_id=session_id or uuid.uuid4().hex,
            scene=scene,
            company=company,
            role=role,
            style=style,
            resume_text=resume_text or "",
            jd_text=jd_text or "",
        )

    # -- 派生属性 ----------------------------------------------------------

    @property
    def last_question(self) -> Question | None:
        return self.asked_questions[-1] if self.asked_questions else None

    @property
    def last_direction(self) -> str | None:
        question = self.last_question
        return question.direction if question else None

    @property
    def questions_asked(self) -> int:
        """已提问数量（API 契约中的 turn_count）。"""
        return len(self.asked_questions)

    @property
    def summary(self) -> str:
        return self.rolling_summary

    @property
    def context_usage_pct(self) -> float:
        return round(self.token_ratio * 100.0, 2)

    @property
    def elapsed_seconds(self) -> float:
        end = self.ended_at if self.ended_at is not None else time.time()
        return max(0.0, end - self.started_at)

    @property
    def token_ratio(self) -> float:
        return self.token_used / self.token_window if self.token_window > 0 else 0.0

    @property
    def answered_turns(self) -> int:
        return self.turn_count

    # -- 提问 / 回答 --------------------------------------------------------

    def begin_question(
        self,
        text: str,
        *,
        direction: str,
        rule_id: RuleId | None = None,
    ) -> Question:
        """登记一道新问题（一次只允许一个未回答的问题）。

        自动计算追问深度与同方向连续次数，并执行换向保护：
        - 同方向追问已满 ``FORCE_SWITCH_AFTER_FOLLOWUPS`` 次 → 必须换向（抛 ``ValueError``）；
        - ``rule_id="D5"`` 时 direction 必须与上一问不同。
        """
        if self.status is not SessionStatus.ACTIVE:
            raise ValueError("会话已结束，不能继续提问")
        if self.current_question is not None:
            raise ValueError("上一问尚未回答，不能提出新问题（一次一问）")
        if not (text or "").strip():
            raise ValueError("问题文本不能为空")
        if not (direction or "").strip():
            raise ValueError("考察方向 direction 不能为空")
        if rule_id is not None and rule_id not in RULE_IDS:
            raise ValueError(f"未知规则编号: {rule_id!r}，应为 {RULE_IDS}")

        last_direction = self.last_direction
        same_direction = last_direction is not None and direction == last_direction

        if same_direction and self.follow_up_depth >= FORCE_SWITCH_AFTER_FOLLOWUPS:
            raise ValueError(
                f"同方向追问已达上限（{FORCE_SWITCH_AFTER_FOLLOWUPS} 次），必须换向（D5）"
            )
        if rule_id == "D5" and same_direction:
            raise ValueError("D5 要求强制换向，direction 不能与上一问相同")

        depth = self.follow_up_depth + 1 if same_direction else 0
        if depth > MAX_FOLLOWUP_DEPTH:
            raise ValueError(f"追问深度超过上限 {MAX_FOLLOWUP_DEPTH}")

        question = Question(
            id=f"q{len(self.asked_questions) + 1}",
            text=text.strip(),
            direction=direction.strip(),
            turn=self.turn_count + 1,
            rule_id=rule_id,
            follow_up_depth=depth,
        )
        self.current_question = question
        self.asked_questions.append(question)
        self.follow_up_depth = depth
        self.same_direction_streak = (
            self.same_direction_streak + 1 if same_direction else 1
        )
        self.direction_counts[question.direction] = (
            self.direction_counts.get(question.direction, 0) + 1
        )
        return question

    def record_answer(self, answer: str, *, elapsed_seconds: float = 0.0) -> Turn:
        """登记候选人对当前问题的回答（空回答视为非法输入）。"""
        if self.status is not SessionStatus.ACTIVE:
            raise ValueError("会话已结束，不能记录回答")
        if self.current_question is None:
            raise ValueError("当前没有待回答的问题")
        answer_text = (answer or "").strip()
        if not answer_text:
            raise ValueError("回答不能为空（候选人跳过时请显式记录说明）")

        question = self.current_question
        turn = Turn(
            turn=question.turn,
            question=question.text,
            answer=answer_text,
            direction=question.direction,
            rule_id=question.rule_id,
            follow_up_depth=question.follow_up_depth,
            elapsed_seconds=max(0.0, float(elapsed_seconds)),
        )
        self.turns.append(turn)
        self.turn_count += 1
        self.current_question = None
        return turn

    # -- 追问决策 ----------------------------------------------------------

    def should_force_switch(self) -> bool:
        """同一方向追问是否已达上限（下一问必须换向，D5）。"""
        return self.follow_up_depth >= FORCE_SWITCH_AFTER_FOLLOWUPS

    def decide_next_rule(
        self,
        answer: str,
        *,
        jd_conflict: bool = False,
        extra_tech_terms: Iterable[str] = (),
    ) -> RuleId:
        """根据本轮回答判定下一问应使用的规则（D5 换向保护优先）。"""
        if self.should_force_switch():
            return "D5"
        return decide_rule(
            answer,
            follow_up_depth=self.follow_up_depth,
            jd_conflict=jd_conflict,
            extra_tech_terms=extra_tech_terms,
        )

    # -- 滚动摘要 / token 兜底 ---------------------------------------------

    def should_summarize(self) -> bool:
        """是否触发滚动摘要：每 ``SUMMARY_EVERY_TURNS`` 轮一次（已摘要过的不重复触发）。"""
        return (
            self.status is SessionStatus.ACTIVE
            and self.turn_count > 0
            and self.turn_count - self._summarized_until_turn >= SUMMARY_EVERY_TURNS
        )

    def mark_summarized(self, summary: str) -> None:
        """记录滚动摘要结果（由 context_manager 生成后回填）。"""
        self.rolling_summary = (summary or "").strip()
        self.summary_version += 1
        self._summarized_until_turn = self.turn_count

    def add_key_point(self, point: str) -> None:
        """追加候选人关键回答摘要要点（去重、忽略空串）。"""
        point = (point or "").strip()
        if point and point not in self.key_points:
            self.key_points.append(point)

    def update_token_stats(self, used: int, window: int) -> None:
        """引擎在每次调用模型后回填 token 占用（供压缩 / 结束兜底判断）。"""
        if used < 0 or window <= 0:
            raise ValueError("token 统计非法：used >= 0 且 window > 0")
        self.token_used = used
        self.token_window = window

    def needs_token_compression(self) -> bool:
        """token 占用是否达到压缩阈值（90% 窗口）。"""
        return self.token_ratio >= TOKEN_COMPRESS_RATIO

    # -- 结束条件 ----------------------------------------------------------

    def check_auto_finish(self) -> FinishReason | None:
        """自动结束判定（优先级：token 硬上限 > 达到最大轮数）。

        用户主动结束由 ``finish(FinishReason.USER_END)`` 处理，优先级最高。
        """
        if self.status is SessionStatus.FINISHED:
            return self.finish_reason
        if self.token_window > 0 and self.token_ratio >= TOKEN_HARD_LIMIT_RATIO:
            return FinishReason.TOKEN_LIMIT
        if self.turn_count >= MAX_TURNS:
            return FinishReason.MAX_TURNS
        return None

    def finish(self, reason: FinishReason = FinishReason.USER_END) -> FinishReason:
        """结束会话（幂等：已结束则返回首次结束原因）。"""
        if not isinstance(reason, FinishReason):
            raise ValueError(f"未知结束原因: {reason!r}")
        if self.status is SessionStatus.FINISHED:
            assert self.finish_reason is not None
            return self.finish_reason
        self.status = SessionStatus.FINISHED
        self.ended_at = time.time()
        self.finish_reason = reason
        return reason

    # -- 序列化（供 API 响应 / 模块 D 评分） -------------------------------

    def snapshot(self) -> dict[str, Any]:
        """面向前端的会话状态（不含简历 / JD 原文，降低暴露面）。"""
        return {
            "session_id": self.session_id,
            "scene": self.scene,
            "status": self.status.value,
            "turn_count": self.questions_asked,
            "answered_turns": self.turn_count,
            "questions_asked": self.questions_asked,
            "current_question": (
                self.current_question.to_dict() if self.current_question else None
            ),
            "follow_up_depth": self.follow_up_depth,
            "same_direction_streak": self.same_direction_streak,
            "key_points": list(self.key_points),
            "summary": self.rolling_summary,
            "summary_version": self.summary_version,
            "context_usage_pct": self.context_usage_pct,
            "finish_reason": self.finish_reason.value if self.finish_reason else None,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "token_stats": {
                "used": self.token_used,
                "window": self.token_window,
                "ratio": round(self.token_ratio, 4),
            },
        }

    def transcript(self) -> list[dict[str, Any]]:
        """完整多轮对话记录（模块 D 评分输入）。"""
        return [turn.to_dict() for turn in self.turns]


# ---------------------------------------------------------------------------
# 题库加载（模型不可用时的离线兜底）
# ---------------------------------------------------------------------------


def load_question_bank(path: str | Path | None = None) -> list[dict[str, Any]]:
    """加载 ``data/questions/intern_questions.jsonl``（一行一个 JSON 对象）。

    文件缺失 / 行损坏时跳过该行，不抛错（离线演示优先）。
    """
    file_path = Path(path) if path is not None else DEFAULT_QUESTIONS_PATH
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return []
    items: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and str(item.get("question") or "").strip():
            items.append(item)
    return items


def _clip(text: str, limit: int = 300) -> str:
    """压缩空白并截断（用于 Prompt / pinned facts，控制 token）。"""
    compact = " ".join(str(text or "").split())
    return compact if len(compact) <= limit else compact[:limit] + "…"


# ---------------------------------------------------------------------------
# 依赖协议（T1 公共底座接线用；本文件不 import 具体实现，避免测试被依赖卡住）
# ---------------------------------------------------------------------------


class ChatStreamProtocol(Protocol):
    """``model_router`` 需满足的最小接口（流式）。"""

    def chat_stream(
        self,
        route: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> Iterable[Any]:
        """按模块名流式返回增量块（``StreamChunk.delta``）。"""
        ...

    def chat(
        self,
        route: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> Any:
        """非流式调用（返回含 ``.text`` 的结果对象）。"""
        ...


class ContextManagerProtocol(Protocol):
    """``context_manager`` 需满足的最小接口。"""

    def add_user(self, text: str) -> None: ...

    def add_assistant(self, text: str) -> None: ...

    def add_pinned_fact(self, fact: str) -> None: ...

    def build_messages(self) -> list[dict[str, str]]: ...

    def token_usage(self) -> int: ...

    def compress(self, force: bool = False) -> bool: ...

    def should_compress(self) -> bool: ...


# ---------------------------------------------------------------------------
# 面试官 Agent（引擎）
# ---------------------------------------------------------------------------


class InterviewAgent:
    """模块 C 面试官 Agent：首问生成 / 多轮追问 / 流式输出 / 摘要与 token 兜底。

    依赖注入设计（便于 mock 单测与离线演示）：
    - ``router``：具备 ``chat(route, messages) -> .text`` 与 ``chat_stream(route, messages)``；
      默认懒加载 ``backend.services.model_router.get_router()``；
    - ``context_factory(system_prompt) -> ContextManager``：默认懒加载
      ``ContextManager.from_route("interview_agent", system_prompt=...)``；
    - ``prompts_dir``：Prompt 目录（默认 ``prompts/interviewer``）；
    - ``question_bank``：题库种子（默认加载 ``data/questions/intern_questions.jsonl``）。

    模型调用失败（无 Key / 401 / 超时）时降级为题库 / 规则式追问语，保证演示不中断。
    """

    def __init__(
        self,
        *,
        router: Any = None,
        context_factory: Callable[[str], Any] | None = None,
        prompts_dir: str | Path | None = None,
        question_bank: list[dict[str, Any]] | None = None,
    ) -> None:
        self._router = router
        self._context_factory = context_factory
        self._prompts_dir = Path(prompts_dir) if prompts_dir is not None else DEFAULT_PROMPTS_DIR
        self._question_bank = (
            question_bank if question_bank is not None else load_question_bank()
        )
        self._sessions: dict[str, InterviewSession] = {}
        self._contexts: dict[str, Any] = {}
        self._templates: dict[str, tuple[str, str]] = {}

    # ---------------- 依赖懒加载 ----------------

    def _get_router(self) -> Any:
        if self._router is None:
            from .model_router import get_router  # 延迟导入，避免纯逻辑测试拉起 yaml / openai

            self._router = get_router()
        return self._router

    def _new_context(self, system_prompt: str) -> Any:
        if self._context_factory is not None:
            return self._context_factory(system_prompt)
        from .context_manager import ContextManager  # 延迟导入

        return ContextManager.from_route("interview_agent", system_prompt=system_prompt)

    def _templates_for(self, scene: str) -> tuple[str, str]:
        if scene not in self._templates:
            base = (self._prompts_dir / "system_base.md").read_text(encoding="utf-8")
            scene_path = self._prompts_dir / f"scene_{scene}.md"
            scene_template = scene_path.read_text(encoding="utf-8") if scene_path.is_file() else ""
            self._templates[scene] = (base, scene_template)
        return self._templates[scene]

    # ---------------- 会话读取 / 状态 ----------------

    def _require_session(self, session_id: str) -> InterviewSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"会话不存在: {session_id!r}")
        return session

    def _role_vars(self, session: InterviewSession) -> dict[str, str]:
        return {
            "company": session.company or DEFAULT_COMPANY,
            "role": session.role or DEFAULT_ROLES.get(session.scene, "面试岗位"),
            "style": session.style or DEFAULT_STYLES.get(session.scene, "专业、自然"),
        }

    def _render_system_prompt(
        self, session: InterviewSession, rule_id: RuleId | None
    ) -> str:
        base, scene_template = self._templates_for(session.scene)
        return build_system_prompt(
            base,
            scene_template,
            scene=session.scene,
            rule_id=rule_id,
            **self._role_vars(session),
        )

    def _build_messages(
        self, session: InterviewSession, rule_id: RuleId | None
    ) -> list[dict[str, Any]]:
        """组装本轮调用消息：system（含本轮规则）+ 上下文窗口历史。"""
        context = self._contexts[session.session_id]
        messages = list(context.build_messages())
        system_prompt = self._render_system_prompt(session, rule_id)
        if messages and messages[0].get("role") == "system":
            messages[0] = {"role": "system", "content": system_prompt}
        else:
            messages.insert(0, {"role": "system", "content": system_prompt})
        return messages

    def _sync_token_stats(self, session: InterviewSession) -> None:
        """从上下文管理器回填 token 用量（失败静默：不能因计数中断面试）。"""
        context = self._contexts.get(session.session_id)
        if context is None:
            return
        try:
            used = int(context.token_usage())
            window = int(getattr(context, "context_window_tokens", 0) or 0)
            if used >= 0 and window > 0:
                session.update_token_stats(used, window)
        except Exception:  # noqa: BLE001 - token 计数失败不影响主流程
            logger.debug("token 统计失败，跳过本轮回填", exc_info=True)

    def _maybe_summarize(self, session: InterviewSession) -> None:
        """每 5 轮滚动摘要 + token ≥90% 压缩兜底（失败静默，保证面试不中断）。"""
        context = self._contexts.get(session.session_id)
        if context is None:
            return
        try:
            should = session.should_summarize() or session.needs_token_compression()
            if not should:
                return
            context.compress(force=True)
            summary = getattr(context, "summary", None)
            if summary:
                session.mark_summarized(str(summary))
        except Exception:  # noqa: BLE001 - 摘要失败不能中断面试
            logger.warning("上下文摘要失败，跳过本轮摘要", exc_info=True)

    # ---------------- 追问决策 / 出题 ----------------

    def _detect_jd_conflict(self, session: InterviewSession, answer: str) -> bool:
        """D3 的轻量启发式：回答出现否定表述且命中 JD 中提到的技术名词。"""
        jd_text = session.jd_text.strip()
        if not jd_text:
            return False
        if not any(marker in answer for marker in _NEGATION_MARKERS):
            return False
        jd_lower = jd_text.lower()
        answer_lower = answer.lower()
        return any(
            term.lower() in jd_lower and term.lower() in answer_lower
            for term in TECH_TERMS
        )

    def _decide_rule(self, session: InterviewSession, answer: str) -> RuleId:
        if session.should_force_switch():
            return "D5"
        return decide_rule(
            answer,
            follow_up_depth=session.follow_up_depth,
            jd_conflict=self._detect_jd_conflict(session, answer),
        )

    def _pick_new_direction(self, session: InterviewSession) -> str:
        """选一个尚未考察（其次考察最少）的方向作为新方向。"""
        directions = DIRECTIONS_BY_SCENE.get(
            session.scene, DIRECTIONS_BY_SCENE[DEFAULT_SCENE]
        )
        counts = session.direction_counts
        for direction in directions:
            if counts.get(direction, 0) == 0:
                return direction
        return min(directions, key=lambda item: (counts.get(item, 0), directions.index(item)))

    def _next_direction(self, session: InterviewSession, rule_id: RuleId) -> str:
        if rule_id in ("D4", "D5") or session.last_direction is None:
            return self._pick_new_direction(session)
        return session.last_direction

    def _first_turn_user_prompt(self, session: InterviewSession) -> str:
        parts = ["面试开始。请基于以下信息提出第一个面试问题（只输出问题正文，不要编号或解释）。"]
        parts.append(f"【岗位】{self._role_vars(session)['role']}")
        if session.resume_text.strip():
            parts.append(f"【候选人简历】\n{_clip(session.resume_text, 1200)}")
        if session.jd_text.strip():
            parts.append(f"【岗位 JD】\n{_clip(session.jd_text, 800)}")
        return "\n\n".join(parts)

    _RULE_FALLBACK_QUESTION: dict[RuleId, str] = {
        "D1": "能说得再具体一些吗？比如当时的具体做法，以及可以衡量的结果。",
        "D2": "能再往下讲一层原理吗？为什么这样选，而不是另一种方案？",
        "D3": "我理解可能是这样，能具体说说你的实际做法吗？",
    }

    def _fallback_question(
        self, session: InterviewSession, *, direction: str, rule_id: RuleId | None
    ) -> str:
        """模型不可用时的兜底出题：追问用规则式问法，换向用题库种子。"""
        if rule_id in self._RULE_FALLBACK_QUESTION:
            return self._RULE_FALLBACK_QUESTION[rule_id]
        candidates = [
            item
            for item in self._question_bank
            if item.get("scene") == session.scene and item.get("direction") == direction
        ]
        if not candidates:
            candidates = [item for item in self._question_bank if item.get("scene") == session.scene]
        if not candidates:
            candidates = list(self._question_bank)
        if not candidates:
            return "能先介绍一下你最近做的一个项目吗？"
        index = session.questions_asked % len(candidates)
        return str(candidates[index].get("question") or "能先介绍一下你最近做的一个项目吗？")

    def _generate_question(
        self,
        session: InterviewSession,
        *,
        direction: str,
        rule_id: RuleId | None,
    ) -> str:
        """调用模型生成下一问；失败降级题库 / 规则式问法。"""
        messages = self._build_messages(session, rule_id)
        try:
            result = self._get_router().chat("interview_agent", messages)
            text = str(getattr(result, "text", "") or "").strip()
            if text:
                return text
        except Exception:  # noqa: BLE001 - 模型不可用 → 离线兜底
            logger.warning("面试提问模型调用失败，降级题库兜底", exc_info=True)
        return self._fallback_question(session, direction=direction, rule_id=rule_id)

    def _state_payload(self, session: InterviewSession) -> dict[str, Any]:
        """会话状态（docs/api.md 契约键：turn_index / questions_asked / summary /
        context_usage_pct / ended；``asked_questions`` 为 /turns 契约的同义键）。"""
        asked = [item.to_dict() for item in session.asked_questions]
        return {
            "session_id": session.session_id,
            "scene": session.scene,
            "turn_index": session.turn_count,
            "asked_questions": asked,
            "questions_asked": asked,
            "rule_id": (
                session.current_question.rule_id if session.current_question else None
            ),
            "summary": session.rolling_summary,
            "summary_version": session.summary_version,
            "context_usage_pct": session.context_usage_pct,
            "ended": session.status is SessionStatus.FINISHED,
            "finish_reason": (
                session.finish_reason.value if session.finish_reason else None
            ),
        }

    def _usage_payload(
        self, session: InterviewSession, completion_chars: int = 0
    ) -> dict[str, Any]:
        """SSE done 事件 usage（契约键：prompt_tokens / completion_tokens / total_tokens）。"""
        completion_tokens = max(0, int(completion_chars))
        return {
            "prompt_tokens": session.token_used,
            "completion_tokens": completion_tokens,
            "total_tokens": session.token_used + completion_tokens,
        }

    # ---------------- 对外接口（契约冻结 v1） ----------------

    def create_session(
        self,
        resume_text: str,
        *,
        jd_text: str | None = None,
        scene: str = DEFAULT_SCENE,
        company: str | None = None,
        role: str | None = None,
        style: str | None = None,
    ) -> dict[str, Any]:
        """创建面试会话并生成首问（POST /api/interview/sessions）。

        返回 ``docs/api.md`` 契约 data：``{session_id, question, state}``
        （create 时 ``state.turn_index=0``、``state.asked_questions=[]``）。
        """
        if not (resume_text or "").strip():
            raise ValueError("简历文本 resume_text 不能为空")
        session = InterviewSession.create(
            scene=scene,
            company=company or "",
            role=role or "",
            style=style or "",
            resume_text=resume_text,
            jd_text=jd_text or "",
        )
        system_prompt = self._render_system_prompt(session, None)
        context = self._new_context(system_prompt)
        self._sessions[session.session_id] = session
        self._contexts[session.session_id] = context

        context.add_pinned_fact(f"面试场景：{SCENE_LABELS[session.scene]}")
        context.add_pinned_fact(f"目标岗位：{self._role_vars(session)['role']}")
        if session.resume_text.strip():
            context.add_pinned_fact(f"简历要点：{_clip(session.resume_text, 160)}")
        if session.jd_text.strip():
            context.add_pinned_fact(f"JD 要点：{_clip(session.jd_text, 160)}")

        direction = self._pick_new_direction(session)
        context.add_user(self._first_turn_user_prompt(session))
        question_text = self._generate_question(
            session, direction=direction, rule_id=None
        )
        question = session.begin_question(question_text, direction=direction, rule_id=None)
        context.add_assistant(question_text)
        self._sync_token_stats(session)

        # docs/api.md 契约：data = {session_id, question, state}
        state = self._state_payload(session)
        state["turn_index"] = 0
        state["asked_questions"] = []
        return {
            "session_id": session.session_id,
            "question": question.to_dict(),
            "state": state,
        }

    def submit_answer(self, session_id: str, answer: str) -> dict[str, Any]:
        """提交一轮回答并返回下一问（POST /api/interview/sessions/{sid}/turns）。"""
        session = self._require_session(session_id)
        context = self._contexts[session_id]

        turn = session.record_answer(answer)
        context.add_user(turn.answer)
        self._sync_token_stats(session)

        finish_reason = session.check_auto_finish()
        if finish_reason is not None:
            session.finish(finish_reason)
            return {
                "session_id": session_id,
                "turn_index": turn.turn,
                "question": None,
                "state": self._state_payload(session),
            }

        rule_id = self._decide_rule(session, turn.answer)
        direction = self._next_direction(session, rule_id)
        question_text = self._generate_question(
            session, direction=direction, rule_id=rule_id
        )
        question = session.begin_question(question_text, direction=direction, rule_id=rule_id)
        context.add_assistant(question_text)

        self._maybe_summarize(session)
        self._sync_token_stats(session)
        finish_reason = session.check_auto_finish()
        if finish_reason is not None:
            session.finish(finish_reason)

        return {
            "session_id": session_id,
            "turn_index": turn.turn,
            "question": question.to_dict(),
            "state": self._state_payload(session),
        }

    def stream_turn(self, session_id: str, answer: str) -> Iterator[dict[str, Any]]:
        """流式提交一轮回答（POST /api/interview/sessions/{sid}/turns/stream）。

        产出事件（契约冻结 v1）：
        - ``{"event": "delta", "text": "..."}``（增量文本）
        - ``{"event": "done", "question": {...}, "state": {...}, "usage": {...}}``
        - ``{"event": "error", "code": 2001, "message": "...", "retryable": true}``
        """
        session = self._require_session(session_id)
        context = self._contexts[session_id]

        turn = session.record_answer(answer)
        context.add_user(turn.answer)
        self._sync_token_stats(session)

        finish_reason = session.check_auto_finish()
        if finish_reason is not None:
            session.finish(finish_reason)
            yield {
                "event": "done",
                "question": None,
                "state": self._state_payload(session),
                "usage": self._usage_payload(session),
            }
            return

        rule_id = self._decide_rule(session, turn.answer)
        direction = self._next_direction(session, rule_id)
        messages = self._build_messages(session, rule_id)

        pieces: list[str] = []
        try:
            for chunk in self._get_router().chat_stream("interview_agent", messages):
                delta = str(getattr(chunk, "delta", "") or "")
                if delta:
                    pieces.append(delta)
                    yield {"event": "delta", "text": delta}
        except Exception:  # noqa: BLE001 - 流式失败 → 离线兜底，不让面试中断
            logger.warning("面试流式生成失败，降级兜底出题", exc_info=True)

        question_text = "".join(pieces).strip()
        if not question_text:
            question_text = self._fallback_question(
                session, direction=direction, rule_id=rule_id
            )
            yield {"event": "delta", "text": question_text}

        question = session.begin_question(question_text, direction=direction, rule_id=rule_id)
        context.add_assistant(question_text)

        self._maybe_summarize(session)
        self._sync_token_stats(session)
        finish_reason = session.check_auto_finish()
        if finish_reason is not None:
            session.finish(finish_reason)

        yield {
            "event": "done",
            "question": question.to_dict(),
            "state": self._state_payload(session),
            "usage": self._usage_payload(session, len(question_text)),
        }

    def end_session(
        self, session_id: str, reason: FinishReason = FinishReason.USER_END
    ) -> dict[str, Any]:
        """主动结束面试（POST /api/interview/sessions/{sid}/end）。

        返回 ``docs/api.md`` 契约 data：``{session_id, transcript, state, report_input}``。
        """
        session = self._require_session(session_id)
        session.finish(reason)
        return {
            "session_id": session_id,
            "transcript": session.transcript(),
            "state": self._state_payload(session),
            "report_input": self._report_input(session),
        }

    def get_session(self, session_id: str) -> dict[str, Any]:
        """查询会话状态（GET /api/interview/sessions/{sid}）。"""
        return self._state_payload(self._require_session(session_id))

    def _report_input(self, session: InterviewSession) -> dict[str, Any]:
        """模块 D 评分输入（docs/api.md 契约键：resume_text / jd_text / scene / turn_count）。"""
        role_vars = self._role_vars(session)
        return {
            "resume_text": session.resume_text,
            "jd_text": session.jd_text,
            "scene": session.scene,
            "turn_count": session.turn_count,
            "company": role_vars["company"],
            "role": role_vars["role"],
            "style": role_vars["style"],
            "transcript": session.transcript(),
            "summary": session.rolling_summary,
            "key_points": list(session.key_points),
            "duration_seconds": round(session.elapsed_seconds, 3),
            "finish_reason": session.finish_reason.value if session.finish_reason else None,
        }


# ---------------------------------------------------------------------------
# 进程内单例（供 backend/api/interview.py 接线；会话状态需跨请求保留）
# ---------------------------------------------------------------------------

_DEFAULT_AGENT: "InterviewAgent | None" = None


def get_interview_agent() -> "InterviewAgent":
    """返回进程内单例 ``InterviewAgent``（内存会话状态跨请求保留）。"""
    global _DEFAULT_AGENT
    if _DEFAULT_AGENT is None:
        _DEFAULT_AGENT = InterviewAgent()
    return _DEFAULT_AGENT


def reset_interview_agent() -> None:
    """重置单例（测试隔离 / 热重载用）。"""
    global _DEFAULT_AGENT
    _DEFAULT_AGENT = None
