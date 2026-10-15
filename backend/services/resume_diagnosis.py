"""模块 A：简历诊断（v1 骨架，阶段 2 实现）

流程：简历解析（PDF/Word）→ 结构化抽取（ResumeStructured）→ 检查清单式诊断 → few-shot 改写。
Prompt：prompts/resume_diagnosis.md（5 项检查清单 + 3–5 组“原句 → 改写”示例）。
输出：诊断报告（问题清单 + 优先级 + 改写示例）。
"""
