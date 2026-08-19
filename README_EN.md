# Remit

<p align="center">
  <img src="./assets/remit-icon.png" alt="Remit mark" width="160" />
</p>

Remit is a local-first workbench for mathematical modeling. It organizes problem analysis, model design, code execution, result validation, and paper writing into an inspectable and recoverable multi-agent workflow.

## Capabilities

- Four-stage agent workflow for coordination, modeling, coding, and writing.
- Independent provider, model, key, and base URL configuration per agent.
- Local MATLAB execution with Python fallback.
- Task-scoped storage for code, figures, checkpoints, logs, and papers.
- Resume, approval, retry, and model-revision workflows.
- Immutable source extraction plus per-question structured analysis of objectives, data, variables, constraints, outputs, dependencies, risks, and validation requirements.
- Attachment scouting and literature research before data-verified problem understanding is submitted for approval.
- Hierarchical domain → subdomain → method retrieval with explainable Top-K candidates per question.
- Optional model council, web search, OpenAlex, RAG, and E2B integrations.

## Quick start

### Windows

1. Copy `backend/.env.example` to `backend/.env.dev` and configure your model provider.
2. Install backend and frontend dependencies.
3. Run `win_start.bat`.
4. Open <http://127.0.0.1:15173>.

```powershell
cd backend
uv sync

cd ..\frontend
pnpm install

cd ..
.\win_start.bat
```

Use `win_stop.bat` to stop the local services.

### Docker

```bash
docker compose up --build
```

## Model configuration

```dotenv
COORDINATOR_API_TYPE=openai-responses
COORDINATOR_API_KEY=your-key
COORDINATOR_MODEL=your-model
COORDINATOR_BASE_URL=https://your-provider.example/
COORDINATOR_MAX_TOKENS=8192
```

Use the same fields with the `MODELER`, `CODER`, and `WRITER` prefixes. See [configuration](./docs/configuration.md) for all supported fields.

Never commit `backend/.env.dev` or `backend/.env.council`.

## Project layout

```text
backend/      FastAPI, workflow, agents, providers, and interpreters
frontend/     Vue 3 workbench
tools/        Windows desktop shell, launchers, and local Redis
assets/       Remit brand assets
docs/         Architecture and configuration documentation
tests/        Windows launcher regression tests
```

Task artifacts are stored in `backend/project/work_dir/<task-id>/`. Service logs are stored in `logs/`.

## Development

```powershell
cd backend
.\.venv\Scripts\python.exe -m ruff check app
.\.venv\Scripts\python.exe -m pytest tests

cd ..\frontend
pnpm run build
npx biome check src
```

## Security boundary

Remit is designed for a trusted local workstation. Add authentication, tenant isolation, and a hardened network boundary before exposing it to an untrusted network.

## Branding

The Remit name, mark, and product copy are original to this project.
