"""模块 D：即时反馈（v1 骨架，阶段 2 实现）

- 实时评分：相关性 / 结构性 / 具体性 / 岗位匹配（各 1–10 分）
- Prompt：prompts/scoring.md（显式 rubric + 锚定 few-shot + 强制引用原文）
- 交叉复核：每场结束由 cross_review 路由（不同模型）复核打分，输出偏差说明
- 输出：InterviewEvaluation（得分 / 亮点 / 短板 / 建议），报告可导出 Markdown
"""
