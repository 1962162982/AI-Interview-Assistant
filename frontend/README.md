# frontend/ · 交互层（Vue 3 + Vite）

> 决策①：Web 应用（FastAPI 后端 + Vue 3 / Vite 前端）。页面路由：**首页 / 简历诊断 / JD 匹配 / 模拟面试 / 反馈报告**。
>
> 当前状态（阶段 3 + 阶段 5 加分项）：**已从 Mock 切换到真实后端联调**——5 个页面按 `docs/api.md` 契约调用真实 `/api` 接口，
> 面试页使用 SSE 增量渲染，首页展示真实 `/api/meta/routes` 与 localStorage 历史；JD 匹配页新增 **SVG 手绘 5 维能力雷达图**（无第三方库，
> 真实 / Mock 两种模式均出图，含 hover 数值、aria-label 与等价文本列表）；真实模式 `pnpm build` 通过，
> 并完成 CORS 预检 + 简历上传解析 + 面试建会话 / 一轮 SSE / 结束的真实链路实测（记录见第 6、7 节）。
>
> Mock 数据层仍保留：`frontend/.env` 中 `VITE_USE_MOCK=true` 时可在无 Key / 无后端环境完整演示五页（含雷达图）。

## 1. 页面路由

| 路由 | 视图 | 模块 | 主要交互 | 契约接口（`docs/api.md` v1） |
| --- | --- | --- | --- | --- |
| `/` | `src/views/HomeView.vue` | 首页 | 模块导航、真实模型路由表（决策④）、后端连通性自检、localStorage 历史（简历 / 会话 / 报告） | `GET /health`、`GET /api/meta/routes` |
| `/resume` | `src/views/ResumeView.vue` | A 简历诊断 | PDF/DOCX 上传解析（含 degraded 提示）、粘贴文本降级、诊断报告渲染、可取消请求 | `POST /api/resume/parse`、`/structure`、`/diagnose` |
| `/jd` | `src/views/JdMatchView.vue` | B JD 匹配 | 简历文本（优先取本地历史）、JD 输入、匹配度 / 能力项覆盖 / 差距清单、**SVG 5 维能力雷达图**、可取消请求 | `POST /api/jd/structure`、`/match` |
| `/interview` | `src/views/InterviewView.vue` | C 模拟面试 | 三场景（实习 `intern` 默认 / 秋招 `autumn` / 保研 `grad`）、SSE 逐字渲染、防重复提交、停止生成、结束面试 | `POST /api/interview/sessions`、`.../turns`、`.../turns/stream`、`GET .../{sid}`、`POST .../{sid}/end` |
| `/report` | `src/views/ReportView.vue` | D 即时反馈 | 生成新报告（需确认，2 次模型调用）/ 按 report_id 拉取、维度分 + 证据、交叉复核、Markdown 导出 | `POST /api/report/generate`、`GET /api/report/{rid}`、`GET .../markdown` |

技术栈：Vue 3.5 + Vue Router 4 + Vite 8；构建需要 Node.js 20.19+。

## 2. 目录结构

```
frontend/
├── index.html                  # 应用入口
├── package.json                # 依赖与 scripts（dev / build / preview）
├── vite.config.js              # Vite 构建配置
├── pnpm-workspace.yaml         # 依赖 store/cache 固定在项目内；nodeLinker: hoisted
├── .env.example                # 环境变量模板（复制为 .env；不含任何 Key）
├── .env                        # 本地真实联调配置（gitignored，不提交）：VITE_USE_MOCK=false
├── scripts/
│   └── sandbox-vite-preload.cjs# 仅 DSH 沙箱构建 / dev 用兼容补丁（普通开发机不需要）
└── src/
    ├── main.js                 # 入口
    ├── App.vue                 # 布局 + 导航 + 当前模式标记（Mock / 真实后端）
    ├── styles.css              # 蓝白学术调性设计系统
    ├── router/index.js         # 5 个页面路由
    ├── data/samples.js         # 虚构示例简历 / JD（联调兜底素材）
    ├── stores/history.js       # localStorage 历史：最近简历 / session_id / report_id
    ├── utils/radar.js          # ★ JD 匹配雷达图数据推导（纯函数，可 Node 单测）
    ├── api/
    │   ├── client.js           # ★ 唯一 API 出口：统一响应壳 / 错误码 / SSE / 超时 / 取消
    │   └── mock.js             # Mock 数据层（VITE_USE_MOCK=true 时启用，虚构数据）
    └── views/                  # 5 个页面视图
```

