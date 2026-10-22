#!/bin/bash
# Rebuild only the native entry point, never the Python application.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$(uname -s)" != Darwin ]]; then
  echo "Ce script est réservé à macOS." >&2
  exit 1
fi
/usr/bin/clang -framework Foundation -Os "$ROOT/scripts/macos_launcher.m" \
  -o "$ROOT/Jardin.app/Contents/MacOS/Jardin"
/usr/bin/codesign --force --sign - "$ROOT/Jardin.app"
/usr/bin/codesign --verify --strict --verbose=2 "$ROOT/Jardin.app"
