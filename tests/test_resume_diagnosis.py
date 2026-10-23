"""模块 A 简历诊断服务测试（mock router：不依赖真实 Key、不做真实模型调用）

运行：
    AI-Interview-Assistant\\.venv\\Scripts\\python.exe -m pytest tests/test_resume_diagnosis.py -q

契约（Lead 冻结 v1）：
- 输出 data：{summary, issues:[{check_item,priority,original_quote,problem,rewrite}], strengths:[]}
- 模型调用统一走 backend.services.model_router.get_router()，服务内禁止新建 OpenAI 客户端。
覆盖：Prompt v2 版本头 / 占位符渲染 / mock router 注入 adapter / Schema 校验失败降级 / 空输入。
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

from backend.services import resume_diagnosis as rd  # noqa: E402
from backend.services.model_router import ModelJSONError  # noqa: E402

VALID_PAYLOAD = {
    "summary": "整体项目经历尚可，但成果缺少量化，关键词覆盖不足。",
    "issues": [
        {
            "check_item": "弱动词",
            "priority": "medium",
            "original_quote": "参与了校园二手交易平台的后端开发",
            "problem": "使用“参与”弱动词，个人贡献边界不清。",
            "rewrite": "负责校园二手交易平台商品发布、订单创建 2 个接口的设计与实现。",
        },
        {
            "check_item": "1. 量化表达",
            "priority": "高",
            "original_quote": "项目上线后运行较稳定，得到了老师和同学的好评",
            "problem": "成果无任何数字支撑。",
            "rewrite": "上线后支撑日均 300+ 交易请求，接口 P95 响应 180ms。",
        },
    ],
    "strengths": ["课程设计独立完成，代码量约 3000 行"],
}


class FakeRouter:
    """mock 路由器：只实现 chat_json（服务若走 chat / 自建客户端会直接失败）。"""

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
            raise ModelJSONError(route, "fake-model", json.dumps(item, ensure_ascii=False), str(exc)) from exc
        return SimpleNamespace(
            value=value,
            result=SimpleNamespace(text=json.dumps(item, ensure_ascii=False)),
            repair_attempts=kwargs.get("max_repair", 0),
        )


def _patched(fake: FakeRouter):
    return patch.object(rd, "get_router", return_value=fake)


class TestPromptAndMessages(unittest.TestCase):
    def test_prompt_file_has_v2_header(self) -> None:
        first_line = (ROOT / "prompts" / "resume_diagnosis.md").read_text(encoding="utf-8").splitlines()[0]
        self.assertIn("prompt_version: v2", first_line)
        self.assertIn("few-shot", first_line)

    def test_build_messages_renders_placeholders(self) -> None:
        messages = rd.build_messages("简历正文：张一帆", "目标 JD：后端开发实习生")
        self.assertEqual(len(messages), 1)
        content = messages[0]["content"]
        self.assertIn("张一帆", content)
        self.assertIn("后端开发实习生", content)
        self.assertNotIn("{{", content)
        self.assertNotIn("prompt_version", content)  # 版本注释不进入模型上下文

    def test_build_messages_without_jd_marks_not_provided(self) -> None:
        content = rd.build_messages("简历正文", "")[0]["content"]
        self.assertIn("（未提供）", content)
        self.assertNotIn("{{", content)

    def test_empty_resume_raises_without_calling_router(self) -> None:
        fake = FakeRouter()
        with _patched(fake):
            with self.assertRaises(ValueError):
                rd.diagnose_resume("   ")
        self.assertEqual(fake.calls, [])


class TestDiagnoseResumeWithMockRouter(unittest.TestCase):
    def test_mock_router_injection_adapter(self) -> None:
        """adapter 测试：服务必须经 get_router().chat_json 调用、路由/消息/Schema 正确。"""
        fake = FakeRouter(dict(VALID_PAYLOAD))
        with _patched(fake):
            report = rd.diagnose_resume("简历正文：张一帆", "JD 正文：后端开发实习生")

        self.assertEqual(len(fake.calls), 1)
        call = fake.calls[0]
        self.assertEqual(call["route"], "resume_diagnosis")
        self.assertIs(call["schema"], rd.DiagnosisPayload)
        self.assertEqual(call["kwargs"].get("max_repair"), 2)
        self.assertIn("张一帆", call["messages"][0]["content"])  # type: ignore[index]

        self.assertFalse(report.degraded)
        self.assertEqual(len(report.issues), 2)
        self.assertEqual(report.issues[0].priority, "high")  # 高优先级排前（兜底排序）
        self.assertEqual(report.issues[0].check_item, "量化表达")  # 别名归一化
        self.assertEqual(report.issues[1].check_item, "动词弱化")
        self.assertEqual(report.strengths, VALID_PAYLOAD["strengths"])

    def test_custom_max_retries_passed_to_router(self) -> None:
        fake = FakeRouter(dict(VALID_PAYLOAD))
        with _patched(fake):
            rd.diagnose_resume("简历正文", max_retries=1)
        self.assertEqual(fake.calls[0]["kwargs"].get("max_repair"), 1)

    def test_model_json_error_degrades_to_plain_text(self) -> None:
        error = ModelJSONError("resume_diagnosis", "deepseek-chat", "模型原始坏输出", "bad json")
        fake = FakeRouter(error)
        with _patched(fake):
            report = rd.diagnose_resume("简历正文")

        self.assertTrue(report.degraded)
        self.assertEqual(report.issues, [])
        self.assertEqual(report.raw_text, "模型原始坏输出")
        self.assertIn("降级", report.summary)

    def test_schema_invalid_payload_degrades(self) -> None:
        bad = {"summary": "x", "issues": [{"check_item": "量化表达"}], "strengths": []}  # 缺 problem/rewrite
        fake = FakeRouter(bad)
        with _patched(fake):
            report = rd.diagnose_resume("简历正文")
        self.assertTrue(report.degraded)

    def test_negative_max_retries_raises(self) -> None:
        with self.assertRaises(ValueError):
            rd.diagnose_resume("简历正文", max_retries=-1)

    def test_service_has_no_self_built_openai_client(self) -> None:
        source = Path(rd.__file__).read_text(encoding="utf-8")
        self.assertIn("from backend.services.model_router import", source)
        self.assertNotIn("OpenAI(", source)
        self.assertNotIn("from openai import", source)

    def test_markdown_render(self) -> None:
        fake = FakeRouter(dict(VALID_PAYLOAD))
        with _patched(fake):
            report = rd.diagnose_resume("简历正文")
        markdown = rd.format_report_markdown(report)
        self.assertIn("简历诊断报告", markdown)
        self.assertIn("高优先级问题", markdown)
        self.assertIn("原句：", markdown)
        self.assertIn("改写：", markdown)


if __name__ == "__main__":
    unittest.main()
