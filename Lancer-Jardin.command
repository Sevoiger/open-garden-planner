#!/bin/bash
set -uo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 1
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Environnement manquant. Dans le Terminal, exécutez :"
  printf '  bash %q\n' "$ROOT/scripts/setup_macos.sh"
  read -r -p "Appuyez sur Entrée pour fermer. "
  exit 1
fi
mkdir -p "$ROOT/.local/logs"
"$ROOT/.venv/bin/python" -m open_garden_planner "$@" >> "$ROOT/.local/logs/launcher.log" 2>&1
STATUS=$?
if [[ "$STATUS" -ne 0 ]]; then
  echo "L'application s'est arrêtée avec le code $STATUS."
  echo "Journal : $ROOT/.local/logs/launcher.log"
  tail -n 25 "$ROOT/.local/logs/launcher.log"
  read -r -p "Appuyez sur Entrée pour fermer. "
fi
exit "$STATUS"
