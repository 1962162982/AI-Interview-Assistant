"""模块 B JD 匹配服务测试（mock router：不依赖真实 Key、不做真实模型调用）

运行：
    AI-Interview-Assistant\\.venv\\Scripts\\python.exe -m pytest tests/test_jd_matching.py -q

契约（Lead 冻结 v1）：
- 输出 data：{score, summary, items:[{capability,status,evidence,comment}], gaps:[{capability,gap,suggestion}]}
- 两段式均走 backend.services.model_router.get_router().chat_json，服务内禁止新建 OpenAI 客户端。
覆盖：两段式调用顺序 / mock router 注入 adapter / 第 1 段失败降级 / 第 2 段失败降级 /
      覆盖与差距一致性校验 / T6 文本输入路径（resume_text 先结构化再匹配）。
"""
from __future__ import annotations

import json
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import ValidationError  # noqa: E402

from backend.schemas.jd_structured import JDStructured  # noqa: E402
from backend.schemas.resume_structured import ResumeStructured  # noqa: E402
from backend.services import jd_matching as jm  # noqa: E402
from backend.services import resume_structure as rs  # noqa: E402
from backend.services.model_router import ModelJSONError  # noqa: E402

JD_TEXT = "岗位职责：负责后端接口开发。任职要求：熟悉 Python，了解 Redis。"
JD_PAYLOAD = {
    "title": "后端开发实习生",
    "responsibilities": ["负责后端接口开发"],
    "capabilities": [
        {"capability": "Python", "level": "熟悉", "evidence": "熟悉 Python"},
        {"capability": "Redis", "level": "了解", "evidence": "了解 Redis"},
    ],
}
RESUME = ResumeStructured(
    name="张一帆",
    education=[],
    experiences=[],
    skills=["Python", "MySQL"],
    projects=[],
)
RESUME_PAYLOAD = RESUME.model_dump()
MATCH_PAYLOAD = {
    "score": 78,
    "summary": "Python 已覆盖，Redis 未体现，建议补缓存实践。",
    "items": [
        {"capability": "Python", "status": "matched", "evidence": "技能清单含 Python", "comment": "课程项目使用"},
        {"capability": "Redis", "status": "missing", "evidence": "简历未提及", "comment": "无语料"},
    ],
    "gaps": [{"capability": "Redis", "gap": "无缓存实践", "suggestion": "补一个 Redis 缓存小项目并写清效果"}],
}

#: T6 文本路径：简历原文（含标题分区，可被 resume_structure 规则分段兜底）
RAW_RESUME_TEXT = """张一帆
求职意向：后端开发实习生
教育经历
某某大学 计算机科学与技术 本科 2023.09–2027.06
实习经历
星野科技 后端开发实习生 2025.07–2025.09
- 负责内部工单系统接口开发
项目经历
校园二手交易平台
- 使用 Python + MySQL 实现订单接口
技能清单
- Python、MySQL、Git
"""


class FakeRouter:
    """mock 路由器：只实现 chat_json，按预设输出依次返回或抛异常。"""

    def __init__(self, *outputs: object) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, object]] = []

    def chat_json(self, route: str, messages: list[dict[str, str]], schema: type, **kwargs: object):
        self.calls.append(
            {
                "route": route,
                "messages": [dict(message) for message in messages],
                "schema": schema,
                "kwargs": dict(kwargs),
            }
        )
        if not self.outputs:
            raise AssertionError("FakeRouter 的预设输出已耗尽")
        item = self.outputs.pop(0)
        if isinstance(item, BaseException):
            raise item
        try:
            value = schema.model_validate(item)  # type: ignore[attr-defined]
        except ValidationError as exc:
            raise ModelJSONError(route, "glm-4-air", json.dumps(item, ensure_ascii=False), str(exc)) from exc
        return SimpleNamespace(
            value=value,
            result=SimpleNamespace(text=json.dumps(item, ensure_ascii=False)),
            repair_attempts=kwargs.get("max_repair", 0),
        )


def _patched(fake: FakeRouter):
    """只 patch jd_matching.get_router（结构化路径）。"""
    return patch.object(jm, "get_router", return_value=fake)


def _patched_both(fake: FakeRouter):
    """同时 patch jd_matching + resume_structure 的 get_router（文本路径：先结构化再匹配）。"""
    stack = ExitStack()
    stack.enter_context(patch.object(jm, "get_router", return_value=fake))
    stack.enter_context(patch.object(rs, "get_router", return_value=fake))
    return stack


