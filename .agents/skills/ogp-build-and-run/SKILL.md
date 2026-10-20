---
name: ogp-build-and-run
description: Set up or diagnose native Apple Silicon OGP source execution and dependency failures.
---

# Native macOS environment

Use `.venv/bin/python`; bootstrap or repair with `bash scripts/setup_macos.sh`.
Inspect that script and `pyproject.toml` before changing dependency resolution;
`uv.lock` is authoritative and `pylock.toml` its export. Do not use stale requirements
files or the global Python environment for application validation.

Launch with `.venv/bin/python -m open_garden_planner` or `Lancer-Jardin.command`.
Reproduce the import/startup failure and capture the actual traceback. Check native
architecture and Qt/plugin paths when relevant. Tests must use existing isolated
fixtures; a standalone process needs its own app-data/settings isolation too.

No Windows packaging is needed. For translations use `docs/TRANSLATING.md`.
Setup details and platform history live in `docs/07-deployment-view/`; distinguish
upstream deployment from this fork's editable-source acceptance.
