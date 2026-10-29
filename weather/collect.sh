#!/bin/sh
# POSIX wrapper for cron; no dependency on the caller's working directory.
set -eu
weather_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(dirname -- "$weather_dir")
cd "$repo_dir"
mkdir -p .local/weather/briollay
exec "$repo_dir/.venv/bin/python" -m open_garden_planner.services.weather_collection \
  --config "$weather_dir/config.json" --output "$repo_dir/.local/weather/briollay" \
  >> "$repo_dir/.local/weather/briollay/cron.log" 2>&1
