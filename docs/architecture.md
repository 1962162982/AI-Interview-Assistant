# 架构说明（v2 · 2026-10-19）

四层架构：交互层 / 功能层 / 模型层 / 数据层。

**模型层（决策④：每模块独立模型）**：四个功能模块各绑定一个独立模型——A 简历诊断 `deepseek-chat`（DeepSeek-V3）/ B JD 匹配 `glm-4-air`（GLM-4-Air）/ C 模拟面试 `glm-4-flash`（GLM-4-Flash）/ D 即时反馈 `glm-4-plus`（GLM-4-Plus）；交叉复核使用 `deepseek-chat`（与评分模型不同）。由 `model_router` 按模块路由到不同模型 / 供应商，配置唯一事实来源为 `backend/config/models.yaml`。

TODO：
- [ ] 画一张端到端数据流草图：简历 / JD → 解析 → 结构化抽取 → 大模型（按模块路由到各模块独立模型）→ 结构化输出
- [ ] 补充模块交互图与时序图（model_router / context_manager 的位置）
- [ ] 补充"模块 ↔ 模型路由"图（决策④）
