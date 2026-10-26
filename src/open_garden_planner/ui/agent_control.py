"""Authenticated MCP control of the actual Qt application, on its main thread.

This adapter sends normal Qt input and invokes the application's existing actions;
it never evaluates code, sets arbitrary attributes or edits document storage.
Dialog-opening input is queued: a modal exec() must not monopolise the MCP call.
Receipts distinguish queued/running/completed/failed and deduplicate retries for
this controller's session. Handles expire with the QObject or the session.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from PyQt6 import sip
from PyQt6.QtCore import (
    QAbstractListModel,
    QBuffer,
    QByteArray,
    QDate,
    QDateTime,
    QEvent,
    QIODevice,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    QPoint,
    QPointF,
    Qt,
    QThread,
    QTimer,
)
from PyQt6.QtGui import (
    QAction,
    QColor,
    QContextMenuEvent,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QInputMethodEvent,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QWheelEvent,
    QWindow,
)
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QAbstractButton,
    QAbstractItemDelegate,
    QAbstractItemView,
    QAbstractSlider,
    QApplication,
    QColorDialog,
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGraphicsView,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QSpinBox,
    QTabBar,
    QTabWidget,
    QTextEdit,
    QWidget,
)


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("Expected a finite number")
    return float(value)


def _integer(value: Any) -> int:
    number = _number(value)
    if not number.is_integer():
        raise ValueError("Expected an integer")
    return int(number)


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected text")
    return value


def _json_data(value: Any, max_length: int = 16384) -> Any:
    """Expose JSON role data only; never stringify opaque Qt/Python objects."""
    def known_scalar(obj: Any) -> str:
        if isinstance(obj, (Path, UUID)):
            return str(obj)
        raise TypeError("Opaque role data")

    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, default=known_scalar)
    except (TypeError, ValueError, OverflowError):
        return None
    return json.loads(encoded) if len(encoded) <= max_length else None


def _field_label(widget: QWidget) -> str:
    """Recover labels for direct and composite QFormLayout fields."""
    current = widget
    while (parent := current.parentWidget()) is not None:
        layouts = [parent.layout()] if parent.layout() is not None else []
        while layouts:
            layout = layouts.pop()
            if isinstance(layout, QFormLayout):
                for row in range(layout.rowCount()):
                    field = layout.itemAt(row, QFormLayout.ItemRole.FieldRole)
                    if field is None:
                        continue
                    fields = [field]
                    contains = False
                    while fields:
                        child = fields.pop()
                        if child.widget() is current:
                            contains = True
                            break
                        nested = child.layout()
                        if nested is not None:
                            fields.extend(nested.itemAt(i) for i in range(nested.count()))
                    if contains:
                        label = layout.itemAt(row, QFormLayout.ItemRole.LabelRole)
                        if label is not None and isinstance(label.widget(), QLabel):
                            return label.widget().text()
            layouts.extend(item.layout() for i in range(layout.count())
                           if (item := layout.itemAt(i)) is not None and item.layout() is not None)
        current = parent
    return ""


class ApplicationUiController:
    """One per running application; no filesystem access and no persistent settings."""

    def __init__(self, owner: QWidget) -> None:
        self.owner = owner
        self._test: Any = QTest  # Qt namespace; PyQt stubs incorrectly declare instance methods.
        self.session = str(uuid4())
        self._objects: dict[str, QObject] = {}
        self._reverse: dict[int, str] = {}
        self._operations: dict[str, dict[str, Any]] = {}
        self._requests: dict[str, tuple[str, str]] = {}
        self._serial = 0
        self._closed = False
        self._native_dialogs = QApplication.testAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)

    def close(self) -> None:
        self._closed = True
        # Never leave automation's dialog policy in the owner's UI after stop.
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, self._native_dialogs)
        self._objects.clear()
        self._reverse.clear()

    def _remember(self, obj: QObject) -> str:
        key = self._reverse.get(id(obj))
        if key is None:
            self._serial += 1
            key = f"{self.session}:{self._serial}"
            self._objects[key] = obj
            self._reverse[id(obj)] = key
        return key

    def _resolve(self, key: str, expected: type | tuple[type, ...] = QObject) -> Any:
        obj = self._objects.get(key)
        if obj is None or sip.isdeleted(obj) or not isinstance(obj, expected):
            raise ValueError("Unknown, stale or incompatible UI handle; refresh get_application_ui")
        return obj

    def _prune(self) -> None:
        for key, obj in list(self._objects.items()):
            if sip.isdeleted(obj):
                del self._objects[key]
                self._reverse.pop(id(obj), None)

    def dispatch(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        app = QApplication.instance()
        if app is None or QThread.currentThread() is not app.thread():
            raise RuntimeError("UI control must run on the Qt main thread")
        if self._closed:
            raise ValueError("UI control session has ended")
        self._prune()
        if operation == "snapshot":
            return self.snapshot(**arguments)
        if operation == "status":
            identity = arguments["operation_id"]
            if identity not in self._operations:
                raise ValueError("Unknown UI operation in this session")
            return dict(self._operations[identity])
        if operation == "render":
            return self.render(arguments.get("widget_id"))
        if operation == "model":
            view = self._resolve(arguments["widget_id"], QAbstractItemView)
            path = arguments.get("index_path")
            parent = self._index(view, path) if path else QModelIndex()
            start, count = _integer(arguments.get("start", 0)), _integer(arguments.get("count", 50))
            if start < 0 or not 1 <= count <= 200:
                raise ValueError("Model page requires start >= 0 and count in 1..200")
            return self._model_rows(view, parent, start, count)
        if operation not in {"action", "value", "interact"}:
            raise ValueError("Unknown UI operation")
        return self._enqueue(operation, arguments)

    def _enqueue(self, kind: str, arguments: dict[str, Any]) -> dict[str, Any]:
        args = dict(arguments)
        request_key = args.pop("request_key", None)
        if not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 256:
            raise ValueError("A nonempty request_key of at most 256 characters is required")
        encoded = json.dumps([kind, args], sort_keys=True, ensure_ascii=False, allow_nan=False)
        if len(encoded) > 1_000_000:
            raise ValueError("UI request is too large")
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        previous = self._requests.get(request_key)
        if previous:
            if previous[0] != fingerprint:
                raise ValueError("Idempotence conflict: request_key already used for different input")
            return {**self._operations[previous[1]], "replayed": True}
        if len(self._requests) >= 10000:
            raise ValueError("UI session receipt capacity reached; restart the MCP server")
        # Preflight handles. Revalidate all controls again at execution time:
        # another queued operation can have opened a modal or destroyed them.
        self._resolve(args["action_id"] if kind == "action" else args["widget_id"])
        identity = str(uuid4())
        receipt = {"operation_id": identity, "session_id": self.session,
                   "status": "queued", "operation": kind,
                   "request_key": request_key, "queued_at": datetime.now(UTC).isoformat()}
        self._operations[identity] = receipt
        self._requests[request_key] = (fingerprint, identity)

        def run() -> None:
            if self._closed:
                receipt.update(status="failed", error="UI session ended before execution")
                return
            receipt["status"] = "running"
            receipt["started_at"] = datetime.now(UTC).isoformat()
            try:
                QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)
                if kind == "action":
                    self._invoke_action(args["action_id"])
                elif kind == "value":
                    self._set_value(args["widget_id"], args["value"])
                else:
                    self._interact(args["widget_id"], args["action"], args.get("parameters", {}))
            except Exception as exc:  # Boundary: report application/input failures to the remote client.
                receipt.update(status="failed", error=str(exc) or type(exc).__name__,
                               error_type=type(exc).__name__)
            else:
                receipt["status"] = "completed"
            finally:
                receipt["finished_at"] = datetime.now(UTC).isoformat()

        QTimer.singleShot(0, run)
        return dict(receipt)

    @staticmethod
    def _modal_guard(obj: QWidget | QWindow) -> None:
        modal = QApplication.activeModalWidget()
        if modal is not None:
            if isinstance(obj, QWidget):
                allowed = obj is modal or modal.isAncestorOf(obj)
            else:
                allowed = obj is modal.windowHandle()
            if not allowed:
                raise ValueError("A modal dialog is active; interact with that dialog first")
        if not obj.isVisible():
            raise ValueError("UI target is hidden; reveal its panel/tab first")
        if isinstance(obj, QWidget) and not obj.isEnabled():
            raise ValueError("UI target is disabled")

    def _invoke_action(self, identity: str) -> None:
        action = self._resolve(identity, QAction)
        if not action.isEnabled() or not action.isVisible() or action.isSeparator():
            raise ValueError("Action is disabled, hidden or a separator")
        menus = [o for o in action.associatedObjects() if isinstance(o, QMenu) and o.isVisible()]
        if menus:
            menu = menus[0]
            self._modal_guard(menu)
            # Context menus dispatch on exec()'s returned QAction. Calling
            # QAction.trigger() alone would leave exec() waiting indefinitely.
            menu.setActiveAction(action)
            self._test.keyClick(menu, Qt.Key.Key_Return)
            return
        modal = QApplication.activeModalWidget()
        if modal is not None and not any(
            isinstance(o, QWidget) and (o is modal or modal.isAncestorOf(o))
            for o in action.associatedObjects()
        ):
            raise ValueError("A modal dialog is active; finish it before invoking another action")
        if action.menu() is not None:
            action.menu().popup(self.owner.mapToGlobal(QPoint(20, 20)))
        else:
            action.trigger()

    def snapshot(self, root_id: str | None = None, max_nodes: int = 1000,
                 include_hidden: bool = False) -> dict[str, Any]:
        if isinstance(max_nodes, bool) or not 1 <= max_nodes <= 10000:
            raise ValueError("max_nodes must be in 1..10000")
        windows = [self.owner, *[w for w in QApplication.topLevelWidgets()
                               if w is not self.owner and w.isVisible()]]
        roots = [self._resolve(root_id, (QWidget, QWindow))] if root_id else windows
        nodes: list[dict[str, Any]] = []
        actions: dict[str, dict[str, Any]] = {}
        truncated = False
        seen: set[int] = set()

        def collect_action(action: QAction) -> None:
            identity = self._remember(action)
            menu = action.menu()
            actions[identity] = {
                "id": identity, "text": action.text(), "object_name": action.objectName(),
                "enabled": action.isEnabled(), "visible": action.isVisible(),
                "checkable": action.isCheckable(), "checked": action.isChecked(),
                "shortcut": action.shortcut().toString(QKeySequence.SequenceFormat.PortableText), "tooltip": action.toolTip(),
                "submenu": menu is not None, "separator": action.isSeparator(),
                "menu_id": self._remember(menu) if menu is not None else None,
                "associated_widget_ids": [self._remember(o) for o in action.associatedObjects() if isinstance(o, QWidget)],
            }

        def visit(obj: QWidget | QWindow, parent_id: str | None) -> None:
            nonlocal truncated
            if id(obj) in seen or (parent_id is not None and not include_hidden and not obj.isVisible()):
                return
            seen.add(id(obj))
            if len(nodes) >= max_nodes:
                truncated = True
                return
            identity = self._remember(obj)
            if isinstance(obj, QWidget):
                rect = obj.rect()
                origin = obj.mapToGlobal(rect.topLeft())
                title = obj.windowTitle()
                enabled = obj.isEnabled()
            else:
                rect = obj.geometry()
                origin = obj.mapToGlobal(QPoint(0, 0))
                title, enabled = obj.title(), True
            node: dict[str, Any] = {
                "id": identity, "parent_id": parent_id, "class": type(obj).__name__,
                "object_name": obj.objectName(), "title": title, "visible": obj.isVisible(),
                "enabled": enabled,
                "bounds": {"x": origin.x(), "y": origin.y(), "width": rect.width(), "height": rect.height()},
            }
            nodes.append(node)
            if isinstance(obj, QWidget):
                node["accessible_name"] = obj.accessibleName()
                node["tooltip"] = obj.toolTip()
                self._describe_widget(obj, node)
                # Label buddies make translated, otherwise unnamed fields discoverable.
                if isinstance(obj, QLabel):
                    buddy = obj.buddy()
                    if buddy is not None:
                        node["buddy_id"] = self._remember(buddy)
                owned_actions = [*obj.actions(), *obj.findChildren(QAction)]
                node["action_ids"] = [self._remember(a) for a in obj.actions()]
                for action in owned_actions:
                    collect_action(action)
                children = obj.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly)
                node["child_ids"] = [self._remember(child) for child in children]
                for child in children:
                    visit(child, identity)
                # Embedded 3D surfaces receive input on their QWindow, not on
                # the QWidget container. Expose that real event target too.
                for native_child in obj.findChildren(QWindow, options=Qt.FindChildOption.FindDirectChildrenOnly):
                    visit(native_child, identity)
            else:
                for native_child in obj.findChildren(QWindow, options=Qt.FindChildOption.FindDirectChildrenOnly):
                    visit(native_child, identity)

        for root in roots:
            visit(root, None)
        # A Qt3DWindow can be parented to the container's native QWindow and
        # therefore absent from the QObject QWidget tree.
        if root_id is None:
            for window in QApplication.allWindows():
                if window.isVisible() and type(window).__name__ not in {"QWindow", "QWidgetWindow"}:
                    visit(window, None)
        modal = QApplication.activeModalWidget()
        popup = QApplication.activePopupWidget()
        return {"session_id": self.session, "nodes": nodes, "actions": list(actions.values()),
                "truncated": truncated, "modal_id": self._remember(modal) if modal else None,
                "popup_id": self._remember(popup) if popup else None,
                "operation_statuses": [dict(v) for v in self._operations.values()
                                       if v["status"] in {"queued", "running"}]}

    def _describe_widget(self, widget: QWidget, node: dict[str, Any]) -> None:
        semantic_state = getattr(widget, "agent_ui_state", None)
        if callable(semantic_state):
            node["custom_state"] = _json_data(semantic_state(), max_length=1_000_000)
        node["label"] = _field_label(widget)
        groups = []
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QGroupBox):
                groups.append(parent.title())
            parent = parent.parentWidget()
        node["groups"] = list(reversed(groups))
        if isinstance(widget, QMenu):
            node["text"] = widget.title()
        elif isinstance(widget, QLineEdit):
            node.update(value=widget.text() if widget.echoMode() == QLineEdit.EchoMode.Normal else "<redacted>",
                        read_only=widget.isReadOnly(), placeholder=widget.placeholderText())
        elif isinstance(widget, (QTextEdit, QPlainTextEdit)):
            node.update(value=widget.toPlainText(), read_only=widget.isReadOnly())
        elif isinstance(widget, (QLabel, QAbstractButton)):
            node["text"] = widget.text()
            if isinstance(widget, QAbstractButton):
                node.update(checkable=widget.isCheckable(), checked=widget.isChecked())
        elif isinstance(widget, QComboBox):
            node.update(value=widget.currentText(), current_index=widget.currentIndex(),
                        editable=widget.isEditable(),
                        choices=[{"index": i, "text": widget.itemText(i),
                                  "user_data": _json_data(widget.itemData(i))} for i in range(widget.count())])
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox, QAbstractSlider)):
            node.update(value=widget.value(), minimum=widget.minimum(), maximum=widget.maximum())
            if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                special = bool(widget.specialValueText()) and widget.value() == widget.minimum()
                node.update(display_text=widget.text(), special_value_text=widget.specialValueText(),
                            value_is_special=special)
                if special:
                    node.update(raw_value=widget.value(), value=None)
        elif isinstance(widget, QDateTimeEdit):
            is_date = isinstance(widget, QDateEdit)
            current = widget.date() if is_date else widget.dateTime()
            minimum = widget.minimumDate() if is_date else widget.minimumDateTime()
            maximum = widget.maximumDate() if is_date else widget.maximumDateTime()
            special = bool(widget.specialValueText()) and current == minimum
            raw = current.toString(Qt.DateFormat.ISODate)
            node.update(value=None if special else raw, display_text=widget.text(),
                        minimum=minimum.toString(Qt.DateFormat.ISODate),
                        maximum=maximum.toString(Qt.DateFormat.ISODate),
                        special_value_text=widget.specialValueText(), value_is_special=special)
            if special:
                node["raw_value"] = raw
        elif isinstance(widget, (QTabWidget, QTabBar)):
            node.update(current_index=widget.currentIndex(), choices=[
                {"index": i, "text": widget.tabText(i), "enabled": widget.isTabEnabled(i)}
                for i in range(widget.count())])
        elif isinstance(widget, QColorDialog):
            node["value"] = widget.currentColor().name(QColor.NameFormat.HexArgb)
        if isinstance(widget, QAbstractItemView):
            node["model"] = self._model_rows(widget, QModelIndex(), 0)
        if isinstance(widget, QGraphicsView):
            viewport = widget.viewport()
            if viewport is not None:
                node["viewport_id"] = self._remember(viewport)
            transform = widget.viewportTransform()
            node["scene_to_viewport"] = [transform.m11(), transform.m12(), transform.m21(),
                                           transform.m22(), transform.dx(), transform.dy()]
            scene = widget.scene()
            node["selected_item_ids"] = [str(i.item_id) for i in scene.selectedItems()
                                          if hasattr(i, "item_id")] if scene is not None else []

    def _model_rows(self, view: QAbstractItemView, parent: QModelIndex, start: int, count: int = 50) -> dict[str, Any]:
        model = view.model()
        if model is None:
            return {"row_count": 0, "column_count": 0, "rows": []}
        rows = model.rowCount(parent)
        columns = 1 if isinstance(model, QAbstractListModel) else model.columnCount(parent)
        parent_path = []
        ancestor = parent
        while ancestor.isValid():
            parent_path.insert(0, ancestor.row())
            ancestor = ancestor.parent()
        data = []
        for row in range(start, min(rows, start + count)):
            cells = []
            for column in range(min(columns, 50)):
                index = model.index(row, column, parent)
                value = model.data(index, Qt.ItemDataRole.DisplayRole)
                check = model.data(index, Qt.ItemDataRole.CheckStateRole)
                flags = model.flags(index)
                embedded = view.indexWidget(index)
                if (value is None or value == "") and embedded is not None:
                    # setItemWidget rows often have no DisplayRole at all.
                    labels = [embedded, *embedded.findChildren(QLabel)]
                    value = embedded.accessibleName() or " · ".join(
                        label.text() for label in labels
                        if isinstance(label, QLabel) and not label.isHidden() and label.text())
                cells.append({"column": column, "text": str(value) if value is not None else "",
                              "user_data": _json_data(model.data(index, Qt.ItemDataRole.UserRole)),
                              "accessible_text": _json_data(model.data(index, Qt.ItemDataRole.AccessibleTextRole)),
                              "tooltip": _json_data(model.data(index, Qt.ItemDataRole.ToolTipRole)),
                              "widget_id": self._remember(embedded) if embedded is not None else None,
                              "enabled": bool(flags & Qt.ItemFlag.ItemIsEnabled),
                              "selectable": bool(flags & Qt.ItemFlag.ItemIsSelectable),
                              "editable": bool(flags & Qt.ItemFlag.ItemIsEditable),
                              "check_state": check.value if isinstance(check, Qt.CheckState) else check})
            data.append({"row": row, "index_path": [*parent_path, row], "cells": cells,
                         "header": _json_data(model.headerData(row, Qt.Orientation.Vertical)),
                         "child_count": model.rowCount(model.index(row, 0, parent))})
        selection_model = view.selectionModel()
        return {"row_count": rows, "column_count": columns, "start": start, "rows": data,
                "headers": [_json_data(model.headerData(c, Qt.Orientation.Horizontal))
                            for c in range(min(columns, 50))],
                "has_more": start + len(data) < rows, "columns_truncated": columns > 50,
                "selected_rows": sorted({i.row() for i in selection_model.selectedIndexes()}) if selection_model else []}

    @staticmethod
    def _index(view: QAbstractItemView, path: Any, column: Any = 0) -> QModelIndex:
        if not isinstance(path, list) or not path or len(path) > 100:
            raise ValueError("index_path must be a nonempty list of row indices")
        parent = QModelIndex()
        model = view.model()
        if model is None:
            raise ValueError("View has no model")
        for row in path[:-1]:
            row = _integer(row)
            if not 0 <= row < model.rowCount(parent):
                raise ValueError("Model row is out of range")
            parent = model.index(row, 0, parent)
        row, column = _integer(path[-1]), _integer(column)
        columns = 1 if isinstance(model, QAbstractListModel) else model.columnCount(parent)
        if not 0 <= row < model.rowCount(parent) or not 0 <= column < columns:
            raise ValueError("Model cell is out of range")
        return model.index(row, column, parent)

    def _set_value(self, identity: str, value: Any) -> None:
        widget = self._resolve(identity, QWidget)
        self._modal_guard(widget)
        if isinstance(widget, QLineEdit):
            if widget.isReadOnly():
                raise ValueError("Text field is read only")
            text = _text(value)
            if len(text) > widget.maxLength():
                raise ValueError("Text exceeds the field's maximum length")
            validator = widget.validator()
            if validator is not None:
                state, _, _ = validator.validate(text, len(text))
                if state != validator.State.Acceptable:
                    raise ValueError("Text does not satisfy the field validator")
            widget.setFocus(Qt.FocusReason.OtherFocusReason)
            widget.selectAll()
            event = QInputMethodEvent()
            event.setCommitString(text)
            QApplication.sendEvent(widget, event)
        elif isinstance(widget, (QPlainTextEdit, QTextEdit)):
            if widget.isReadOnly():
                raise ValueError("Text field is read only")
            text = _text(value)
            widget.setFocus(Qt.FocusReason.OtherFocusReason)
            widget.selectAll()
            widget.insertPlainText(text)
        elif isinstance(widget, QAbstractButton) and widget.isCheckable():
            if not isinstance(value, bool):
                raise ValueError("Checkable control requires a boolean")
            if widget.isChecked() != value:
                widget.click()
        elif isinstance(widget, QComboBox):
            if isinstance(value, dict) and set(value) == {"index"}:
                index = _integer(value["index"])
            else:
                index = widget.findText(_text(value), Qt.MatchFlag.MatchExactly)
                if index < 0 and widget.isEditable():
                    editor = widget.lineEdit()
                    if editor is None:
                        raise ValueError("Editable combo has no text editor")
                    self._set_value(self._remember(editor), value)
                    return
            if not 0 <= index < widget.count():
                raise ValueError("Unknown combo choice")
            model = widget.model()
            if model is None:
                raise ValueError("Combo has no model")
            model_index = model.index(index, widget.modelColumn(), widget.rootModelIndex())
            if not model.flags(model_index) & Qt.ItemFlag.ItemIsEnabled:
                raise ValueError("Combo choice is disabled")
            widget.setFocus(Qt.FocusReason.OtherFocusReason)
            widget.setCurrentIndex(index)
            if not sip.isdeleted(widget):
                widget.activated.emit(index)
        elif isinstance(widget, (QSpinBox, QDoubleSpinBox, QAbstractSlider)):
            if value is None and isinstance(widget, (QSpinBox, QDoubleSpinBox)) and widget.specialValueText():
                number = widget.minimum()
            else:
                number = _number(value) if isinstance(widget, QDoubleSpinBox) else _integer(value)
            if not widget.minimum() <= number <= widget.maximum():
                raise ValueError("Number is outside the control's range")
            widget.setFocus(Qt.FocusReason.OtherFocusReason)
            if isinstance(widget, QAbstractSlider):
                widget.setSliderDown(True)
                try:
                    widget.setValue(int(number))
                finally:
                    if not sip.isdeleted(widget):
                        widget.setSliderDown(False)
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(number)
            else:
                widget.setValue(int(number))
        elif isinstance(widget, QDateEdit):
            day = widget.minimumDate() if value is None and widget.specialValueText() else QDate.fromString(_text(value), Qt.DateFormat.ISODate)
            if not day.isValid() or not widget.minimumDate() <= day <= widget.maximumDate():
                raise ValueError("Invalid or out of range ISO date")
            widget.setFocus(Qt.FocusReason.OtherFocusReason)
            widget.setDate(day)
        elif isinstance(widget, QDateTimeEdit):
            instant = widget.minimumDateTime() if value is None and widget.specialValueText() else QDateTime.fromString(_text(value), Qt.DateFormat.ISODate)
            if not instant.isValid() or not widget.minimumDateTime() <= instant <= widget.maximumDateTime():
                raise ValueError("Invalid or out of range ISO datetime")
            widget.setFocus(Qt.FocusReason.OtherFocusReason)
            widget.setDateTime(instant)
        elif isinstance(widget, (QTabWidget, QTabBar)):
            if isinstance(value, dict) and set(value) == {"index"}:
                index = _integer(value["index"])
            else:
                matches = [i for i in range(widget.count()) if widget.tabText(i) == _text(value)]
                if len(matches) != 1:
                    raise ValueError("Tab text must identify exactly one tab")
                index = matches[0]
            if not 0 <= index < widget.count() or not widget.isTabEnabled(index):
                raise ValueError("Tab is missing or disabled")
            widget.setCurrentIndex(index)
        elif isinstance(widget, QColorDialog):
            color = QColor(_text(value))
            if not color.isValid():
                raise ValueError("Invalid colour")
            widget.setCurrentColor(color)
        else:
            raise ValueError("Use interact_ui input events for this custom control")
        # FocusOut delivers editingFinished and the UI's normal undo commit.
        if not sip.isdeleted(widget):
            widget.clearFocus()

    def _interact(self, identity: str, action: str, params: dict[str, Any]) -> None:
        obj = self._resolve(identity, (QWidget, QWindow))
        self._modal_guard(obj)
        if action == "click":
            if isinstance(obj, QAbstractButton) and not params:
                obj.click()
            else:
                coordinates = {"x": obj.width() / 2, "y": obj.height() / 2, **params}
                self._pointer(obj, "mouse_press", coordinates)
                if not sip.isdeleted(obj):
                    self._pointer(obj, "mouse_release", coordinates)
        elif action == "focus":
            if isinstance(obj, QWidget):
                obj.setFocus(Qt.FocusReason.OtherFocusReason)
            else:
                obj.requestActivate()
        elif action == "close":
            obj.close()  # Normal closeEvent, including the application's unsaved-work prompt.
        elif action in {"accept", "reject"}:
            if not isinstance(obj, QDialog):
                raise ValueError("accept/reject requires a dialog")
            # Activate the default/cancel button through Qt, so slots attached
            # to buttons (rather than accept overrides) still validate forms.
            self._test.keyClick(obj, Qt.Key.Key_Return if action == "accept" else Qt.Key.Key_Escape)
        elif action == "move_rows":
            self._move_rows(obj, params)
        elif action == "drop":
            self._drop(obj, params)
        elif action in {"select", "activate", "edit_cell", "expand", "collapse"}:
            self._model_interaction(obj, action, params)
        elif action == "select_scene":
            self._select_scene(obj, params)
        elif action == "double_click":
            for step in ("mouse_press", "mouse_release", "double_click", "mouse_release"):
                if sip.isdeleted(obj):
                    break
                self._pointer(obj, step, params)
        elif action in {"mouse_press", "mouse_release", "mouse_move", "context_menu", "wheel"}:
            self._pointer(obj, action, params)
        elif action in {"key_sequence", "key_press", "key_release", "type_text"}:
            self._keyboard(obj, action, params)
        else:
            raise ValueError("Unknown or incompatible UI interaction")

    def _move_rows(self, obj: QObject, params: dict[str, Any]) -> None:
        if not isinstance(obj, QAbstractItemView):
            raise ValueError("move_rows requires an item view")
        model = obj.model()
        if model is None or obj.dragDropMode() != QAbstractItemView.DragDropMode.InternalMove:
            raise ValueError("This view does not allow internal row moves")
        parent = self._index(obj, params["parent_path"]) if params.get("parent_path") else QModelIndex()
        destination = self._index(obj, params["destination_parent_path"]) if params.get("destination_parent_path") else QModelIndex()
        row, count, before = (_integer(params.get(key)) for key in ("row", "count", "destination"))
        if row < 0 or count < 1 or row + count > model.rowCount(parent) or not 0 <= before <= model.rowCount(destination):
            raise ValueError("Row move range is invalid")
        if parent == destination and row <= before <= row + count:
            raise ValueError("Row move would not change the order")
        for source_row in range(row, row + count):
            flags = model.flags(model.index(source_row, 0, parent))
            if not flags & Qt.ItemFlag.ItemIsDragEnabled or not flags & Qt.ItemFlag.ItemIsEnabled:
                raise ValueError("Source row cannot be dragged")
        # Use the model's native move implementation and rowsMoved signal;
        # LayersPanel turns that signal into its usual ReorderLayersCommand.
        if not model.moveRows(parent, row, count, destination, before):
            raise ValueError("The model rejected the row move")

    def _drop(self, obj: QWidget | QWindow, params: dict[str, Any]) -> None:
        from open_garden_planner.ui.panels.plant_lists_panel import _DraggablePlantListWidget
        from open_garden_planner.ui.widgets.catalog_palette import CatalogList

        source = self._resolve(_text(params.get("source_id")), QAbstractItemView)
        self._modal_guard(source)
        index = self._index(source, params.get("index_path"), params.get("column", 0))
        if not source.model().flags(index) & Qt.ItemFlag.ItemIsDragEnabled:
            raise ValueError("Source item is not draggable")
        if isinstance(source, (CatalogList, _DraggablePlantListWidget)):
            mime = source.mime_for_index(index)
        else:
            mime = source.model().mimeData([index])
        if mime is None:
            raise ValueError("Source did not provide a drag payload")
        target = obj.viewport() if isinstance(obj, QGraphicsView) else obj
        if target is None:
            raise ValueError("Drop target has no viewport")
        point = QPointF(_number(params.get("x")), _number(params.get("y")))
        if params.get("coordinate_space", "widget") == "scene":
            if not isinstance(obj, QGraphicsView):
                raise ValueError("Scene coordinates require a graphics view")
            point = QPointF(obj.mapFromScene(point))
        elif params.get("coordinate_space", "widget") != "widget":
            raise ValueError("Unknown coordinate space")
        if not 0 <= point.x() < target.width() or not 0 <= point.y() < target.height():
            raise ValueError("Drop position is outside the viewport")
        modifiers = self._modifiers(params.get("modifiers", []))
        events = [QDragEnterEvent(point.toPoint(), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, modifiers),
                  QDragMoveEvent(point.toPoint(), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, modifiers),
                  QDropEvent(point, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, modifiers)]
        for event in events:
            QApplication.sendEvent(target, event)
            if not event.isAccepted():
                QApplication.sendEvent(target, QDragLeaveEvent())
                raise ValueError("Target rejected the drag/drop payload")

    def _model_interaction(self, obj: QObject, action: str, params: dict[str, Any]) -> None:
        if not isinstance(obj, QAbstractItemView):
            raise ValueError("This operation requires an item view")
        index = self._index(obj, params.get("index_path"), params.get("column", 0))
        model = obj.model()
        if model is None:
            raise ValueError("View has no model")
        flags = model.flags(index)
        if not flags & Qt.ItemFlag.ItemIsEnabled:
            raise ValueError("Model item is disabled")
        if action == "edit_cell":
            if "checked" in params:
                if not flags & Qt.ItemFlag.ItemIsUserCheckable or not isinstance(params["checked"], bool):
                    raise ValueError("Cell is not checkable")
                value = Qt.CheckState.Checked if params["checked"] else Qt.CheckState.Unchecked
                if not model.setData(index, value, Qt.ItemDataRole.CheckStateRole):
                    raise ValueError("The model rejected the checkbox edit")
            else:
                if not flags & Qt.ItemFlag.ItemIsEditable:
                    raise ValueError("Cell is not editable")
                obj.scrollTo(index)
                obj.setCurrentIndex(index)
                obj.setFocus(Qt.FocusReason.OtherFocusReason)
                obj.edit(index)
                editor = QApplication.focusWidget()
                viewport = obj.viewport()
                if editor is None or viewport is None or not viewport.isAncestorOf(editor):
                    raise ValueError("The view did not open a cell editor")
                # Spinbox/date editors focus an internal QLineEdit. Address
                # the delegate's outer editor to retain its range/validation.
                while True:
                    parent_editor = editor.parentWidget()
                    if parent_editor is viewport or parent_editor is None:
                        break
                    editor = parent_editor
                try:
                    self._set_value(self._remember(editor), params.get("value"))
                except Exception:
                    if not sip.isdeleted(editor):
                        # Close this failed editor without committing its value.
                        # Native Escape delivery can leave the view in EditingState,
                        # preventing the next corrected request from opening it.
                        obj.closeEditor(editor, QAbstractItemDelegate.EndEditHint.NoHint)
                    raise
        elif action in {"expand", "collapse"}:
            from PyQt6.QtWidgets import QTreeView
            if not isinstance(obj, QTreeView):
                raise ValueError("expand/collapse requires a tree view")
            obj.setExpanded(index, action == "expand")
        else:
            selection = QItemSelectionModel.SelectionFlag.Select
            if obj.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectRows:
                selection |= QItemSelectionModel.SelectionFlag.Rows
            elif obj.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectColumns:
                selection |= QItemSelectionModel.SelectionFlag.Columns
            if not params.get("additive", False):
                selection |= QItemSelectionModel.SelectionFlag.Clear
            selection_model = obj.selectionModel()
            if selection_model is None:
                raise ValueError("View has no selection model")
            selection_model.setCurrentIndex(index, selection)
            obj.scrollTo(index)
            if action == "activate":
                # One activation only. For views wired to doubleClicked rather
                # than activated, the client can send explicit double_click.
                self._test.keyClick(obj, Qt.Key.Key_Return)

    @staticmethod
    def _select_scene(obj: QObject, params: dict[str, Any]) -> None:
        from uuid import UUID

        from PyQt6.QtWidgets import QGraphicsItem

        if not isinstance(obj, QGraphicsView):
            raise ValueError("select_scene requires a graphics view")
        scene = obj.scene()
        if scene is None:
            raise ValueError("Graphics view has no scene")
        ids = params.get("item_ids")
        if not isinstance(ids, list) or len(ids) > 1000 or len(set(ids)) != len(ids):
            raise ValueError("item_ids must be a unique list of at most 1000 UUIDs")
        items = []
        for identity in ids:
            uuid = UUID(identity)
            matches = [i for i in scene.items() if getattr(i, "item_id", None) == uuid]
            if len(matches) != 1:
                raise ValueError("Unknown or ambiguous scene item UUID")
            item = matches[0]
            if not item.isVisible() or not item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable:
                raise ValueError("Scene item is hidden or not selectable")
            items.append(item)
        scene.clearSelection()
        for item in items:
            item.setSelected(True)

    @staticmethod
    def _modifiers(names: Any) -> Qt.KeyboardModifier:
        if not isinstance(names, list):
            raise ValueError("modifiers must be a list")
        value = Qt.KeyboardModifier.NoModifier
        mapping = {"shift": Qt.KeyboardModifier.ShiftModifier, "ctrl": Qt.KeyboardModifier.ControlModifier,
                   "alt": Qt.KeyboardModifier.AltModifier, "meta": Qt.KeyboardModifier.MetaModifier}
        for name in names:
            if name not in mapping:
                raise ValueError("Unknown keyboard modifier")
            value |= mapping[name]
        return value

    def _pointer(self, obj: QWidget | QWindow, action: str, params: dict[str, Any]) -> None:
        target = obj.viewport() if isinstance(obj, QGraphicsView) else obj
        if target is None:
            raise ValueError("View has no viewport")
        point = QPointF(_number(params.get("x")), _number(params.get("y")))
        if params.get("coordinate_space", "widget") == "scene":
            if not isinstance(obj, QGraphicsView):
                raise ValueError("Scene coordinates require a graphics view handle")
            point = QPointF(obj.mapFromScene(point))
        elif params.get("coordinate_space", "widget") != "widget":
            raise ValueError("coordinate_space must be widget or scene")
        # A press may resize its own widget (accordion headers); a native
        # captured drag/release can legitimately end outside its new bounds.
        if action not in {"mouse_move", "mouse_release"} and not (
            0 <= point.x() < target.width() and 0 <= point.y() < target.height()
        ):
            raise ValueError("Pointer press lies outside the target viewport")
        global_point = QPointF(target.mapToGlobal(point.toPoint()))
        modifiers = self._modifiers(params.get("modifiers", []))
        buttons = {"left": Qt.MouseButton.LeftButton, "right": Qt.MouseButton.RightButton,
                   "middle": Qt.MouseButton.MiddleButton, "none": Qt.MouseButton.NoButton}
        button_name = params.get("button", "left")
        if button_name not in buttons:
            raise ValueError("Unknown pointer button")
        button = buttons[button_name]
        held = Qt.MouseButton.NoButton
        for name in params.get("buttons", []):
            if name not in buttons:
                raise ValueError("Unknown held pointer button")
            held |= buttons[name]
        event: QEvent
        if action == "context_menu":
            event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point.toPoint(), global_point.toPoint(), modifiers)
        elif action == "wheel":
            delta = QPoint(_integer(params.get("delta_x", 0)), _integer(params.get("delta_y", 0)))
            event = QWheelEvent(point, global_point, QPoint(), delta, held, modifiers,
                                Qt.ScrollPhase.NoScrollPhase, False)
        else:
            kind = {"mouse_press": QEvent.Type.MouseButtonPress, "mouse_release": QEvent.Type.MouseButtonRelease,
                    "mouse_move": QEvent.Type.MouseMove, "double_click": QEvent.Type.MouseButtonDblClick}[action]
            if action in {"mouse_press", "double_click"}:
                held |= button
            if action == "mouse_move":
                button = Qt.MouseButton.NoButton
            event = QMouseEvent(kind, point, global_point, button, held, modifiers)
        QApplication.sendEvent(target, event)

    def _keyboard(self, obj: QWidget | QWindow, action: str, params: dict[str, Any]) -> None:
        if isinstance(obj, QWidget):
            obj.setFocus(Qt.FocusReason.OtherFocusReason)
        if action == "type_text":
            text_event = QInputMethodEvent()
            text_event.setCommitString(_text(params.get("text")))
            QApplication.sendEvent(obj, text_event)
            return
        sequence = QKeySequence.fromString(_text(params.get("key")), QKeySequence.SequenceFormat.PortableText)
        if sequence.isEmpty() or any(
            combination.key() == Qt.Key.Key_unknown or
            (0x7f < combination.key().value < Qt.Key.Key_Escape.value)
            for combination in (sequence[i] for i in range(sequence.count()))
        ):
            raise ValueError("Invalid key sequence; use type_text for Unicode text")
        if action == "key_sequence":
            self._test.keySequence(obj, sequence)
        else:
            if sequence.count() != 1:
                raise ValueError("Held key input requires a single key combination")
            combination = sequence[0]
            event = QKeyEvent(QEvent.Type.KeyPress if action == "key_press" else QEvent.Type.KeyRelease,
                              combination.key().value, combination.keyboardModifiers())
            QApplication.sendEvent(obj, event)

    def render(self, identity: str | None) -> dict[str, Any]:
        target = self._resolve(identity, (QWidget, QWindow)) if identity else (QApplication.activeModalWidget() or self.owner)
        if not target.isVisible():
            raise ValueError("Cannot capture a hidden UI target")
        if isinstance(target, QWindow):
            screen = target.screen()
            if screen is None:
                raise ValueError("Window has no screen")
            pixmap = screen.grabWindow(target.winId())
        else:
            pixmap = target.grab()
        if pixmap.isNull():
            raise ValueError("This platform could not capture the UI surface")
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not pixmap.save(buffer, "PNG"):
            raise ValueError("Could not encode UI screenshot")
        return {"png_bytes": data.data(), "width": pixmap.width(), "height": pixmap.height(),
                "device_pixel_ratio": pixmap.devicePixelRatio(), "widget_id": self._remember(target)}
