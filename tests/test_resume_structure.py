"""模块 A 辅助服务测试：简历纯文本 → ResumeStructured（mock router，无 Key）

运行：
    AI-Interview-Assistant\\.venv\\Scripts\\python.exe -m pytest tests/test_resume_structure.py -q

覆盖：Prompt v1 资产 / mock router 注入 adapter / 模型成功路径 /
      ModelJSONError 后规则分段降级（degraded 标记 + raw_text）/ 空输入 /
      真实虚构素材 smoke（data/resumes）。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import ValidationError  # noqa: E402

from backend.schemas.resume_structured import ResumeStructured  # noqa: E402
from backend.services import resume_structure as rs  # noqa: E402
from backend.services.model_router import ModelJSONError  # noqa: E402

SAMPLE_TEXT = """张一帆
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

STRUCTURED_PAYLOAD = {
    "name": "张一帆",
    "education": [
        {"school": "某某大学", "major": "计算机科学与技术", "degree": "本科", "period": "2023.09–2027.06"}
    ],
    "experiences": [
        {
            "organization": "星野科技",
            "role": "后端开发实习生",
            "period": "2025.07–2025.09",
            "highlights": ["负责内部工单系统接口开发"],
        }
    ],
    "skills": ["Python", "MySQL", "Git"],
    "projects": [
        {
            "name": "校园二手交易平台",
            "role": "",
            "description": "",
            "highlights": ["使用 Python + MySQL 实现订单接口"],
        }
    ],
}


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
            raise ModelJSONError(route, "deepseek-chat", json.dumps(item, ensure_ascii=False), str(exc)) from exc
        return SimpleNamespace(
            value=value,
            result=SimpleNamespace(text=json.dumps(item, ensure_ascii=False)),
            repair_attempts=kwargs.get("max_repair", 0),
        )


def _patched(fake: FakeRouter):
    return patch.object(rs, "get_router", return_value=fake)


class TestPromptAsset(unittest.TestCase):
    def test_prompt_v1_header_and_contract(self) -> None:
        path = ROOT / "prompts" / "resume_structured.md"
        content = path.read_text(encoding="utf-8")
        first_line = content.splitlines()[0]
        self.assertIn("prompt_version: v1", first_line)
        self.assertIn("{{resume_text}}", content)
        for key in ("name", "education", "experiences", "skills", "projects"):
            self.assertIn(key, content, f"Prompt 契约缺少键：{key}")
        self.assertIn("禁止编造", content)
        self.assertIn("few-shot", content)

    def test_build_messages_renders_resume(self) -> None:
        messages = rs.build_messages("张一帆 简历正文")
        self.assertEqual(len(messages), 1)
        content = messages[0]["content"]
        self.assertIn("张一帆", content)
        self.assertIn("简历结构化抽取", content)
        self.assertNotIn("{{", content)

    def test_empty_resume_raises(self) -> None:
        with self.assertRaises(ValueError):
            rs.build_messages("   ")
        with self.assertRaises(ValueError):
            rs.structure_resume("   ")


class TestStructureResumeSuccess(unittest.TestCase):
    def test_mock_router_injection_adapter(self) -> None:
        fake = FakeRouter(dict(STRUCTURED_PAYLOAD))
        with _patched(fake):
            result = rs.structure_resume(SAMPLE_TEXT)

        self.assertFalse(result.degraded)
        self.assertEqual(result.raw_text, "")
        self.assertIsInstance(result.data, ResumeStructured)
        self.assertEqual(result.data.name, "张一帆")
        self.assertEqual(len(result.data.education), 1)
        self.assertEqual(len(fake.calls), 1)
        call = fake.calls[0]
        self.assertEqual(call["route"], "resume_diagnosis")  # 复用模块 A 路由
        self.assertIs(call["schema"], ResumeStructured)
        self.assertEqual(call["kwargs"].get("max_repair"), 2)
        self.assertIn("张一帆", call["messages"][0]["content"])  # type: ignore[index]

    def test_custom_max_retries_passed_to_router(self) -> None:
        fake = FakeRouter(dict(STRUCTURED_PAYLOAD))
        with _patched(fake):
            rs.structure_resume(SAMPLE_TEXT, max_retries=0)
        self.assertEqual(fake.calls[0]["kwargs"].get("max_repair"), 0)

    def test_to_structured_helper(self) -> None:
        fake = FakeRouter(dict(STRUCTURED_PAYLOAD))
        with _patched(fake):
            result = rs.structure_resume(SAMPLE_TEXT)
        self.assertIs(rs.to_structured(result), result.data)

    def test_service_uses_router_and_no_self_built_client(self) -> None:
        source = Path(rs.__file__).read_text(encoding="utf-8")
        self.assertIn("from backend.services.model_router import", source)
        self.assertNotIn("OpenAI(", source)
        self.assertNotIn("from openai import", source)

    def test_negative_max_retries_raises(self) -> None:
        with self.assertRaises(ValueError):
            rs.structure_resume(SAMPLE_TEXT, max_retries=-1)


class TestDegradedFallback(unittest.TestCase):
    def test_model_json_error_degrades_to_rule_segmentation(self) -> None:
        error = ModelJSONError("resume_diagnosis", "deepseek-chat", "坏结构化输出", "bad json")
        fake = FakeRouter(error)
        with _patched(fake):
            result = rs.structure_resume(SAMPLE_TEXT)

        self.assertTrue(result.degraded)
        self.assertEqual(result.raw_text, "坏结构化输出")
        self.assertIsInstance(result.data, ResumeStructured)
        # 规则分段应抽出姓名 / 教育 / 实习 / 项目 / 技能
        self.assertEqual(result.data.name, "张一帆")
        self.assertGreaterEqual(len(result.data.education), 1)
        self.assertGreaterEqual(len(result.data.experiences), 1)
        self.assertGreaterEqual(len(result.data.projects), 1)
        self.assertIn("Python", result.data.skills)


class TestSegmentResumeText(unittest.TestCase):
    def test_rule_segmentation(self) -> None:
        data = rs.segment_resume_text(SAMPLE_TEXT)
        self.assertEqual(data.name, "张一帆")
        self.assertEqual(data.education[0].school, "某某大学")
        self.assertEqual(data.education[0].degree, "本科")
        self.assertEqual(data.experiences[0].organization, "星野科技")
        self.assertEqual(data.experiences[0].highlights, ["负责内部工单系统接口开发"])
        self.assertEqual(data.projects[0].name, "校园二手交易平台")
        self.assertIn("Python", data.skills)

    def test_unknown_name_when_no_header(self) -> None:
        data = rs.segment_resume_text("教育经历\n某某大学 计算机科学与技术 本科 2022.09–2026.06")
        self.assertEqual(data.name, rs.UNKNOWN_NAME)

    def test_empty_text_raises(self) -> None:
        with self.assertRaises(ValueError):
            rs.segment_resume_text("   ")

    def test_real_fictional_data_smoke(self) -> None:
        """真实虚构素材 smoke：data/resumes 的简历可被规则分段（无模型调用）。"""
        resume_path = ROOT / "data" / "resumes" / "resume_intern_01.txt"
        data = rs.segment_resume_text(resume_path.read_text(encoding="utf-8"))
        self.assertEqual(data.name, "张一帆")
        self.assertGreaterEqual(len(data.education), 1)
        self.assertIn("Python", data.skills)


if __name__ == "__main__":
    unittest.main()
