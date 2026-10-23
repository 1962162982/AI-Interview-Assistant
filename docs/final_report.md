# AI 模拟面试与简历诊断助手 · 项目报告

> 课程项目交付报告 · v1.0 · 2026-10-23
> 仓库：`AI-Interview-Assistant/`（纯 Prompt 工程 / FastAPI + Vue 3 / DeepSeek + GLM 双供应商设计）
> 说明：本报告数据均来自仓库内真实测试与运行记录；当前联调为「临时全 DeepSeek 模式」，双供应商混用尚未恢复（见第 7 节）。

---

## 1. 项目概述

面向高校学生的 AI 求职练习助手：学生上传**简历**、贴出**目标岗位 JD**，系统完成一条闭环——
**简历诊断 → JD 匹配 → 模拟面试（会追问）→ 即时反馈报告**。

- **四大功能模块**：A 简历诊断 / B JD 匹配 / C 模拟面试 / D 即时反馈。
- **技术路线**：纯 Prompt 工程，不做模型微调；效果保障 = `prompts/` + `evaluation/` 两个核心资产 + 强制回归关卡。
- **技术形态**：Web 应用（FastAPI 后端 + Vue 3 / Vite 前端），支持前后端分离开发与单进程演示两种方式。
- **模型策略**：按任务特征为每个模块绑定独立模型（决策④：A DeepSeek-V3 / B GLM-4-Air / C GLM-4-Flash / D GLM-4-Plus，评分与复核异模型）；双供应商设计保留，当前临时收敛为全 DeepSeek 联调。

## 2. 系统设计与架构

四层架构（详见 [architecture.md](architecture.md)）：

1. **交互层**：Vue 3 + Vite，五页面（首页 / 简历诊断 / JD 匹配 / 模拟面试 / 反馈报告）；SSE 流式面试；localStorage 历史记录。
2. **功能层**：FastAPI 15 条契约接口（`docs/api.md` 冻结 v1），四组路由 + `/api/meta/routes` + `/health`；统一响应壳 `{code, message, data}` 与错误码（1001/1002/2001/2002/2003/3001/5000）。
3. **模型层**：`model_router` 按模块路由到不同模型 / Key / Base URL，统一 `chat()` / `chat_stream()` / `chat_json()`（超时、重试、流式、JSON 修复、可注入 mock）；`context_manager` 滑动窗口 + 90% 摘要压缩。
4. **数据层**：3 个 Pydantic Schema（`ResumeStructured` / `JDStructured` / `InterviewEvaluation`）、PDF/DOCX 解析器、`data/` 虚构素材库。

**工程要点**：`MODELS_CONFIG_PATH` 环境变量支持临时切换路由配置（全 DeepSeek 联调不破坏决策④默认 `models.yaml`）；`frontend/dist` 存在时由 FastAPI 直接托管页面与 API（单进程演示）。

## 3. 关键实现

### 3.1 公共底座
- `model_router.py`：5 路由、超时/指数退避重试、流式、`chat_json()` Schema 校验 + 修复重试；无 Key 清晰报错、不泄露 Key；`client_factory` 可注入 mock。
- `token_counter.py` + `context_manager.py`：token 估算（离线降级）、滑动窗口、90% 摘要；面向 30 分钟长面试。
- `parser/`：pdfplumber → pypdf 兜底、DOCX 段落/表格解析、扫描件降级提示。

### 3.2 四模块服务
| 模块 | 要点 |
|---|---|
| A 简历诊断 | 结构化抽取 + 5 项检查清单诊断；JSON 失败降级纯文本；强制引用原文 |
| B JD 匹配 | 严格两段式（JD 抽取 → 逐项 evidence 对齐评分 0–100 + 差距清单）；支持文本/结构化双路径 |
| C 模拟面试 | 三场景（实习/秋招/保研）、追问决策树 D1–D5、会话状态机、每 5 轮滚动摘要、token 兜底、SSE 流式、题库降级 |
| D 即时反馈 | 4 维 rubric + 三档锚定 + 强制 grounding（证据<2 条该维≤6 分）+ 跨模型交叉复核 + Markdown 报告 |

### 3.3 Prompt 工程
10 个 Prompt 全部带版本注释：A（诊断 v2 / 结构化 v1）、B（抽取 + 匹配 v2）、C（base + 三场景 v2）、D（评分 + 复核 v2）。
方法论细节见 [report_sections/prompt_methodology.md](report_sections/prompt_methodology.md)（检查清单 + few-shot、两段式、角色卡 + 决策树、rubric + 锚定 + 证据引用、版本管理与评测铁律）。

### 3.4 前端与演示
- 五页真实后端联调：上传/粘贴、诊断报告、JD 匹配 + **SVG 5 维雷达图**（无新依赖）、SSE 逐字面试、报告生成/拉取/Markdown 导出；加载/错误/取消态、localStorage 历史。
- 单进程演示：`scripts/run_demo.ps1` 一键构建前端 + 启动 uvicorn；`frontend/dist` 存在时 FastAPI 托管 SPA 与 `/api`。

## 4. 测试与验证

### 4.1 自动化测试（mock，无需 Key）
- 全仓 **314 passed + 1 skipped + 80 subtests**（覆盖契约、路由集成、Prompt 守卫、评测回归、静态托管、解析、上下文等）。
- `evaluation/run_eval.py`（mock）判定 PASS、退出码 0；回归场景可演示退出码 1。

