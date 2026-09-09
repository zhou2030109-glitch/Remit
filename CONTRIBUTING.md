# Contributing to Remit

Thank you for helping improve Remit. Small, focused pull requests are the
easiest to review.

## Before opening a change

- Search existing issues and pull requests.
- Open an issue first for large features, workflow changes, new providers, or
  changes to persisted task data.
- Report security problems privately as described in [SECURITY.md](SECURITY.md).
- Do not contribute confidential data, real API keys, proprietary contest
  material, or source copied from a project whose license is incompatible with
  the MIT License.

By submitting a contribution, you confirm that you have the right to submit it
and agree that it may be distributed under this repository's MIT License.

## Development setup

Requirements:

- Python 3.12 (see `backend/.python-version`) and [uv](https://docs.astral.sh/uv/);
- Node.js 24 (see `frontend/.node-version`) and pnpm 10.6.3;
- Redis for the complete runtime workflow.

```powershell
cd backend
uv sync --frozen

cd ..\frontend
pnpm install --frozen-lockfile
```

Copy `backend/.env.example` to `backend/.env.dev` only when you need to run the
application. Unit tests must not require real provider credentials.

## Required checks

Run the relevant checks before opening a pull request:

```powershell
cd backend
uv run ruff check app tests
uv run ruff format --check app tests
uv run pytest tests -q

cd ..\frontend
pnpm run check
pnpm run typecheck
pnpm run test
pnpm run build

cd ..
backend\.venv\Scripts\python.exe -m pytest tests -q
```

See [development and release](docs/development.md) for the complete local and CI
verification workflow.

Windows launcher tests require Windows. The repository CI runs backend and
launcher tests on Windows and the frontend build on Linux.

## Pull requests

Describe the user-visible outcome, important design choices, and verification
performed. Add regression tests for bug fixes and update documentation when a
public configuration field, API, or workflow behavior changes.
