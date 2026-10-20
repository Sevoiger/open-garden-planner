"""Adjustable sidebar bodies retain height, scroll, and keep canonical order."""

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from open_garden_planner.ui.panels.properties_panel import PropertiesPanel
from open_garden_planner.ui.widgets.collapsible_panel import CollapsiblePanel
from open_garden_planner.ui.widgets.panel_stack import SidebarController


def test_drag_resize_collapse_reopen_and_scroll(qtbot):
    content = QWidget()
    layout = QVBoxLayout(content)
    for i in range(60):
        layout.addWidget(QLabel(f"Field {i}"))
    controller = SidebarController(resizable_panels=True)
    qtbot.addWidget(controller)
    controller.resize(350, 700)
    panel = CollapsiblePanel("Properties", content)
    sibling = CollapsiblePanel("Layers", QLabel("Layers"))
    controller.add_panel("properties", panel)
    controller.add_panel("layers", sibling)
    controller.show()
    order = controller.panels()
    qtbot.mouseClick(panel.header, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel.minimumHeight() == 360)
    assert panel.height() == 360
    assert panel._body_scroll.verticalScrollBar().maximum() > 0
    grip = panel._resize_grip
    start = grip.rect().center()
    qtbot.mousePress(grip, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(grip, start + QPoint(0, 90))
    qtbot.mouseRelease(grip, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel.height() == 450)
    assert panel.preferred_height() == 450
    qtbot.mouseClick(sibling.header, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: sibling.minimumHeight() == 300)
    assert panel.height() == 450
    qtbot.mouseClick(panel.header, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel.maximumHeight() == panel.header_height())
    assert panel._resize_grip.isHidden()
    qtbot.mouseClick(panel.header, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel.minimumHeight() == 450)
    assert panel.height() == 450
    assert controller.panels() == order
    panel._body_scroll.verticalScrollBar().setValue(100)
    assert panel._body_scroll.verticalScrollBar().value() == 100
    qtbot.mouseDClick(grip, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: panel.height() == 360)
    # 360 + 300 fits the original 700px viewport after the reset.
    # Force genuine overflow and wait for Qt to finish updating its layout.
    controller.resize(350, 500)
    qtbot.waitUntil(lambda: controller._scroll.verticalScrollBar().maximum() > 0)


def test_properties_content_change_does_not_shrink_user_height(qtbot):
    from open_garden_planner.ui.canvas.items import RectangleItem

    properties = PropertiesPanel()
    panel = CollapsiblePanel("Properties", properties)
    controller = SidebarController(resizable_panels=True)
    qtbot.addWidget(controller)
    controller.resize(350, 700)
    controller.add_panel("properties", panel)
    controller.show()
    panel.header.pin_toggled.emit(True)
    qtbot.waitUntil(lambda: panel.minimumHeight() == 360)
    panel.set_preferred_height(420)
    item = RectangleItem(0, 0, 400, 200)
    properties.set_selected_items([item])
    qtbot.waitUntil(lambda: panel.height() == 420)
    properties.set_selected_items([])
    qtbot.waitUntil(lambda: panel.height() == 420)
    assert panel.minimumHeight() == panel.maximumHeight() == 420
