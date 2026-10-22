"""Middle drag follows viewport pixels, including short moves and guides."""

import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication


def move_with_middle(canvas, point):
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(point),
                        QPointF(canvas.viewport().mapToGlobal(point)),
                        Qt.MouseButton.NoButton, Qt.MouseButton.MiddleButton,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(canvas.viewport(), event)


@pytest.mark.parametrize('zoom', [.2, 1, 3])
@pytest.mark.parametrize('delta', [QPoint(1, 0), QPoint(0, 1), QPoint(-1, -1), QPoint(23, -17)])
def test_pan_tracks_mouse_in_viewport_pixels(canvas, qtbot, zoom, delta):
    canvas.resize(900, 650)
    canvas.show()
    canvas.set_zoom(zoom)
    canvas.centerOn(2000, 1500)
    qtbot.wait(10)
    anchor = QPointF(2000, 1500)
    before = canvas.mapFromScene(anchor)
    position = QPoint(350, 300)
    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.MiddleButton, pos=position)
    for _ in range(10):
        position += delta
        move_with_middle(canvas, position)
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.MiddleButton, pos=position)
    actual = canvas.mapFromScene(anchor) - before
    assert actual == delta * 10


def test_middle_press_on_ruler_still_pans(canvas, qtbot):
    canvas.resize(900, 650)
    canvas.show()
    canvas.centerOn(2000, 1500)
    start = QPoint(350, 5)
    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.MiddleButton, pos=start)
    assert canvas._panning
    assert canvas._dragging_guide is None
    move_with_middle(canvas, start + QPoint(40, 30))
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.MiddleButton, pos=start + QPoint(40, 30))
    assert not canvas._panning and not canvas.scene().guide_lines
