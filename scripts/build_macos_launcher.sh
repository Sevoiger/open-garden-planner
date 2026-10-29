#!/bin/bash
# Rebuild only the native entry point, never the Python application.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$(uname -s)" != Darwin ]]; then
  echo "Ce script est réservé à macOS." >&2
  exit 1
fi
mkdir -p "$ROOT/Jardin.app/Contents/Resources"
cp "$ROOT/installer/OpenGardenPlanner.icns" "$ROOT/Jardin.app/Contents/Resources/OpenGardenPlanner.icns"
printf '%s' "$ROOT" > "$ROOT/Jardin.app/Contents/Resources/SourceRoot.txt"
/usr/bin/clang -framework Foundation -Os "$ROOT/scripts/macos_launcher.m" \
  -o "$ROOT/Jardin.app/Contents/MacOS/Jardin"
/usr/bin/codesign --force --sign - "$ROOT/Jardin.app"
/usr/bin/codesign --verify --strict --verbose=2 "$ROOT/Jardin.app"
