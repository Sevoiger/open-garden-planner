"""Compact month selectors for botanical recommendations."""

from PyQt6.QtCore import QLocale, Qt
from PyQt6.QtWidgets import QGridLayout, QGroupBox, QLabel, QToolButton, QVBoxLayout, QWidget


class BotanicalCalendarWidget(QWidget):
    """Three primary bands with optional, collapsible specialized periods."""

    def __init__(self, *, editable: bool, extended: bool = False, quick: bool = False, parent=None):
        super().__init__(parent)
        self._editable = editable
        self._buttons = {}
        self._source = {}
        self._source_text = {}
        self._edited = set()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        core = (
            ("flowering", self.tr("Flowering")),
            ("pruning", self.tr("Pruning")),
        )
        self._add_bands(layout, core if quick else (("planting", self.tr("Planting")), *core))
        self._quick = quick
        self.specialized_group = None
        if extended:
            group = QGroupBox(self.tr("Other recommended periods"))
            group.setCheckable(True)
            group.setChecked(False)
            group_layout = QVBoxLayout(group)
            content = QWidget()
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(0, 0, 0, 0)
            self._add_bands(content_layout, (
                ("planting_optimal", self.tr("Optimal planting")),
                ("harvest", self.tr("Harvest and fruiting")),
                ("sowing", self.tr("Sowing")),
                ("propagation", self.tr("Propagation")),
            ))
            content.setVisible(False)
            group.toggled.connect(content.setVisible)
            group_layout.addWidget(content)
            layout.addWidget(group)
            self.specialized_group = group

    def _add_bands(self, outer, activities):
        panel = QWidget()
        layout = QGridLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(4)
        layout.setVerticalSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        locale = QLocale()
        for column in range(1, 13):
            name = locale.monthName(column, QLocale.FormatType.ShortFormat)
            label = QLabel(name)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setFixedWidth(30)
            label.setToolTip(locale.monthName(column, QLocale.FormatType.LongFormat))
            layout.addWidget(label, 0, column)
        for row, (key, title) in enumerate(activities, 1):
            label = QLabel(title)
            layout.addWidget(label, row, 0)
            source_label = QLabel()
            source_label.setWordWrap(True)
            layout.addWidget(source_label, row + len(activities) + 1, 0, 1, 13)
            self._source_text[key] = source_label
            buttons = []
            for month in range(1, 13):
                button = QToolButton()
                button.setText("●")
                button.setFixedSize(30, 26)
                button.setCheckable(True)
                button.setAutoRaise(True)
                button.setEnabled(self._editable)
                button.setAccessibleName(
                    f"{title} — {locale.monthName(month, QLocale.FormatType.LongFormat)}"
                )
                button.clicked.connect(lambda _checked, key=key: self._edited.add(key))
                button.setStyleSheet(
                    "QToolButton { color: #a8afa4; padding: 2px; }"
                    "QToolButton:checked { color: #28784b; }"
                    "QToolButton:disabled:checked { color: #28784b; }"
                )
                layout.addWidget(button, row, month)
                buttons.append(button)
            self._buttons[key] = buttons
            self._source[key] = label
        outer.addWidget(panel)

    def set_calendar(self, calendar: dict) -> None:
        specialized_known = []
        for key, buttons in self._buttons.items():
            period = calendar.get(key) or {}
            months = period.get("months") or []
            source = period.get("source_text") or ""
            self._source[key].setToolTip(source)
            notes = []
            if source:
                notes.append(self.tr("Source: {text}").format(text=source))
            if period.get("manually_edited"):
                notes.append(self.tr("Months corrected manually"))
            self._source_text[key].setText(
                f"{self._source[key].text()} — {' · '.join(notes)}" if notes else ""
            )
            self._source_text[key].setVisible(not self._quick and bool(notes))
            for index, button in enumerate(buttons, 1):
                button.setChecked(index in months)
                button.setToolTip(source)
            if key not in {"planting", "flowering", "pruning"} and (months or source):
                specialized_known.append(self._source[key].text())
        if self.specialized_group is not None:
            self.specialized_group.setToolTip(" · ".join(specialized_known))
        self._edited.clear()

    def months(self) -> dict[str, list[int] | None]:
        return {
            key: [index for index, button in enumerate(buttons, 1) if button.isChecked()] or None
            for key, buttons in self._buttons.items()
        }

    def edited_keys(self) -> set[str]:
        return set(self._edited)
