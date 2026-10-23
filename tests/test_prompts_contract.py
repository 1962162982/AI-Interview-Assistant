"""Prompt 资产守卫测试（只读校验，不调用模型 / 不依赖 Key）

运行：
    $env:TMP = '<repo>\\.tmp'; $env:TEMP = $env:TMP
    AI-Interview-Assistant\\.venv\\Scripts\\python.exe -m pytest tests/test_prompts_contract.py -q -p no:cacheprovider

覆盖（Lead 验收项 + T6 新 Prompt）：
1) 全部 Prompt 文件首行版本注释格式合法（v2+；T6 新增的 v1 草稿见 PENDING_V2_FILES，验收后晋级）；
2) resume_diagnosis：5 项检查清单 + ≥3 组“原句→改写”few-shot + 强制引用原文；
3) jd_structured 低温抽取 / JSON 输出、jd_matching 两段式第二步与逐项 evidence；
4) 无 TODO / {{未替换}} / 明显占位符残留，{{}} 变量必须在白名单内；
5) 实际 Prompt 文件与 prompts/README.md 文件清单表格一致（README 同步由 Lead 负责，
   PENDING_README_SYNC 内的新文件在同步前允许暂缺、同步后自动纳入强校验）。
"""
from __future__ import annotations

import re
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROMPTS_DIR = ROOT / "prompts"
README_PATH = PROMPTS_DIR / "README.md"

#: 首行版本注释契约：<!-- prompt_version: v2 | changed: YYYY-MM-DD | summary: ... -->
VERSION_LINE_RE = re.compile(
    r"^<!-- prompt_version: v(?P<version>\d+) \| changed: (?P<changed>\d{4}-\d{2}-\d{2})"
    r" \| summary: (?P<summary>.+) -->$"
)

#: {{xxx}} 模板变量白名单（渲染侧见 services 与模块 C；出现白名单外变量视为未替换占位符）
ALLOWED_PLACEHOLDERS = {
    "resume_text",
    "jd_text",
    "resume_json",
    "jd_json",
    "company",
    "role",
    "style",
    "scene",
    "rule_id",
    "rule_instruction",
}

#: 明显占位符 / 骨架残留（大小写不敏感）
LEFTOVER_RE = re.compile(r"\bTODO\b|\bFIXME\b|\bXXX\b|未替换|待实现", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"\{\{([^{}]*)\}\}")

#: 新增且尚处 v1 草稿阶段的 Prompt（T6；Lead 验收后再晋级 v2）
PENDING_V2_FILES = {"resume_structured.md"}
#: 已创建、等待 Lead 同步 prompts/README.md 文件清单的 Prompt（同步后自动纳入强校验）
PENDING_README_SYNC = {"resume_structured.md"}

#: 允许内容仍待 T4 完善、但必须出现在 README 清单中的 Prompt
T4_OWNED_FILES = {"scoring.md", "cross_review.md"}
SCENE_FILES = {"interviewer/scene_intern.md", "interviewer/scene_autumn.md", "interviewer/scene_grad.md"}


def prompt_files() -> list[Path]:
    """全部 Prompt 文件（不含规范 README）。"""
    return sorted(path for path in PROMPTS_DIR.rglob("*.md") if path.name != "README.md")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def rel_name(path: Path) -> str:
    return path.relative_to(PROMPTS_DIR).as_posix()


def readme_listed_files() -> dict[str, list[str]]:
    """解析 prompts/README.md 文件清单表格：{相对文件名: 该行单元格}。"""
    listed: dict[str, list[str]] = {}
    for line in read_text(README_PATH).splitlines():
        line = line.strip()
        if not (line.startswith("|") and ".md" in line):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells:
            continue
        match = re.search(r"([\w./-]+\.md)", cells[0])
        if match:
            listed[match.group(1)] = cells
    return listed


