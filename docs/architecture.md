# 架构说明（v2 · 2026-10-23）

> 本文是交互层（前端）与功能层（后端）联调、以及结题报告「系统架构」章节的共同依据，覆盖：四层架构、端到端数据流、「模块 → 模型」路由、model_router / context_manager 的位置。
>
> 决策基线：① Web 应用（FastAPI + Vue 3/Vite）、② DeepSeek + 智谱 GLM 双供应商、③ 首版完整 4 模块、④ 每个模块绑定独立模型。
> 两个唯一事实来源：模型路由 = `backend/config/models.yaml`；接口契约 = `docs/api.md`。
> 状态说明：本文描述**目标架构与当前骨架的结合体**。截至 2026-10-23，文档、目录、Pydantic Schema、模型路由配置与交互层（前端）骨架已就位；业务服务实现处于骨架阶段（以各文件内注释为准）。

---

## 1. 四层架构

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ 交互层  frontend/（Vue 3 + Vite）                                             │
│   路由：/ 首页 · /resume 简历诊断 · /jd JD 匹配 · /interview 模拟面试          │
│         /report 反馈报告                                                      │
│   交互：文件上传（PDF/DOCX）、表单、流式对话（SSE）、报告展示与 Markdown 导出  │
│   代码：src/App.vue · src/router/index.js · src/views/*.vue · src/api/client.js│
└──────────────────────────────▲────────────────────────────┬──────────────────┘
                               │ REST（JSON） / SSE（流式）  │
┌──────────────────────────────┴────────────────────────────▼──────────────────┐
│ 功能层  backend/（FastAPI，路由层是薄壳）                                      │
│   api/：resume.py · jd.py · interview.py · report.py（+ /health）              │
│   services/：resume_diagnosis · jd_matching · interview_agent · evaluation     │
│               parser/(pdf_parser, docx_parser) · context_manager · model_router│
│   schemas/：ResumeStructured · JDStructured · InterviewEvaluation（Pydantic）  │
│   utils/：mask（脱敏）· token_counter（token 估算）                            │
└──────────────────────────────▲────────────────────────────┬──────────────────┘
                               │ 统一 chat() / chat_stream() │
┌──────────────────────────────┴────────────────────────────▼──────────────────┐
│ 模型层  双供应商，均使用 OpenAI 兼容 SDK                                       │
│   DeepSeek：deepseek-chat                                                      │
│   智谱 GLM：glm-4-air · glm-4-flash · glm-4-plus                               │
│   model_router 按模块取模型 / 温度 / API Key / Base URL；超时、重试、主备降级、流式│
└──────────────────────────────▲────────────────────────────┬──────────────────┘
                               │                            │
┌──────────────────────────────┴────────────────────────────▼──────────────────┐
│ 数据层                                                                        │
│   prompts/（Prompt 资产，文件头版本注释）· evaluation/（评测集 + 回归脚本）     │
│   data/（虚构简历 / JD / 题库素材）· data/demo/（演示冷启动素材）              │
│   backend/config/models.yaml（模型路由配置）                                   │
└──────────────────────────────────────────────────────────────────────────────┘
```

分层职责：

| 层 | 目录 | 职责 | 边界规则 |
| --- | --- | --- | --- |
| 交互层 | `frontend/` | 页面路由、上传、表单、聊天流式渲染、报告展示/导出 | 只通过 `docs/api.md` 契约调用后端；不感知具体模型，不保存任何 API Key |
| 功能层 | `backend/api/`、`backend/services/` | 路由薄壳、业务编排、解析、脱敏、上下文管理、Schema 校验 | `api/` 只做参数接收与响应；业务逻辑全部在 `services/` |
| 模型层 | `backend/services/model_router.py` + `backend/config/models.yaml` | 统一模型调用出口：按模块路由模型/温度/Key/Base URL，处理超时、重试、流式、主备降级 | 业务代码只传模块名；「换模型」只改配置，不改业务代码 |
| 数据层 | `prompts/`、`evaluation/`、`data/`、`backend/config/` | Prompt 资产、评测集、虚构素材、模型路由配置 | Prompt 改动必须先过评测回归（项目铁律）；素材只用 LLM 生成/虚构内容 |

---

## 2. 端到端数据流

### 2.1 主链路（用户视角，10 步）

```
① 首页
② 上传简历（PDF/DOCX）或粘贴文本
③ 解析 → 脱敏 → 结构化抽取（ResumeStructured）
④ A 简历诊断：问题清单 + 优先级 + 改写示例
⑤ 输入目标岗位 JD
⑥ B JD 匹配（两段式）：JDStructured → 逐项对齐 → 匹配分 + 差距清单
⑦ 选择面试场景（实习 / 秋招 / 保研）→ 创建面试会话（C）
⑧ 多轮模拟面试：按简历 + JD 出首问，按追问决策树追问（SSE 流式）
⑨ 结束面试 → D 评分（4 维度 + 证据）+ 交叉复核 → 生成报告
⑩ 查看 / 导出报告（Markdown）
```

### 2.2 组件级数据流

```
浏览器（交互层）
  │ multipart（PDF/DOCX）或 JSON（粘贴文本）
  ▼
backend/api/resume.py ──▶ services/parser/（pdf_parser.py / docx_parser.py）
  │                         └─ 扫描件 / 乱码 → 返回降级信号 → 前端提示改用「粘贴文本」通道
  ▼
backend/utils/mask.py（姓名 / 电话 / 邮箱脱敏，再上云）
  ▼
services/resume_diagnosis.py ──router.chat("resume_diagnosis")──▶ deepseek-chat
  │   输出：ResumeStructured（Schema 校验；失败重试；再失败降级纯文本）
  │         + 诊断报告（问题清单 / 优先级 / 原文引用 / 改写示例）
  ▼
ResumeStructured —— 公共依赖：B / C / D 都建立在它之上
  │
  ├─▶ services/jd_matching.py
  │      第一段：jd_structured（低温 0.2）→ JDStructured（title / responsibilities / capabilities）
  │      第二段：简历能力项 × JD 能力项 → 逐项对齐评分（0–100）+ 差距清单
  │
  ├─▶ services/interview_agent.py
  │      输入：ResumeStructured + JDStructured + 场景变体（实习/秋招/保研）→ 生成首问
  │      多轮：追问决策树（含糊→追数据来源；技术名词→深挖原理；与 JD 冲突→温和质疑；
  │            回答优秀→切换维度；同方向追问 ≥2 次→强制换向）
  │      context_manager：messages 维护 / 滑动窗口 / 每 5 轮滚动摘要 / 接近 90% 窗口阈值压缩
  │      └─router.chat_stream("interview_agent")──▶ glm-4-flash（SSE 逐字输出）
  │
  └─▶ services/evaluation.py
         输入：面试对话记录（含简历 / JD 关键信息）
         4 维度 rubric（相关性 / 结构性 / 具体性 / 岗位匹配，各 1–10）
         强制先引用原文证据再给分；证据 <2 条时该维度不得高于 6 分
         └─router.chat("cross_review")──▶ deepseek-chat（与评分模型不同，跨供应商）
  ▼
backend/api/report.py：InterviewEvaluation + 交叉复核偏差说明
  → 报告（维度分 / 证据 / 亮点 / 短板 / 建议，Markdown 可导出）
  ▼
前端 /report：报告展示与 Markdown 导出
```

### 2.3 结构化产物：模块间数据契约

三个 Pydantic Schema 是模块之间的「接口」，先定契约再并行开发：

| Schema | 文件 | 生产者 | 消费者 | 关键字段 |
| --- | --- | --- | --- | --- |
| `ResumeStructured` | `backend/schemas/resume_structured.py` | A 简历诊断（解析 + 抽取） | B / C / D | 姓名、教育、经历、技能、项目 |
| `JDStructured` | `backend/schemas/jd_structured.py` | B JD 匹配（两段式第一段） | C / D | 岗位、职责、能力项（含要求程度与原文依据） |
| `InterviewEvaluation` | `backend/schemas/interview_evaluation.py` | D 即时反馈（评分 + 复核） | 报告页 | 维度分（1–10）+ 证据、亮点、短板、建议 |

---

## 3. 「模块 → 模型」路由图（决策④）

模块与模型**一对一独立绑定**：四个功能模块各用不同模型，评分（`evaluation`）与交叉复核（`cross_review`）必须使用不同模型。配置唯一事实来源为 `backend/config/models.yaml`。

```
                        backend/config/models.yaml（唯一事实来源）
                                       │
        ┌──────────────┬───────────────┼───────────────┬──────────────┐
        ▼              ▼               ▼               ▼              ▼
     模块 A          模块 B          模块 C          模块 D       模块 D·复核
  resume_diagnosis  jd_matching  interview_agent  evaluation   cross_review
        │              │               │               │              │
        ▼              ▼               ▼               ▼              ▼
  deepseek-chat    glm-4-air      glm-4-flash      glm-4-plus    deepseek-chat
  （DeepSeek）     （智谱 GLM）    （GLM 免费档）   （智谱 GLM）   （DeepSeek）
  中文生成/改写    结构化抽取+对齐   长对话+流式      评分一致性     跨供应商复核
```

路由参数（与 `models.yaml` 逐条一致）：

| 路由 / 模块 | 模型 | 供应商 | temperature | timeout(s) | max_retries | stream | API Key 环境变量 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `resume_diagnosis`（A 简历诊断） | `deepseek-chat` | DeepSeek | 0.3 | 60 | 2 | false | `RESUME_DIAGNOSIS_API_KEY` |
| `jd_matching`（B JD 匹配） | `glm-4-air` | 智谱 GLM | 0.2 | 60 | 2 | false | `JD_MATCHING_API_KEY` |
| `interview_agent`（C 模拟面试） | `glm-4-flash` | 智谱 GLM | 0.8 | 120 | 3 | true | `INTERVIEW_AGENT_API_KEY` |
| `evaluation`（D 即时反馈） | `glm-4-plus` | 智谱 GLM | 0.1 | 60 | 2 | false | `EVALUATION_API_KEY` |
| `cross_review`（D 交叉复核） | `deepseek-chat` | DeepSeek | 0.1 | 60 | 2 | false | `CROSS_REVIEW_API_KEY` |

约束：

1. **业务代码只按模块名调用**，不出现具体模型名；更换模型 / 供应商只改 `models.yaml`。
2. 换模型 / 供应商属于**效果变更**：必须先跑 `evaluation/run_eval.py`，分数不降才允许切换（项目铁律第 5 条）。
3. `evaluation` 与 `cross_review` 必须不同模型（当前为 GLM-4-Plus × DeepSeek-V3，跨供应商），以降低同源评分偏差。
4. Key 只从环境变量读取（变量名见 `.env.example`），禁止硬编码或进入前端。

---

## 4. model_router 与 context_manager 的位置

```
backend/api/*.py（薄壳：参数接收 / 响应）
        ▼
backend/services/{resume_diagnosis, jd_matching, interview_agent, evaluation}.py
        │  ▲
        │  └── context_manager.py（模块 C 的对话上下文：滑动窗口 + 摘要压缩 + token 阈值）
        ▼
router.chat(module, messages, stream=False) / router.chat_stream(...)
        ▼
backend/services/model_router.py
        │  读 models.yaml → 选模型 / 温度 / Key / Base URL
        │  统一封装：超时、重试、流式、错误兜底、主备降级（设计）
        ▼
DeepSeek（deepseek-chat） / 智谱 GLM（glm-4-air / glm-4-flash / glm-4-plus）
```

**model_router（模型层出口，阶段 1 公共底座）**

- 输入：模块名（`resume_diagnosis` / `jd_matching` / `interview_agent` / `evaluation` / `cross_review`）+ messages；
- 输出：统一 `chat()` / `chat_stream()` 接口，业务层不感知供应商差异（两家均为 OpenAI 兼容接口）；
- 职责：读取 `models.yaml`、按模块取温度/Key/Base URL、重试与超时、流式增量返回、错误兜底与主备降级。

**context_manager（功能层公共组件，模块 C 的底层支撑）**

- 机制：维护 messages 数组 → 滚动窗口只保留最近 N 轮 → token 计数（tiktoken 估算，失败时降级字符估算）→ 接近上下文窗口 90% 时触发摘要压缩；
- 触发：模块 C 每 5 轮滚动摘要 + 接近上限时自动压缩；
- 保留内容：岗位 / 简历关键信息、已问问题列表、候选人关键回答，避免「堆历史」导致 token 爆炸；
- 与 router 的关系：context_manager 只负责「构造更短的 messages」，最终仍由 model_router 发起调用。

---

## 5. 交互层结构（frontend/）

| 路由 | 视图文件 | 对应模块 | 主要交互 | 契约接口（`docs/api.md` v1） |
| --- | --- | --- | --- | --- |
| `/` | `src/views/HomeView.vue` | 入口 | 模块导航、模型路由表（决策④）、后端连通性自检 | `GET /health`、`GET /api/meta/routes` |
| `/resume` | `src/views/ResumeView.vue` | A 简历诊断 | PDF/DOCX 上传、粘贴文本降级通道、诊断报告渲染 | `POST /api/resume/parse`、`/structure`、`/diagnose` |
| `/jd` | `src/views/JdMatchView.vue` | B JD 匹配 | JD 文本输入、匹配度 / 能力项 / 差距清单渲染 | `POST /api/jd/structure`、`/match` |
| `/interview` | `src/views/InterviewView.vue` | C 模拟面试 | 三场景选择（intern 默认 / autumn / grad）、SSE 流式多轮对话、结束面试 | `POST /api/interview/sessions`、`.../turns`、`.../turns/stream`（SSE）、`GET .../{sid}`、`POST .../{sid}/end` |
| `/report` | `src/views/ReportView.vue` | D 即时反馈 | 4 维度得分 + 证据、亮点/短板/建议、交叉复核、Markdown 导出 | `POST /api/report/generate`、`GET /api/report/{rid}`、`GET .../markdown` |

- 入口：`index.html` → `src/main.js` → `src/App.vue`（导航 + 布局）→ `src/router/index.js`（5 路由）；
- 接口契约：`docs/api.md` **契约冻结 v1（2026-10-23）**——统一响应壳 `{code, message, data}`、错误码表、SSE `event: delta | done | error`；前端全部调用集中在 `src/api/client.js`（页面不直接拼 URL），错误统一拦截并展示 `message`；
- 联调模式：`src/api/mock.js` 提供与 HTTP 层同构的 Mock 数据层，默认 `VITE_USE_MOCK=true`（无 Key / 阶段 1 薄壳期间页面可完整演示）；后端就绪后设 `VITE_USE_MOCK=false` 即切真实 `/api` 调用；
- 构建：`vite.config.js`；开发期 Vite dev server（默认 `127.0.0.1:5173`），生产构建产物为 `frontend/dist/`；Vue 3 + Vite 8（需 Node.js 20.19+）；
- 依赖存储固定在本项目内（`frontend/pnpm-workspace.yaml` 指定 `storeDir` / `cacheDir`，采用 `nodeLinker: hoisted` 保证沙箱内可复现），不写入仓库外目录。

---

## 6. 端到端联调时序（目标形态）

以模拟面试（模块 C）为例，SSE 流式链路：

```
前端 /interview          backend/api/interview.py   interview_agent     model_router     智谱 GLM
    │  创建会话（HTTP）  ───────▶│                        │                   │              │
    │                          │── 组装上下文 ─────────▶│                   │              │
    │  订阅流式响应（SSE）─────▶│── chat_stream ───────▶│── router 调用 ───▶│── 请求 ─────▶│
    │ ◀── data: 增量文本 ──────│◀──── 增量返回 ────────│◀── 流式 chunk ────│◀── 流式 ─────│
    │  （多轮问答：context_manager 每 5 轮摘要 / 90% 阈值压缩）  │              │              │
    │  结束面试（HTTP）  ──────▶│── evaluation + cross_review（不同模型）──▶│              │
    │ ◀── InterviewEvaluation ─│                        │                   │              │
    │  获取报告（HTTP）  ──────▶│◀── 报告 Markdown ─────────────────────────────────────────│
```

接口路径、请求/响应字段与 SSE 事件格式一律以 `docs/api.md` 冻结版为准；本文只描述数据流与组件职责，不预设接口细节。

---

## 7. 关键工程约束

| 约束 | 落地方式 |
| --- | --- |
| API Key 安全 | 只读环境变量；`.env` 不进 git；前端不携带任何 Key |
| 隐私与脱敏 | 本地解析优先；调用云端模型前用 `mask.py` 对姓名 / 电话 / 邮箱脱敏；仓库内只用虚构简历 |
| 输出稳定性 | Pydantic Schema 校验 → JSON 解析失败自动重试 → 仍失败降级纯文本并提示 |
| 可用性 | model_router 统一超时、重试、错误兜底、主备降级（双供应商） |
| 长对话 | context_manager 滑动窗口 + 每 5 轮摘要 + 90% 阈值压缩，保证 30 分钟面试不中断 |
| 效果可回归 | 任何 Prompt 改动 / 换模型先跑 `evaluation/run_eval.py`，分数不降才提交，下降回滚 |
| 分层纪律 | `api/` 薄壳、逻辑在 `services/`、契约在 `schemas/`、Prompt 只在 `prompts/` |
| 前端契约纪律 | 接口调用集中在 `src/api/client.js`，按 `docs/api.md` 冻结 v1 实现统一响应壳 / 错误码 / SSE；Mock 与 HTTP 双实现同构，`VITE_USE_MOCK` 一键切换 |

---

## 8. 运行与部署形态

- **开发**：`uvicorn backend.main:app --reload`（阶段 1：`/health`、`/api/meta/routes` 可用，四组业务路由为 501 薄壳）；前端 `pnpm dev`（Vite dev server），通过 `VITE_API_BASE_URL` 指向 FastAPI；CORS 由后端默认放行 5173 / 3000。
- **构建**：`frontend/` 下 `pnpm build` → `frontend/dist/` 静态产物；构建需 Node.js 20.19+（Vite 8 要求）。**当前状态：构建已通过**（35 modules，`✓ built in ~220ms`；产物 `dist/index.html` + 5 个路由 chunk + CSS/JS 主包，记录见 `frontend/README.md`）。
- **当前验证环境**：本机系统 PATH 无 Node.js；使用 DSH 自带 Node v24.21.0 + pnpm v11.28.5 完成前端依赖安装与 `vite build` 验证。沙箱禁止子进程管道，Vite 在 Windows 上的 `net use` 探测会抛 `spawn EPERM`，构建时需 `NODE_OPTIONS=--require frontend/scripts/sandbox-vite-preload.cjs`（普通开发机不需要该补丁）。
- **演示 / 交付**：可本地可跑（后端 uvicorn + 前端静态托管）或在线 demo 两选一（阶段 5 决策）；前端 `dist/` 已实测可由 `python -m http.server` 托管并正常返回页面与静态资源，后续也可由 FastAPI 静态挂载（属于后端路由改动）。
- **冷启动预案**：`data/demo/` 已预置 2 份虚构示例简历（实习 / 秋招各 1）、1 段历史面试对话记录（5 轮 + 评分 + 交叉复核）与现场 demo 脚本（`data/demo/demo-script.md`），避免现场从零生成。

---

## 9. 版本记录

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| v1 | 2026-10-19 | 骨架：四层架构一句话 + 模型层决策④说明 |
| v2 | 2026-10-23 | 补全四层架构图与分层职责、端到端数据流（用户主链路 + 组件级 + 数据契约表）、「模块 → 模型」路由图与参数表、model_router / context_manager 位置、交互层结构、联调时序、工程约束、运行与部署形态；对齐 `backend/config/models.yaml` 与前端骨架，清除全部待办标记 |
| v2.1 | 2026-10-23 | 对齐接口契约冻结 v1（统一响应壳 / 错误码 / SSE `event` 信封）与前端 `client.js` + Mock 数据层；交互层 5 视图中 4 个业务页可按契约渲染；前端 `pnpm install` + `vite build` 验证通过（DSH 自带 Node/pnpm）；补 `data/demo/` 演示冷启动素材与 demo 脚本；更新运行与部署形态 |