class TestPromptsAndMessages(unittest.TestCase):
    def test_both_prompts_have_v2_header(self) -> None:
        for name in ("jd_structured.md", "jd_matching.md"):
            first_line = (ROOT / "prompts" / name).read_text(encoding="utf-8").splitlines()[0]
            self.assertIn("prompt_version: v2", first_line)

    def test_build_jd_messages_renders_jd(self) -> None:
        content = jm.build_jd_messages(JD_TEXT)[0]["content"]
        self.assertIn("JD 结构化抽取", content)
        self.assertIn("熟悉 Python", content)
        self.assertNotIn("{{", content)

    def test_build_match_messages_renders_both_json(self) -> None:
        content = jm.build_match_messages(RESUME, JD_PAYLOAD)[0]["content"]
        self.assertIn("JD 匹配评分", content)
        self.assertIn("张一帆", content)  # ResumeStructured JSON
        self.assertIn("capabilities", content)  # JDStructured JSON
        self.assertNotIn("{{", content)

    def test_empty_jd_raises(self) -> None:
        with self.assertRaises(ValueError):
            jm.build_jd_messages("   ")
        with self.assertRaises(ValueError):
            jm.extract_jd_structured("   ", max_retries=0)


class TestJDExtraction(unittest.TestCase):
    def test_extract_via_mock_router(self) -> None:
        fake = FakeRouter(dict(JD_PAYLOAD))
        with _patched(fake):
            jd = jm.extract_jd_structured(JD_TEXT)
        self.assertIsInstance(jd, JDStructured)
        self.assertEqual(len(jd.capabilities), 2)
        self.assertEqual(fake.calls[0]["route"], "jd_matching")
        self.assertIs(fake.calls[0]["schema"], JDStructured)
        self.assertEqual(fake.calls[0]["kwargs"].get("max_repair"), 2)

    def test_extract_model_json_error_raises_extraction_error(self) -> None:
        error = ModelJSONError("jd_matching", "glm-4-air", "坏 JD 输出", "bad")
        fake = FakeRouter(error)
        with _patched(fake):
            with self.assertRaises(jm.JDStructureExtractionError) as ctx:
                jm.extract_jd_structured(JD_TEXT)
        self.assertEqual(ctx.exception.raw_text, "坏 JD 输出")

    def test_extract_semantic_invalid_level_raises(self) -> None:
        bad = json.loads(json.dumps(JD_PAYLOAD, ensure_ascii=False))
        bad["capabilities"][0]["level"] = "熟练"  # 不在 LEVELS 内
        fake = FakeRouter(bad)
        with _patched(fake):
            with self.assertRaises(jm.JDStructureExtractionError) as ctx:
                jm.extract_jd_structured(JD_TEXT)
        self.assertIn("level", str(ctx.exception))


