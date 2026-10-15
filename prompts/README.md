# prompts/ — 核心资产一：全部 Prompt

> 纯 Prompt 工程路线下，Prompt 质量 = 效果天花板。本目录与代码同仓库，全部纳入 git 版本管理。

## 铁律（摘自实行清单「Prompt 迭代纪律」）

1. 每个 Prompt 文件头部必须带版本注释（v1 / v2 / v3 … + 变更摘要）
2. 任何 Prompt 修改 → 先跑 `evaluation/run_eval.py` → 分数不降才允许 commit
3. 分数下降 → 回滚，禁止“感觉好了就提交”
4. `prompts/` 与 `evaluation/` 的 git 历史 = 项目效果演化的直接证据，结题报告直接引用

## 版本注释格式

```markdown
<!-- prompt_version: v1 | changed: YYYY-MM-DD | summary: 变更摘要（一句话） -->
```

## 文件清单

| 文件 | 所属模块 | Prompt 策略 |
| --- | --- | --- |
| interviewer/system_base.md | C 模拟面试 | 角色设定 + 追问决策树 + 对话 few-shot |
| interviewer/scene_intern.md | C 模拟面试 | 实习场景变体 |
| interviewer/scene_autumn.md | C 模拟面试 | 秋招场景变体 |
| interviewer/scene_grad.md | C 模拟面试 | 保研场景变体 |
| resume_diagnosis.md | A 简历诊断 | 检查清单式 + few-shot 改写 |
| jd_structured.md | B JD 匹配 | 两段式第一步：JD 抽取（低温 0.2） |
| jd_matching.md | B JD 匹配 | 两段式第二步：对齐评分 |
| scoring.md | D 即时反馈 | 显式 rubric + 锚定示例 + 强制 grounding |
| cross_review.md | D 即时反馈 | 交叉复核（不同模型） |