class TestVersionHeaders(unittest.TestCase):
    """验收项 1：全部 Prompt 文件首行版本注释格式合法。"""

    def test_at_least_eight_prompt_files_exist(self) -> None:
        files = prompt_files()
        self.assertGreaterEqual(len(files), 8, f"Prompt 文件不足 8 个：{[rel_name(p) for p in files]}")

    def test_first_line_version_comment_format(self) -> None:
        for path in prompt_files():
            with self.subTest(prompt=rel_name(path)):
                lines = read_text(path).splitlines()
                self.assertTrue(lines, f"{rel_name(path)} 为空文件")
                first_line = lines[0].strip()
                match = VERSION_LINE_RE.match(first_line)
                self.assertIsNotNone(
                    match,
                    f"{rel_name(path)} 首行版本注释格式非法：{first_line!r}",
                )
                name = rel_name(path)
                version = int(match.group("version"))
                min_version = 1 if name in PENDING_V2_FILES else 2
                self.assertGreaterEqual(
                    version,
                    min_version,
                    f"{name} 版本过低（v{version}，要求 ≥v{min_version}）",
                )
                # 日期必须是真实日期（YYYY-MM-DD）
                datetime.strptime(match.group("changed"), "%Y-%m-%d")
                summary = match.group("summary").strip()
                self.assertTrue(summary, f"{name} summary 为空")
                self.assertNotRegex(
                    summary, r"\bTODO\b|待补|占位", f"{name} summary 仍含占位描述"
                )


class TestResumeDiagnosisPrompt(unittest.TestCase):
    """验收项 2：A 的 5 项清单 + few-shot 改写 + 强制引用原文。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.content = read_text(PROMPTS_DIR / "resume_diagnosis.md")

    def test_five_check_items_explicit(self) -> None:
        for item in ("量化表达", "动词弱化", "关键词对齐", "结构问题", "错别字与格式"):
            with self.subTest(check_item=item):
                self.assertIn(item, self.content, f"resume_diagnosis 缺少检查项：{item}")

    def test_at_least_three_rewrite_few_shots(self) -> None:
        self.assertGreaterEqual(self.content.count("### 示例"), 3, "few-shot 示例少于 3 组")
        self.assertGreaterEqual(self.content.count("原句"), 3, "缺少“原句”示例")
        self.assertGreaterEqual(self.content.count("改写"), 3, "缺少“改写”示例")
        # 每组示例都应同时出现“原句”与“改写”语义
        self.assertIn("原句", self.content)
        self.assertIn("rewrite", self.content)

    def test_force_quote_original_text(self) -> None:
        self.assertIn("强制引用原文", self.content)
        self.assertIn("original_quote", self.content)
        self.assertIn("逐字摘录", self.content)

    def test_json_output_contract_keys(self) -> None:
        for key in ("summary", "issues", "strengths", "check_item", "priority", "problem", "rewrite"):
            with self.subTest(key=key):
                self.assertIn(key, self.content, f"输出契约缺少键：{key}")

    def test_declares_template_placeholders(self) -> None:
        self.assertIn("{{resume_text}}", self.content)
        self.assertIn("{{jd_text}}", self.content)


class TestResumeStructuredPrompt(unittest.TestCase):
    """T6：简历结构化抽取 Prompt（v1）的抽取规则 / JSON 契约 / few-shot。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.content = read_text(PROMPTS_DIR / "resume_structured.md")

    def test_v1_header(self) -> None:
        first_line = self.content.splitlines()[0]
        self.assertIn("prompt_version: v1", first_line)

    def test_extraction_rules_and_forbid_fabrication(self) -> None:
        self.assertIn("禁止编造", self.content)
        self.assertIn("逐字摘录", self.content)
        self.assertIn("{{resume_text}}", self.content)

    def test_json_contract_keys(self) -> None:
        for key in ("name", "education", "experiences", "skills", "projects"):
            with self.subTest(key=key):
                self.assertIn(key, self.content, f"ResumeStructured 契约缺少键：{key}")

    def test_has_few_shot_example(self) -> None:
        self.assertIn("few-shot", self.content)
        self.assertIn("```json", self.content)


