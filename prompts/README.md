# prompts/ — 核心资产一：全部 Prompt

> 纯 Prompt 工程路线下，Prompt 质量 = 效果天花板。本目录与代码同仓库，全部纳入 git 版本管理。

## 铁律（摘自实行清单「Prompt 迭代纪律」）

1. 每个 Prompt 文件头部必须带版本注释（v1 / v2 / v3 … + 变更摘要）
2. 任何 Prompt 修改 → 先跑 `evaluation/run_eval.py` → 分数不降才允许 commit
3. 分数下降 → 回滚，禁止“感觉好了就提交”
4. `prompts/` 与 `evaluation/` 的 git 历史 = 项目效果演化的直接证据，结题报告直接引用
5. 更换任一模块的模型 / 供应商（决策④）→ 同样先跑评测集，分数不降才允许切换

## 版本注释格式

```markdown
<!-- prompt_version: v1 | changed: YYYY-MM-DD | summary: 变更摘要（一句话） -->
```

## 模块 ↔ 模型对应（决策④）

每个功能模块绑定一个独立模型；Prompt 效果与目标模型强相关——**同一 Prompt 换模型后必须重跑评测**。

| 模块 | 主模型 | 相关 Prompt |
| --- | --- | --- |
| A 简历诊断 | `deepseek-chat`（DeepSeek-V3） | `resume_diagnosis.md` |
| B JD 匹配 | `glm-4-air`（GLM-4-Air） | `jd_structured.md` / `jd_matching.md` |
| C 模拟面试 | `glm-4-flash`（GLM-4-Flash，免费档） | `interviewer/*.md` |
| D 即时反馈 | 评分 `glm-4-plus`（GLM-4-Plus）；复核 `deepseek-chat`（DeepSeek-V3） | `scoring.md` / `cross_review.md` |

> 配置唯一事实来源：`backend/config/models.yaml`；完整理由见 `docs/decision_log.md` 决策④。
> 编写 Prompt 时以目标模型的特性为准（如格式偏好、上下文长度），必要时按模型做差异化措辞，但改动仍须过评测。

## 文件清单

| 文件 | 所属模块 | 目标模型 | Prompt 策略 |
| --- | --- | --- | --- |
| interviewer/system_base.md | C 模拟面试 | glm-4-flash | 角色设定 + 追问决策树 + 对话 few-shot |
| interviewer/scene_intern.md | C 模拟面试 | glm-4-flash | 实习场景变体 |
| interviewer/scene_autumn.md | C 模拟面试 | glm-4-flash | 秋招场景变体 |
| interviewer/scene_grad.md | C 模拟面试 | glm-4-flash | 保研场景变体 |
| resume_diagnosis.md | A 简历诊断 | deepseek-chat | 检查清单式 + few-shot 改写 |
| jd_structured.md | B JD 匹配 | glm-4-air | 两段式第一步：JD 抽取（低温 0.2） |
| jd_matching.md | B JD 匹配 | glm-4-air | 两段式第二步：对齐评分 |
| scoring.md | D 即时反馈 | glm-4-plus | 显式 rubric + 锚定示例 + 强制 grounding |
| cross_review.md | D 即时反馈 | deepseek-chat | 交叉复核（与评分不同模型） |
