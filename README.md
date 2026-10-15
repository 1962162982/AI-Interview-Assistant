# AI 模拟面试与简历诊断助手

> 把开题方案落地为一个**可演示、可使用**的产品。
> 拆解逻辑：4 大需求 → 4 个功能模块 → 4 层架构（交互层 / 功能层 / 模型层 / 数据层）。
> 周期参考：10 月 — 次年 1 月（约 12 周）。

**技术路线：纯 Prompt 工程**（不涉及模型微调，控制成本）——`prompts/` 与 `evaluation/` 是本项目最核心的两个资产，全部纳入 git 版本管理，其提交历史即项目效果演化的直接证据。

## 项目文档

| 文件                                  | 说明                                        |
| ------------------------------------- | ------------------------------------------- |
| [实行清单.md](实行清单.md)            | 项目执行清单 v3（范围 / 阶段 / 结构 / 风险 / 决策点） |
| [STORY.md](STORY.md)                  | 开题汇报 PPT 故事骨架（11 页）              |
| [docs/decision_log.md](docs/decision_log.md) | 关键决策记录（含“为什么不上微调”）    |

## 功能模块

| 模块         | 对应需求     | 核心策略                                                     |
| ------------ | ------------ | ------------------------------------------------------------ |
| A 简历诊断   | 简历修改     | 解析 + 结构化抽取 + 5 项检查清单 + few-shot 改写             |
| B JD 匹配    | 岗位 JD 匹配 | 两段式调用：JD 抽取（低温）→ 逐项对齐评分 + 差距分析         |
| C 模拟面试   | 模拟追问     | 角色卡 + 显式追问决策树 + 滚动摘要兜底                       |
| D 即时反馈   | 即时反馈     | rubric 锚定评分 + 强制引用原文 + 交叉复核 + 面试报告         |

## 目录结构

```
├── README.md
├── .gitignore               # 保护 .env / uploads/ / cache/
├── .env.example             # 各模块独立 API Key 变量名（复制为 .env 填写）
├── requirements.txt
├── backend/                 # 功能层 + 模型层
│   ├── main.py              # FastAPI 入口
│   ├── config/models.yaml   # ⭐ 模块级模型路由配置（无微调字段）
│   ├── api/                 # 路由层（薄壳）：resume / jd / interview / report
│   ├── services/            # 业务逻辑层：model_router / context_manager / 四模块 / parser
│   ├── schemas/             # ⭐ 三个核心 JSON Schema（Pydantic）
│   └── utils/               # 脱敏 mask / token 计数
├── prompts/                 # ⭐⭐ 核心资产一：全部 Prompt（版本注释 + git 管理）
├── evaluation/              # ⭐⭐ 核心资产二：Prompt 评测集（强制回归关卡）
├── data/                    # 素材库（虚构样例，可入库）
├── frontend/                # 交互层（技术形态待决策：Web 应用 / Streamlit）
├── tests/
└── docs/                    # architecture / api / decision_log
```

## 快速开始

```bash
cp .env.example .env       # 按模块填入真实 API Key（.env 不进库）
pip install -r requirements.txt
python -m backend.main     # 启动后端（骨架期仅 /health 可用）
```

## Prompt 迭代纪律（项目铁律）

1. 每个 Prompt 文件头部必须带版本注释（v1 / v2 / v3 … + 变更摘要）
2. 任何 Prompt 修改 → 先跑 `evaluation/run_eval.py` → 分数不降才允许 commit
3. 分数下降 → 回滚，禁止“感觉好了就提交”
4. `prompts/` 与 `evaluation/` 的 git 历史 = 项目效果演化的直接证据，结题报告直接引用
