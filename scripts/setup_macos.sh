#!/bin/bash
# Prepare an editable, locked, native macOS development environment.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ "$(uname -s)" != Darwin ]]; then
  echo "Ce script est réservé à macOS." >&2
  exit 1
fi
if [[ "$(sysctl -in sysctl.proc_translated 2>/dev/null || true)" == 1 ]]; then
  echo "Ouvrez un Terminal natif Apple Silicon (sans Rosetta), puis réessayez." >&2
  exit 1
fi
export UV_CACHE_DIR="$ROOT/.tools/uv-cache"
export UV_PYTHON_INSTALL_DIR="$ROOT/.tools/python"
mkdir -p "$ROOT/.tools"
PIN="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["tool"]["uv"]["required-version"].removeprefix("=="))')"
UV="$ROOT/.tools/bootstrap/bin/uv"
if [[ ! -x "$UV" ]] || [[ "$("$UV" --version)" != "uv $PIN"* ]]; then
  command -v uv >/dev/null || { echo "uv manque : installez uv, puis relancez ce script." >&2; exit 1; }
  uv --no-config venv --python python3 "$ROOT/.tools/bootstrap"
  uv --no-config pip install --python "$ROOT/.tools/bootstrap/bin/python" "uv==$PIN"
fi
"$UV" sync --locked --python 3.11 --all-extras --group build --no-install-project
"$UV" pip install --python .venv/bin/python --no-deps --no-build-isolation --editable .
"$UV" pip check --python .venv/bin/python
.venv/bin/python scripts/check_dependency_lock.py --uv "$UV"
echo "Installation prête ($(uname -m)). Ouvrez Lancer-Jardin.command."
