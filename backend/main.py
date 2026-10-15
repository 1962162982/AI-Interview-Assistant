"""AI 模拟面试与简历诊断助手 · 后端入口（v1 骨架）

TODO：
- 阶段 1：挂载 api/ 路由（resume / jd / interview / report）
- 阶段 3：统一状态管理与错误处理（解析失败 / API 超时 / 空输入 / JSON 重试）
"""
from fastapi import FastAPI

app = FastAPI(title="AI Interview Assistant", version="0.1.0")


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    """健康检查（骨架期探活用）。"""
    return {"status": "ok"}
