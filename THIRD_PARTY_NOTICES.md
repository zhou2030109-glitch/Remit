# Third-party notices

The MIT License in the repository root applies to Remit-owned source and
project-authored synthetic example data. It does not replace the licenses of
third-party software.

## Source dependencies

Desktop builds retain Python package metadata and license files in the bundled
site-packages, plus a package inventory under `licenses/`. TinyTeX retains
`LICENSE.TL`, `LICENSE.CTAN` and installed package documentation. Fandol font
license documentation is also copied to `licenses/fandol/`.
The Windows installer distributes Microsoft's original signed Visual C++
Redistributable installer under Microsoft's redistribution terms; it is not
covered by Remit's MIT license. See `tools/desktop-dependencies.json` for source
URLs and checksums. Third-party licenses, including copyleft components, continue
to apply to their respective files; Remit's license does not override them.

Python and JavaScript dependencies are declared in `backend/pyproject.toml`,
`backend/uv.lock`, `frontend/package.json`, and `frontend/pnpm-lock.yaml`.
Each dependency remains subject to its own license. Generated or adapted UI
primitives under `frontend/src/components/ui/` follow conventions from the
open-source Vue UI ecosystem and use the declared Reka UI packages.

## Historical source provenance

Earlier Remit revisions evolved from MathModelAgent. The current tree was
independently reworked, but archived revisions remain subject to their original
provenance and any applicable terms. See [NOTICE.md](NOTICE.md) and
[docs/originality-audit.md](docs/originality-audit.md).

## Imported contest skills

The text under `backend/app/competition_skills/vendor/` includes MIT-licensed material from math-modeling-skill and Mathodology. Their license files and pinned source hashes are preserved in that directory and `sources.lock.json`. The public release does not include the developer's private paper library or excerpts.
