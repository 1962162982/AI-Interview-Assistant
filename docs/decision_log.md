# 决策记录（decision_log）

> 记录项目关键决策及理由；结题报告“Prompt 设计方法论与迭代记录”部分的事实来源。

## 2026-10-15 · 确认技术路线：纯 Prompt 工程（不涉及微调）

- **决策**：采用 Prompt + few-shot + 评测迭代路线，不做任何模型微调。
- **理由**：
  1. 控制成本（无训练算力开销）；
  2. 效果天花板由 Prompt 质量决定，`prompts/` 与 `evaluation/` 列为核心资产；
  3. 评测集升级为 Prompt 改动的强制回归关卡，作为质量保障手段。
- **影响**：`models.yaml` 不含微调字段；目录结构删除训练相关预留。

## 2026-10-15 · MVP 范围裁剪

- **决策**：首版仅 Web 端；主线 = 简历诊断 + 模拟面试 + 即时反馈，JD 匹配作为增强。
- **裁剪项**（本版不做）：桌面端、语音对话、微信小程序、雷达图可视化、模型微调。
- **理由**：12 周周期内优先保证主线可演示、可使用。

## 2026-10-15 · 初始化 git 仓库

- **决策**：在项目目录内初始化仓库（分支 main），`prompts/` 与 `evaluation/` 从第一次提交起即纳入版本管理。
- **理由**：Prompt 改动强制回归关卡依赖 git 历史；提交历史即效果演化证据。

## 2026-10-19 · 决策①：技术形态 = Web 应用

- **决策**：采用 Web 应用形态——后端 FastAPI，前端独立轻量前端（Vue 3 + Vite，构建细节后续细化）；不采用 Streamlit 作为最终形态。
- **理由**：
  1. 完整 4 模块需要文件上传（PDF/DOCX 简历解析）、服务端会话与多轮上下文管理，Web 应用的 API + 前后端分离形态最合适；
  2. 清单已裁剪桌面端 / 小程序 / 语音，Web 是唯一保留终端，一套服务覆盖全部使用场景；
  3. Streamlit 在组件定制与流式对话 UI 上受限，不利于面试场景与结题演示。
- **影响**：
  - `frontend/` 目录正式启用（已预留 `frontend/src/views`），按 `frontend/README.md` 规划搭建；
  - `backend/api/` 四组路由（resume / jd / interview / report）从薄壳变为正式契约，`docs/api.md` 随之细化；
  - 模拟面试多轮对话需流式输出（SSE 或 WebSocket），`interview_agent.py` 需支持增量返回；
  - 部署：本地 uvicorn 开发起步，结题前可上云（API 服务 + 前端静态托管）。
- **后续动作**：细化前端技术栈与构建方式；评审 API 契约；联调"上传 → 解析 → 结构化"链路。

## 2026-10-19 · 决策②：大模型来源 = DeepSeek + GLM 双供应商

- **决策**：双供应商策略——DeepSeek 系列为主力，智谱 GLM 系列为辅助 / 备选；两者均提供 OpenAI 兼容接口，用统一 SDK 适配。
- **分工原则（初步，待实测后固化）**：
  - 常规生成类（简历诊断、JD 结构化、JD 匹配、面试对话）：DeepSeek-V3 主力，GLM-4-Flash（免费档）用于开发期调用与备选；
  - 质量敏感类（评分、复核）：GLM-4-Plus 保证一致性，DeepSeek 备选。
- **理由**：
  1. 成本：开发期依托免费额度，验证清单成本测算（单份诊断 ≈3–5K token、单场面试 ≈30–80K token，目标撑 30 场面试 + 50 份诊断）；
  2. 可用性：单供应商限流 / 故障时不中断开发与演示；
  3. 中文场景两者均有竞争力，便于做供应商 A/B 对比，丰富结题素材。
