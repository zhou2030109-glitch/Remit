# Remit — Mathematical Modeling AI Agent

<p align="center">
  <img src="./assets/remit-icon.png" alt="Remit mark" width="150" />
</p>

<p align="center">
  A local-first, inspectable, and recoverable mathematical-modeling workbench
</p>

<p align="center">
  <a href="https://github.com/zhou2030109-glitch/Remit/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/zhou2030109-glitch/Remit/actions/workflows/ci.yml/badge.svg" /></a>
  <a href="./LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg" /></a>
  <a href="./README.md">中文</a>
</p>

Remit is an open-source, local-first AI assistant for mathematical modeling
(数模 Agent). It organizes problem analysis, data inspection, literature search,
candidate model comparison, code execution, result validation, and paper writing
into an inspectable, recoverable multi-agent workflow. Review results at key
checkpoints, return steps for revision, and resume interrupted tasks with the
analysis, evidence, code, figures, and paper artifacts kept in one workspace.

The project is at version `0.1.x`; APIs and workflows may still change.

[Quick start](#quick-start) · [Features](#features) · [WeChat community](#wechat-community)

## Features

- staged Coordinator, Modeler, Coder, and Writer collaboration;
- OpenAI Chat/Responses, Anthropic, Gemini, and compatible provider adapters;
- task-scoped files, messages, checkpoints, deliverables, and resume support;
- local Python or MATLAB execution with optional E2B sandboxing;
- problem-PDF extraction, figure interpretation, attachment scouting, method
  retrieval, and open literature discovery;
- model review, quality gates, and traceable paper delivery.

See [the architecture guide](docs/architecture.md) for module boundaries and
workflow details.

## Requirements

- Windows 10/11 for the desktop launcher, macOS 12+ or Linux via the shell
  launcher, or a Docker Compose environment;
- Python 3.12+ and [uv](https://docs.astral.sh/uv/);
- Node.js 20+ and pnpm 10;
- Redis for the complete workflow. Windows source mode can use the bundled
  Redis runtime files. On macOS use `brew install redis`; on Linux use the
  distribution package manager. The launcher starts a dedicated instance on
  port 16379 and never takes ownership of an external Redis process.

Provider calls may incur external API charges. Some workflows execute
model-generated code; run Remit only on a trusted workstation and inspect your
inputs.

## Quick start

### Windows from source

```powershell
git clone https://github.com/zhou2030109-glitch/Remit.git
cd Remit

Copy-Item backend/.env.example backend/.env.dev

cd backend
uv sync --frozen

cd ../frontend
pnpm install --frozen-lockfile

cd ..
./win_start.bat
```

Open <http://127.0.0.1:15173>. Backend API documentation is available at
<http://127.0.0.1:18000/docs>. Run `win_stop.bat` to stop all services.

### macOS / Linux from source

```bash
git clone https://github.com/zhou2030109-glitch/Remit.git
cd Remit

cp backend/.env.example backend/.env.dev

cd backend
uv sync --frozen

cd ../frontend
pnpm install --frozen-lockfile

cd ..
bash tools/start_services.sh
```

Open <http://127.0.0.1:15173>. Backend API documentation is available at
<http://127.0.0.1:18000/docs>. Run `bash tools/stop_services.sh` to stop all
services. On macOS you can instead double-click `mac_start.command` /
`mac_stop.command`. Install Redis and `lsof` first (`brew install redis` on
macOS; use the distribution package manager on Linux). The launcher creates a
missing `backend/.env.dev` automatically. `tools/start_services.sh --check`
validates the launch dependencies at any time.

### Docker Compose

```bash
cp backend/.env.example backend/.env.dev
docker compose up --build
```

The default ports are `15173` for the frontend, `18000` for the backend, and
`16379` for Redis.

## Model configuration

Edit your local `backend/.env.dev`. Each core role uses the same field shape:

```dotenv
COORDINATOR_API_TYPE=openai-responses
COORDINATOR_API_KEY=your-key
COORDINATOR_MODEL=your-model
COORDINATOR_BASE_URL=https://your-provider.example/
COORDINATOR_MAX_TOKENS=8192
```

Replace `COORDINATOR` with `MODELER`, `CODER`, or `WRITER` as needed. See the
[configuration guide](docs/configuration.md) for all fields. Never commit a
`.env` file or real credentials.

## Synthetic example

The repository includes only a project-authored synthetic community-cooling
dataset, not third-party contest statements or attachments. Create a demo task
with `POST /example` and `{"example_id": "urban-cooling"}`, or upload your own
problem and data through the UI.

## Development

```bash
cd backend
uv run ruff check app tests
uv run pytest tests -q

cd ../frontend
pnpm run lint
pnpm run build

cd ..
# Repository-level launcher and config contract tests (Windows: .venv\Scripts\python.exe)
backend/.venv/bin/python -m pytest tests -q
```

To build the Windows installer, run `tools/package_win.ps1`. Its default output
is `Remit/build/output/RemitSetup.exe` under the current user's local application
data directory.

## Security boundary

Remit targets a trusted, single-user local environment. It does not provide the
authentication, authorization, or execution isolation required for a public
multi-tenant service. See [SECURITY.md](SECURITY.md) for private vulnerability
reporting and deployment guidance.

## Contributing and license

Issues and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md)
and the [community code of conduct](CODE_OF_CONDUCT.md) first. Remit-owned
source and synthetic example data are available under the [MIT License](LICENSE).
Dependencies and bundled runtime files retain their respective licenses; see
[third-party notices](THIRD_PARTY_NOTICES.md).

The current source was audited and independently reworked against
MathModelAgent. Rebuilding branch history does not change the provenance of
earlier published revisions. See [NOTICE.md](NOTICE.md) and the
[source-provenance audit](docs/originality-audit.md) for the technical scope and
limitations. Those documents are transparent disclosures, not legal opinions.

## Star History ⭐

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/zhou2030109-glitch/Remit/refs/heads/star-history/assets/star-history/star-history-dark.svg?v=2" />
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/zhou2030109-glitch/Remit/refs/heads/star-history/assets/star-history/star-history-light.svg?v=2" />
    <img alt="Remit Star History" src="https://raw.githubusercontent.com/zhou2030109-glitch/Remit/refs/heads/star-history/assets/star-history/star-history-light.svg?v=2" width="800" />
  </picture>
</p>

## WeChat community

Scan the QR code to join the **Remit (数模 Agent)** WeChat group for usage
questions, mathematical modeling workflows, feedback, and development discussion.

<p align="center">
  <a href="./assets/remit-wechat-group.png">
    <img src="./assets/remit-wechat-group.png" alt="Remit WeChat group QR code, valid before September 14, 2026" width="360" />
  </a>
</p>

> Updated September 7, 2026. The image states that the QR code is valid before
> September 14. Click the image for full resolution; if it expires, open an issue
> to request an update.
