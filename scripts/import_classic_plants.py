"""Import starter plant lists while OGP is closed, preserving the existing library."""

import argparse
import json
from pathlib import Path

from open_garden_planner.services.classic_plant_lists import export_classics, install_classics
from open_garden_planner.services.plant_library import get_app_data_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-path", type=Path, help="Default: OGP app-data/plant_lists.json")
    parser.add_argument("--export-dir", type=Path, help="Export portable lists instead of installing")
    args = parser.parse_args()
    if args.export_dir:
        print(json.dumps([str(p) for p in export_classics(args.export_dir)], ensure_ascii=False))
    else:
        path = args.storage_path or get_app_data_dir() / "plant_lists.json"
        print(json.dumps(install_classics(path), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
