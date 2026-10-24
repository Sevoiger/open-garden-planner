"""Keep themed dialog buttons wide enough for their current translated labels."""

from PyQt6.QtCore import QEvent, QObject, QSize
from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton, QStyle, QStyleOptionButton


def fit_button_text(button: QPushButton) -> None:
    """Measure styled content, including padding and any icon, after polishing."""
    if not button.text():
        return
    button.ensurePolished()
    option = QStyleOptionButton()
    option.initFrom(button)
    style = button.style()
    if style is None:
        return
    contents = style.subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
    horizontal = max(0, button.width() - contents.width())
    vertical = max(0, button.height() - contents.height())
    text = button.text().replace("&&", "\0").replace("&", "").replace("\0", "&")
    width = button.fontMetrics().horizontalAdvance(text) + horizontal
    if not button.icon().isNull():
        width += button.iconSize().width() + 4
    size = QSize(width, button.fontMetrics().height() + vertical)
    button.setMinimumSize(button.minimumSize().expandedTo(button.minimumSizeHint()).expandedTo(size))


class DialogButtonSizing(QObject):
    """Cover Qt's own message boxes, which bypass ResponsiveDialog."""

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._fitting = False

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:  # noqa: N802
        if (not self._fitting and isinstance(watched, QMessageBox) and event is not None
                and event.type() in (QEvent.Type.Show, QEvent.Type.FontChange,
                                     QEvent.Type.StyleChange, QEvent.Type.LanguageChange)):
            self._fitting = True
            try:
                for button in watched.findChildren(QPushButton):
                    fit_button_text(button)
            finally:
                self._fitting = False
        return super().eventFilter(watched, event)
