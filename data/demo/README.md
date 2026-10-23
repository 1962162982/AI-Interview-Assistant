# data/demo/ · 演示冷启动素材

> 用途：阶段 5「演示冷启动预案」——预置示例简历与历史对话，现场 demo 不从零开始生成。
> 所有素材均为 **LLM 生成的虚构示例**，不含真实个人信息；仅用于课程项目演示与联调。

## 素材清单

| 文件 | 说明 |
| --- | --- |
| `resumes/resume_demo_01_intern.md` | 示例简历 01 · 实习场景：刻意保留「弱动词 / 无量化 / 关键词缺失」问题，适合现场演示模块 A 的诊断与改写 |
| `resumes/resume_demo_02_autumn.md` | 示例简历 02 · 秋招场景：量化较充分、含 Redis 缓存实践，适合演示模块 B 的匹配度与差距分析 |
| `jds/jd_demo_backend_intern.md` | 示例 JD · 后端开发实习生（Python 方向），覆盖 Python / MySQL / Redis / 高并发能力项 |
| `transcripts/interview_demo_01.json` | 1 段历史面试对话记录（实习场景，5 轮）+ 评分与交叉复核结果，可跳过实时对话直接演示反馈报告 |
| `demo-script.md` | 现场 demo 脚本（准备、演示流程、兜底预案、讲解要点） |

## 使用方式

- **前端演示（推荐）**：交互层默认 `VITE_USE_MOCK=true`，页面内「填入示例简历 / 填入示例 JD」可直接使用同构 mock 数据；
  若已切到真实后端（`VITE_USE_MOCK=false`），把本目录的简历 / JD 文本复制进页面输入框即可。
- **后端 / 评测演示**：可把示例简历与 JD 作为联调输入；历史对话记录可用于离线验证报告数据结构是否符合
  `backend/schemas/interview_evaluation.py` 与 `docs/api.md` 的报告契约。

## 注意

- 本目录素材与 `data/resumes/`、`data/jds/`（模块 A/B 评测素材，由 T2 维护）相互独立，避免演示素材与评测集耦合。
- 修改示例内容不得引入真实姓名、电话、邮箱等个人信息；一律使用虚构信息。
