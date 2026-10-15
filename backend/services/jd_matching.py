"""模块 B：JD 匹配（v1 骨架，阶段 2 实现）

两段式调用（禁止一步到位）：
1. JD → JDStructured 能力项 JSON（低温 0.2，prompts/jd_structured.md）
2. 简历结构化 JSON + JD 能力项 JSON → 逐项对齐 + 评分（prompts/jd_matching.md）

输出：匹配度评分（0–100）+ 差距清单（缺什么、怎么补）。
"""
