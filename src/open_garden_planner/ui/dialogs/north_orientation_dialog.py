"""Choose true north without rotating the drawing or its background."""

import math

from PyQt6.QtCore import QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent, QPainter, QPaintEvent
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from open_garden_planner.services.north_arrow import draw_north_arrow


class NorthCompass(QWidget):
    angle_chosen = pyqtSignal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.angle_deg = 0.0
        self.setFixedSize(200, 200)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_angle(self, angle: float) -> None:
        self.angle_deg = angle
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self.palette().mid().color())
        painter.drawEllipse(QPointF(100, 100), 85, 85)
        draw_north_arrow(painter, QPointF(100, 100), 85, self.angle_deg,
                         self.palette().windowText().color())
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        delta = event.position() - QPointF(100, 100)
        if math.hypot(delta.x(), delta.y()) < 5:
            return
        self.angle_chosen.emit(math.degrees(math.atan2(delta.x(), -delta.y())) % 360)


class NorthOrientationDialog(QDialog):
    def __init__(self, angle: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("North orientation"))
        layout = QVBoxLayout(self)
        explanation = QLabel(self.tr(
            "Point the arrow in the same direction as true north on your imported plan. "
            "The drawing and background stay in place."
        ))
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.compass = NorthCompass(self)
        layout.addWidget(self.compass, alignment=Qt.AlignmentFlag.AlignCenter)
        self.angle_spin = QDoubleSpinBox(self)
        self.angle_spin.setRange(0, 359.99)
        self.angle_spin.setDecimals(2)
        self.angle_spin.setSuffix("°")
        self.angle_spin.setWrapping(True)
        self.angle_spin.setKeyboardTracking(False)
        self.angle_spin.valueChanged.connect(self.compass.set_angle)
        self.compass.angle_chosen.connect(self.angle_spin.setValue)
        self.angle_spin.setValue(angle)
        self.compass.set_angle(angle)
        form = QFormLayout()
        form.addRow(self.tr("Clockwise angle from the top:"), self.angle_spin)
        layout.addLayout(form)
        hint = QLabel(self.tr("0° = up · 90° = right · 135° = down-right · 180° = down"))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(440, 360)

    @property
    def north_angle_deg(self) -> float:
        return self.angle_spin.value()
