"""模块 C：模拟面试（v1 骨架，阶段 2 实现）

- 面试官 Agent：基于简历 + JD 生成首问（prompts/interviewer/）
- 会话状态对象：当前题目 / 已问列表 / 追问深度 / 候选人关键回答摘要
- 多轮追问引擎：按 prompts/interviewer/system_base.md 的追问决策树动态追问、深挖
- 每 5 轮触发滚动摘要，接入 context_manager；token 接近上限自动压缩，保证 30 分钟面试不中断
- 场景切换：实习 / 秋招 / 保研（三个 scene 变体文件）
"""