- **影响**：
  - `backend/config/models.yaml` 五路由条目需补全"主模型 / 备模型"两列（本次仅记录决策，暂不改配置）；
  - `.env.example` 已按模块独立 Key（`RESUME_DIAGNOSIS_` / `JD_MATCHING_` / `INTERVIEW_AGENT_` / `EVALUATION_` / `CROSS_REVIEW_`），双供应商下每个模块可能需主 / 备两把 Key；
  - `model_router.py` 需支持按模块路由到不同供应商，并实现主备降级。
- **后续动作**：申请两把 Key 填入 `.env`；用同一条 prompt 对两家做小样本对比，实测后固化 `models.yaml`。

## 2026-10-19 · 决策③：首版范围 = 完整 4 模块

- **决策**：首版即完整 4 模块——① 简历诊断 ② JD 匹配 ③ 模拟面试 ④ 评估报告 / 即时反馈；JD 匹配由 2026-10-15 版清单中的"增强项"升级为主模块。裁剪项维持不变：桌面端、语音对话、微信小程序、雷达图可视化、模型微调本版不做。
- **理由**：
  1. 结题验收要求完整度，4 模块全上更符合预期；
  2. 4 模块共用同一套底座（结构化 JSON Schema + 校验重试 + 模型路由 + 上下文管理），后续模块边际成本低；
  3. JD 匹配与简历诊断共享"结构化解析"产物，可并行开发、互相验证。
- **影响**：
  - 阶段 1 工作量增加：JD 匹配不再后置，需与主线并行排期；
  - `evaluation/golden_resumes` 与 `evaluation/golden_qa` 需按 4 模块准备黄金样本（≥3 份虚构简历 + ≥3 条 JD：实习 / 秋招 / 保研各 1）；
  - `data/jds/`、`data/questions/` 目录激活使用。
- **风险与对策**：
  - 4 模块同时开工易返工 → 按"公共底座 → 简历诊断 → 模拟面试 → JD 匹配 → 评估报告"顺序推进，前一模块评测通过再开下一个；
  - 免费额度被 4 模块分摊后不足 → 开发期重场景用 GLM-4-Flash，正式演示前切换主力模型。
- **后续动作**：先完成清单中的"本周最小一步"（输入简历 + JD → 输出 JSON 首问 + 3 个追问方向），验证公共底座后再铺开 4 模块。

## 2026-10-19 · 决策④：每个模块绑定独立模型（模块级模型差异化）

- **决策**：四个功能模块各绑定一个独立模型，**模块之间不共用同一模型**；`cross_review`（交叉复核）必须与 `evaluation`（评分）使用不同模型。模型分配以 `backend/config/models.yaml` 为唯一事实来源。
- **模型分配（初版，A/B 实测后固化）**：

| 模块 / 路由 | 主模型 | 供应商 | 温度 | 选型理由 |
| --- | --- | --- | --- | --- |
| A 简历诊断 `resume_diagnosis` | `deepseek-chat`（DeepSeek-V3） | DeepSeek | 0.3 | 中文生成 / 改写质量高、成本低、JSON 输出稳定 |
| B JD 匹配 `jd_matching` | `glm-4-air`（GLM-4-Air） | 智谱 GLM | 0.2 | 性价比高的通用档位，适合结构化抽取 + 对齐评分 |
| C 模拟面试 `interview_agent` | `glm-4-flash`（GLM-4-Flash，免费档） | 智谱 GLM | 0.8 | 免费额度可支撑 30–80K token 长对话，支持流式输出 |
| D 即时反馈 `evaluation` | `glm-4-plus`（GLM-4-Plus） | 智谱 GLM | 0.1 | 能力更强的档位，保证评分一致性 |
| D 交叉复核 `cross_review` | `deepseek-chat`（DeepSeek-V3） | DeepSeek | 0.1 | 跨供应商复核（与评分模型不同），降低同源偏差 |

