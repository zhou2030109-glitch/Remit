# Remit backend

FastAPI services, agent workflow, model-provider adapters, local execution, and
task persistence for [Remit](../README_EN.md).

```bash
uv sync --frozen
cp .env.example .env.dev          # Windows: Copy-Item .env.example .env.dev
uv run uvicorn app.main:app --host 127.0.0.1 --port 18000
```

See the root documentation for frontend, security, and full application
setup. This package is licensed under the repository's MIT License.
