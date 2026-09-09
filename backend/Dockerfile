# 使用官方推荐的 Python 基础镜像
FROM python:3.12-slim AS builder

# 安装中文字体（确保 matplotlib 图表中文正常显示）
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk && rm -rf /var/lib/apt/lists/*

# 安装 uv（推荐直接用官方 distroless 镜像复制二进制文件，速度快且干净）
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /uvx /bin/

WORKDIR /app

# 先复制依赖文件，利用 Docker 层缓存
COPY pyproject.toml uv.lock ./

# 安装依赖（不安装项目本身，加快后续构建）
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# 只复制运行所需源码与资源，用户密钥、日志和任务资料不属于镜像。
COPY app ./app
COPY README.md ./
RUN mkdir -p project/work_dir project/repair_backups

# 安装项目本身
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=4 \
    CMD python -c "import json,urllib.request; s=json.load(urllib.request.urlopen('http://127.0.0.1:8000/status',timeout=3)); raise SystemExit(0 if s['redis']['status']=='running' else 1)"

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# uv 使用镜像中的 .venv 运行服务。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--ws-ping-interval", "60", "--ws-ping-timeout", "120"]
