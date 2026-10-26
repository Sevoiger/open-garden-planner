"""Qt-free import and execution boundaries for migrated domain modules."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_domain_modules_import_and_generate_tasks_without_qt() -> None:
    """Reject Qt imports throughout the import and execution path."""
    script = r'''
import builtins
import datetime
import importlib.abc
import sys
import tempfile
from pathlib import Path

blocked_roots = ("PyQt6", "PySide6", "PySide2")
original_import = builtins.__import__

class QtBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == root or fullname.startswith(root + ".") for root in blocked_roots):
            raise AssertionError(f"Qt import attempted: {fullname}")
        return None

sys.meta_path.insert(0, QtBlocker())

def reject_qt_import(name, *args, **kwargs):
    if any(name == root or name.startswith(root + ".") for root in blocked_roots):
        raise AssertionError(f"Qt import attempted: {name}")
    return original_import(name, *args, **kwargs)

builtins.__import__ = reject_qt_import
modules = (
    "open_garden_planner.core.plan_io",
    "open_garden_planner.core.project_data",
    "open_garden_planner.agent_api.schema",
    "open_garden_planner.agent_api.queries",
    "open_garden_planner.agent_api.creates",
    "open_garden_planner.agent_api.edits",
    "open_garden_planner.agent_api.domain",
    "open_garden_planner.services.task_generator",
)
for module in modules:
    __import__(module)

from open_garden_planner.services.task_generator import (
    BedInput,
    PlanState,
    generate_all,
)
from open_garden_planner.services.weather_service import FrostAlert
from open_garden_planner.core.plan_io import read_plan, write_plan
from open_garden_planner.core.project_data import ProjectData

state = PlanState(
    today=datetime.date(2026, 10, 26),
    year=2026,
    beds=(BedInput(bed_id="bed-1", name="Bed", mismatch_plants=("Tomato",)),),
    frost_alerts=(FrostAlert(
        date="2026-10-26", min_temp=1.5, severity="red", affected_plant_ids=[]
    ),),
    actionable_only=False,
)
tasks = generate_all(state)
assert {task.task_type for task in tasks} >= {"soil_mismatch", "frost_alert_red"}
assert any(task.title.startswith("Soil mismatch in ") for task in tasks)
assert any(task.title == "Frost 1.5°C" for task in tasks)
with tempfile.TemporaryDirectory() as directory:
    target = Path(directory) / "synthetic.ogp"
    payload = ProjectData.from_dict({
        "canvas": {"width": 900, "height": 700},
        "layers": [],
        "objects": [],
        "future_field": {"keep": True},
    }).to_dict()
    write_plan(target, payload)
    assert read_plan(target)["future_field"] == {"keep": True}
assert not any(
    name == root or name.startswith(root + ".")
    for name in sys.modules
    for root in blocked_roots
)
'''
    env = os.environ.copy()
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_historical_package_exports_remain_available() -> None:
    from open_garden_planner import agent_api, core, services
    from open_garden_planner.agent_api.schema import PlanSummary
    from open_garden_planner.core.project import ProjectData as ProjectDataCompat
    from open_garden_planner.core.project_data import ProjectData

    assert core.ProjectData is ProjectData
    assert ProjectDataCompat is ProjectData
    assert core.ProjectManager.__module__ == "open_garden_planner.core.project"
    assert services.PlantLibrary.__module__ == "open_garden_planner.services.plant_library"
    assert agent_api.PlanSummary is PlanSummary