class TestMatchResumeToJD(unittest.TestCase):
    def test_mock_router_injection_adapter_two_stage(self) -> None:
        """adapter 测试：两段式都必须经 get_router().chat_json，且顺序与 Schema 正确。"""
        fake = FakeRouter(dict(JD_PAYLOAD), dict(MATCH_PAYLOAD))
        with _patched(fake):
            result = jm.match_resume_to_jd(RESUME, JD_TEXT)

        self.assertEqual(len(fake.calls), 2)
        first, second = fake.calls
        self.assertEqual(first["route"], "jd_matching")
        self.assertIs(first["schema"], JDStructured)
        self.assertIn("JD 结构化抽取", first["messages"][0]["content"])  # type: ignore[index]
        self.assertIs(second["schema"], jm.JDMatchPayload)
        self.assertIn("JD 匹配评分", second["messages"][0]["content"])  # type: ignore[index]
        # 第 2 段只送结构化 JSON，不再送 JD 原文（严格两段式）
        self.assertNotIn("岗位职责：负责后端接口开发", second["messages"][0]["content"])  # type: ignore[index]
        self.assertEqual(second["kwargs"].get("max_repair"), 2)

        self.assertFalse(result.degraded)
        self.assertEqual(result.score, 78)
        self.assertEqual(len(result.items), 2)
        self.assertEqual(len(result.gaps), 1)

    def test_skip_step1_when_jd_structured_provided(self) -> None:
        fake = FakeRouter(dict(MATCH_PAYLOAD))
        with _patched(fake):
            result = jm.match_resume_to_jd(
                RESUME, jd_structured=JDStructured.model_validate(JD_PAYLOAD)
            )
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(result.score, 78)

    def test_accepts_dict_jd_structured(self) -> None:
        fake = FakeRouter(dict(MATCH_PAYLOAD))
        with _patched(fake):
            result = jm.match_resume_to_jd(RESUME, jd_structured=JD_PAYLOAD)
        self.assertEqual(result.score, 78)

    def test_step1_failure_degrades(self) -> None:
        error = ModelJSONError("jd_matching", "glm-4-air", "坏 JD 输出", "bad")
        fake = FakeRouter(error)
        with _patched(fake):
            result = jm.match_resume_to_jd(RESUME, JD_TEXT)
        self.assertTrue(result.degraded)
        self.assertIsNone(result.score)
        self.assertEqual(result.raw_text, "坏 JD 输出")
        self.assertEqual(len(fake.calls), 1)  # 第 1 段失败后不再进入第 2 段

    def test_step1_semantic_failure_degrades(self) -> None:
        bad = json.loads(json.dumps(JD_PAYLOAD, ensure_ascii=False))
        bad["capabilities"][0]["evidence"] = ""
        fake = FakeRouter(bad)
        with _patched(fake):
            result = jm.match_resume_to_jd(RESUME, JD_TEXT)
        self.assertTrue(result.degraded)

    def test_step2_failure_degrades(self) -> None:
        error = ModelJSONError("jd_matching", "glm-4-air", "坏匹配输出", "bad")
        fake = FakeRouter(error)
        with _patched(fake):
            result = jm.match_resume_to_jd(
                RESUME, jd_structured=JDStructured.model_validate(JD_PAYLOAD)
            )
        self.assertTrue(result.degraded)
        self.assertIsNone(result.score)
        self.assertEqual(result.raw_text, "坏匹配输出")

    def test_items_not_covering_capabilities_degrades(self) -> None:
        bad = json.loads(json.dumps(MATCH_PAYLOAD, ensure_ascii=False))
        bad["items"] = [item for item in bad["items"] if item["capability"] != "Redis"]
        fake = FakeRouter(bad)
        with _patched(fake):
            result = jm.match_resume_to_jd(
                RESUME, jd_structured=JDStructured.model_validate(JD_PAYLOAD)
            )
        self.assertTrue(result.degraded)

    def test_gap_for_matched_capability_degrades(self) -> None:
        bad = json.loads(json.dumps(MATCH_PAYLOAD, ensure_ascii=False))
        bad["gaps"].append({"capability": "Python", "gap": "x", "suggestion": "y"})
        fake = FakeRouter(bad)
        with _patched(fake):
            result = jm.match_resume_to_jd(
                RESUME, jd_structured=JDStructured.model_validate(JD_PAYLOAD)
            )
        self.assertTrue(result.degraded)

    def test_status_alias_normalized(self) -> None:
        payload = json.loads(json.dumps(MATCH_PAYLOAD, ensure_ascii=False))
        payload["items"][0]["status"] = "已覆盖"
        fake = FakeRouter(payload)
        with _patched(fake):
            result = jm.match_resume_to_jd(
                RESUME, jd_structured=JDStructured.model_validate(JD_PAYLOAD)
            )
        self.assertEqual(result.items[0].status, "matched")

    def test_score_out_of_range_degrades(self) -> None:
        bad = json.loads(json.dumps(MATCH_PAYLOAD, ensure_ascii=False))
        bad["score"] = 120
        fake = FakeRouter(bad)
        with _patched(fake):
            result = jm.match_resume_to_jd(
                RESUME, jd_structured=JDStructured.model_validate(JD_PAYLOAD)
            )
        self.assertTrue(result.degraded)

    def test_requires_jd_input(self) -> None:
        with self.assertRaises(ValueError):
            jm.match_resume_to_jd(RESUME, None)

    def test_negative_max_retries_raises(self) -> None:
        with self.assertRaises(ValueError):
            jm.match_resume_to_jd(RESUME, JD_TEXT, max_retries=-1)

    def test_service_has_no_self_built_openai_client(self) -> None:
        source = Path(jm.__file__).read_text(encoding="utf-8")
        self.assertIn("from backend.services.model_router import", source)
        self.assertNotIn("OpenAI(", source)
        self.assertNotIn("from openai import", source)

    def test_markdown_render(self) -> None:
        fake = FakeRouter(dict(MATCH_PAYLOAD))
        with _patched(fake):
            result = jm.match_resume_to_jd(
                RESUME, jd_structured=JDStructured.model_validate(JD_PAYLOAD)
            )
        markdown = jm.format_match_markdown(result)
        self.assertIn("匹配度：78", markdown)
        self.assertIn("已覆盖", markdown)
        self.assertIn("差距清单", markdown)


