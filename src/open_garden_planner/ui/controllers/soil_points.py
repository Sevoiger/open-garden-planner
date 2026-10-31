"""Qt adapter for plan-local soil observations and undoable edits."""
from __future__ import annotations

from copy import deepcopy

from PyQt6.QtCore import QCoreApplication, QObject, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QDialog,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsObject,
    QInputDialog,
    QMenu,
    QMessageBox,
)

from open_garden_planner.core.commands import (
    AddSoilTestCommand,
    DeleteSoilTestCommand,
    EditSoilTestCommand,
)
from open_garden_planner.core.tools.base_tool import ToolType
from open_garden_planner.models.soil_test import SoilTestRecord
from open_garden_planner.services.soil_samples import (
    POINT_PREFIX,
    positions_from_record,
    prepare_sample,
)
from open_garden_planner.ui.dialogs.soil_sample_dialog import SoilSampleDialog


class SoilMarker(QGraphicsObject):
    """Screen-sized marker; data stays in the soil history, not the scene."""

    def __init__(self, controller, target, record, position=None):
        super().__init__()
        self._controller, self._target, self._record = controller, target, record
        position = position or record["sample_position"]
        self.setPos(position["x_cm"], position["y_cm"])
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setZValue(10000)
        self.setToolTip(QCoreApplication.translate("SoilPointsController", "Soil point — %1 — pH %2").replace("%1", record.get("date", "?"))
                        .replace("%2", str(record.get("ph", "?"))))
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)

    def boundingRect(self):
        return QRectF(-6, -6, 12, 12)

    def paint(self, painter: QPainter, _option, _widget=None):
        painter.setPen(QPen(QColor("#ffffff"), 1))
        painter.setBrush(QColor("#80502f"))
        painter.drawEllipse(QRectF(-4, -4, 8, 8))

    def mousePressEvent(self, event):
        event.accept()

    def mouseDoubleClickEvent(self, event):
        target, controller = self._target, self._controller
        QTimer.singleShot(0, lambda: controller.choose(target, "edit"))
        event.accept()

    def contextMenuEvent(self, event):
        menu = QMenu()
        edit = menu.addAction(QCoreApplication.translate("SoilPointsController", "Edit soil point…"))
        delete = menu.addAction(QCoreApplication.translate("SoilPointsController", "Delete soil point…"))
        chosen = menu.exec(event.screenPos())
        target, controller = self._target, self._controller
        if chosen == edit:
            QTimer.singleShot(0, lambda: controller.choose(target, "edit"))
        elif chosen == delete:
            QTimer.singleShot(0, lambda: controller.choose(target, "delete"))
        event.accept()