- **理由**：
  1. **任务异质性**：四个模块分别是“生成改写 / 结构化对齐 / 长对话 / 评分”，对模型能力要求不同，单一模型难以在所有任务上都最优；
  2. **优势互补**：DeepSeek 中文生成强，GLM 免费档与评分档覆盖成本与质量两端，按任务分配可强化总体效果；
  3. **降低同源偏差**：模块之间、评分与复核之间不共用同一模型，减少单一模型的系统性偏差；
  4. **成本分层**：token 消耗最大的模拟面试走免费档，质量敏感的评分走强模型，整体成本可控。
- **影响**：
  - `backend/config/models.yaml` 五个路由全部落到具体模型（四个模块四个不同模型；复核与评分不同），并同步更新 `.env.example` 注释；
  - `.env.example` 已按模块独立 Key / Base URL，无需新增变量；
  - `model_router.py` 需按模块路由到不同模型 / 供应商，并实现超时、重试、流式与错误兜底；
  - 前端与业务层不感知具体模型，只按模块名调用；
  - 各模块 Prompt 可按目标模型特征微调（不同模型对格式 / 风格的偏好不同），但改动仍须过评测回归。
- **约束（写入迭代纪律）**：更换任一模块的模型 / 供应商属于效果变更，**必须先跑评测集，分数不降才允许切换**。
- **后续动作**：确认两家 Key（DeepSeek / 智谱 GLM）→ 用黄金样本做小样本 A/B → 实测后固化模型名与温度；效果不达标时只改配置即可换模型。

## 2026-10-23 · 阶段 1 完成记录与 Prompt v2 提交口径（临时）

- **事实**：阶段 1 已交付并分批提交——公共底座（`model_router` / `token_counter` / `context_manager` / parser）、四模块服务首版、10 个 Prompt（A/B/C/D + 三场景 + 简历结构化）、评测回归框架（金标准样本 + `run_eval.py`）、Vue 3 前端骨架、`docs/api.md` 契约冻结；全仓 275 项 mock 测试 + 80 subtests 通过，API 15 条路由与前端构建均已本地验证。
- **Prompt 铁律的临时口径（必须跟踪）**：由于当前**没有可用 API Key**，Prompt v2 修改只完成了「mock 回归」（`run_eval.py` 默认 mock 模式，验证的是评测流水线与回归阈值，不能代表真实效果）。为不阻塞阶段 1 交付，v2 先随阶段 1 提交；在真实回归通过前，**不得基于 v2 继续叠加 Prompt 效果优化**。
  - 有效 Key 到位后**必须**执行：`python evaluation/run_eval.py --real`（评分路由 `evaluation` 需 GLM-4-Plus Key；复核路由 `cross_review` 需 DeepSeek Key）；
  - 结果不降 → 保留 v2；结果下降 → 按铁律回滚到 `evaluation/prompt_baselines/` 中的基线版本；
  - 真实回归结论需补录到本节（追加日期与分数对比）。
- **Key 状态（截至 2026-10-23）**：用户提供的 DeepSeek Key（脱敏尾号 `****509d`）对 `https://api.deepseek.com` 实测 **401 invalid**；GLM Key 未配置。模块 A 与 `cross_review` 的真实链路验收、以及完整四模块真实链路与真实评测回归，均**阻塞于有效 Key**；已备 `scripts/smoke_deepseek.py`（模块 A + cross_review，退出码 0/2/1），换 Key 后一条命令即可验收。
- **安全与合规**：所有 Key 仅存本地 `.env`（`.gitignore` 保护），已用 `git log -S` 核验未进入任何提交；所有改动均限 `AI-Interview-Assistant/` 仓库内。

## 2026-10-23 · 临时联调模式：全部模块先走 DeepSeek（用户指示）

