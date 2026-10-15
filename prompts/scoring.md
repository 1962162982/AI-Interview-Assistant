<!-- prompt_version: v1 | changed: 2026-10-15 | summary: 初版骨架：rubric + 锚定示例 + 强制 grounding 占位 -->

# 面试评分 Prompt

> Prompt 策略：显式 rubric + 分维度评分 + 强制 grounding（见实行清单 模块 D）。

## 4 个评分维度（各 1–10 分）

1. 相关性
2. 结构性
3. 具体性
4. 岗位匹配

## 强制 grounding（抑制 LLM 乱打分）

- 必须先引用原文证据，再给分
- 引用证据不足两条时，该维度不得高于 6 分

## 评分锚点 few-shot

TODO（阶段 2 为题目写 差（1–4）/ 中（5–7）/ 优（8–10）三档示例答案）。

## 输出

按 backend/schemas/interview_evaluation.py 校验：维度分 + 证据 + 亮点 + 短板 + 建议。
