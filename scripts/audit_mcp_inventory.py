#!/usr/bin/env python3
"""Inventory product surfaces from source, without opening personal app storage.

This records declarations, not successful MCP coverage. Runtime-only actions
(conditional context menus, data-driven catalogues) must also be discovered in Qt.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SURFACES = {"addAction", "addMenu", "addTab", "addRow", "setHorizontalHeaderLabels",
            "QAction", "QPushButton", "QToolButton", "QCheckBox", "QRadioButton",
            "QGroupBox", "setWindowTitle"}


def inventory() -> list[dict]:
    records = []
    for path in sorted((ROOT / "src/open_garden_planner").rglob("*.py")):
        if not any(part in path.parts for part in ("ui", "app", "models")):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = [ast.unparse(b) for b in node.bases]
                if path.parent.name == "models" or any("Dialog" in b for b in bases):
                    records.append({"source": str(path.relative_to(ROOT)), "line": node.lineno,
                                    "kind": "model" if path.parent.name == "models" else "dialog",
                                    "name": node.name, "status": "non_testee"})
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name not in SURFACES:
                continue
            labels = [n.value for arg in node.args for n in ast.walk(arg)
                      if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.strip()]
            if labels:
                records.append({"source": str(path.relative_to(ROOT)), "line": node.lineno,
                                "kind": name, "labels": labels, "status": "non_testee"})
    return sorted(records, key=lambda r: (r["source"], r["line"], r["kind"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="JSON output; defaults to stdout")
    args = parser.parse_args()
    text = json.dumps({"claim": "Source declarations only; not proof of end-to-end coverage.",
                       "surfaces": inventory()}, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
