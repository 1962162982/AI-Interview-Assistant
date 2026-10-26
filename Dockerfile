# syntax=docker/dockerfile:1
# ============================================================
# AI 模拟面试与简历诊断助手 · 单容器部署镜像
# 阶段 1：Node 构建前端（frontend/dist）
# 阶段 2：Python 运行 FastAPI，并静态托管前端（单服务 / 单端口 / 同源）
#
# 构建：docker build -t ai-interview-assistant .
# 运行：docker run --rm -p 8000:8000 --env-file .env ai-interview-assistant
#       （云端平台通过控制台注入 Secret 环境变量，切勿把 .env 打进镜像）
# ============================================================

# ---------- 阶段 1：构建前端 ----------
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend

# 使用 pnpm（lockfile v9）；如需固定版本可改为 pnpm@9.x
RUN npm install -g pnpm@9

COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY frontend/ ./
# 生产构建：走真实后端（同源 /api），不使用 Mock 数据
ENV VITE_USE_MOCK=false
RUN pnpm build

# ---------- 阶段 2：运行后端 + 托管前端 ----------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # 默认临时全 DeepSeek 模式；云端若要恢复决策④，可在平台设置 MODELS_CONFIG_PATH=（空）
    MODELS_CONFIG_PATH=backend/config/models.deepseek.yaml

WORKDIR /app

# 仅用于镜像内 HEALTHCHECK（Render 等平台可改用其自身 healthCheckPath）
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY prompts ./prompts
COPY data ./data
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/health" || exit 1

# 兼容各平台注入的 PORT（Render 默认 10000、HF Spaces 常用 7860）
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
