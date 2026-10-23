# 部署与演示指南（阶段 5）

> 适用场景：本地开发、答辩 / 演示的单进程托管、无 Node 机器的预构建部署。
> **安全红线**：`.env` 与任何 API Key **绝不进库、不写进文档、不打印到终端/日志**；本仓库素材全部为 LLM 生成的虚构数据。
> 相关契约：[docs/api.md](api.md)；前端说明：[frontend/README.md](../frontend/README.md)。

## 一、环境要求

| 项 | 要求 | 说明 |
| --- | --- | --- |
| Python | >= 3.10（本项目实测 3.14.6） | 后端 |
| 后端依赖 | `pip install -r requirements.txt` | fastapi / uvicorn / pydantic / pyyaml / openai / tiktoken / pdfplumber / python-docx / python-multipart / pytest |
| Node.js | >= 20.19（仅构建前端需要） | 演示机若已有 `frontend/dist` 可不需要 Node |
| pnpm | 任意较新版本 | 构建前端；存储 / 缓存固定到 `frontend/.pnpm-store`、`frontend/.npm-cache` |
| 模型 API | DeepSeek / 智谱 GLM Key（按模块配置） | 当前联调期为「临时全 DeepSeek」模式，见第四节 |

## 二、本地开发（前后端分离）

```bash
# 1) 后端（热重载，默认 http://127.0.0.1:8000）
AI-Interview-Assistant\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload

# 2) 前端开发服务器（默认 http://127.0.0.1:5173）
cd frontend
pnpm install
pnpm dev
```

- 前端开发期默认 `VITE_USE_MOCK=true`（mock 数据层，可无 Key 浏览全部页面）；联调真实后端时在 `frontend/.env` 设置
  `VITE_USE_MOCK=false`、`VITE_API_BASE_URL=http://127.0.0.1:8000`（`frontend/.env` 已在 `.gitignore`，不提交）。
- 后端 CORS 默认放行 `localhost/127.0.0.1` 的 **5173 / 3000** 端口；其他来源用环境变量 `CORS_ORIGINS` 覆盖（逗号分隔）。

## 三、单进程演示（推荐用于答辩 / 交付）

原理：后端 `backend/main.py` 在 `frontend/dist/index.html` 存在时，由同一个 FastAPI 进程托管前端构建产物
（`/assets/*` 静态资源 + SPA 回退），`/api`、`/health`、`/docs`、`/openapi.json` 不被接管。
一条 uvicorn 命令即可完成演示，无需另起静态服务器。

### 3.1 一键脚本（Windows / PowerShell 5.1+）

> 脚本文件为 **UTF-8 with BOM** 编码，Windows PowerShell 5.1 下中文提示可正常解析；
> 若使用 PowerShell 7，把下文的 `powershell` 换成 `pwsh` 即可。

```powershell
# 默认 8000 端口；dist 缺失时自动用 DSH 自带 Node/pnpm 构建
powershell -NoProfile -File scripts/run_demo.ps1

# 指定端口
powershell -NoProfile -File scripts/run_demo.ps1 -Port 8080

# 已构建过前端，跳过构建/安装（快速启动）
powershell -NoProfile -File scripts/run_demo.ps1 -SkipBuild
```

> 若系统执行策略拦截脚本，可在命令中追加 `-ExecutionPolicy Bypass`。

脚本行为：

1. 校验 `.venv\Scripts\python.exe` 与核心后端依赖，缺失时给出明确的安装提示并退出；
2. `frontend/dist/index.html` 缺失时，使用 DSH 自带 Node/pnpm 构建（`node_modules`、store、cache 均固定项目内），
   构建失败会打印可执行的处理建议；
3. 检查 `.env` 是否存在（**不读取 / 不打印 Key**）与端口是否被占用；
4. 启动 uvicorn，并打印页面地址、接口文档、健康检查地址与停止方式（Ctrl+C）。

### 3.2 手动单进程启动

```powershell
# 1) 构建前端（有 Node 的机器）
cd frontend
pnpm install --store-dir .pnpm-store
pnpm build                  # 产物：frontend/dist/

# 2) 启动单进程服务（API + 前端）
cd ..
AI-Interview-Assistant\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/` 即为前端页面；`http://127.0.0.1:8000/docs` 为后端接口文档。

## 四、环境变量清单（只列变量名，不含任何值）

根目录 `.env` 由 `.env.example` 复制而来，已被 `.gitignore` 保护。**变量名如下，值只存在本机。**

| 变量 | 是否必填 | 用途 |
| --- | --- | --- |
| `RESUME_DIAGNOSIS_API_KEY` / `_BASE_URL` | 模块 A 必填 | 简历诊断（DeepSeek） |
| `JD_MATCHING_API_KEY` / `_BASE_URL` | 模块 B 必填 | JD 匹配（GLM） |
| `INTERVIEW_AGENT_API_KEY` / `_BASE_URL` | 模块 C 必填 | 模拟面试（GLM） |
| `EVALUATION_API_KEY` / `_BASE_URL` | 模块 D 必填 | 即时反馈评分（GLM） |
| `CROSS_REVIEW_API_KEY` / `_BASE_URL` | 模块 D 必填 | 交叉复核（与评分不同模型） |
| `MODELS_CONFIG_PATH` | 可选 | 临时切换模型路由配置（如 `backend/config/models.deepseek.yaml`）；相对路径按仓库根解析 |
| `CORS_ORIGINS` | 可选 | 覆盖 CORS 白名单（逗号分隔），默认放行 5173 / 3000 |

