# frontend/ · 交互层（Vue 3 + Vite）

> 决策①：Web 应用（FastAPI 后端 + Vue 3 / Vite 前端）。页面路由：**首页 / 简历诊断 / JD 匹配 / 模拟面试 / 反馈报告**。
>
> 当前状态：**可运行骨架**——5 个页面 + API 客户端（契约冻结 v1）+ Mock 数据层；`pnpm install` 与 `vite build` 已在 DSH 沙箱验证通过（记录见第 6 节）。后端业务路由按 `docs/api.md` 实现后，设 `VITE_USE_MOCK=false` 即可联调真实接口。

## 1. 页面路由

| 路由 | 视图 | 模块 | 主要交互 | 契约接口（`docs/api.md` v1） |
| --- | --- | --- | --- | --- |
| `/` | `src/views/HomeView.vue` | 首页 | 模块导航、模型路由表（决策④）、后端连通性自检 | `GET /health`、`GET /api/meta/routes` |
| `/resume` | `src/views/ResumeView.vue` | A 简历诊断 | 上传 PDF/DOCX、粘贴文本降级通道、诊断报告渲染 | `POST /api/resume/parse`、`/structure`、`/diagnose` |
| `/jd` | `src/views/JdMatchView.vue` | B JD 匹配 | JD 输入、匹配度 / 能力项覆盖 / 差距清单渲染 | `POST /api/jd/structure`、`/match` |
| `/interview` | `src/views/InterviewView.vue` | C 模拟面试 | 三场景选择（实习 `intern` 默认 / 秋招 `autumn` / 保研 `grad`）、SSE 流式多轮对话、结束面试 | `POST /api/interview/sessions`、`.../turns`、`.../turns/stream`、`GET .../{sid}`、`POST .../{sid}/end` |
| `/report` | `src/views/ReportView.vue` | D 即时反馈 | 4 维度得分 + 证据、亮点/短板/建议、交叉复核、Markdown 导出 | `POST /api/report/generate`、`GET /api/report/{rid}`、`GET .../markdown` |

技术栈：Vue 3.5 + Vue Router 4 + Vite 8；构建需要 Node.js 20.19+。

## 2. 目录结构

```
frontend/
├── index.html                  # 应用入口
├── package.json                # 依赖与 scripts（dev / build / preview）
├── vite.config.js              # Vite 构建配置
├── pnpm-workspace.yaml         # 依赖 store/cache 固定在项目内；nodeLinker: hoisted
├── .env.example                # VITE_API_BASE_URL / VITE_USE_MOCK 模板（无任何 Key）
├── scripts/
│   └── sandbox-vite-preload.cjs# 仅 DSH 沙箱构建用兼容补丁（普通开发机不需要）
└── src/
    ├── main.js                 # 入口
    ├── App.vue                 # 布局 + 导航 + Mock 模式标记
    ├── styles.css              # 蓝白学术调性设计系统
    ├── router/index.js         # 5 个页面路由
    ├── api/
    │   ├── client.js           # ★ 唯一 API 出口：统一响应壳 / 错误码 / SSE 解析
    │   └── mock.js             # ★ Mock 数据层（默认启用，虚构示例数据）
    └── views/                  # 5 个页面视图
```

## 3. 本地开发

```powershell
cd AI-Interview-Assistant\frontend

pnpm install --ignore-scripts   # 依赖无需 postinstall；store/cache 固定在本目录
pnpm dev                        # http://127.0.0.1:5173
pnpm build                      # 产物 dist/
pnpm preview                    # 预览 dist/
```

环境变量：复制 `.env.example` 为 `.env`（`.env` 已被 `.gitignore` 忽略）。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `VITE_API_BASE_URL` | `http://127.0.0.1:8000` | 后端 FastAPI 基地址 |
| `VITE_USE_MOCK` | `true` | `true` 走 Mock 数据层；`false` 走真实 `/api` 调用 |

- **Mock 模式（默认）**：按 `docs/api.md` 契约返回同构虚构数据，无 Key / 后端薄壳阶段亦可完整演示五个页面；页面底部会显示「Mock 数据模式」。
- **真实后端**：后端实现业务路由 + 配置 Key（仅后端 `.env`）后，设 `VITE_USE_MOCK=false` 重新构建；后端已默认放行 5173 / 3000 CORS。
- **安全**：前端不保存、不请求任何 API Key；Key 只允许出现在后端环境变量中。

