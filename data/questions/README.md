# data/questions/ — 模块 C 面试题库种子

> 用途：为模块 C 模拟面试提供**锚定简历 / JD 的面试题种子**，同时供模块 D + 评测（T4 `evaluation/golden_qa`）复用。
> 场景标签：`intern`（实习）/ `autumn`（秋招）/ `grad`（保研）三种，默认使用 `intern`。

## 文件

| 文件 | 说明 |
| --- | --- |
| `intern_questions.jsonl` | 面试题库种子（18 条，一行一个 JSON 对象，UTF-8；当前分布：intern 12 / autumn 3 / grad 3） |

## 字段约定

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 题目编号，`Q001` 起；全库唯一，供评测集引用 |
| `scene` | string | 场景标签：`intern` / `autumn` / `grad` |
| `direction` | string | 考察方向：项目经历 / 技术基础 / 学习能力 / 工程实践 / 问题排查 / 团队协作 / 实习动机 / 岗位匹配 / 科研经历 / 专业基础 / 研究兴趣 / 职业规划 |
| `difficulty` | string | 难度枚举：`easy` / `medium` / `hard` |
| `question` | string | 主问题（面试官可直接使用；实际面试中应结合简历 / JD 具体化，不照搬） |
| `followups` | string[] | 2–3 条可追问方向，对应追问决策树 D1（要细节 / 数据来源）或 D2（深挖原理）；**不是固定文案**，由引擎按规则选择 |

## 使用约定

1. **模块 C**：`interview_agent` 按 `scene` 过滤题库，作为离线兜底出题与方向参考；按 `direction` 选方向、按 `difficulty` 控制深度；`question` 只作为出题参考，必须结合 `resume_text` / `jd_text` 具体化（禁止照搬原文，遵守 `prompts/interviewer/system_base.md` 的“必须锚定简历或 JD”）。
2. **模块 D / 评测**：`id` 与 `question` 作为 `evaluation/golden_qa` 的题面键；三档锚定答案（差 1–4 / 中 5–7 / 优 8–10）由 T4 编写，本文件不重复建设。
3. 新增题目保持字段不变；`scene` 取值随场景扩展同步修改本 README。