class TestTextInputPath(unittest.TestCase):
    """T6：/api/jd/match 的 {resume_text, jd_text} 文本输入路径（先结构化再两段式匹配）。"""

    def test_text_path_structures_then_matches(self) -> None:
        fake = FakeRouter(dict(RESUME_PAYLOAD), dict(JD_PAYLOAD), dict(MATCH_PAYLOAD))
        with _patched_both(fake):
            result = jm.match_resume_to_jd(resume_text=RAW_RESUME_TEXT, jd_text=JD_TEXT)

        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(fake.calls[0]["route"], "resume_diagnosis")  # resume_structure 复用 A 路由
        self.assertIs(fake.calls[0]["schema"], ResumeStructured)
        self.assertEqual(fake.calls[1]["route"], "jd_matching")
        self.assertIs(fake.calls[1]["schema"], JDStructured)
        self.assertIs(fake.calls[2]["schema"], jm.JDMatchPayload)
        self.assertEqual(result.score, 78)
        self.assertFalse(result.degraded)
        self.assertNotIn(jm.RESUME_DEGRADED_NOTE, result.summary)

    def test_positional_plain_text_resume_uses_text_path(self) -> None:
        fake = FakeRouter(dict(RESUME_PAYLOAD), dict(JD_PAYLOAD), dict(MATCH_PAYLOAD))
        with _patched_both(fake):
            result = jm.match_resume_to_jd(RAW_RESUME_TEXT, JD_TEXT)
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(result.score, 78)

    def test_resume_text_with_jd_structured_skips_jd_extraction(self) -> None:
        fake = FakeRouter(dict(RESUME_PAYLOAD), dict(MATCH_PAYLOAD))
        with _patched_both(fake):
            result = jm.match_resume_to_jd(
                resume_text=RAW_RESUME_TEXT,
                jd_structured=JDStructured.model_validate(JD_PAYLOAD),
            )
        self.assertEqual(len(fake.calls), 2)  # 结构化 + 匹配（跳过第 1 段 JD 抽取）
        self.assertEqual(result.score, 78)

    def test_json_string_resume_stays_structured_path(self) -> None:
        fake = FakeRouter(dict(MATCH_PAYLOAD))
        with _patched(fake):
            result = jm.match_resume_to_jd(
                json.dumps(RESUME_PAYLOAD, ensure_ascii=False),
                jd_structured=JDStructured.model_validate(JD_PAYLOAD),
            )
        self.assertEqual(len(fake.calls), 1)  # JSON 字符串按结构化路径处理
        self.assertEqual(result.score, 78)

    def test_structure_degraded_falls_back_and_notes_summary(self) -> None:
        structure_error = ModelJSONError("resume_diagnosis", "deepseek-chat", "坏结构化输出", "bad")
        fake = FakeRouter(structure_error, dict(MATCH_PAYLOAD))
        with _patched_both(fake):
            result = jm.match_resume_to_jd(
                resume_text=RAW_RESUME_TEXT,
                jd_structured=JDStructured.model_validate(JD_PAYLOAD),
            )
        # 规则分段兜底后仍继续匹配
        self.assertEqual(len(fake.calls), 2)
        self.assertFalse(result.degraded)
        self.assertEqual(result.score, 78)
        self.assertIn(jm.RESUME_DEGRADED_NOTE, result.summary)
        # 规则分段应解析出姓名并进入匹配消息
        self.assertIn("张一帆", fake.calls[1]["messages"][0]["content"])  # type: ignore[index]

    def test_match_resume_text_to_jd_convenience(self) -> None:
        fake = FakeRouter(dict(RESUME_PAYLOAD), dict(JD_PAYLOAD), dict(MATCH_PAYLOAD))
        with _patched_both(fake):
            result = jm.match_resume_text_to_jd(RAW_RESUME_TEXT, JD_TEXT)
        self.assertEqual(result.score, 78)
        self.assertEqual(len(fake.calls), 3)

    def test_resume_and_resume_text_conflict_raises(self) -> None:
        with self.assertRaises(ValueError):
            jm.match_resume_to_jd(RESUME, JD_TEXT, resume_text=RAW_RESUME_TEXT)

    def test_missing_resume_input_raises(self) -> None:
        with self.assertRaises(ValueError):
            jm.match_resume_to_jd(None, jd_structured=JD_PAYLOAD)


if __name__ == "__main__":
    unittest.main()
