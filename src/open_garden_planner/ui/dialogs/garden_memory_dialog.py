"""Perennial garden cards and interventions, backed by the shared command service."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from PyQt6.QtCore import QCoreApplication, Qt, QUrl
from PyQt6.QtGui import QAction, QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
from open_garden_planner.core.garden_memory import GardenMemoryService
from open_garden_planner.models.garden_memory import optional_number, period
from open_garden_planner.services.garden_queries import (
    BED_TYPES,
    card_for,
    garden_objects,
    garden_record,
    search_events,
)
from open_garden_planner.ui.widgets.responsive_dialog import ResponsiveDialog


def event_type_choices() -> list[tuple[str, str]]:
    return [
        (QCoreApplication.translate("EventEditor", "Planting"), "planting"), (QCoreApplication.translate("EventEditor", "Relocation"), "relocation"),
        (QCoreApplication.translate("EventEditor", "Amendment"), "amendment"), (QCoreApplication.translate("EventEditor", "Fertilisation"), "fertilisation"),
        (QCoreApplication.translate("EventEditor", "Mulching"), "mulching"), (QCoreApplication.translate("EventEditor", "Sowing"), "sowing"),
        (QCoreApplication.translate("EventEditor", "Pruning"), "pruning"), (QCoreApplication.translate("EventEditor", "Occasional watering"), "watering"),
        (QCoreApplication.translate("EventEditor", "Observation"), "observation"), (QCoreApplication.translate("EventEditor", "Removal or loss"), "removal_loss"),
        ]


def _edited_period(text: str, original: dict[str, str]) -> dict[str, str]:
    """Keep structured precision/labels when an unrelated field is edited."""
    return period(original) if text == (original["label"] or original["value"]) else period(text)


class EventEditor(ResponsiveDialog):
    """A single intervention; an empty occurrence date explicitly means unknown."""

    def __init__(self, targets: list[dict[str, str]], event: dict[str, Any] | None = None,
                 parent: QWidget | None = None, choices: list[dict[str, str]] | None = None, project: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Intervention"))
        self.resize(560, 520)
        self.targets = targets
        self.project = project
        self.value: dict[str, Any] | None = None
        event = event or {}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.type_combo = QComboBox()
        self.type_choices = event_type_choices()
        for label, code in self.type_choices:
            self.type_combo.addItem(label, code)
        self.type_combo.setCurrentIndex(max(0, self.type_combo.findData(event.get("type", "observation"))))
        form.addRow(self.tr("Type:"), self.type_combo)
        self.status_combo = QComboBox()
        self.status_combo.addItem(self.tr("Planned"), "planned")
        self.status_combo.addItem(self.tr("Completed"), "done")
        self.status_combo.setCurrentIndex(max(0, self.status_combo.findData(event.get("status", "done"))))
        form.addRow(self.tr("Status:"), self.status_combo)
        self._original_period = period(event.get("period"))
        self.date_edit = QLineEdit(self._original_period["label"] or self._original_period["value"])
        self.date_edit.setToolTip(self._original_period["value"])
        self.date_edit.setPlaceholderText(self.tr("YYYY-MM-DD, YYYY-MM, autumn 2023, YYYY, or blank"))
        form.addRow(self.tr("Occurrence:"), self.date_edit)
        self.description = QPlainTextEdit(event.get("description", ""))
        self.description.setMaximumHeight(90)
        form.addRow(self.tr("Description:"), self.description)
        self.target_list = QListWidget()
        self.target_list.setMaximumHeight(90)
        selected_ids = {t["id"] for t in targets}
        available = {t["id"]: t for t in (choices or targets)}
        available.update({t["id"]: t for t in targets if t["id"] not in available})
        for target in available.values():
            row = QListWidgetItem(target["name"])
            row.setData(Qt.ItemDataRole.UserRole, target)
            row.setCheckState(Qt.CheckState.Checked if target["id"] in selected_ids else Qt.CheckState.Unchecked)
            self.target_list.addItem(row)
        form.addRow(self.tr("Targets:"), self.target_list)
        self.optional: dict[str, QLineEdit] = {}
        for key, label in [
            ("material", self.tr("Material or product:")), ("quantity", self.tr("Quantity (optional):")),
            ("unit", self.tr("Unit:")), ("application", self.tr("Application method:")),
            ("area_m2", self.tr("Treated area (m²):")), ("depth_cm", self.tr("Depth or thickness (cm):")),
        ]:
            raw = event.get(key)
            edit = QLineEdit("" if raw is None else str(raw))
            self.optional[key] = edit
            form.addRow(label, edit)
        form_widget = QWidget()
        form_widget.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_widget)
        layout.addWidget(scroll, 1)
        self.media = list(event.get("media", []))
        self.media_list = QListWidget()
        self.media_list.setMaximumHeight(70)
        self.media_list.addItems(self.media)
        layout.addWidget(self.media_list)
        media_actions = QHBoxLayout()
        attachment_menu = QMenu(self)
        attachment_button = QPushButton(self.tr("Attachments…"))
        attachment_button.setMenu(attachment_menu)
        media_actions.addWidget(attachment_button)
        for label, handler in [(self.tr("Attach photo or document…"), self._attach),
                               (self.tr("Open attachment"), self._open_attachment),
                               (self.tr("Remove attachment reference"), self._remove_attachment)]:
            action = QAction(label, attachment_menu)
            action.triggered.connect(handler)
            attachment_menu.addAction(action)
        layout.addLayout(media_actions)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _attach(self) -> None:
        if not self.project:
            QMessageBox.information(self, self.tr("Intervention"), self.tr("Save the project before attaching files."))
            return
        source, _ = QFileDialog.getOpenFileName(self, self.tr("Attach photo or document"),
                                               str(default_dialog_dir(self.project)))
        if source:
            try:
                from open_garden_planner.services.garden_archive import import_attachment

                reference = import_attachment(Path(source), self.project)
                if reference not in self.media:
                    self.media.append(reference)
                    self.media_list.addItem(reference)
            except Exception as exc:
                QMessageBox.warning(self, self.tr("Invalid garden data"), str(exc))

    def _open_attachment(self) -> None:
        row = self.media_list.currentItem()
        if row is not None and self.project is not None:
            try:
                path = (self.project.parent / row.text()).resolve()
                path.relative_to(self.project.parent.resolve())
                if not path.is_file():
                    raise ValueError(self.tr("Attachment is missing."))
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            except Exception as exc:
                QMessageBox.warning(self, self.tr("Invalid garden data"), str(exc))

    def _remove_attachment(self) -> None:
        index = self.media_list.currentRow()
        if index >= 0:
            self.media.pop(index)
            self.media_list.takeItem(index)

    def accept(self) -> None:
        try:
            self.targets = []
            for i in range(self.target_list.count()):
                target = self.target_list.item(i)
                if target is not None and target.checkState() == Qt.CheckState.Checked:
                    self.targets.append(target.data(Qt.ItemDataRole.UserRole))
            value: dict[str, Any] = {
                "type": self.type_combo.currentData(), "status": self.status_combo.currentData(),
                "period": _edited_period(self.date_edit.text(), self._original_period), "description": self.description.toPlainText(),
                "targets": self.targets, "media": self.media,
            }
            for key, edit in self.optional.items():
                value[key] = (optional_number(edit.text(), key) if key in {"quantity", "area_m2", "depth_cm"}
                              else edit.text())
            # Validate before closing; id/time are assigned by the shared service.
            from open_garden_planner.models.garden_memory import validate_event

            validate_event(value)
            self.value = value
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, self.tr("Invalid garden data"),
                                self.tr("Check the entered values: {error}").format(error=str(exc)))
            return
        super().accept()


class GardenMemoryDialog(ResponsiveDialog):
    """Select any shape (including obscured plants), edit its card and browse history."""

    def __init__(self, service: GardenMemoryService, target_id: str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.target_id = target_id
        self.setWindowTitle(self.tr("Garden records"))
        self.resize(980, 700)
        layout = QVBoxLayout(self)
        split = QSplitter()
        self.targets = QListWidget()
        self.targets.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.targets.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.targets.setMinimumWidth(150)
        self.targets.setMaximumWidth(280)
        self.targets.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.targets.itemSelectionChanged.connect(self._target_changed)
        split.addWidget(self.targets)
        body = QWidget()
        right = QVBoxLayout(body)
        right.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        form.addRow(self.tr("Label:"), self.name_edit)
        self.kind_combo = QComboBox()
        for label, code in [(self.tr("Individual"), "individual"), (self.tr("Planting group"), "group"),
                            (self.tr("Zone"), "zone")]:
            self.kind_combo.addItem(label, code)
        form.addRow(self.tr("Tracked object:"), self.kind_combo)
        self.state_combo = QComboBox()
        for label, code in [(self.tr("Planned"), "planned"), (self.tr("In place"), "in_place"),
                            (self.tr("Dormant"), "dormant"), (self.tr("Uncertain condition"), "uncertain"),
                            (self.tr("Confirmed dead"), "confirmed_dead"),
                            (self.tr("Removed or moved"), "removed_moved")]:
            self.state_combo.addItem(label, code)
        form.addRow(self.tr("Condition:"), self.state_combo)
        self.period_edit = QLineEdit()
        self.period_edit.setPlaceholderText(self.tr("YYYY-MM-DD, YYYY-MM, autumn 2023, YYYY, or blank"))
        self.period_edit.setToolTip(self.tr("Partial dates retain their precision. Search intervals use conventional calendar boundaries."))
        form.addRow(self.tr("Planting period:"), self.period_edit)
        self.quantity_edit = QLineEdit()
        form.addRow(self.tr("Approximate count (optional):"), self.quantity_edit)
        self.zone_combo = QComboBox()
        form.addRow(self.tr("Explicit zone:"), self.zone_combo)
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setMaximumHeight(90)
        form.addRow(self.tr("Notes:"), self.notes_edit)
        card_widget = QWidget()
        card_widget.setLayout(form)
        self.card_scroll = QScrollArea()
        self.card_scroll.setWidgetResizable(True)
        self.card_scroll.setWidget(card_widget)
        right.addWidget(self.card_scroll, 1)
        save = QPushButton(self.tr("Save card"))
        save.clicked.connect(self._save_card)
        right.addWidget(save)
        self.save_card_button = save
        self.zone_members = QComboBox()
        self.zone_members.activated.connect(self._select_zone_member)
        right.addWidget(self.zone_members)
        filter_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(self.tr("Search interventions…"))
        self.search_edit.textChanged.connect(self._refresh_events)
        self.filter_type = QComboBox()
        self.filter_type.addItem(self.tr("All intervention types"), None)
        for label, code in event_type_choices():
            self.filter_type.addItem(label, code)
        self.filter_type.currentIndexChanged.connect(self._refresh_events)
        filter_row.addWidget(self.search_edit)
        filter_row.addWidget(self.filter_type)
        right.addLayout(filter_row)
        dates = QHBoxLayout()
        self.since_edit, self.until_edit = QLineEdit(), QLineEdit()
        for edit, label in [(self.since_edit, self.tr("From YYYY-MM-DD")),
                            (self.until_edit, self.tr("To YYYY-MM-DD"))]:
            edit.setPlaceholderText(label)
            edit.editingFinished.connect(self._refresh_events)
            dates.addWidget(edit)
        right.addLayout(dates)
        self.events = QListWidget()
        right.addWidget(self.events, 1)
        actions = QHBoxLayout()
        for label, handler in [(self.tr("Add intervention…"), self._add_event),
                               (self.tr("Edit intervention…"), self._edit_event),
                               (self.tr("Delete intervention"), self._delete_event)]:
            button = QPushButton(label)
            button.clicked.connect(handler)
            actions.addWidget(button)
        right.addLayout(actions)
        split.addWidget(body)
        split.setSizes([260, 720])
        layout.addWidget(split)
        exports = QHBoxLayout()
        export_menu = QMenu(self)
        export_button = QPushButton(self.tr("Export…"))
        export_button.setMenu(export_menu)
        exports.addWidget(export_button)
        for label, kind in [(self.tr("Export complete JSON…"), "json"),
                            (self.tr("Export tracking CSV…"), "csv"),
                            (self.tr("Portable backup…"), "zip")]:
            action = QAction(label, export_menu)
            action.triggered.connect(lambda _checked=False, export_kind=kind: self._export(export_kind))
            export_menu.addAction(action)
        restore = QPushButton(self.tr("Restore backup…"))
        restore.clicked.connect(self._restore)
        exports.addWidget(restore)
        layout.addLayout(exports)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        history = QHBoxLayout()
        for label, handler in [(self.tr("Undo"), self.service.commands.undo),
                               (self.tr("Redo"), self.service.commands.redo)]:
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, operation=handler: self._history(operation))
            history.addWidget(button)
        history.addWidget(close)
        layout.addLayout(history)
        self.service.project.garden_memory_changed.connect(self._refresh_events)
        self._reload_targets()

    def _snapshot(self) -> dict[str, Any]:
        return self.service.project.snapshot_dict(self.service.scene)

    def _reload_targets(self) -> None:
        self.targets.blockSignals(True)
        self.targets.clear()
        all_row = QListWidgetItem(self.tr("All garden interventions"))
        all_row.setData(Qt.ItemDataRole.UserRole, None)
        self.targets.addItem(all_row)
        current = all_row
        for obj in garden_objects(self._snapshot()):
            row = QListWidgetItem(obj.get("name") or obj["object_type"])
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
            self.zone_combo.addItem(self.tr("No explicit zone"), "")
            for obj in garden_objects(snap):
                if obj.get("object_type") in BED_TYPES and obj["item_id"] != self.target_id:
                    self.zone_combo.addItem(obj.get("name") or obj["object_type"], obj["item_id"])
            self.zone_combo.setCurrentIndex(max(0, self.zone_combo.findData(card.get("zone_id", ""))))
        self.zone_members.clear()
        if self.target_id:
            record = garden_record(self._snapshot(), self.target_id)
            self.zone_members.addItem(self.tr("Plantings in this zone…"), None)
            for card in record["plantings"]:
                self.zone_members.addItem(card["name"] or card["item_id"], card["item_id"])
            self.zone_members.setVisible(bool(record["plantings"]))
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
        QMessageBox.warning(self, self.tr("Invalid garden data"),
                            self.tr("Check the entered values: {error}").format(error=str(exc)))

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
            direct, inherited = None, set()
            if self.target_id:
                record = garden_record(snap, self.target_id)
                direct = {e["id"] for e in record["direct_events"]}
                inherited = {e["id"] for e in record["zone_events"]}
            self.events.clear()
            for entry in entries:
                if direct is not None and entry["id"] not in direct | inherited:
                    continue
                prefix = self.tr("Zone: ") if entry["id"] in inherited else ""
                when = entry["period"]["label"] or self.tr("Unknown date")
                status = self.tr("Completed") if entry["status"] == "done" else self.tr("Planned")
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
            QMessageBox.information(self, self.tr("Garden records"), self.tr("Save the project before creating a portable backup."))
            return
        target, _ = QFileDialog.getSaveFileName(self, self.tr("Export garden records"),
                                                default_save_path("garden." + kind, project))
        if target:
            try:
                path = Path(target)
                if project and path.resolve() == project.resolve():
                    raise ValueError(self.tr("Choose an export file different from the open project."))
                if kind == "zip" and project:
                    portable_backup(self._snapshot(), project, path)
                elif kind == "csv":
                    export_csv(self._snapshot(), path)
                else:
                    export_json(self._snapshot(), path)
            except Exception as exc:
                self._error(exc)

    def _restore(self) -> None:
        from open_garden_planner.services.garden_archive import restore_backup

        project = self.service.project.current_file
        source, _ = QFileDialog.getOpenFileName(self, self.tr("Restore garden backup"),
                                               str(default_dialog_dir(project)), self.tr("ZIP archives (*.zip)"))
        if not source:
            return
        parent = QFileDialog.getExistingDirectory(self, self.tr("Choose restoration parent folder"),
                                                  str(default_dialog_dir(project)))
        if parent:
            try:
                restored = restore_backup(Path(source), Path(parent) / ("garden-restored-" + uuid4().hex[:10]))
                QMessageBox.information(self, self.tr("Garden records"),
                                        self.tr("Restored project: {path}\nOpen this file using File > Open.").format(path=restored))
            except Exception as exc:
                self._error(exc)

    def _history(self, operation: Any) -> None:
        operation()
        self._reload_targets()

    def _target_choices(self) -> list[dict[str, str]]:
        return [{"id": o["item_id"], "name": o.get("name") or o["object_type"]}
                for o in garden_objects(self._snapshot())]

    def _add_event(self) -> None:
        if not self.target_id:
            QMessageBox.information(self, self.tr("Intervention"), self.tr("Select a target on the left first."))
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