class SoilPointsController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.scene = window.canvas_scene
        self.view = window.canvas_view
        self.pm = window._project_manager
        self._markers = []
        self._pending_markers = []
        self._pending_positions = []
        self._visible = False
        self._full = False
        self.scene.document_replaced.connect(self._clear)
        self.scene.document_replaced.connect(self._clear_pending)
        self.pm.soil_tests_changed.connect(self.refresh)
        self.view.soil_point_requested.connect(self._place)
        self.view.soil_points_finished.connect(self._finish)
        self.view.soil_points_cancelled.connect(self._clear_pending)
        self.view.tool_type_changed.connect(self._tool_changed)
        self.refresh(self.pm.soil_tests)

    def start(self, full):
        self._clear_pending()
        self._full = full
        self.window.soil_points_visible_action.setChecked(True)
        self.view.set_active_tool(ToolType.SOIL_POINT)
        if full:
            self.view.set_status_message(self.tr("Click up to 15 sampling locations; Enter finishes, Esc cancels."))
        else:
            self.view.set_status_message(self.tr("Click the sampling location on the plan; Esc cancels."))

    def set_visible(self, visible):
        self._visible = bool(visible)
        for marker in self._markers:
            marker.setVisible(self._visible)

    def _clear_pending(self):
        for marker in self._pending_markers:
            if marker.scene() is self.scene:
                self.scene.removeItem(marker)
        self._pending_markers.clear()
        self._pending_positions.clear()

    def _tool_changed(self, tool_type):
        if tool_type != ToolType.SOIL_POINT:
            self._clear_pending()

    def _clear(self):
        for marker in self._markers:
            if marker.scene() is self.scene:
                self.scene.removeItem(marker)
        self._markers.clear()

    def refresh(self, histories):
        self._clear()
        for target, history in histories.items():
            if not target.startswith(POINT_PREFIX) or not isinstance(history, dict):
                continue
            records = [record for record in history.get("records", [])
                       if isinstance(record, dict) and isinstance(record.get("sample_position"), dict)
                       and all(isinstance(record["sample_position"].get(k), (int, float)) for k in ("x_cm", "y_cm"))]
            if records:
                record = max(enumerate(records), key=lambda pair: (pair[1].get("date", ""), pair[0]))[1]
                for position in positions_from_record(record):
                    marker = SoilMarker(self, target, record, position)
                    self.scene.addItem(marker)
                    marker.setVisible(self._visible)
                    self._markers.append(marker)

    def _document(self):
        return {"canvas": {"width": self.scene.width_cm, "height": self.scene.height_cm},
                "soil_tests": deepcopy(self.pm.soil_tests)}

    def _place(self, x, y):
        point = {"x_cm": x, "y_cm": y}
        if not (0 <= x <= self.scene.width_cm and 0 <= y <= self.scene.height_cm):
            QMessageBox.warning(self.window, self.tr("Invalid soil point"),
                                self.tr("The sampling location must be inside the plan."))
            return
        if not self._full:
            self.view.set_active_tool(ToolType.SELECT)
            self._open(None, None, point, False)
            return
        self._pending_positions.append(point)
        self.view.set_status_message(self.tr("Sampling locations: %1 / 15. Enter finishes; Esc cancels.")
                                     .replace("%1", str(len(self._pending_positions))))
        marker = QGraphicsEllipseItem(-4, -4, 8, 8)
        marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        marker.setBrush(QColor("#80502f"))
        marker.setPen(QPen(QColor("#ffffff"), 1))
        marker.setZValue(10000)
        marker.setPos(x, y)
        self.scene.addItem(marker)
        self._pending_markers.append(marker)
        if len(self._pending_positions) == 15:
            self._finish()

    def _finish(self):
        if not self._full or not self._pending_positions:
            return
        positions = deepcopy(self._pending_positions)
        self._clear_pending()
        self.view.set_active_tool(ToolType.SELECT)
        self._open(None, None, positions, True)

    def choose(self, target=None, action="edit"):
        histories = deepcopy(self.pm.soil_tests)
        generation = getattr(self.scene, "_document_generation", 0)
        records = [(key, record) for key, history in histories.items()
                   if key.startswith(POINT_PREFIX) and (target is None or target == key)
                   for record in history.get("records", [])]
        if not records:
            QMessageBox.information(self.window, self.tr("Soil points"), self.tr("No soil points in this plan."))
            return
        if len(records) == 1:
            selected = 0
        else:
            labels = [f"{i + 1}. {r.get('date', '?')} — pH {r.get('ph', '—')} "
                      f"({r.get('sample_position', {}).get('x_cm', '?')}, {r.get('sample_position', {}).get('y_cm', '?')} cm)"
                      for i, (_, r) in enumerate(records)]
            choice, accepted = QInputDialog.getItem(self.window, self.tr("Soil points"),
                self.tr("Select an observation"), labels, 0, False)
            if not accepted:
                return
            selected = labels.index(choice)
        key, record = records[selected]
        if (generation != getattr(self.scene, "_document_generation", 0)
                or self.pm.soil_tests.get(key) != histories.get(key)):
            QMessageBox.warning(self.window, self.tr("Soil point changed"),
                                self.tr("The plan or this point changed while the form was open. Reopen it to continue."))
            return
        if action == "delete":
            self.delete(key, record["id"])
        else:
            self.edit(key, record["id"])

    def edit(self, target, record_id):
        self._open(target, record_id, None, False)

    def _open(self, target, record_id, position, full):
        before = deepcopy(self.pm.soil_tests.get(target)) if target else None
        generation = getattr(self.scene, "_document_generation", 0)
        record = next((r for r in before.get("records", []) if r.get("id") == record_id), None) if before else None
        if target and record is None:
            return
        dialog = SoilSampleDialog(self.window, full=full, record=record, positions=position if isinstance(position, list) else None)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            if generation != getattr(self.scene, "_document_generation", 0) or (target and self.pm.soil_tests.get(target) != before):
                QMessageBox.warning(self.window, self.tr("Soil point changed"),
                                    self.tr("The plan or this point changed while the form was open. Reopen it to continue."))
                return
            try:
                fields = dialog.fields()
                if record_id:
                    fields["record_id"] = record_id
                elif not isinstance(position, list):
                    fields["position"] = position
                candidate, result = prepare_sample(self._document(),
                    "edit_soil_sample" if record_id else "record_soil_sample", target, fields)
                new_record = next(SoilTestRecord.from_dict(r) for r in candidate["soil_tests"][result["target_id"]]["records"]
                                  if r["id"] == result["record_id"])
                command = (EditSoilTestCommand if record_id else AddSoilTestCommand)(self.pm, result["target_id"], new_record)
                if result["status"] != "unchanged":
                    self.view.command_manager.execute(command)
                return
            except ValueError:
                QMessageBox.warning(dialog, self.tr("Invalid soil point"),
                    self.tr("Check the date, position and pH. Each result needs a parameter and a value; reports are limited to 200,000 bytes."))

    def delete(self, target, record_id):
        before = deepcopy(self.pm.soil_tests.get(target))
        generation = getattr(self.scene, "_document_generation", 0)
        answer = QMessageBox.question(self.window, self.tr("Delete soil point"),
                                      self.tr("Delete this soil observation?"))
        if answer != QMessageBox.StandardButton.Yes:
            return
        if generation != getattr(self.scene, "_document_generation", 0) or self.pm.soil_tests.get(target) != before:
            QMessageBox.warning(self.window, self.tr("Soil point changed"),
                                self.tr("The plan or this point changed while the form was open. Reopen it to continue."))
            return
        try:
            prepare_sample(self._document(), "delete_soil_sample", target, {"record_id": record_id})
        except ValueError:
            QMessageBox.warning(self.window, self.tr("Invalid soil point"),
                                self.tr("This observation is no longer available."))
            return
        self.view.command_manager.execute(DeleteSoilTestCommand(self.pm, target, record_id))
