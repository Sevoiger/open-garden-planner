#!/bin/sh
# POSIX wrapper for cron; no dependency on the caller's working directory.
set -eu
weather_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(dirname -- "$weather_dir")
cd "$repo_dir"
archive_dir=$("$repo_dir/.venv/bin/python" -c 'from open_garden_planner.services.weather_workspace import default_archive; print(default_archive())')
mkdir -p "$archive_dir"
exec "$repo_dir/.venv/bin/python" -m open_garden_planner.services.weather_collection \
  --config "$weather_dir/config.json" --output "$archive_dir" \
  >> "$archive_dir/cron.log" 2>&1
