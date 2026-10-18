"""Install French starter cards and/or a folder of JSON sheets while OGP is closed."""

import argparse
import json
from pathlib import Path

from open_garden_planner.services.french_plant_catalog import (
    french_collections,
    localize_classic_lists,
)
from open_garden_planner.services.local_plant_import import install_collections, read_sheets
from open_garden_planner.services.plant_library import get_app_data_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?")
    parser.add_argument("--french-catalog", action="store_true")
    parser.add_argument("--app-data", type=Path)
    parser.add_argument("--collection-key", help="Stable unique batch key; default: folder name")
    args = parser.parse_args()
    if not args.directory and not args.french_catalog:
        parser.error("Supply a directory and/or --french-catalog")
    collections = french_collections() if args.french_catalog else []
    if args.directory:
        key = args.collection_key or args.directory.name
        collections.append((f"sheets/{key}", f"Mes plantes — {key}", read_sheets(args.directory)))
    app_data = args.app_data or get_app_data_dir()
    result = install_collections(app_data, collections)
    if args.french_catalog:
        result["classic_names_translated"] = localize_classic_lists(app_data)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