## 3. 本地开发

```powershell
cd AI-Interview-Assistant\frontend

pnpm install --ignore-scripts   # 依赖无需 postinstall；store/cache 固定在本目录
pnpm dev                        # http://127.0.0.1:5173（Vite dev server）
pnpm build                      # 产物 dist/
pnpm preview                    # 预览 dist/
```

### 3.1 真实后端联调（当前默认）

1. 启动后端（另开终端）：

```powershell
cd AI-Interview-Assistant
$env:TMP=(Resolve-Path .tmp).Path; $env:TEMP=$env:TMP
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
# 验证：http://127.0.0.1:8000/health → {"status":"ok"}
```

2. 启动前端：

```powershell
cd AI-Interview-Assistant\frontend
pnpm dev   # 若在 DSH 沙箱内：先设 NODE_OPTIONS="--require <frontend>\scripts\sandbox-vite-preload.cjs"
```

3. `frontend/.env`（**gitignored，不提交**）：

| 变量 | 真实联调值 | 说明 |
| --- | --- | --- |
| `VITE_USE_MOCK` | `false` | 走真实 `/api` 调用 |
| `VITE_API_BASE_URL` | `http://127.0.0.1:8000` | 后端 FastAPI 基地址 |

- 后端已默认放行 `http://127.0.0.1:5173` / `http://localhost:5173` 等开发源 CORS（`CORS_ORIGINS` 可覆盖）。
- **安全**：前端不保存、不请求任何 API Key；Key 只允许出现在仓库根目录 `.env`（后端读取）。

### 3.2 Mock 模式（无后端 / 无 Key 演示）

将 `frontend/.env` 的 `VITE_USE_MOCK` 改为 `true`（或删除 `.env`，默认即 Mock），重启 dev / 重新 build；
页面底部与页头会显示「Mock 数据模式」。Mock 数据与真实契约同构，切换无需改页面代码。

## 4. 依赖安装位置（不写仓库外）

- `pnpm-workspace.yaml` 固定 `storeDir: .pnpm-store`、`cacheDir: .npm-cache`（均位于本目录，已列入 `.gitignore`）；
- `nodeLinker: hoisted`（npm 风格扁平布局）：DSH Windows 沙箱中 pnpm 的 junction 无法被 Node ESM 的 realpath 解析，隔离布局下 Vite 会 `ERR_MODULE_NOT_FOUND`；扁平布局在沙箱与普通开发机上均可稳定复现；
- 依赖仅 `vue` / `vue-router` / `vite` / `@vitejs/plugin-vue`，无可执行 postinstall 的包，故安装加 `--ignore-scripts`。

## 5. API 契约与联调约定

- 唯一来源：`docs/api.md`（**契约冻结 v1 · 2026-10-23**）：Base 前缀 `/api`、统一响应壳 `{code, message, data}`、错误码表、SSE `data: {"event":"delta"|"done"|"error", ...}`。
- 前端所有接口调用集中在 `src/api/client.js`，页面不直接拼接 URL：
  - 统一拦截：HTTP 非 2xx 或 `code != 0` → `ApiError`（含业务码 / 可重试标记 / 取消 / 超时标记），页面展示 `message`；
  - 错误码提示：1001 参数 / 1002 解析 / 2001 模型调用 / 2002 JSON 校验 / 2003 未实现 / 3001 不存在 / 5000 内部错误；
  - 超时与取消：JSON 请求按接口设置 15–300s 超时（`AbortController`），页面提供「取消请求 / 停止生成」；
  - SSE：`fetch + ReadableStream` 按 `event` 字段分发；`delta` 增量渲染、`done` 更新题目 / 状态、`error` 友好提示（`retryable` 时提示可重试）。
- 历史记录：`src/stores/history.js` 使用 `localStorage`（键 `aia.history.v1`）保存最近简历文本、`session_id`、`report_id`；首页可查看 / 清空，报告页可按 ID 拉取。
- 真实模式下，简历诊断 / JD 匹配 / 面试首问 / 每轮追问 / 报告生成都会调用真实模型；报告生成 = 评分 + 交叉复核 2 次调用，页面默认不自动触发，需手动确认。

