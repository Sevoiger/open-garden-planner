"""Position custom popups within the anchor's available screen geometry."""

from PyQt6.QtCore import QPoint, QRect, QSize
from PyQt6.QtWidgets import QWidget


def popup_rect(anchor: QRect, available: QRect, preferred: QSize) -> QRect:
    """Prefer below, then above, clamping both axes in logical screen pixels."""
    size = preferred.boundedTo(available.size())
    x = min(max(anchor.left(), available.left()), available.right() - size.width() + 1)
    y = anchor.bottom() + 1
    if y + size.height() > available.bottom() + 1:
        y = anchor.top() - size.height()
    y = min(max(y, available.top()), available.bottom() - size.height() + 1)
    return QRect(QPoint(x, y), size)


def show_popup_below(popup: QWidget, anchor: QWidget, preferred: QSize) -> None:
    """Size before showing, then account for the platform's actual window frame."""
    screen = anchor.screen()
    if screen is None:
        return
    available = screen.availableGeometry()
    anchor_rect = QRect(anchor.mapToGlobal(QPoint()), anchor.size())
    target = popup_rect(anchor_rect, available, preferred)
    popup.setMaximumSize(available.size())
    popup.resize(target.size())
    popup.move(target.topLeft())
    popup.show()
    frame = popup.frameGeometry()
    # Styles/platforms can add a native frame outside the requested client size.
    if frame.width() > available.width() or frame.height() > available.height():
        popup.resize(popup.width() - max(0, frame.width() - available.width()),
                     popup.height() - max(0, frame.height() - available.height()))
        frame = popup.frameGeometry()
    position = popup_rect(anchor_rect, available, frame.size()).topLeft()
    popup.move(popup.pos() + position - frame.topLeft())