说明：

- 模型 / 温度 / 超时 / 上下文窗口等路由信息以 `backend/config/models.yaml` 为唯一事实来源（决策④：每模块独立模型、
  评分与复核异模型）；`Base URL` 未显式配置时按模型名前缀使用默认供应商地址。
- **临时全 DeepSeek 联调模式**：`.env` 设置 `MODELS_CONFIG_PATH=backend/config/models.deepseek.yaml` 即可启用
  （不修改决策④默认配置）；删除 / 注释该变量即恢复双供应商混用。恢复后须按铁律跑评测回归。
- 任何 Key 只通过 `.env` / 进程环境变量注入；禁止写入代码、配置、文档或提交历史。

## 五、无 Node 机器的部署策略

1. 在**有 Node 的开发机**执行 `pnpm build`，得到 `frontend/dist/`；
2. 将整个项目（含 `frontend/dist/`）拷贝到演示机，确保 `frontend/dist/index.html` 存在；
3. 演示机只需 Python 与 `.venv` 依赖，运行 `scripts/run_demo.ps1 -SkipBuild` 或手动 uvicorn 即可。
   - `frontend/dist` 不存在时后端行为与阶段 1 一致：页面路径 404，但 `/api`、`/health`、`/docs` 仍可用（仅 API 模式）。

## 六、部署前自检

```powershell
# 默认检查：Python/依赖、frontend/dist、根 .env、5 组 Key 是否已配置（不显示值）、模型路由配置
AI-Interview-Assistant\.venv\Scripts\python.exe scripts\deploy_check.py

# 附加 HTTP 探活（服务已启动时）
AI-Interview-Assistant\.venv\Scripts\python.exe scripts\deploy_check.py --health-url http://127.0.0.1:8000/health

# 调试“无 Key”场景（指定另一个 env 文件，不读取根 .env 的 Key）
AI-Interview-Assistant\.venv\Scripts\python.exe scripts\deploy_check.py --env-file .tmp\empty.env
```

- 输出为 PASS / WARN / FAIL 清单；存在 FAIL 时**退出码为 1**，全部通过（允许 WARN）为 **0**。
- 自检只报告 Key「是否配置」，**绝不打印任何 Key 值**；同时校验 `evaluation` 与 `cross_review` 是否异模型（决策④）。

## 七、端口与 CORS

- 单进程演示默认端口 `8000`（可用 `-Port` 或 uvicorn `--port` 修改）；
- 前端开发服务器默认 `5173`，另预留 `3000`；
- 跨域白名单通过 `CORS_ORIGINS` 覆盖，例如：`CORS_ORIGINS=http://127.0.0.1:5173,http://192.168.1.10:8000`；
- 单进程部署时前端与 API 同源，浏览器不产生跨域请求；CORS 主要服务于「前端 dev server + 后端分开跑」的开发模式。

## 八、常见问题（FAQ）

1. **启动报「未找到 .venv」**：在项目根目录执行 `python -m venv .venv`，再
   `.venv\Scripts\python.exe -m pip install -r requirements.txt`。
2. **页面 404 / 只有 API 能用**：`frontend/dist/index.html` 不存在。运行 `scripts/run_demo.ps1` 自动构建，或先在有 Node 的机器上 `pnpm build` 后拷贝 `frontend/dist/`。
3. **接口返回 `code=2001`**：对应模块 Key 未配置或失效。先跑 `scripts/deploy_check.py` 查看哪个 Key 缺失，再到 `.env` 补齐（不要贴 Key 到聊天/文档）。
4. **端口被占用**：`-Port 8080` 换端口，或先找到占用进程（`Get-NetTCPConnection -LocalPort 8000`）。
5. **前端构建失败**：确认 DSH Node 路径存在、网络可访问 npm registry；也可在有 Node 的机器构建后拷贝 `frontend/dist/`，演示机用 `-SkipBuild`。
6. **临时全 DeepSeek 模式忘了恢复**：`deploy_check.py` 会以 WARN 提示当前使用 `MODELS_CONFIG_PATH`；恢复时删除 `.env` 中该变量，并按铁律重跑评测回归再提交。
7. **/health 正常但页面空白**：检查浏览器控制台与 `frontend/dist` 是否为最新构建（重新 `pnpm build`）；Vue Router 使用 history 模式的路由由后端 SPA 回退兜底。
8. **自检退出码非 0**：按 FAIL 项逐条修复；WARN 不影响退出码（例如未显式配置 Base URL、未做 HTTP 探活）。
