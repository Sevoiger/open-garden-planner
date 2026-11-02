"""Restored toolbar layouts must survive the first real window show.

Run with QT_QPA_PLATFORM=cocoa on macOS to exercise the reported native crash;
the usual offscreen run checks the same restored visibility and row behavior.
A child process contains a possible native fault and owns only temporary data.
"""

import os
import subprocess
import sys


def test_restored_toolbar_layout_survives_collection_and_show(tmp_path):
    script = '''
import faulthandler, gc, sys
from pathlib import Path
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QMainWindow, QToolBar
faulthandler.enable()
root = Path(sys.argv[1])
import open_garden_planner.app.settings as settings
settings.ORGANIZATION_NAME = "OGP-startup-test"
settings.APPLICATION_NAME = "isolated-" + root.name
import open_garden_planner.services.plant_library as library
library._default_app_data_dir = lambda: root / "app-data"
from open_garden_planner.app.ui_state import UiStateStore
from open_garden_planner.app.application import GardenPlannerApp
from open_garden_planner.ui.theme import apply_theme
GardenPlannerApp._startup_sequence = lambda self: None
GardenPlannerApp._start_update_check = lambda self: None
app = QApplication([])
apply_theme(app, "dark")
# An older session with two rows and hidden core bars, not the owner's state.
seed = QMainWindow()
seed.resize(1400, 850)
for name in ("MainToolbar", "ConstraintToolbar", "CategoryToolbar"):
    bar = QToolBar(name, seed)
    bar.setObjectName(name)
    if name == "CategoryToolbar":
        seed.addToolBarBreak()
    seed.addToolBar(bar)
    bar.hide()
UiStateStore().save_geometry(seed)
window = GardenPlannerApp()
assert window._geometry_restored
gc.collect()
window.show()
app.processEvents()
bars = (window.main_toolbar, window.constraint_toolbar, window.category_toolbar)
assert all(bar.isVisible() for bar in bars)
assert len({bar.y() for bar in bars}) == 1, "Core toolbars must occupy one row"
assert window._sun_toolbar.isHidden()
assert window.soil_overlay_toolbar.isHidden()
visible_before_quit = []
def finish():
    visible_before_quit.append(window.isVisible())
    app.quit()
QTimer.singleShot(200, finish)
app.exec()
assert visible_before_quit == [True]
window._project_manager.mark_clean()
window.close()
app.processEvents()
settings.create_qsettings().clear()
print("startup-layout-ok", flush=True)
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env={**os.environ, "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "offscreen")},
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "startup-layout-ok" in result.stdout
