# Vendored code

## ice/ — ICE V.01 (Immutable Chain Evidence)

- Source: https://github.com/Dhammerr6304/ice-v01
- Pinned commit: `8b7a2c14ac27ba48433f8731a5a3bd8273acb915` (2026-10-23)
- License: MIT, kept at `ice/LICENSE` (Copyright (c) 2026 REKT☆WRLD Labs / Dhammerr6304)
- Copied: `src/`, `schemas/`, `examples/`, `docs/`, `LICENSE`, `README.md`, `pyproject.toml`
- Left out: `receipts/`, the upstream author's own receipt files, which are data from
  another project and not part of the library.

The files are unmodified. `ice.sha256` lists their SHA-256 hashes, and
`tests/test_vendored_ice.py` fails if any file changes, appears or disappears. Signing,
Postgres persistence, verification and anchoring live in `agent/evidence/`, which wraps
this package without editing it.

To update: export the new commit with `git archive <commit> -- <paths above>`, replace
`ice/`, regenerate `ice.sha256` with `find ice -type f -not -path "*/__pycache__/*" -not -path "*.egg-info/*" | sort | xargs sha256sum`, and
update the commit hash here and in CLAUDE.md.