## 4. 依赖安装位置（不写仓库外）

- `pnpm-workspace.yaml` 固定 `storeDir: .pnpm-store`、`cacheDir: .npm-cache`（均位于本目录，已列入 `.gitignore`）；
- `nodeLinker: hoisted`（npm 风格扁平布局）：DSH Windows 沙箱中 pnpm 的 junction 无法被 Node ESM 的 realpath 解析，隔离布局下 Vite 会 `ERR_MODULE_NOT_FOUND`；扁平布局在沙箱与普通开发机上均可稳定复现；
- 依赖仅 `vue` / `vue-router` / `vite` / `@vitejs/plugin-vue`，无可执行 postinstall 的包，故安装加 `--ignore-scripts`。

## 5. API 契约

- 唯一来源：`docs/api.md`（**契约冻结 v1 · 2026-10-23**）：Base 前缀 `/api`、统一响应壳 `{code, message, data}`、错误码表、SSE `data: {"event":"delta"|"done"|"error", ...}`。
- 前端所有接口调用集中在 `src/api/client.js`，页面不直接拼接 URL；HTTP 非 2xx 或 `code != 0` 统一抛 `ApiError` 并展示 `message`（`code=2003` 表示模块开发中）。
- Mock 层与 HTTP 层接口同构（同一组函数签名），切换只改 `VITE_USE_MOCK`，页面代码无需改动。

## 6. 构建验证记录（2026-10-23 · DSH 沙箱）

- **环境**：系统 PATH 无 Node.js；使用 DSH 内置 **Node v24.21.0 + pnpm v11.28.5**（node：`C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe`；pnpm：`...\dependencies\pnpm\bin\pnpm.mjs`；cwd = `frontend/`）。
- **安装**：`<node.exe> <pnpm.mjs> install --ignore-scripts` → **exit 0**，37 packages，`Done in 1.5s`；store 落在 `frontend/.pnpm-store`，已确认未写入仓库外目录（`node_modules` 已 gitignore）。
- **构建**（沙箱内需兼容补丁）：

```powershell
$env:NODE_OPTIONS="--require <frontend>\scripts\sandbox-vite-preload.cjs"
<node.exe> <pnpm.mjs> run build
```

→ **exit 0**：`vite v8.3.5 building client environment`、`✓ 35 modules transformed`、`✓ built in ~220ms`（实测 221–224ms）；
产物：`dist/index.html`（0.61 kB）、`dist/assets/index-*.js`（110.93 kB / gzip 43.38 kB）、`dist/assets/index-*.css`（9.87 kB）、5 个路由 chunk（Home / Resume / JdMatch / Interview / Report）。
- **可复现安装**：`install --ignore-scripts --frozen-lockfile` → exit 0（`Already up to date`，639ms）；`pnpm-lock.yaml` 随仓库提交。
- **静态托管冒烟**：`python -m http.server 4180`（cwd=`dist/`）→ `GET /` 200，页面引用的 JS / CSS 均 200（`text/javascript` / `text/css`）。
- **对照**：同一命令不加 `NODE_OPTIONS` 时，沙箱禁止子进程管道 → Vite 的 `net use` 探测抛 `spawn EPERM`（普通开发机无此限制，直接 `pnpm build` 即可，无需该补丁）。

## 7. 骨架阶段边界与联调步骤

- 后端阶段 1：`/health`、`/api/meta/routes` 可用，四组业务路由为 501 薄壳；前端默认 Mock，因此页面可完整演示，但**不代表真实模型效果已实现**。
- 联调步骤：① 后端按 `docs/api.md` 实现业务路由；② 后端 `.env` 配置有效 Key；③ 前端 `.env` 设 `VITE_USE_MOCK=false`；④ 逐接口联调（`client.js` 已就绪，含 SSE 解析）。
- 演示冷启动素材：`../data/demo/`（2 份虚构示例简历 + 1 段历史对话记录 + 现场 demo 脚本）。
- 演示建议：无 Node.js 的演示机可用 `dist/` 静态产物 + 本机 Python 起静态服务（见 `../data/demo/demo-script.md` 准备章节）。