### 4.2 真实模型链路（临时全 DeepSeek）
- `scripts/smoke_deepseek.py`：模块 A 诊断/结构化 + `cross_review` 真实调用通过（exit=0）。
- `scripts/e2e_real.py --with-report`：**13/13 步通过**（约 5.6 min）——结构抽取 → 诊断 → JD 提取/匹配 → 面试建会话/SSE（delta+done）/结束 → 报告生成/查询/Markdown；各步真实耗时见 [docs/decision_log.md](decision_log.md)。
- 单进程托管冒烟：`/`、`/interview` SPA 回退、`/assets/*`、`/health`、`/api/meta/routes` 均 200，`/api/not-exist` 404+3001，CORS 预检通过。

### 4.3 阶段 4 实验（临时同供应商，小样本）
- **评分一致性抽样**：10 条 golden_qa（medium 档）真实评分 vs 人工分带中心，**平均相对偏差 12.50%（≤15% PASS）**，最大 29.17%（个体波动，如实记录）。报告：[evaluation/reports/consistency.md](../evaluation/reports/consistency.md)。
- **模型分配 A/B**：2 条 × 三档 × 两配置（混合配置 vs 全 flash），真实调用 12 次：`band_hit_rate` 均 1.000；`MAE` 混合配置 0.208 < 全 flash 0.292；属小样本噪声、不构成切换依据。报告：[evaluation/reports/model_ab.md](../evaluation/reports/model_ab.md)。
- **评测集规模**：`golden_resumes` **20 份**、`golden_qa` **30 条**（三场景、三档锚定、全虚构脱敏）。

### 4.4 部署自检
- `scripts/deploy_check.py`：正常场景 13 PASS / 2 WARN / 0 FAIL，退出码 0；无 Key 场景正确 5 个 FAIL、退出码 1，且输出不含任何 Key。
- `scripts/run_demo.ps1`：语法 0 错误；`-SkipBuild -Port 8124` 实跑验证页面/健康检查/SPA 回退正常，结束后端口释放。

## 5. 工程与协作

- **Git**：按功能分批提交（核心底座 / Prompt / 数据 / 服务 / 评测 / 前端 / 文档 / 配置 / 集成），Prompt 与评测的提交历史即效果演化证据。
- **团队**：5 个智能体成员（后端底座、A+B、C、D+评测、前端），按任务板隔离写作用域、Lead 统一提交与验收。
- **安全**：API Key 仅存本地 `.env`（gitignore 保护）；已用 `git log -S` 核验全历史无 Key；仓库外无改动。

## 6. 创新点与亮点

1. **评测即关卡**：Prompt 改动必须过 `run_eval.py`（分数下降非零退出），把“感觉变好”变成可比较的数据。
2. **可解释评分**：强制先引用原文证据再给分，交叉复核使用不同模型，报告附偏差说明。
3. **追问可维护**：把“面试官怎么追问”拆成显式决策树（D1–D5 规则编号），代码判定 + 模型措辞，避免纯自由发挥。
4. **工程化底座**：模型路由 / 上下文管理 / JSON 修复 / 契约冻结 / 静态托管，均为可复用的模块化组件。
5. **诚实的口径**：临时全 DeepSeek、小样本 A/B、Prompt v2 临时提交等均如实记录，不虚构跑分。

## 7. 局限与下一步

- **双供应商混用未恢复**：当前临时全 DeepSeek；需补 GLM Key 后删除 `MODELS_CONFIG_PATH` 回到决策④，并按铁律跑全量真实 Prompt 回归再固化 v2。
- **真实回归待完成**：临时模式下 v1 基线不适配 V4-Pro，无法完成同模型 v1 vs v2 比较（详见 [decision_log.md](decision_log.md)）。
- **实验样本小**：一致性 10 条、A/B 2 条，结论仅作方向参考；需扩到全量再决策。
- **邀请试用/演示视频/在线部署**未完成；单进程演示与部署文档已就绪。
- 加分项：雷达图已实现；后续可加历史会话持久化、PDF 报告导出。

## 8. 交付物索引与快速开始

| 交付物 | 位置 |
|---|---|
| 项目说明书 | [说明书.md](../说明书.md) |
| API 契约（冻结） | [docs/api.md](api.md) |
| 架构说明 | [docs/architecture.md](architecture.md) |
| 决策记录 | [docs/decision_log.md](decision_log.md) |
| 部署文档 | [docs/deployment.md](deployment.md) |
| 演示脚本 / 答辩 FAQ | [data/demo/demo-script.md](../data/demo/demo-script.md) / [report_sections/demo_faq.md](report_sections/demo_faq.md) |
| Prompt 方法论 | [report_sections/prompt_methodology.md](report_sections/prompt_methodology.md) |

```powershell
# 单进程演示（先构建前端，再用 FastAPI 托管页面 + /api）
cd AI-Interview-Assistant
.\scripts\run_demo.ps1            # 或：cd frontend; pnpm build; cd ..; .\.venv\Scripts\python.exe -m uvicorn backend.main:app --port 8000
# 打开 http://127.0.0.1:8000

# 验收命令
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider   # 314 passed + 1 skipped + 80 subtests
.\.venv\Scripts\python.exe scripts\e2e_real.py --with-report   # 真实 E2E（需后端已启动 + 有效 Key）
.\.venv\Scripts\python.exe scripts\deploy_check.py             # 部署自检
```
