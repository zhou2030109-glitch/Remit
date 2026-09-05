# syntax=docker/dockerfile:1
FROM node:20-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN npm install -g pnpm@10.6.3
RUN --mount=type=cache,target=/root/.local/share/pnpm/store pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run build

FROM python:3.12-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /bin/
# 科学计算需要 OpenMP；论文导出需要 XeLaTeX 与中文字体。
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 fonts-noto-cjk fonts-texgyre \
    texlive-xetex texlive-lang-chinese texlive-latex-extra \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
ENV PATH="/app/backend/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy CODE_EXECUTION_BACKEND=python \
    REMIT_USER_CONFIG_PATH=/app/config/.env.user
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev
# Pandoc 的默认模板直接引用 lmodern.sty，并使用 Unicode 数学字体。
RUN apt-get update && apt-get install -y --no-install-recommends \
    lmodern texlive-fonts-recommended && rm -rf /var/lib/apt/lists/*
COPY backend/app ./app
COPY --from=frontend /build/frontend/dist /app/frontend/dist
COPY LICENSE NOTICE.md THIRD_PARTY_NOTICES.md /app/
RUN mkdir -p /app/config project/work_dir project/repair_backups logs
ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.title="Remit" \
    org.opencontainers.image.version="0.1.0" \
    org.opencontainers.image.source="https://github.com/zhou2030109-glitch/Remit" \
    org.opencontainers.image.revision=$SOURCE_REVISION
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=4 \
    CMD python -c "import json,urllib.request; s=json.load(urllib.request.urlopen('http://127.0.0.1:8000/status',timeout=3)); assert s['redis']['status']=='running'"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--ws-ping-interval", "60", "--ws-ping-timeout", "120"]
