"""Wheel gestures scroll forms instead of changing closed plant trait fields."""

import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication, QComboBox, QScrollArea, QVBoxLayout, QWidget

from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.ui.panels.plant_database_panel import PlantDatabasePanel
from open_garden_planner.ui.widgets.combo_wheel_guard import ComboWheelGuard
from tests.ui.test_plant_database_panel import _plant


def _wheel(widget, *, pixels=False):
    event = QWheelEvent(
        QPointF(5, 5), QPointF(widget.mapToGlobal(QPoint(5, 5))),
        QPoint(0, -40) if pixels else QPoint(),
        QPoint() if pixels else QPoint(0, -120), Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate if pixels else Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(widget, event)


@pytest.mark.parametrize("focused", [False, True])
@pytest.mark.parametrize("pixels", [False, True])
def test_plant_trait_wheel_scrolls_form_without_editing(qtbot, qapp, focused, pixels):
    guard = ComboWheelGuard()
    qapp.installEventFilter(guard)
    try:
        panel = PlantDatabasePanel()
        qtbot.addWidget(panel)
        panel.set_selected_items([_plant(PlantSpeciesData(scientific_name="Test", common_name="Test"))])
        panel.resize(420, 450)
        panel.show()
        qapp.processEvents()
        scroll = panel.findChild(QScrollArea)
        scroll.ensureWidgetVisible(panel.cycle_combo)
        combo = panel.cycle_combo
        if focused:
            combo.setFocus()
        else:
            combo.clearFocus()
        qapp.processEvents()
        scrollbar = scroll.verticalScrollBar()
        before = scrollbar.value()
        assert before < scrollbar.maximum()
        index = combo.currentIndex()
        changes = QSignalSpy(combo.currentIndexChanged)
        _wheel(combo, pixels=pixels)
        assert combo.currentIndex() == index
        assert len(changes) == 0
        assert scrollbar.value() > before
        assert panel._current_plant_data.cycle.value == "unknown"
        combo.setFocus()
        qtbot.keyClick(combo, Qt.Key.Key_Down)
        assert combo.currentIndex() != index
    finally:
        qapp.removeEventFilter(guard)


def test_open_choice_list_keeps_wheel_scrolling(qtbot, qapp):
    host = QWidget()
    qtbot.addWidget(host)
    layout = QVBoxLayout(host)
    combo = QComboBox()
    combo.addItems([f"Choice {i}" for i in range(200)])
    combo.setMaxVisibleItems(8)
    layout.addWidget(combo)
    guard = ComboWheelGuard(host)
    qapp.installEventFilter(guard)
    try:
        host.show()
        combo.showPopup()
        qapp.processEvents()
        view = combo.view()
        assert view.isVisible()
        before = view.verticalScrollBar().value()
        _wheel(view.viewport())
        assert view.verticalScrollBar().value() > before
        combo.hidePopup()
        assert combo.currentIndex() == 0
    finally:
        qapp.removeEventFilter(guard)


def test_real_main_window_installs_policy(qtbot, qapp, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from open_garden_planner.app.application import GardenPlannerApp

    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Discard)
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    combo = QComboBox(window)
    combo.addItems(["A", "B", "C"])
    combo.setCurrentIndex(1)
    _wheel(combo)
    assert combo.currentIndex() == 1
    qapp.removeEventFilter(window._combo_wheel_guard)