- **用户指示**：“暂时都使用 DEEPSEEK，等模型跑通，再混用两个模型。”
- **实现方式（不破坏决策④）**：
  - 新增 `backend/config/models.deepseek.yaml`（5 路由全 DeepSeek 的临时配置）；
  - `model_router` 支持环境变量 `MODELS_CONFIG_PATH`：设置后加载临时配置，未设置时仍加载默认 `backend/config/models.yaml`（决策④ 双供应商）；相对路径按仓库根解析；
  - 本机 `.env` 设置 `MODELS_CONFIG_PATH=backend/config/models.deepseek.yaml`（`.env` 不入库），并把 5 组模块 Key / Base URL 全部指向 `https://api.deepseek.com`。
- **临时模型映射**（该端点当前仅支持两个模型）：

| 路由 | 临时模型 | 说明 |
| --- | --- | --- |
| A `resume_diagnosis` | `deepseek-v4-pro` | 质量档（诊断 / 结构化） |
| B `jd_matching` | `deepseek-flash` | 速度档（两段式抽取 + 匹配） |
| C `interview_agent` | `deepseek-flash` | 速度档（长对话 + 流式） |
| D `evaluation` | `deepseek-v4-pro` | 质量档（评分一致性优先） |
| D `cross_review` | `deepseek-flash` | 与 evaluation **不同模型**（保留决策④异模型原则，但同供应商属临时） |

- **恢复方式**：删除 / 注释 `.env` 中 `MODELS_CONFIG_PATH` 即回到决策④配置；恢复前需补齐 GLM Key，并按铁律先跑真实评测回归（分数不降才切换）。
- **约束**：本模式仅用于“先跑通模型链路”的联调；`models.yaml`（决策④）保持不变，不得把临时映射写进 README 的模型策略表或结题结论。
- **真实链路验证结果（2026-10-23）**：
  - `scripts/smoke_deepseek.py` exit=0：模块 A 诊断（v4-pro，75.7s，7 条问题）、结构化（49.5s）、cross_review（flash，15.9s，verdict=adjust）；
  - API 全链路真实调用 200（约 252s）：`/api/meta/routes` → `resume/structure` → `resume/diagnose` → `jd/structure` → `jd/match` → `interview/sessions` → `turns/stream`(SSE) → `get` → `end` → `report/generate` → `get` → `markdown`；
  - **Prompt 真实回归未完成（非 v2 回归，属临时模型/基线不匹配）**：`run_eval.py --real` 子集（3 样本 × 3 档）在 v_old（`evaluation/prompt_baselines/scoring_v1.md`，按 GLM 调优的历史 v1 基线）阶段中止——DeepSeek-V4-Pro 对该 Prompt 的输出不满足 `InterviewEvaluation`（`dimensions` 实际 0 项 / weaknesses、suggestions 类型不符）；v_new（`prompts/scoring.md` v2）在 E2E 报告链路中已产出合法 4 维评分。
  - **结论**：临时全 DeepSeek 模式下无法完成“同一模型下 v1 vs v2”的回归比较；Prompt v2 维持“临时提交”口径（见 2026-10-23 阶段 1 记录）。待 GLM Key 到位、恢复决策④原配置后，按铁律重跑全量真实回归，再决定 v2 固化或回滚。

## 2026-10-23 · 阶段 3 集成联调完成（临时全 DeepSeek 模式）

