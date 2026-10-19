# AI 模拟面试与简历诊断助手

> 把开题方案落地为一个**可演示、可使用**的产品。
> 拆解逻辑：4 大需求 → 4 个功能模块 → 4 层架构（交互层 / 功能层 / 模型层 / 数据层）。
> 周期参考：10 月 — 次年 1 月（约 12 周）。

**技术路线：纯 Prompt 工程**（不涉及模型微调，控制成本）——`prompts/` 与 `evaluation/` 是本项目最核心的两个资产，全部纳入 git 版本管理，其提交历史即项目效果演化的直接证据。

## 项目文档

| 文件                                  | 说明                                        |
| ------------------------------------- | ------------------------------------------- |
| [实行清单.md](实行清单.md)            | 项目执行清单 v4（范围 / 阶段 / 结构 / 风险 / 决策点 / 模型分配） |
| [说明书.md](说明书.md)                | 项目详细说明书（定位 / 四模块 / 架构 / Prompt 与评测 / 进度 / 运行指南） |
| [STORY.md](STORY.md)                  | 开题汇报 PPT 故事骨架（11 页）              |
| [docs/decision_log.md](docs/decision_log.md) | 关键决策记录（含“为什么不上微调”、决策④“每模块独立模型”）    |

## 功能模块

| 模块         | 对应需求     | 核心策略                                                     |
| ------------ | ------------ | ------------------------------------------------------------ |
| A 简历诊断   | 简历修改     | 解析 + 结构化抽取 + 5 项检查清单 + few-shot 改写             |
| B JD 匹配    | 岗位 JD 匹配 | 两段式调用：JD 抽取（低温）→ 逐项对齐评分 + 差距分析         |
| C 模拟面试   | 模拟追问     | 角色卡 + 显式追问决策树 + 滚动摘要兜底                       |
| D 即时反馈   | 即时反馈     | rubric 锚定评分 + 强制引用原文 + 交叉复核 + 面试报告         |

## 模型策略：每个模块独立模型（决策④）

四个功能模块**各绑定一个独立模型**，按任务特征选型，避免“一个模型打全场”；评分（evaluation）与复核（cross_review）使用不同模型。

| 模块 | 主模型 | 供应商 | 选型理由 |
| --- | --- | --- | --- |
| A 简历诊断 | `deepseek-chat`（DeepSeek-V3） | DeepSeek | 中文生成/改写质量高、成本低、JSON 稳定 |
| B JD 匹配 | `glm-4-air`（GLM-4-Air） | 智谱 GLM | 性价比高，适合结构化抽取 + 对齐评分 |
| C 模拟面试 | `glm-4-flash`（GLM-4-Flash，免费档） | 智谱 GLM | 免费额度支撑 30–80K token 长对话 + 流式输出 |
| D 即时反馈 | `glm-4-plus`（GLM-4-Plus） | 智谱 GLM | 评分一致性优先 |
| D 交叉复核 | `deepseek-chat`（DeepSeek-V3） | DeepSeek | 跨供应商复核，降低同源偏差 |

> 配置唯一事实来源：`backend/config/models.yaml`；完整理由见 [docs/decision_log.md](docs/decision_log.md) 决策④。
> 更换任一模块的模型/供应商属于效果变更 → 必须先跑评测集、分数不降才允许切换。

## 目录结构

```
├── README.md
├── 说明书.md                # 项目详细说明书（总讲解）
├── 实行清单.md              # 执行清单 v4
├── STORY.md                 # 开题汇报 PPT 故事骨架
├── .gitignore               # 保护 .env / uploads/ / cache/
├── .env.example             # 各模块独立 API Key / Base URL 变量名（复制为 .env 填写）
├── requirements.txt
├── backend/                 # 功能层 + 模型层
│   ├── main.py              # FastAPI 入口
│   ├── config/models.yaml   # ⭐ 模块级模型路由（每模块独立模型，决策④）
│   ├── api/                 # 路由层（薄壳）：resume / jd / interview / report
│   ├── services/            # 业务逻辑层：model_router / context_manager / 四模块 / parser
│   ├── schemas/             # ⭐ 三个核心 JSON Schema（Pydantic）
│   └── utils/               # 脱敏 mask / token 计数
├── prompts/                 # ⭐⭐ 核心资产一：全部 Prompt（版本注释 + git 管理）
├── evaluation/              # ⭐⭐ 核心资产二：Prompt 评测集（强制回归关卡）
├── data/                    # 素材库（虚构样例，可入库）
├── frontend/                # 交互层（Web 应用：Vue 3 + Vite，待搭建）
├── tests/
└── docs/                    # architecture / api / decision_log
```

## 快速开始

```bash
cp .env.example .env       # 按模块填入真实 API Key / Base URL（.env 不进库）
pip install -r requirements.txt
uvicorn backend.main:app --reload   # 启动后端（骨架期仅 /health 可用）
```

## Prompt 迭代纪律（项目铁律）

1. 每个 Prompt 文件头部必须带版本注释（v1 / v2 / v3 … + 变更摘要）
2. 任何 Prompt 修改 → 先跑 `evaluation/run_eval.py` → 分数不降才允许 commit
3. 分数下降 → 回滚，禁止“感觉好了就提交”
4. `prompts/` 与 `evaluation/` 的 git 历史 = 项目效果演化的直接证据，结题报告直接引用
5. 更换任一模块的模型/供应商（决策④）→ 同样先跑评测集，分数不降才允许切换
