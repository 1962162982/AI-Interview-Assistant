<!-- prompt_version: v1 | changed: 2026-10-15 | summary: 初版骨架：两段式第一步（JD 抽取）占位 -->

# JD 结构化抽取 Prompt（两段式 · 第一步）

> Prompt 策略：两段式调用，禁止一步到位。第一次调用：JD → JDStructured 能力项 JSON，**低温 0.2**。
> 拆分理由：单次“抽取并匹配”易遗漏；拆开后每步可控、可分别评测。

## 输入

- 目标 JD 全文

## 输出

JSON（按 backend/schemas/jd_structured.py 校验）：岗位 / 职责 / 能力项（含要求程度与 JD 原文依据）。

TODO（阶段 2 定稿）。
