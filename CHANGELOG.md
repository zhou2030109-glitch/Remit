# Changelog

All notable user-visible changes should be recorded here. The project follows
[Semantic Versioning](https://semver.org/) for releases.

## [Unreleased]

### Added

- Reproducible Python, Node.js, uv, and pnpm versions for local development,
  CI, and release builds.
- Formatting and dependency-consistency checks in CI.
- Python and frontend production dependency audits plus a high-confidence
  Bandit scan.
- A tag-driven release workflow for the production Docker image.
- A maintainer-facing development and release guide.

### Security

- Raised the minimum versions of FastAPI, Uvicorn, Pydantic, Pillow, Requests,
  and python-multipart to releases without known advisories, and refreshed the
  locked dependency graph.

### Fixed

- Local PDF delivery tests now skip cleanly when the optional SimHei font is not
  installed, instead of failing with a missing-file error.