- **前端真实联调**：5 个页面切真实后端（`.env` 中 `VITE_USE_MOCK=false`，本地 `.env` 不入库），补齐 SSE 增量渲染、加载/错误/取消态、localStorage 历史记录；真实模式 `pnpm build` exit 0（37 modules，产物无 mock 残留）。
- **单进程演示托管**：`backend/main.py` 在 `frontend/dist` 存在时托管 `/`、`/assets/*` 与 SPA 回退；`/api`、`/health`、`/docs`、`/openapi.json` 不受影响；契约（15 条）零变更；CORS 5173 预检通过。
- **真实端到端验收**：新增 `scripts/e2e_real.py`；在真实后端（临时全 DeepSeek）执行 `--with-report`：**13/13 步通过**（结构→诊断→JD 提取/匹配→面试建会话/SSE/结束→报告生成/查询/Markdown，约 5.6 min）；真实模型调用产生费用，脚本默认跳过报告、需显式 `--with-report`。
- **评测集扩充第一批**：`evaluation/golden_qa` 10→**20 条**、`golden_resumes` 3→**8 份**（全虚构脱敏，测试与 mock 回归全绿：`pytest evaluation` 56 passed、`run_eval.py` mock PASS）。
- **遗留**：① 仍为临时全 DeepSeek 模式，恢复决策④双供应商需 GLM Key + 全量真实回归；② JD 匹配雷达图（加分项）未做；③ 阶段 4 目标 20 简历 / 30 QA 尚未补齐。

## 2026-10-23 · 阶段 4 收尾 + 阶段 5 交付完成（临时全 DeepSeek 模式）

- **评测集达标**：`golden_resumes` 20 份、`golden_qa` 30 条（三场景、三档锚定、全虚构脱敏）；`evaluation/README.md` 计数与测试同步。
- **评分一致性抽样**（真实调用 10 条 medium）：平均相对偏差 **12.50%（≤15% PASS）**，最大 29.17%（个体波动，如实记录）→ `evaluation/reports/consistency.md`。
- **模型分配 A/B**（真实调用 12 次，2 条 × 三档 × 2 配置）：`band_hit_rate` 均 1.000；MAE 混合配置 0.208 < 全 flash 0.292；小样本 + 临时同供应商，**不构成切换依据**，待 GLM Key 后按决策④跑全量 → `evaluation/reports/model_ab.md`。
- **阶段 5 交付**：`docs/final_report.md`（项目报告）；`scripts/run_demo.ps1` 一键单进程演示、`scripts/deploy_check.py` 部署自检、`docs/deployment.md`；`data/demo/demo-script.md` v2（3/8 分钟演示）与 `docs/report_sections/demo_faq.md`（15 条答辩 FAQ）；`docs/report_sections/prompt_methodology.md`（Prompt 方法论）。
- **加分项**：JD 匹配页 SVG 5 维雷达图（无新依赖，真实/mock 双模式可用）。
- **验证**：全仓 **314 passed + 1 skipped + 80 subtests**；`run_eval.py` mock PASS；`deploy_check.py` 正常 13 PASS/2 WARN、无 Key 场景 5 FAIL 且不泄露 Key；前端真实模式构建 exit 0。
- **遗留（外部事项）**：邀请 3–5 名同学试用、演示视频录制、在线部署；Prompt v2 仍为临时提交口径，待恢复决策④后跑全量真实回归再固化。

## 关键决策点总览

| # | 决策点 | 结论 | 状态 |
| --- | --- | --- | --- |
| 1 | 技术形态 | Web 应用（FastAPI 后端 + 轻量前端） | ✅ 已确认 2026-10-19 |
| 2 | 大模型来源 | DeepSeek + GLM 双供应商 | ✅ 已确认 2026-10-19 |
| 3 | 首版范围 | 完整 4 模块 | ✅ 已确认 2026-10-19 |
| 4 | 技术路线 | ✅ 纯 Prompt 工程，不涉及模型微调 | 已确认 2026-10-15 |
| 5 | 模型分配 | **每个模块独立模型**（A DeepSeek-V3 / B GLM-4-Air / C GLM-4-Flash / D GLM-4-Plus），评分与复核使用不同模型 | ✅ 已确认 2026-10-19 |

> 模型选型决策原则：优先选择“免费额度足够撑 30 场面试 + 50 份简历诊断”的模型。
> 决策④在此基础上进一步要求：**每个模块独立选型**——DeepSeek-V3（A）、GLM-4-Air（B）、GLM-4-Flash（C）、GLM-4-Plus（D），复核使用跨供应商模型，具体见 `backend/config/models.yaml`。
