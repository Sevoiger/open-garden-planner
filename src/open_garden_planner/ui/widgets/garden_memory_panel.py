"""Perennial garden cards and interventions, backed by the shared command service."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from open_garden_planner.app.paths import default_dialog_dir, default_save_path
from open_garden_planner.app.settings import create_qsettings
from open_garden_planner.core.garden_memory import GardenMemoryService
from open_garden_planner.core.object_types import get_translated_display_name
from open_garden_planner.models.garden_memory import optional_number
from open_garden_planner.services.garden_queries import (
    BED_TYPES,
    card_for,
    garden_objects,
    garden_record,
    search_events,
)
from open_garden_planner.ui.dialogs.garden_memory_dialog import (
    EventEditor,
    _edited_period,
    event_type_choices,
)

if TYPE_CHECKING:
    from open_garden_planner.ui.canvas.canvas_scene import CanvasScene


class GardenMemoryPanel(QWidget):
    """Select any shape (including obscured plants), edit its card and browse history."""

    def __init__(self, service: GardenMemoryService, target_id: str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.scene = cast("CanvasScene", service.scene)
        self.target_id = target_id
        self._settings = create_qsettings()
        self._hidden_layers = set(self._settings.value("garden_records/hidden_layers", [], type=list))
        layout = QVBoxLayout(self)
        split = QSplitter()
        self.targets = QListWidget()
        self.targets.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.targets.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.targets.setMinimumWidth(150)
        self.targets.setMaximumWidth(280)
        self.targets.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.targets.itemSelectionChanged.connect(self._target_changed)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.layer_filter_button = QPushButton(QCoreApplication.translate("GardenMemoryDialog", "Displayed layers…"))
        self.layer_filter_button.setObjectName("garden_record_layers")
        self.layer_filter_menu = QMenu(self)
        self.layer_filter_button.setMenu(self.layer_filter_menu)
        left_layout.addWidget(self.layer_filter_button)
        left_layout.addWidget(self.targets)
        split.addWidget(left)
        body = QWidget()
        right = QVBoxLayout(body)
        right.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        form.addRow(QCoreApplication.translate("GardenMemoryDialog", "Label:"), self.name_edit)
        self.kind_combo = QComboBox()
        for label, code in [(QCoreApplication.translate("GardenMemoryDialog", "Individual"), "individual"), (QCoreApplication.translate("GardenMemoryDialog", "Planting group"), "group"),
                            (QCoreApplication.translate("GardenMemoryDialog", "Zone"), "zone")]:
            self.kind_combo.addItem(label, code)
        form.addRow(QCoreApplication.translate("GardenMemoryDialog", "Tracked object:"), self.kind_combo)
        self.state_combo = QComboBox()
        for label, code in [(QCoreApplication.translate("GardenMemoryDialog", "Planned"), "planned"), (QCoreApplication.translate("GardenMemoryDialog", "In place"), "in_place"),
                            (QCoreApplication.translate("GardenMemoryDialog", "Dormant"), "dormant"), (QCoreApplication.translate("GardenMemoryDialog", "Uncertain condition"), "uncertain"),
                            (QCoreApplication.translate("GardenMemoryDialog", "Confirmed dead"), "confirmed_dead"),
                            (QCoreApplication.translate("GardenMemoryDialog", "Removed or moved"), "removed_moved")]:
            self.state_combo.addItem(label, code)
        form.addRow(QCoreApplication.translate("GardenMemoryDialog", "Condition:"), self.state_combo)
        self.period_edit = QLineEdit()
        self.period_edit.setPlaceholderText(QCoreApplication.translate("GardenMemoryDialog", "YYYY-MM-DD, YYYY-MM, autumn 2023, YYYY, or blank"))
        self.period_edit.setToolTip(QCoreApplication.translate("GardenMemoryDialog", "Partial dates retain their precision. Search intervals use conventional calendar boundaries."))
        form.addRow(QCoreApplication.translate("GardenMemoryDialog", "Planting period:"), self.period_edit)
        self.quantity_edit = QLineEdit()
        form.addRow(QCoreApplication.translate("GardenMemoryDialog", "Approximate count (optional):"), self.quantity_edit)
        self.zone_combo = QComboBox()
        form.addRow(QCoreApplication.translate("GardenMemoryDialog", "Explicit zone:"), self.zone_combo)
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setMaximumHeight(90)
        form.addRow(QCoreApplication.translate("GardenMemoryDialog", "Notes:"), self.notes_edit)
        card_widget = QWidget()
        card_widget.setLayout(form)
        self.card_scroll = QScrollArea()
        self.card_scroll.setWidgetResizable(True)
        self.card_scroll.setWidget(card_widget)
        right.addWidget(self.card_scroll, 1)
        save = QPushButton(QCoreApplication.translate("GardenMemoryDialog", "Save card"))
        save.clicked.connect(self._save_card)
        right.addWidget(save)
        self.save_card_button = save
        self.zone_members = QComboBox()
        self.zone_members.activated.connect(self._select_zone_member)
        right.addWidget(self.zone_members)
        filter_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(QCoreApplication.translate("GardenMemoryDialog", "Search interventions…"))
        self.search_edit.textChanged.connect(self._refresh_events)
        self.filter_type = QComboBox()
        self.filter_type.addItem(QCoreApplication.translate("GardenMemoryDialog", "All intervention types"), None)
        for label, code in event_type_choices():
            self.filter_type.addItem(label, code)
        self.filter_type.currentIndexChanged.connect(self._refresh_events)
        filter_row.addWidget(self.search_edit)
        filter_row.addWidget(self.filter_type)
        right.addLayout(filter_row)
        dates = QHBoxLayout()
        self.since_edit, self.until_edit = QLineEdit(), QLineEdit()
        for edit, label in [(self.since_edit, QCoreApplication.translate("GardenMemoryDialog", "From YYYY-MM-DD")),
                            (self.until_edit, QCoreApplication.translate("GardenMemoryDialog", "To YYYY-MM-DD"))]:
            edit.setPlaceholderText(label)
            edit.editingFinished.connect(self._refresh_events)
            dates.addWidget(edit)
        right.addLayout(dates)
        self.events = QListWidget()
        right.addWidget(self.events, 1)
        actions = QHBoxLayout()
        for label, handler in [(QCoreApplication.translate("GardenMemoryDialog", "Add intervention…"), self._add_event),
                               (QCoreApplication.translate("GardenMemoryDialog", "Edit intervention…"), self._edit_event),
                               (QCoreApplication.translate("GardenMemoryDialog", "Delete intervention"), self._delete_event)]:
            button = QPushButton(label)
            button.clicked.connect(handler)
            actions.addWidget(button)
        right.addLayout(actions)
        split.addWidget(body)
        split.setSizes([260, 720])
        layout.addWidget(split)
        exports = QHBoxLayout()
        export_menu = QMenu(self)
        export_button = QPushButton(QCoreApplication.translate("GardenMemoryDialog", "Export…"))
        export_button.setMenu(export_menu)
        exports.addWidget(export_button)
        for label, kind in [(QCoreApplication.translate("GardenMemoryDialog", "Export complete JSON…"), "json"),
                            (QCoreApplication.translate("GardenMemoryDialog", "Export tracking CSV…"), "csv"),
                            (QCoreApplication.translate("GardenMemoryDialog", "Portable backup…"), "zip")]:
            action = QAction(label, export_menu)
            action.triggered.connect(lambda _checked=False, export_kind=kind: self._export(export_kind))
            export_menu.addAction(action)
        restore = QPushButton(QCoreApplication.translate("GardenMemoryDialog", "Restore backup…"))
        restore.clicked.connect(self._restore)
        exports.addWidget(restore)
        layout.addLayout(exports)
        history = QHBoxLayout()
        for label, handler in [(QCoreApplication.translate("GardenMemoryDialog", "Undo"), self.service.commands.undo),
                               (QCoreApplication.translate("GardenMemoryDialog", "Redo"), self.service.commands.redo)]:
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, operation=handler: self._history(operation))
            history.addWidget(button)
        layout.addLayout(history)
        self.service.project.garden_memory_changed.connect(self._refresh_events)
        self._reload_targets()

    def _snapshot(self) -> dict[str, Any]:
        # Browsing needs identities and tracking fields, never PNGs or geometry.
        # A full plan export compresses every background again on each keystroke.
        from open_garden_planner.ui.canvas.items import (
            CircleItem,
            EllipseItem,
            PolygonItem,
            PolylineItem,
            RectangleItem,
        )

        objects = []
        layers = {str(layer.id): layer.name for layer in self.scene.layers}
        for item in reversed(self.service.scene.items()):
            if isinstance(item, (RectangleItem, EllipseItem, CircleItem, PolygonItem, PolylineItem)) and item.object_type is not None:
                objects.append({
                    "item_id": str(item.item_id), "object_type": item.object_type.name,
                    "name": item.name,
                    "display_type": get_translated_display_name(item.object_type),
                    "layer_id": str(item.layer_id) if item.layer_id is not None else "",
                    "layer_name": layers.get(str(item.layer_id), QCoreApplication.translate("GardenMemoryDialog", "No layer")),
                    "metadata": {key: deepcopy(item.metadata[key])
                                 for key in ("garden_tracking", "plant_instance") if key in item.metadata},
                })
        return {"objects": objects, "garden_memory": self.service.project.garden_memory}

    def _visible_objects(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        return [obj for obj in garden_objects(snapshot)
                if obj.get("layer_id", "") not in self._hidden_layers]

    def _set_layer_shown(self, layer_id: str, shown: bool) -> None:
        if shown:
            self._hidden_layers.discard(layer_id)
        else:
            self._hidden_layers.add(layer_id)
        self._settings.setValue("garden_records/hidden_layers", sorted(self._hidden_layers))
        self._reload_targets()

    def _show_all_layers(self) -> None:
        current_layers = {str(layer.id) for layer in self.scene.layers}
        current_layers.update(obj["layer_id"] for obj in garden_objects(self._snapshot()))
        self._hidden_layers.difference_update(current_layers)
        self._settings.setValue("garden_records/hidden_layers", sorted(self._hidden_layers))
        self._reload_targets()

    def _reload_layer_menu(self, snapshot: dict[str, Any]) -> None:
        self.layer_filter_menu.clear()
        show_all = QAction(QCoreApplication.translate("GardenMemoryDialog", "Show all layers"), self.layer_filter_menu)
        self.layer_filter_menu.addAction(show_all)
        show_all.triggered.connect(self._show_all_layers)
        self.layer_filter_menu.addSeparator()
        layers = {str(layer.id): layer.name for layer in self.scene.layers}
        for obj in garden_objects(snapshot):
            layers.setdefault(obj["layer_id"], obj["layer_name"])
        for identity, name in layers.items():
            action = QAction(name, self.layer_filter_menu)
            self.layer_filter_menu.addAction(action)
            action.setCheckable(True)
            action.setChecked(identity not in self._hidden_layers)
            action.setData(identity)
            action.triggered.connect(lambda checked, layer_id=identity: self._set_layer_shown(layer_id, checked))

    def _reload_targets(self) -> None:
        snapshot = self._snapshot()
        self._reload_layer_menu(snapshot)
        self.targets.blockSignals(True)
        self.targets.clear()
        all_row = QListWidgetItem(QCoreApplication.translate("GardenMemoryDialog", "All garden interventions"))
        all_row.setData(Qt.ItemDataRole.UserRole, None)
        self.targets.addItem(all_row)
        current = all_row
        for obj in self._visible_objects(snapshot):
            name = obj.get("name") or obj["display_type"]
            row = QListWidgetItem(name + " — " + obj["layer_name"])
            row.setToolTip(row.text())
            row.setData(Qt.ItemDataRole.UserRole, obj["item_id"])
            self.targets.addItem(row)
            if obj["item_id"] == self.target_id:
                current = row
        self.targets.setCurrentItem(current)
        self.targets.blockSignals(False)
        self._target_changed()

    def _target_changed(self) -> None:
        row = self.targets.currentItem()
        self.target_id = row.data(Qt.ItemDataRole.UserRole) if row else None
        enabled = bool(self.target_id)
        self.card_scroll.setVisible(enabled)
        self.save_card_button.setVisible(enabled)
        for w in (self.name_edit, self.kind_combo, self.state_combo, self.period_edit,
                  self.quantity_edit, self.zone_combo, self.notes_edit):
            w.setEnabled(enabled)
        if self.target_id:
            snap = self._snapshot()
            card = card_for(snap, self.target_id)
            self.name_edit.setText(card["name"])
            self.kind_combo.setCurrentIndex(self.kind_combo.findData(card["kind"]))
            self.state_combo.setCurrentIndex(self.state_combo.findData(card["state"]))
            self._original_card_period = card["period"]
            self.period_edit.setText(card["period"]["label"] or card["period"]["value"])
            self.quantity_edit.setText("" if card.get("quantity") is None else str(card["quantity"]))
            self.notes_edit.setPlainText(card["notes"])
            self.zone_combo.clear()
            self.zone_combo.addItem(QCoreApplication.translate("GardenMemoryDialog", "No explicit zone"), "")
            for obj in garden_objects(snap):
                if obj.get("object_type") in BED_TYPES and obj["item_id"] != self.target_id:
                    self.zone_combo.addItem(obj.get("name") or obj["object_type"], obj["item_id"])
            self.zone_combo.setCurrentIndex(max(0, self.zone_combo.findData(card.get("zone_id", ""))))
        self.zone_members.clear()
        if self.target_id:
            snapshot = self._snapshot()
            record = garden_record(snapshot, self.target_id)
            visible_ids = {obj["item_id"] for obj in self._visible_objects(snapshot)}
            self.zone_members.addItem(QCoreApplication.translate("GardenMemoryDialog", "Plantings in this zone…"), None)
            for card in record["plantings"]:
                if card["item_id"] in visible_ids:
                    self.zone_members.addItem(card["name"] or card["item_id"], card["item_id"])
            self.zone_members.setVisible(self.zone_members.count() > 1)
        else:
            self.zone_members.hide()
        self._refresh_events()

    def _select_zone_member(self) -> None:
        identity = self.zone_members.currentData()
        if identity:
            for index in range(self.targets.count()):
                row = self.targets.item(index)
                if row is not None and row.data(Qt.ItemDataRole.UserRole) == identity:
                    self.targets.setCurrentItem(row)
                    break

    def _error(self, exc: Exception) -> None:
        QMessageBox.warning(self, QCoreApplication.translate("GardenMemoryDialog", "Invalid garden data"),
                            QCoreApplication.translate("GardenMemoryDialog", "Check the entered values: {error}").format(error=str(exc)))

    def _save_card(self) -> None:
        if not self.target_id:
            return
        try:
            card = card_for(self._snapshot(), self.target_id)
            for key in ("item_id", "name", "notes", "cultivar"):
                card.pop(key, None)
            card.update(name=self.name_edit.text(), notes=self.notes_edit.toPlainText(),
                        kind=self.kind_combo.currentData(), state=self.state_combo.currentData(),
                        period=_edited_period(self.period_edit.text(), self._original_card_period),
                        quantity=optional_number(self.quantity_edit.text(), "quantity"),
                        zone_id=self.zone_combo.currentData())
            self.service.set_card(self.target_id, card)
            self._reload_targets()
        except (ValueError, TypeError) as exc:
            self._error(exc)

    def _refresh_events(self, _changed: Any = None) -> None:
        try:
            snap = self._snapshot()
            entries = search_events(snap, event_type=self.filter_type.currentData(),
                                    since=self.since_edit.text() or None, until=self.until_edit.text() or None,
                                    text=self.search_edit.text())
            hidden_ids = {obj["item_id"] for obj in garden_objects(snap)
                          if obj.get("layer_id", "") in self._hidden_layers}
            direct, inherited = None, set()
            if self.target_id:
                record = garden_record(snap, self.target_id)
                direct = {e["id"] for e in record["direct_events"]}
                inherited = {e["id"] for e in record["zone_events"]}
            self.events.clear()
            for entry in entries:
                # Preserve mixed-target events and the history of deleted items.
                # An explicitly selected planting still inherits its zone history.
                if not self.target_id and entry["targets"] and all(
                    target["id"] in hidden_ids for target in entry["targets"]
                ):
                    continue
                if direct is not None and entry["id"] not in direct | inherited:
                    continue
                prefix = QCoreApplication.translate("GardenMemoryDialog", "Zone: ") if entry["id"] in inherited else ""
                when = entry["period"]["label"] or QCoreApplication.translate("GardenMemoryDialog", "Unknown date")
                status = QCoreApplication.translate("GardenMemoryDialog", "Completed") if entry["status"] == "done" else QCoreApplication.translate("GardenMemoryDialog", "Planned")
                row = QListWidgetItem(prefix + when + " — " + status + ": " + entry["description"])
                type_name = {code: label for label, code in event_type_choices()}[entry["type"]]
                row.setToolTip(row.text() + "\n" + status + " / " + type_name)
                row.setData(Qt.ItemDataRole.UserRole, entry["id"])
                self.events.addItem(row)
        except (ValueError, TypeError) as exc:
            self._error(exc)

    def _export(self, kind: str) -> None:
        from open_garden_planner.services.garden_archive import (
            export_csv,
            export_json,
            portable_backup,
        )

        project = self.service.project.current_file
        if kind == "zip" and project is None:
            QMessageBox.information(self, QCoreApplication.translate("GardenMemoryDialog", "Garden records"), QCoreApplication.translate("GardenMemoryDialog", "Save the project before creating a portable backup."))
            return
        target, _ = QFileDialog.getSaveFileName(self, QCoreApplication.translate("GardenMemoryDialog", "Export garden records"),
                                                default_save_path("garden." + kind, project))
        if target:
            try:
                path = Path(target)
                if project and path.resolve() == project.resolve():
                    raise ValueError(QCoreApplication.translate("GardenMemoryDialog", "Choose an export file different from the open project."))
                if kind == "zip" and project:
                    portable_backup(self.service.project.snapshot_dict(self.service.scene), project, path)
                elif kind == "csv":
                    export_csv(self.service.project.snapshot_dict(self.service.scene), path)
                else:
                    export_json(self.service.project.snapshot_dict(self.service.scene), path)
            except Exception as exc:
                self._error(exc)

    def _restore(self) -> None:
        from open_garden_planner.services.garden_archive import restore_backup

        project = self.service.project.current_file
        source, _ = QFileDialog.getOpenFileName(self, QCoreApplication.translate("GardenMemoryDialog", "Restore garden backup"),
                                               str(default_dialog_dir(project)), QCoreApplication.translate("GardenMemoryDialog", "ZIP archives (*.zip)"))
        if not source:
            return
        parent = QFileDialog.getExistingDirectory(self, QCoreApplication.translate("GardenMemoryDialog", "Choose restoration parent folder"),
                                                  str(default_dialog_dir(project)))
        if parent:
            try:
                restored = restore_backup(Path(source), Path(parent) / ("garden-restored-" + uuid4().hex[:10]))
                QMessageBox.information(self, QCoreApplication.translate("GardenMemoryDialog", "Garden records"),
                                        QCoreApplication.translate("GardenMemoryDialog", "Restored project: {path}\nOpen this file using File > Open.").format(path=restored))
            except Exception as exc:
                self._error(exc)

    def _history(self, operation: Any) -> None:
        operation()
        self._reload_targets()

    def _target_choices(self) -> list[dict[str, str]]:
        return [{"id": o["item_id"], "name": o.get("name") or o["object_type"]}
                for o in self._visible_objects(self._snapshot())]

    def _add_event(self) -> None:
        if not self.target_id:
            QMessageBox.information(self, QCoreApplication.translate("GardenMemoryDialog", "Intervention"), QCoreApplication.translate("GardenMemoryDialog", "Select a target on the left first."))
            return
        card = card_for(self._snapshot(), self.target_id)
        dialog = EventEditor([{"id": self.target_id, "name": card["name"] or self.target_id}], parent=self, choices=self._target_choices(), project=self.service.project.current_file)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.value:
            try:
                self.service.add_event(dialog.value, str(uuid4()))
                self._refresh_events()
            except (ValueError, TypeError) as exc:
                self._error(exc)

    def _edit_event(self) -> None:
        row = self.events.currentItem()
        if not row:
            return
        identity = row.data(Qt.ItemDataRole.UserRole)
        event = self.service.project.garden_memory["events"][identity]
        dialog = EventEditor(event["targets"], event, self, choices=self._target_choices(), project=self.service.project.current_file)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.value:
            try:
                self.service.edit_event(identity, dialog.value)
                self._refresh_events()
            except (ValueError, TypeError) as exc:
                self._error(exc)

    def _delete_event(self) -> None:
        row = self.events.currentItem()
        if row:
            try:
                self.service.edit_event(row.data(Qt.ItemDataRole.UserRole), None)
                self._refresh_events()
            except (ValueError, TypeError) as exc:
                self._error(exc)
