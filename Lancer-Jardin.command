#!/bin/bash
set -uo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 1
DEBUG=1
if [[ "${1:-}" == "--no-console" ]]; then
  DEBUG=0
  shift
fi
report_error() {
  if [[ "$DEBUG" == 1 ]]; then
    printf '%s\n' "$1"
    read -r -p "Appuyez sur Entrée pour fermer. "
  else
    /usr/bin/osascript - "$1" <<'APPLESCRIPT'
on run argv
  display alert "Jardin — lancement impossible" message (item 1 of argv) as critical
end run
APPLESCRIPT
  fi
}
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  printf -v SETUP_COMMAND 'bash %q' "$ROOT/scripts/setup_macos.sh"
  report_error "Environnement manquant. Dans le Terminal, exécutez :
$SETUP_COMMAND"
  exit 1
fi
mkdir -p "$ROOT/.local/logs"
if [[ "$DEBUG" == 1 ]]; then
  "$ROOT/.venv/bin/python" -m open_garden_planner "$@" 2>&1 | tee -a "$ROOT/.local/logs/launcher.log"
  STATUS=${PIPESTATUS[0]}
else
  "$ROOT/.venv/bin/python" -m open_garden_planner "$@" >> "$ROOT/.local/logs/launcher.log" 2>&1
  STATUS=$?
fi
if [[ "$STATUS" -ne 0 ]]; then
  if [[ "$DEBUG" == 1 ]]; then
    tail -n 25 "$ROOT/.local/logs/launcher.log"
  fi
  report_error "L'application s'est arrêtée avec le code $STATUS.
Journal : $ROOT/.local/logs/launcher.log"
fi
exit "$STATUS"