### 5.1 JD 匹配雷达图（阶段 5 加分项 · T13）

- **实现**：`src/utils/radar.js`（纯函数推导 + SVG 几何） + `JdMatchView.vue`（内联 SVG `viewBox="0 0 360 320"`）；**无第三方图表库、无新增依赖**。
- **5 个维度与规则**（全部由 `/api/jd/match` 的 `items` 状态与 `score` 推导，规则同时展示在页面文本列表中）：
  1. 技能匹配 = 加权覆盖率 ×10（matched=1 / partial=0.5 / missing=0）；
  2. 项目经验 = matched 能力项占比 ×10；
  3. 量化表达 = `evidence` 含数字的能力项占比 ×10；
  4. 岗位相关 = 总分 `score / 100 × 10`；
  5. 成长潜力 = `(1 − missing 占比) ×10`（缺口越少、补齐上限越高）。
  所有维度四舍五入保留 1 位小数并 clamp 到 [0, 10]。
- **交互与无障碍**：网格环 20%–100%、轴线、数据多边形；每个顶点可 hover（放大 + 高亮对应文本行 + 原生 `<title>` tooltip）；轴标签直接带数值；
  `<svg role="img" :aria-label="…">` 提供整体描述，右侧 `<ul>` 文本列表是图表等价信息；`items` 为空时显示「无法绘制」占位而非空图。
- **真实 / Mock 均出图**：两种模式复用同一视图与推导函数（响应结构同构）；已分别构建验证（见 6.1）。

## 6. 构建验证记录（DSH 沙箱）

> 环境：系统 PATH 无 Node.js；使用 DSH 内置 **Node v24.21.0 + pnpm v11.28.5**
> （node：`C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe`；
> pnpm：`...\dependencies\pnpm\bin\pnpm.mjs`；cwd = `frontend/`）。

### 6.1 真实模式构建（2026-10-23，`frontend/.env`：VITE_USE_MOCK=false）

```powershell
$env:NODE_OPTIONS="--require <frontend>\scripts\sandbox-vite-preload.cjs"
<node.exe> <pnpm.mjs> run build
```

→ **exit 0**：`vite v8.3.5 building client environment`、`✓ 38 modules transformed`、`✓ built in ~250–270ms`（实测多次 244–269ms）；
产物 `dist/`（9 文件）：`index.html`（0.6 kB）+ `assets/index-*.js`（99.4 kB）+ `assets/index-*.css`（11.4 kB）+ 5 个路由 chunk（JdMatchView 11.4 kB，含雷达图）+ `history` 公共 chunk（1.7 kB）。
**真实模式确认**：构建产物中 `isMockMode` 被常量折叠为 `false`（`Nc=!1`）；Mock 数据层同构代码被 tree-shaking 移除（产物中无 `sess_mock` / mock 题库文案）；`VITE_USE_MOCK` 仅剩页面标题文案中的字面量。

**Mock 模式构建对照**（`VITE_USE_MOCK=true` 环境变量覆盖）：同样 **exit 0**（38 modules，244ms）；产物含 mock 数据层（`sess_mock` 可见）与雷达图代码 —— 证明「真实 / mock 两模式均出图」。

**雷达图推导单测**：`node .tmp/radar-unit-check.mjs`（临时脚本，位于 gitignored `.tmp/`，验证后已删除）→ 23 项断言全 PASS：
mock 数据（score 78 + 4 能力项）得到 技能匹配 5.0 / 项目经验 2.5 / 量化表达 5.0 / 岗位相关 7.8 / 成长潜力 7.5；
真实同构数据（score 62 + 5 能力项）得到技能匹配 6.0 / 岗位相关 6.2；`items` 为空 / null → 返回 null（视图显示占位）；全 matched / 全 missing 边界正确。

### 6.2 历史记录（阶段 1，Mock 模式构建）

`install --ignore-scripts --frozen-lockfile` → exit 0（`pnpm-lock.yaml` 随仓库提交）；
Mock 模式构建 → `✓ 35 modules transformed`、`✓ built in ~220ms`；`python -m http.server` 静态托管冒烟 200。