class TestJDPromptsContract(unittest.TestCase):
    """验收项 3：B1 低温抽取 JSON、B2 两段式第二步与逐项 evidence。"""

    def test_jd_structured_low_temperature_json(self) -> None:
        content = read_text(PROMPTS_DIR / "jd_structured.md")
        self.assertIn("0.2", content, "jd_structured 未声明低温 0.2")
        self.assertIn("JSON", content)
        self.assertIn("{{jd_text}}", content)
        for key in ("title", "responsibilities", "capabilities", "capability", "level", "evidence"):
            with self.subTest(key=key):
                self.assertIn(key, content, f"JDStructured 输出契约缺少键：{key}")
        self.assertIn("逐字摘录", content, "能力项必须含 JD 原文依据")
        self.assertIn("只输出", content, "缺少只输出 JSON 的约束")

    def test_jd_matching_two_stage_and_evidence(self) -> None:
        content = read_text(PROMPTS_DIR / "jd_matching.md")
        self.assertIn("两段式", content)
        self.assertIn("第二步", content)
        self.assertIn("不得重新抽取", content, "第二步禁止重新抽取 JD")
        for key in ("score", "summary", "items", "gaps", "capability", "status", "evidence"):
            with self.subTest(key=key):
                self.assertIn(key, content, f"匹配输出契约缺少键：{key}")
        self.assertIn("来自简历", content, "逐项 evidence 必须来自简历")
        self.assertIn("{{resume_json}}", content)
        self.assertIn("{{jd_json}}", content)


class TestNoPlaceholderLeftovers(unittest.TestCase):
    """验收项 4：无 TODO / {{未替换}} / 明显占位符残留。"""

    def test_no_todo_or_skeleton_markers(self) -> None:
        for path in prompt_files():
            with self.subTest(prompt=rel_name(path)):
                content = read_text(path)
                hits = LEFTOVER_RE.findall(content)
                self.assertEqual(hits, [], f"{rel_name(path)} 仍有骨架残留：{hits}")
                self.assertNotIn("{{未替换}}", content)

    def test_placeholder_variables_in_whitelist(self) -> None:
        for path in prompt_files():
            with self.subTest(prompt=rel_name(path)):
                used = set(PLACEHOLDER_RE.findall(read_text(path)))
                unknown = used - ALLOWED_PLACEHOLDERS
                self.assertEqual(unknown, set(), f"{rel_name(path)} 存在未知模板变量：{sorted(unknown)}")

    def test_required_placeholders_present(self) -> None:
        required_by_prompt = {
            "resume_diagnosis.md": {"resume_text", "jd_text"},
            "resume_structured.md": {"resume_text"},
            "jd_structured.md": {"jd_text"},
            "jd_matching.md": {"resume_json", "jd_json"},
            "interviewer/system_base.md": {"company", "role", "style", "scene"},
        }
        for name, required in required_by_prompt.items():
            with self.subTest(prompt=name):
                used = set(PLACEHOLDER_RE.findall(read_text(PROMPTS_DIR / name)))
                missing = required - used
                self.assertEqual(missing, set(), f"{name} 缺少必填模板变量：{sorted(missing)}")


class TestReadmeFileListConsistency(unittest.TestCase):
    """验收项 5：实际 Prompt 文件与 prompts/README.md 文件清单表格一致。"""

    def test_readme_lists_every_prompt_file(self) -> None:
        listed = set(readme_listed_files())
        actual = {rel_name(path) for path in prompt_files()}
        # README 不应列出不存在的文件
        self.assertEqual(
            listed - actual, set(), f"README 清单存在不存在的文件：{sorted(listed - actual)}"
        )
        # 所有 Prompt 文件都应进 README 清单；仅 PENDING_README_SYNC（等 Lead 同步）允许暂缺
        missing = actual - listed
        unexpected = missing - PENDING_README_SYNC
        self.assertEqual(unexpected, set(), f"README 文件清单缺少：{sorted(unexpected)}")

    def test_scene_and_t4_files_listed(self) -> None:
        listed = set(readme_listed_files())
        for name in sorted(SCENE_FILES | T4_OWNED_FILES):
            with self.subTest(prompt=name):
                self.assertIn(name, listed, f"README 文件清单缺少：{name}")

    def test_readme_rows_have_required_columns(self) -> None:
        rows = readme_listed_files()
        self.assertGreaterEqual(len(rows), 8, "README 文件清单条目不足 8 条")
        for name, cells in rows.items():
            with self.subTest(prompt=name):
                self.assertGreaterEqual(len(cells), 3, f"{name} 行缺少列（文件/模块/模型/策略）")
                self.assertTrue(all(cells[1:4]), f"{name} 行存在空列：{cells}")


if __name__ == "__main__":
    unittest.main()
