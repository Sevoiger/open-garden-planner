"""Screen-bounded dialogs whose forms scroll rather than compress their controls."""

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QResizeEvent, QShowEvent
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLayout,
    QLayoutItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from open_garden_planner.ui.widgets.dialog_button_sizing import fit_button_text


def _is_action_row(item: QLayoutItem | None) -> bool:
    """Recognize terminal button rows without pulling form fields into the footer."""
    if item is None:
        return False
    widget = item.widget()
    if widget is not None:
        return isinstance(widget, (QDialogButtonBox, QPushButton))
    layout = item.layout()
    if layout is None or not layout.count():
        return False
    for i in range(layout.count()):
        child = layout.itemAt(i)
        if child is None or (child.spacerItem() is None and not _is_action_row(child)):
            return False
    return True



def bounded_dialog_rect(frame: QRect, available: QRect) -> QRect:
    """Keep a frame inside the available logical desktop, including negative origins."""
    size = frame.size().boundedTo(available.size())
    return QRect(
        QPoint(
            min(max(frame.x(), available.left()), available.right() - size.width() + 1),
            min(max(frame.y(), available.top()), available.bottom() - size.height() + 1),
        ),
        size,
    )


class _ActionScrollArea(QScrollArea):
    """Keep a wide action row readable on narrow windows, with horizontal access."""

    def resizeEvent(self, event: QResizeEvent | None) -> None:
        super().resizeEvent(event)
        self.update_height()

    def update_height(self) -> None:
        body = self.widget()
        if body is None:
            return
        natural = body.minimumSizeHint()
        height = natural.height()
        bar = self.horizontalScrollBar()
        if natural.width() > self.width() and bar is not None:
            height += bar.sizeHint().height()
        self.setFixedHeight(height)


class ResponsiveDialog(QDialog):
    """Preserve natural form minima and leave final action buttons outside the scroll.

    Installation happens once, on first show, after subclass constructors have built
    their ordinary layouts. Existing inner tables, tab widgets and scroll areas keep
    their own scrolling. No settings, fonts, signals or acceptance paths are changed.
    """

    def _prepare_scrollable_form(self) -> None:
        if hasattr(self, "_responsive_scroll"):
            return
        layout = self.layout()
        if layout is None:
            return
        # Resolve the stylesheet font/padding before layouts cache their minima.
        for widget in [self, *self.findChildren(QWidget)]:
            widget.ensurePolished()
            if isinstance(widget, QPushButton) and widget.text():
                fit_button_text(widget)
            widget_layout = widget.layout()
            if widget_layout is not None:
                widget_layout.invalidate()
        self._requested_maximum = self.maximumSize()
        preferred = self.size().expandedTo(self.sizeHint())
        footer = None
        if layout.count() and _is_action_row(layout.itemAt(layout.count() - 1)):
            footer = layout.takeAt(layout.count() - 1)
        body = QWidget()
        body.setObjectName("dialogForm")
        # QWidget.setLayout transfers the old layout and its widgets from the dialog.
        body.setLayout(layout)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        scroll = QScrollArea(self)
        scroll.setObjectName("dialogFormScroll")
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        scroll.show()
        if footer is not None:
            actions = QWidget(self)
            action_layout = QVBoxLayout(actions)
            footer_widget = footer.widget()
            footer_layout = footer.layout()
            if footer_widget is not None:
                action_layout.addWidget(footer_widget)
            elif footer_layout is not None:
                action_layout.addLayout(footer_layout)
            action_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
            action_scroll = _ActionScrollArea(self)
            action_scroll.setObjectName("dialogActionsScroll")
            action_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            action_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            action_scroll.setWidgetResizable(True)
            action_scroll.setWidget(actions)
            outer.addWidget(action_scroll)
            action_scroll.show()
        self._responsive_scroll = scroll
        self.resize(preferred)
        self._screen_fit_timer = QTimer(self)
        self._screen_fit_timer.setSingleShot(True)
        self._screen_fit_timer.timeout.connect(self._fit_to_screen)

    def showEvent(self, event: QShowEvent | None) -> None:
        self._prepare_scrollable_form()
        super().showEvent(event)
        if not hasattr(self, "_responsive_scroll"):
            return
        self._fit_to_screen()
        # Native frame margins are finalized after the first show event.
        self._screen_fit_timer.start(0)

    def _fit_to_screen(self) -> None:
        # Qt can revise native/QSS button hints when children become visible.
        for button in self.findChildren(QPushButton):
            if button.text():
                fit_button_text(button)
        for actions in self.findChildren(_ActionScrollArea):
            actions.update_height()
        screen = self.screen()
        if screen is None:
            return
        available = screen.availableGeometry()
        frame = self.frameGeometry()
        decoration = frame.size() - self.size()
        limit = (available.size() - decoration).boundedTo(self._requested_maximum)
        minimum = self.minimumSize().boundedTo(limit)
        self.setMinimumSize(minimum)
        self.setMaximumSize(limit)
        self.resize(self.size().boundedTo(limit).expandedTo(QSize(1, 1)))
        frame = self.frameGeometry()
        target = bounded_dialog_rect(frame, available)
        self.move(target.topLeft())
