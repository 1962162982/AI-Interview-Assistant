# 现场 Demo 脚本（AI 模拟面试与简历诊断助手）

> 目标：5–8 分钟内完整演示「简历诊断 → JD 匹配 → 模拟面试 → 反馈报告」主链路。
> 本脚本配合 `data/demo/` 冷启动素材使用，确保现场不从零开始生成、不依赖实时模型可用性。

## 一、准备（演示前 10 分钟）

### 1. 后端（可选，提供 `/health` 与 `/api/meta/routes`）

```powershell
cd AI-Interview-Assistant
# 若 .venv 不存在请按 README 安装依赖；本轮团队环境已就绪，不要重复 pip install
.\.venv\Scripts\Activate.ps1
uvicorn backend.main:app --reload
# 浏览器验证：http://127.0.0.1:8000/health → {"status":"ok"}
```

> 阶段 1 现状：业务路由为 501 薄壳占位；前端保持 Mock 模式演示，后端在线只影响首页「连通性自检」与「模块→模型路由表」是否取到真实数据。

### 2. 前端（二选一）

**A. 开发服务器（有 Node.js / pnpm 的机器）**

```powershell
cd AI-Interview-Assistant\frontend
pnpm install --ignore-scripts
pnpm dev
# 浏览器打开 http://127.0.0.1:5173
```

**B. 静态产物（无 Node.js 的机器，用本机 Python 起静态服务）**

```powershell
cd AI-Interview-Assistant\frontend
pnpm build   # 若已有 dist/ 可跳过
cd dist
python -m http.server 5173
# 浏览器打开 http://127.0.0.1:5173
```

> 浏览器需支持 ES Module（Chrome / Edge 近两年版本均可）。静态产物为纯前端，不依赖后端即可演示。

### 3. 环境变量

- 默认 `VITE_USE_MOCK=true`：按 `docs/api.md` 契约返回同构虚构数据，**无 API Key 也能完整演示**。
- 若后端 Key 已就绪且业务路由已实现：把 `frontend/.env` 中 `VITE_USE_MOCK=false`，重新 `pnpm dev` / `pnpm build`。

### 4. 打开页面并最小自检

1. `/` 首页：四个模块卡片、模块→模型路由表（`GET /api/meta/routes`，Mock 下为同构数据）、后端连通性自检；
2. 确认页面底部显示「Mock 数据模式」；
3. 浏览器缩放 100%、关闭无关标签页，准备投屏。

## 二、演示流程（5–8 分钟）

| 步骤 | 操作 | 页面 | 讲解要点 |
| --- | --- | --- | --- |
| ① 开场（30s） | 打开首页 | `/` | 四层架构一句话；纯 Prompt 工程不微调；4 模块、5 页面路由；底部 Mock 模式说明 |
| ② 模型路由（30s） | 指出首页路由表 | `/` | 决策④：A `deepseek-chat` / B `glm-4-air` / C `glm-4-flash` / D `glm-4-plus`，评分与复核跨模型；换模型只改 `models.yaml`，但须过评测回归 |
| ③ 简历诊断（2min） | 进入「简历诊断」→「填入示例简历」→「开始诊断」 | `/resume` | 展示问题清单：量化表达（high）/ 动词弱化 / 关键词对齐 / 结构 / 格式；逐条读「原文 → 问题 → 改写」，强调**强制引用原文**与优先级 |
| ④ JD 匹配（1.5min） | 进入「JD 匹配」→「填入示例 JD」→「开始匹配」 | `/jd` | 两段式：先 JD 能力项抽取（低温 0.2），再逐项对齐；展示匹配度 78/100、能力项已覆盖/部分/缺失、差距清单与补齐建议 |
| ⑤ 模拟面试（2min） | 进入「模拟面试」→ 场景选「实习」→「开始面试」→ 输入一段回答（可用示例回答）→「发送回答」 | `/interview` | SSE 流式逐字输出；追问决策树：回答含糊 → 追问数据来源；场景三选一（实习 / 秋招 / 保研）；右侧会话状态展示追问深度与上下文占用 |
| ⑥ 反馈报告（1.5min） | 面试页点「结束面试并生成报告」→ 自动跳转报告页 | `/report` | 4 维度分 + 原文证据；亮点 / 短板 / 建议；交叉复核（不同模型）偏差说明；「下载 Markdown」现场导出 |
| ⑦ 收尾（30s） | 回到首页 | `/` | 工程亮点：Schema 契约、上下文管理、评测回归关卡、Mock 与真实后端一键切换 |

### 可选加演（时间充裕时）

- 场景切换：面试页分别选「秋招」「保研」，展示首问语气与问题侧重差异；
- 历史对话冷启动：打开 `data/demo/transcripts/interview_demo_01.json`，说明该记录可直接用于演示报告页（不依赖实时生成）；
- 构建与架构文档：展示 `docs/architecture.md` 四层架构、数据流与模块→模型路由图。

## 三、冷启动素材使用

| 素材 | 用法 |
| --- | --- |
| `resumes/resume_demo_01_intern.md` | 步骤③「填入示例简历」的等价内容；可复制文本粘贴演示 |
| `resumes/resume_demo_02_autumn.md` | 若演示「秋招」场景，可作为更强的简历对照 |
| `jds/jd_demo_backend_intern.md` | 步骤④「填入示例 JD」的等价内容 |
| `transcripts/interview_demo_01.json` | 历史对话 + 评分 + 交叉复核结果；现场时间不足时可直接展示报告结构 |

## 四、兜底预案

| 风险 | 现象 | 兜底动作 |
| --- | --- | --- |
| 模型 Key 无效 / 无 Key | 真实后端返回 2001 / 401 | 保持 `VITE_USE_MOCK=true` 演示（默认即如此），不展示真实调用 |
| 后端未启动 | 首页连通性自检显示连接失败 | 不点「检测后端」；直接演示前端 Mock 流程，并说明后端 `/health` 已可独立启动 |
| 网络异常 | 页面空白或资源加载失败 | 改用本地静态产物：`cd frontend/dist && python -m http.server 5173` |
| 时间不足 | 流程走不完 | 跳过面试逐轮输入，直接打开 `/report`（自动生成 Mock 报告） |
| 页面报错 | 控制在 10s 内刷新重试 | 二次失败则切静态产物；提前在另一标签页备好 `dist/index.html` |

## 五、口径纪律（避免夸大）

- 当前为**骨架阶段**：前端交互与 Mock 数据可演示，后端业务模块尚未实现真实模型调用；
- 不宣称「已实现 / 已上线」任何效果指标；时延 <3s、评分偏差 ≤15% 等均为目标值；
- 不展示任何真实 API Key、`.env` 内容或真实用户简历；示例素材均为虚构。

## 六、演示后的收尾

1. 关闭 uvicorn / 前端 dev server / 静态服务器；
2. 确认没有把 `.env`、API Key 或真实简历带进投屏与截图；
3. 把本次演示发现的问题记入项目报告「演示复盘」一节，对应到后续阶段 4 的评测与迭代。
