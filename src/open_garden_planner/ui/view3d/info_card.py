"""The info card of the 3D pane, v0 (Phase 17 L1.8, ADR-065). Engine-free.

A small panel BELOW the 3D view — never over it: the view is a native window and
nothing a sibling widget paints shows on top of it (§8.9.9). It is shown while
exactly one item is selected and the 3D pane is visible, and says what the 3D
view shows of that item:

- its name and kind; for a plant its species, common and scientific;
- its HEIGHT and — a plant's — CANOPY DIAMETER at the plan date. These are read
  from the item's applied ``Record`` (``height_cm``, ``params["radius_cm"]``): the
  very numbers the 3D model was built from, never a second computation. A plant
  the plan gives no height says so;
- for a built item the height and its footprint: size, diameter, area or length,
  by the shape it was drawn as;
- the bed it stands on;
- and, when the 3D view shows NO model of it, why — in the words the "Not Shown in
  3D" list uses for the same reason (handed in: ``problem``).

One action: "Show in Plan".

EVERY string here comes from the plan file or is a translated label, and a plan
file is untrusted (``core/plain_text.py``, §8.11): every label is
``Qt.TextFormat.PlainText`` — a name that reads ``<b>x</b><img src="file:…">`` is
shown as those characters — and every plan-derived text is normalised and clipped
(``clip_line``). No tooltip carries plan text.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from PyQt6.QtCore import QDate, QLocale, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from open_garden_planner.core.measurements import format_area, format_length
from open_garden_planner.core.object_types import ObjectType, get_translated_display_name
from open_garden_planner.core.plain_text import clip_line
from open_garden_planner.core.plant_art import PLANT_KINDS
from open_garden_planner.core.scene3d import (
    PARAM_RADIUS_CM,
    SHAPE_CIRCLE,
    SHAPE_ELLIPSE,
    SHAPE_POLYLINE,
    SHAPE_RECTANGLE,
    Record,
)

#: A scientific name is clipped like any name, but may be a little longer.
_SPECIES_MAX_CHARS: Final = 120


@dataclass(frozen=True, slots=True)
class CardSubject:
    """What the card is asked to show — gathered by the workspace, plain data but for
    the canvas item it reads the name and the species from.

    ``record``: the item's APPLIED record (``SceneSync.records``), None when the 3D
    scene holds none. ``has_model``: the engine holds at least one mesh for it.
    ``problem``: why it has none, in the user's words ("" when no reason was
    reported — then the card's own sentence says what is the case). ``parent_name``:
    the display name of the bed it is linked to ("" for none). ``plan_date``: the day
    the record's sizes are for.
    """

    item: Any
    record: Record | None = None
    has_model: bool = False
    problem: str = ""
    parent_name: str = ""
    plan_date: date | None = None


def _span(ring: Sequence[Sequence[float]]) -> tuple[float, float]:
    xs = [point[0] for point in ring]
    ys = [point[1] for point in ring]
    return (max(xs) - min(xs), max(ys) - min(ys))


def _length(path: Sequence[Sequence[float]]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:], strict=False))


class View3DInfoCard(QFrame):
    """The card (module docstring). ``show_in_plan`` carries the item's id."""

    show_in_plan = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._item_id = ""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        self._title = self._label(bold=True)
        head.addWidget(self._title, 1)
        self._button = QPushButton(self.tr("Show in Plan"), self)
        self._button.setToolTip(self.tr("Centre the plan on this object"))
        self._button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._button.clicked.connect(self._on_show)
        head.addWidget(self._button)
        outer.addLayout(head)
        self._form = QFormLayout()
        self._form.setContentsMargins(0, 0, 0, 0)
        self._form.setHorizontalSpacing(10)
        self._form.setVerticalSpacing(2)
        outer.addLayout(self._form)
        self._note = self._label(wrap=True)
        outer.addWidget(self._note)
        self._rows: list[tuple[str, str]] = []
        self.hide()

    def _label(self, text: str = "", *, bold: bool = False, wrap: bool = False) -> QLabel:
        label = QLabel(text, self)
        # PLAIN text, said out loud: a QLabel left on AutoText renders markup it finds
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setWordWrap(wrap)
        if bold:
            font = label.font()
            font.setBold(True)
            label.setFont(font)
        return label

    # ── reads (the tests', and the workspace's) ──────────────────────────────

    @property
    def item_id(self) -> str:
        """The id of the item shown ("" while the card shows none)."""
        return self._item_id

    def title(self) -> str:
        return self._title.text()

    def rows(self) -> list[tuple[str, str]]:
        """The card's ``(label, value)`` lines, top to bottom."""
        return list(self._rows)

    def value(self, label: str) -> str | None:
        """The value shown beside ``label`` (None: the card has no such line)."""
        for name, value in self._rows:
            if name == label:
                return value
        return None

    def note(self) -> str:
        return self._note.text()

    def text(self) -> str:
        """Everything the card says, one line per line."""
        lines = [self.title(), *(f"{label} {value}" for label, value in self._rows)]
        if self.note():
            lines.append(self.note())
        return "\n".join(lines)

    def labels(self) -> list[QLabel]:
        """Every label of the card — for the rule that each is plain text."""
        return self.findChildren(QLabel)

    # ── content ──────────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Show nothing (and hide)."""
        self._item_id = ""
        self._set_rows(())
        self._title.setText("")
        self._note.setText("")
        self.hide()

    def show_subject(self, subject: CardSubject) -> None:
        """Fill the card for one item and show it."""
        item, record = subject.item, subject.record
        self._item_id = str(getattr(item, "item_id", "") or "")
        kind = self._kind_text(item, record)
        name = clip_line(getattr(item, "name", "") or "")
        self._title.setText(name or kind)
        rows: list[tuple[str, str]] = [(self.tr("Type:"), kind)]
        is_plant = record is not None and record.kind in PLANT_KINDS or (
            record is None and getattr(getattr(item, "object_type", None), "name", "") in PLANT_KINDS)
        if is_plant:
            rows += self._species_rows(item)
        if record is not None:
            if is_plant:
                rows += self._plant_rows(record)
            else:
                rows += self._built_rows(record)
        if subject.parent_name:
            rows.append((self.tr("Bed:"), clip_line(subject.parent_name)))
        if record is not None and subject.plan_date is not None and is_plant:
            day = subject.plan_date
            rows.append((self.tr("Sizes on:"), QLocale().toString(
                QDate(day.year, day.month, day.day), QLocale.FormatType.ShortFormat)))
        self._set_rows(rows)
        self._note.setText(self._model_note(subject))
        self._note.setVisible(bool(self._note.text()))
        self.show()

    def _kind_text(self, item: Any, record: Record | None) -> str:
        object_type = getattr(item, "object_type", None)
        if isinstance(object_type, ObjectType):
            return clip_line(get_translated_display_name(object_type))
        if record is not None:
            try:
                return clip_line(get_translated_display_name(ObjectType[record.kind]))
            except KeyError:
                return clip_line(record.kind)
        if item is not None and callable(getattr(item, "childItems", None)) and item.childItems():
            return self.tr("Group")
        return self.tr("Object")

    def _species_rows(self, item: Any) -> list[tuple[str, str]]:
        metadata = getattr(item, "metadata", None)
        species = metadata.get("plant_species") if isinstance(metadata, Mapping) else None
        common = scientific = ""
        if isinstance(species, Mapping):
            common = str(species.get("common_name") or "")
            scientific = str(species.get("scientific_name") or "")
        common = common or str(getattr(item, "plant_species", "") or "")
        rows = []
        if common.strip():
            rows.append((self.tr("Species:"), clip_line(common)))
        if scientific.strip():
            rows.append((self.tr("Scientific name:"), clip_line(scientific, _SPECIES_MAX_CHARS)))
        return rows

    def _height_row(self, record: Record) -> tuple[str, str]:
        if record.height_cm is None:
            return (self.tr("Height:"), self.tr("not known"))
        return (self.tr("Height:"), format_length(record.height_cm))

    def _plant_rows(self, record: Record) -> list[tuple[str, str]]:
        rows = [self._height_row(record)]
        radius = record.params.get(PARAM_RADIUS_CM)
        if isinstance(radius, (int, float)) and not isinstance(radius, bool) and radius > 0.0:
            rows.append((self.tr("Canopy diameter:"), format_length(2.0 * float(radius))))
        return rows

    def _built_rows(self, record: Record) -> list[tuple[str, str]]:
        rows = [self._height_row(record)]
        radius = record.params.get(PARAM_RADIUS_CM)
        if record.shape == SHAPE_CIRCLE and isinstance(radius, (int, float)):
            rows.append((self.tr("Diameter:"), format_length(2.0 * float(radius))))
        elif record.shape == SHAPE_POLYLINE and len(record.path) >= 2:
            rows.append((self.tr("Length:"), format_length(_length(record.path))))
        elif record.shape in (SHAPE_RECTANGLE, SHAPE_ELLIPSE) and record.footprints:
            width, depth = _span(record.footprints[0])
            rows.append((self.tr("Size:"), self.tr("{width} × {depth}").format(
                width=format_length(width), depth=format_length(depth))))
        else:
            rows.append((self.tr("Area:"), format_area(record.plan_area_cm2)))
        return rows

    def _model_note(self, subject: CardSubject) -> str:
        if subject.has_model:
            return ""
        if subject.problem:
            return self.tr("Not shown in 3D: {reason}.").format(reason=clip_line(subject.problem, 200))
        if subject.record is None:
            return self.tr("Not shown in 3D: this kind of object has no 3D form.")
        return self.tr("Drawn on the ground in 3D: the plan gives it no height.")

    def _set_rows(self, rows: Sequence[tuple[str, str]]) -> None:
        if list(rows) == self._rows:
            return  # every pass of the pipeline asks: the same lines keep their labels
        while self._form.rowCount():
            self._form.removeRow(0)
        self._rows = list(rows)
        for label, value in rows:
            self._form.addRow(self._label(label), self._label(value))

    def _on_show(self) -> None:
        if self._item_id:
            self.show_in_plan.emit(self._item_id)
