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
| [docs/final_report.md](docs/final_report.md) | **项目报告**（测试 / 实验 / 交付物索引 / 快速开始） |
| [docs/deployment.md](docs/deployment.md) | 部署与单进程演示指南（含一键脚本说明） |

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

> **临时联调模式（2026-10-23，用户指示：先全 DeepSeek，跑通后再混用）**：本机 `.env` 通过
> `MODELS_CONFIG_PATH=backend/config/models.deepseek.yaml` 临时把 5 个路由全部指向 DeepSeek
> （`deepseek-v4-pro` / `deepseek-flash`，评分与复核仍异模型）；删除该变量即恢复上表决策④配置。
> 详见 [docs/decision_log.md](docs/decision_log.md)。

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
├── evaluation/              # ⭐⭐ 核心资产二：金标准（20 简历 / 30 Q&A）+ run_eval 强制回归 + 阶段4实验
├── data/                    # 素材库（LLM 虚构样例）：resumes / jds / questions / demo
├── frontend/                # 交互层：Vue 3 + Vite 五页 + 真实后端联调（可 mock / 单进程托管 / 雷达图）
├── scripts/                 # 冒烟 / 真实 E2E / 部署自检 / 一键演示 脚本
├── tests/                   # 314 passed + 1 skipped + 80 subtests（全 mock；真实链路另测）
└── docs/                    # api.md / architecture.md / decision_log.md / deployment.md / final_report.md
```

## 当前进度（2026-10-23 · 阶段 1/3/4/5 交付完成，临时全 DeepSeek 模式）

**已完成**
- 公共底座：`model_router`（5 路由 / 重试 / 超时 / 流式 / JSON 修复）、`token_counter`、`context_manager`、PDF/DOCX 解析、FastAPI 四组路由（统一响应壳 + 错误码）。
- 四模块服务首版：A 简历诊断（结构化抽取 + 诊断）、B JD 匹配（两段式）、C 模拟面试（三场景 + 追问决策树 + 会话状态 + 流式）、D 评分 / 交叉复核 / 报告。
- Prompt v2：A/B/C/D 全部模块 + 三场景变体（10 个文件，全部带版本注释）。
- 评测：`evaluation/` 金标准样本（**20 份简历 + 30 条 Q&A**）+ `run_eval.py` 强制回归（v_old vs v_new，分数下降非零退出）+ 评分一致性抽样 / 模型分配 A/B 实验脚本。
- 前端：Vue 3 + Vite 五页 + 真实后端联调（SSE 增量渲染 / 加载错误态 / localStorage 历史）；真实模式构建通过，可 mock / 单进程托管。
- 测试与契约：`docs/api.md` 契约冻结；全仓 **314 passed + 1 skipped + 80 subtests**（全 mock，无需真实 Key）；真实链路另测（见下）。

**真实联调已验证（2026-10-23 · 临时全 DeepSeek 模式）**
- `scripts/smoke_deepseek.py`：模块 A 真实诊断/结构化 + cross_review 真实调用全部通过（exit=0）；
- API 全链路真实调用 200：结构抽取 → 诊断 → JD 提取/匹配 → 模拟面试（含 SSE 流式）→ 报告生成/查询/Markdown，单次 E2E 约 252s；
- Prompt 真实回归：`run_eval.py --real` 子集尝试中止——v_old（`evaluation/prompt_baselines/scoring_v1.md`，按 GLM 调优的 v1 基线）在 DeepSeek-V4-Pro 下无法产出合法 JSON；v_new（scoring v2）已在 E2E 报告链路产出合法 4 维评分。待恢复决策④配置（GLM 评分模型）后重跑全量回归，详见 `docs/decision_log.md`。

**阶段 3 系统集成（2026-10-23，已完成）**
- 前端已切真实后端（`VITE_USE_MOCK=false`）：五页全流程 + SSE 增量渲染 + 加载/错误/取消态 + localStorage 历史；真实模式构建 exit 0（37 modules，产物无 mock 残留）。
- 后端单进程演示：`frontend/dist` 存在时 FastAPI 托管 `/` 与 SPA 回退、`/assets/*`；`/api` 契约不变；CORS（5173）预检通过。
- 真实端到端验收 `scripts/e2e_real.py --with-report`：**13/13 通过**（真实模型链路含报告/Markdown，约 5.6 min）。
- 阶段 4 第一批：golden_qa **20 条**、golden_resumes **8 份**。

**阶段 4 收尾 + 阶段 5 交付（2026-10-23，已完成）**
- 评测集达标：golden_resumes **20 份**、golden_qa **30 条**（三场景、三档锚定、全虚构脱敏）。
- 评分一致性抽样（真实模型）：10 条 medium，平均相对偏差 **12.50%（≤15% PASS）**，最大 29.17%；报告 `evaluation/reports/consistency.md`。
- 模型分配 A/B（真实模型，小样本）：`band_hit_rate` 均 1.000，MAE 混合配置 0.208 < 全 flash 0.292（小样本噪声、不切换）；报告 `evaluation/reports/model_ab.md`。
- 阶段 5 交付：`docs/final_report.md`（项目报告）、`docs/deployment.md` + `scripts/run_demo.ps1`（一键单进程演示）、`scripts/deploy_check.py`（部署自检）、`data/demo/demo-script.md`（3/8 分钟演示脚本）、答辩 FAQ；JD 匹配页新增 **SVG 5 维雷达图**。

**待完成**
- 恢复双供应商混用：补 GLM Key 后删除 `.env` 的 `MODELS_CONFIG_PATH` 切回 `models.yaml`（决策④），并按铁律跑全量真实评测回归（当前 v2 为临时提交口径）；
- 外部事项：邀请 3–5 名同学试用、演示视频录制、在线部署（本地单进程部署已就绪）。

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

有**有效 DeepSeek Key** 时，先跑真实联调冒烟（覆盖模块 A 诊断/结构化 + cross_review，两个 DeepSeek 路由）：

```bash
.\.venv\Scripts\python.exe scripts/smoke_deepseek.py
# 退出码：0=全部通过；2=Key 缺失/鉴权失败；1=其他失败（网络/JSON 校验等）
```

> 注：临时全 DeepSeek 模式下 `evaluation/run_eval.py --real` 也用 DeepSeek Key 即可跑；
> 恢复双供应商混用后才需要 GLM 评分 Key（`EVALUATION_API_KEY`，路由 evaluation = glm-4-plus）。

**真实端到端验收**（需后端已启动；默认跳过报告生成以控制成本）：

```bash
.\.venv\Scripts\python.exe scripts/e2e_real.py                 # 主流程：解析→诊断→JD匹配→面试(SSE)→结束
.\.venv\Scripts\python.exe scripts/e2e_real.py --with-report   # 追加：评分/交叉复核/报告/Markdown
# 退出码：0=通过；1=任一步骤失败；2=无法连接后端
```

前端（需 Node.js 18+；或使用演示机预构建）：

```bash
cd frontend
pnpm install
pnpm dev          # 默认 mock 数据层；本地 .env 可设 VITE_USE_MOCK=false 联调真实后端
pnpm build        # 产物在 frontend/dist（已被 .gitignore 忽略）
```

**单进程演示**（推荐一键脚本；或手动构建后用 FastAPI 同时托管页面与 `/api`）：

```powershell
.\scripts\run_demo.ps1            # 自动检查环境 + 构建前端（如需）+ 启动 uvicorn

# 或手动：
cd frontend && pnpm build && cd ..
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --port 8000
# 打开 http://127.0.0.1:8000 （SPA 页面 + API 同端口）
```

## Prompt 迭代纪律（项目铁律）

1. 每个 Prompt 文件头部必须带版本注释（v1 / v2 / v3 … + 变更摘要）
2. 任何 Prompt 修改 → 先跑 `evaluation/run_eval.py` → 分数不降才允许 commit
3. 分数下降 → 回滚，禁止“感觉好了就提交”
4. `prompts/` 与 `evaluation/` 的 git 历史 = 项目效果演化的直接证据，结题报告直接引用
5. 更换任一模块的模型 / 供应商（决策④）→ 同样先跑评测集，分数不降才允许切换
