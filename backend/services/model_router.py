"""模型路由器（v1 骨架）—— ⭐ 阶段 1 核心公共组件

职责（见实行清单）：
- 按模块独立配置 模型 / 温度 / API Key / Base URL（读取 backend/config/models.yaml）
- 统一接口、重试、流式输出、超时兜底
- 五个路由条目：resume_diagnosis / jd_matching / interview_agent / evaluation / cross_review

TODO（阶段 1 实现）：加载 models.yaml → 按模块名取配置 → 提供统一 chat() 入口。
"""
