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
| [docs/api.md](docs/api.md)            | **已冻结**的后端 HTTP 契约（15 条接口 + 统一响应壳 + 错误码） |
| [docs/architecture.md](docs/architecture.md) | 四层架构、端到端数据流与“模块→模型”路由图 |

## 功能模块

| 模块         | 对应需求     | 核心策略                                                     |
| ------------ | ------------ | ------------------------------------------------------------ |
| A 简历诊断   | 简历修改     | 解析 + 结构化抽取 + 5 项检查清单 + few-shot 改写             |
| B JD 匹配    | 岗位 JD 匹配 | 两段式调用：JD 抽取（低温）→ 逐项对齐评分 + 差距分析         |
| C 模拟面试   | 模拟追问     | 角色卡 + 显式追问决策树 + 滚动摘要兜底（实习/秋招/保研三场景） |
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
├── .gitignore               # 保护 .env / uploads/ / cache/ / .venv / node_modules
├── .env.example             # 各模块独立 API Key / Base URL 变量名（复制为 .env 填写）
├── requirements.txt
├── backend/                 # 功能层 + 模型层
│   ├── main.py              # FastAPI 入口（/health + 15 条契约接口）
│   ├── config/models.yaml   # ⭐ 模块级模型路由（每模块独立模型，决策④）
│   ├── api/                 # 路由层（薄壳）：resume / jd / interview / report + common/meta/deps
│   ├── services/            # 业务层：model_router / token_counter / context_manager / parser /
│   │                        #        resume_diagnosis / resume_structure / jd_matching /
│   │                        #        interview_agent / evaluation
│   ├── schemas/             # ⭐ 三个核心 JSON Schema（Pydantic）
│   └── utils/               # 脱敏 mask / token 计数
├── prompts/                 # ⭐⭐ 核心资产一：10 个 Prompt（版本注释 + git 管理）
├── evaluation/              # ⭐⭐ 核心资产二：金标准样本 + run_eval.py 强制回归关卡
├── data/                    # 素材库（LLM 虚构样例）：resumes / jds / questions / demo
├── frontend/                # 交互层：Vue 3 + Vite 五页骨架 + API 层（可切 mock）
├── tests/                   # 274 passed + 80 subtests（全 mock，不需要真实 Key）
└── docs/                    # api.md（契约冻结）/ architecture.md / decision_log.md
```

## 当前进度（2026-10-23 · 阶段 1 完成）

**已完成**
- 公共底座：`model_router`（5 路由 / 重试 / 超时 / 流式 / JSON 修复）、`token_counter`、`context_manager`、PDF/DOCX 解析、FastAPI 四组路由（统一响应壳 + 错误码）。
- 四模块服务首版：A 简历诊断（结构化抽取 + 诊断）、B JD 匹配（两段式）、C 模拟面试（三场景 + 追问决策树 + 会话状态 + 流式）、D 评分 / 交叉复核 / 报告。
- Prompt v2：A/B/C/D 全部模块 + 三场景变体（10 个文件，全部带版本注释）。
- 评测：`evaluation/` 金标准样本（3 份简历 + 10 条 Q&A）+ `run_eval.py` 强制回归（v_old vs v_new，分数下降非零退出）。
- 前端：Vue 3 + Vite 五页骨架 + `src/api/client.js`（按冻结契约，可切真实后端 / mock）；`vite build` 已验证通过。
- 测试与契约：`docs/api.md` 契约冻结；全仓 **274 passed + 80 subtests**（全 mock，无需真实 Key）。

**待完成**
- 真实模型联调：当前 DeepSeek Key 无效、GLM Key 未配置，验证全部走 mock；换有效 Key 后需做一次端到端实测与 Prompt 真实评测回归。
- 阶段 2/3：体验打磨（真流式、历史记录）、评测集扩展（20 简历 / 30 Q&A）、部署与演示脚本。

## 快速开始

```bash
cd AI-Interview-Assistant

# 1. 创建虚拟环境并安装依赖（Python 3.10+）
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt      # Windows PowerShell
# macOS / Linux: source .venv/bin/activate && pip install -r requirements.txt

# 2. 准备环境变量（.env 已被 .gitignore 保护，严禁提交）
Copy-Item .env.example .env      # Windows PowerShell；Linux/macOS 用 cp

# 3. 启动后端
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload
# 打开 http://127.0.0.1:8000/docs 可看到 15 条契约接口
```

无 Key 也能启动与演示：`/api/meta/routes`、`/api/interview/*`（题库降级）可用；依赖模型的接口会按契约返回 `2001/2002` 错误码。

前端（需 Node.js 18+；或使用演示机预构建）：

```bash
cd frontend
pnpm install
pnpm dev          # 默认 mock 数据层
pnpm build        # 产物在 frontend/dist（已被 .gitignore 忽略）
```

## Prompt 迭代纪律（项目铁律）

1. 每个 Prompt 文件头部必须带版本注释（v1 / v2 / v3 … + 变更摘要）
2. 任何 Prompt 修改 → 先跑 `evaluation/run_eval.py` → 分数不降才允许 commit
3. 分数下降 → 回滚，禁止“感觉好了就提交”
4. `prompts/` 与 `evaluation/` 的 git 历史 = 项目效果演化的直接证据，结题报告直接引用
5. 更换任一模块的模型 / 供应商（决策④）→ 同样先跑评测集，分数不降才允许切换