### 6.3 沙箱注意事项

不加 `NODE_OPTIONS` 时，沙箱禁止子进程管道 → Vite 的 `net use` 探测抛 `spawn EPERM`（普通开发机无此限制，直接 `pnpm dev` / `pnpm build` 即可，无需该补丁）。

## 7. 阶段 3 真实联调记录（2026-10-23）

**启动命令**：后端 `$env:TMP=(Resolve-Path .tmp).Path; .\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`；
前端 `NODE_OPTIONS=--require scripts/sandbox-vite-preload.cjs <node.exe> <pnpm.mjs> run dev`（Vite v8.3.5 ready in 660ms）。

| 检查项 | 接口 / 命令 | 结果 |
| --- | --- | --- |
| 健康检查 | `GET /health`（Origin: 127.0.0.1:5173） | 200 `{"status":"ok"}`，响应含 `access-control-allow-origin` |
| CORS 预检 | `OPTIONS /api/interview/sessions` | 200；`allow-origin` 回显、`allow-methods` / `allow-headers` 正确 |
| 模型路由表 | `GET /api/meta/routes` | `code=0`；**当前临时全 DeepSeek 模式**：`resume_diagnosis`/`evaluation` = `deepseek-v4-pro`，`jd_matching`/`interview_agent`/`cross_review` = `deepseek-flash` |
| 简历上传解析 | `POST /api/resume/parse`（docx multipart） | `code=0`；`filename/kind=docx`、`text` 68 字、1 段、`degraded=false`（0 次模型调用） |
| 错误壳 404 | `GET /api/interview/sessions/sess_not_exist_123` | HTTP 404 / `code=3001`「面试会话不存在或已过期」 |
| 错误壳 400 | `POST /api/interview/sessions`（缺 `resume_text`） | HTTP 400 / `code=1001` 参数校验失败 |
| 建会话（真实调用 #1） | `POST /api/interview/sessions`（示例简历 + JD，scene=intern） | `code=0`，耗时 2.6s，返回 `session_id` 与首问 `q1` |
| SSE 一轮追问（真实调用 #2） | `POST /api/interview/sessions/{sid}/turns/stream` | HTTP 200 `text/event-stream`；20 个 `delta` + 1 个 `done`（逐字 36 字符；`done` 更新下一题 `q2`、`state.rule_id=D2`、`follow_up_depth=1`），无 `error` 事件 |
| 结束面试 | `POST /api/interview/sessions/{sid}/end` | `code=0`，3ms，`transcript` 1 轮，`ended=true` |
| 前端 dev 环境注入 | `GET http://127.0.0.1:5173/src/api/client.js` | 顶部注入 `import.meta.env = {..., "VITE_API_BASE_URL":"http://127.0.0.1:8000", "VITE_USE_MOCK":"false"}` |
| dev 编译 | 直接请求 9 个源码模块（视图 / 路由 / stores / client） | 全部 200，无 SFC 编译错误 |

- **真实模型调用合计 2 次**（建会话 + 一轮 SSE），远低于 ≤8 次预算；按 Lead 要求**未生成评分报告**（report 生成 = 评分 + 交叉复核 2 次调用），报告路径保留 Mock 验证 + 契约实现。
- 测试临时脚本与 docx 仅在 `AI-Interview-Assistant/.tmp/`（已 gitignore）创建，验证后已删除；未向仓库外写入，未提交任何 Key。

## 8. 阶段边界与遗留

- **模型配置**：后端当前为「临时全 DeepSeek 模式」（`MODELS_CONFIG_PATH` 指向临时配置）；恢复决策④ 双供应商份配置后，前端无需改动（模型细节全部收口在 `models.yaml`，页面只按接口调用）。
- **报告生成**：接口 / 页面 / Markdown 导出已按契约实现，但真实模型链路未实测（成本控制），待后续单次验收。
- **演示机无 Node.js**：可用 `dist/` 静态产物 + 本机 Python 静态服务；构建产物 `dist/` 已被 gitignore，需在有 Node 的环境构建或另行确定托管方案。
- **演示冷启动素材**：`../data/demo/`（2 份虚构示例简历 + 1 段历史对话记录 + 现场 demo 脚本）。
