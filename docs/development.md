# Development and release workflow

This document is the maintainer-facing contract for local development, CI, and
releases. It contains no project credentials or deployment-specific values.

## Reproducible toolchain

The repository pins the versions used by CI and release builds:

- Python: `backend/.python-version` (3.12)
- uv: 0.12.10
- Node.js: `frontend/.node-version` (24)
- pnpm: 10.6.3, declared by `frontend/package.json#packageManager`

`uv sync --frozen` and `pnpm install --frozen-lockfile` must be used for
local setup and CI. Do not rely on an untracked global interpreter or package
manager version.

## Local verification

Run the same checks as CI from a clean checkout:

```bash
cd backend
uv sync --frozen
uv run ruff check app tests
uv run ruff format --check app tests
uv run pytest tests -q
uv pip check

cd ../frontend
pnpm install --frozen-lockfile
pnpm run check
pnpm run typecheck
pnpm run test
pnpm run build

cd ..
backend/.venv/bin/python -m pytest tests -q
```

On Windows, replace the final interpreter path with
`backend\.venv\Scripts\python.exe`.

## Continuous integration

The CI workflow runs:

- backend lint, formatting, dependency consistency, and tests on Windows,
  macOS, and Linux;
- repository launcher and configuration contract tests;
- Python and frontend production dependency audits plus a high-confidence
  Bandit scan;
- frontend formatting, lint, tests, and production build on Linux;
- a single `CI status` check so branch protection does not need to track every
  matrix entry or individual job.

The production Docker image is built on every pull request and main push. The
release workflow publishes the image only for version tags or an explicitly
approved manual run.

## Release workflow

The release workflow is triggered by a `v*` tag or manually. It validates that
the tag, `backend/pyproject.toml`, and `frontend/package.json` versions agree,
builds the production image, and publishes it to GitHub Container Registry. A
manual run can build without publishing.

The release image is defined by the root `Dockerfile` and is consumed by
`docker-compose.release.yml`. Keep the image tag and both package versions in
sync before creating a release tag.
