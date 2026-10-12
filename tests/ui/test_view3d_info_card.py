"""The info card of the 3D pane, v0 (Phase 17 L1.8, gate G6 — the widget alone).

``ui/view3d/info_card.View3DInfoCard`` shows what the 3D view shows of the one
selected item, from that item's applied ``Record`` — the numbers the model was built
from. Two things are pinned hard here: the height on the card IS the record's
height, and nothing a plan file can hold is ever rendered as markup.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel

from open_garden_planner.core.measurements import format_area, format_length
from open_garden_planner.core.object_types import ObjectType
from open_garden_planner.core.scene3d import Material, Params, Record, Transform
from open_garden_planner.ui.view3d.info_card import CardSubject, View3DInfoCard

HOSTILE = '<b>x</b><img src="file:///C:/Windows/win.ini">'


def rect(width: float, depth: float) -> tuple[tuple[float, float], ...]:
    half_w, half_d = width / 2.0, depth / 2.0
    return ((-half_w, -half_d), (half_w, -half_d), (half_w, half_d), (-half_w, half_d))


def record(kind: str = "RAISED_BED", *, shape: str = "rectangle", ring=None, height: float | None = 40.0,
           params: dict[str, Any] | None = None, path=(), width: float | None = None,
           parent: str | None = None, base: float = 0.0) -> Record:
    return Record(item_id="it", kind=kind, shape=shape, footprints=(ring or rect(300.0, 120.0),),
                  transform=Transform(100.0, 200.0, 30.0, base), material=Material(kind),
                  height_cm=height, params=Params({"seed": "it", **(params or {})}), path=path,
                  path_width_cm=width, parent_id=parent)


def item(name: str = "", kind: ObjectType | None = ObjectType.RAISED_BED, **more: Any) -> Any:
    return SimpleNamespace(item_id="it", name=name, object_type=kind, metadata=more.pop("metadata", {}),
                           plant_species=more.pop("plant_species", ""), **more)


@pytest.fixture()
def card(qtbot) -> View3DInfoCard:
    widget = View3DInfoCard()
    qtbot.addWidget(widget)
    return widget


def shown(card: View3DInfoCard, **subject: Any) -> View3DInfoCard:
    card.show_subject(CardSubject(**subject))
    return card


# ── what it says ─────────────────────────────────────────────────────────────


def test_it_starts_hidden_and_empty(card: View3DInfoCard) -> None:
    assert card.isHidden() and card.item_id == "" and card.rows() == [] and card.text() == ""


def test_a_built_item_shows_its_kind_its_height_and_its_footprint(card: View3DInfoCard) -> None:
    built = record(height=42.5)
    shown(card, item=item("North bed"), record=built, has_model=True)
    assert not card.isHidden() and card.item_id == "it"
    assert card.title() == "North bed"
    assert card.value("Type:") == "Raised Bed"
    assert card.value("Height:") == format_length(built.height_cm) == "42.5 cm"
    assert card.value("Size:") == "3.00 m × 1.20 m", "the drawn size, not the turned bounding box"
    assert card.note() == "" and card.value("Species:") is None


def test_an_item_without_a_name_is_titled_by_its_kind(card: View3DInfoCard) -> None:
    shown(card, item=item(""), record=record(), has_model=True)
    assert card.title() == "Raised Bed"


@pytest.mark.parametrize(("subject", "label", "value"), [
    (record("RAIN_BARREL", shape="circle", params={"radius_cm": 45.0}), "Diameter:", "90.0 cm"),
    (record("FENCE", shape="polyline", ring=rect(700.0, 5.0), width=5.0,
            path=((-350.0, 0.0), (0.0, 0.0), (0.0, 400.0))), "Length:", "7.50 m"),
    (record("HOUSE", shape="polygon", ring=((0.0, 0.0), (800.0, 0.0), (800.0, 600.0), (0.0, 600.0))),
     "Area:", format_area(480_000.0)),
    (record("POND_POOL", shape="ellipse", ring=rect(250.0, 90.0)), "Size:", "2.50 m × 90.0 cm"),
])
def test_the_footprint_is_said_in_the_way_the_shape_was_drawn(card, subject, label, value) -> None:
    shown(card, item=item(kind=ObjectType[subject.kind]), record=subject, has_model=True)
    assert card.value(label) == value
    assert sum(label_ in ("Diameter:", "Length:", "Area:", "Size:") for label_, _v in card.rows()) == 1


def test_a_plant_shows_species_height_and_canopy_at_the_plan_date(card: View3DInfoCard) -> None:
    plant = record("TREE", shape="circle", height=412.0, params={"radius_cm": 135.5}, parent="bed-1",
                   base=38.0)
    tree = item("Old apple", ObjectType.TREE, metadata={"plant_species": {
        "common_name": "Apple", "scientific_name": "Malus domestica"}})
    shown(card, item=tree, record=plant, has_model=True, parent_name="Kitchen bed",
          plan_date=date(2026, 6, 21))
    assert card.title() == "Old apple" and card.value("Type:") == "Tree"
    assert card.value("Species:") == "Apple" and card.value("Scientific name:") == "Malus domestica"
    assert card.value("Height:") == format_length(plant.height_cm) == "4.12 m"
    assert card.value("Canopy diameter:") == format_length(2 * 135.5) == "2.71 m"
    assert card.value("Bed:") == "Kitchen bed"
    assert card.value("Sizes on:") and "21" in card.value("Sizes on:")  # the plan date, localised
    assert card.value("Size:") is None and card.value("Diameter:") is None


def test_the_height_on_the_card_is_the_records_height(card: View3DInfoCard) -> None:
    """Gate G6: not a second computation — whatever the record holds is what is shown."""
    for height in (0.5, 37.25, 99.94, 100.0, 180.0, 1234.5):
        shown(card, item=item(kind=ObjectType.TREE), has_model=True,
              record=record("TREE", shape="circle", height=height, params={"radius_cm": 20.0}))
        assert card.value("Height:") == format_length(height)


def test_a_plant_without_a_known_height_says_so(card: View3DInfoCard) -> None:
    unknown = record("PERENNIAL", shape="circle", height=None, params={"radius_cm": 20.0})
    shown(card, item=item(kind=ObjectType.PERENNIAL, plant_species="Lavender"), record=unknown,
          has_model=True)   # the grey placeholder dome IS a model
    assert card.value("Height:") == "not known"
    assert card.value("Canopy diameter:") == "40.0 cm" and card.value("Species:") == "Lavender"
    assert card.note() == ""


def test_the_species_string_of_the_item_is_used_when_there_is_no_species_record(card) -> None:
    shown(card, item=item(kind=ObjectType.SHRUB, plant_species="  Rosa canina "), has_model=True,
          record=record("SHRUB", shape="circle", params={"radius_cm": 60.0}))
    assert card.value("Species:") == "Rosa canina" and card.value("Scientific name:") is None


# ── an item without a 3D model ───────────────────────────────────────────────


def test_an_item_the_3d_view_refused_shows_the_reason_in_words(card: View3DInfoCard) -> None:
    shown(card, item=item("Twisted fence", ObjectType.FENCE), record=None, has_model=False,
          problem="its outline cannot be turned into a solid")
    assert card.note() == "Not shown in 3D: its outline cannot be turned into a solid."
    assert card.rows() == [("Type:", "Fence")], "no height of a model that does not exist"


def test_a_flat_item_is_said_to_be_drawn_on_the_ground(card: View3DInfoCard) -> None:
    lawn = record("LAWN", shape="polygon", height=None)
    shown(card, item=item("Lawn", ObjectType.LAWN), record=lawn, has_model=False)
    assert card.note() == "Drawn on the ground in 3D: the plan gives it no height."
    assert card.value("Height:") == "not known"


def test_an_item_with_no_record_and_no_reason(card: View3DInfoCard) -> None:
    shown(card, item=item("A note", None), record=None, has_model=False)
    assert card.note() == "Not shown in 3D: this kind of object has no 3D form."
    assert card.value("Type:") == "Object"


def test_a_group_is_called_a_group(card: View3DInfoCard) -> None:
    group = SimpleNamespace(item_id="g", name="", object_type=None, metadata={}, plant_species="",
                            childItems=lambda: [object(), object()])
    shown(card, item=group, record=None, has_model=True)
    assert card.title() == "Group" and card.value("Type:") == "Group" and card.note() == ""


# ── plan text is untrusted (the security add-on) ─────────────────────────────


def test_a_name_that_is_markup_is_shown_as_its_characters(card: View3DInfoCard) -> None:
    """The story's own example: nothing is rendered, nothing is fetched — the label is
    plain text and holds exactly those characters."""
    shown(card, item=item(HOSTILE), record=record(), has_model=True)
    assert card.title() == HOSTILE
    title = next(label for label in card.labels() if label.text() == HOSTILE)
    assert title.textFormat() == Qt.TextFormat.PlainText


def test_every_label_of_the_card_is_plain_text_whatever_it_shows(card: View3DInfoCard) -> None:
    hostile = item(HOSTILE, ObjectType.TREE, plant_species=HOSTILE, metadata={"plant_species": {
        "common_name": HOSTILE, "scientific_name": "<table>" + "<tr><td>x" * 5000}})
    for subject in (
        CardSubject(hostile, record("TREE", shape="circle", params={"radius_cm": 9.0}, parent="p"),
                    True, "", HOSTILE, date(2026, 1, 2)),
        CardSubject(hostile, None, False, HOSTILE),
        CardSubject(item("plain"), record(), True),
    ):
        card.show_subject(subject)
        labels = card.labels()
        assert len(labels) >= 3
        for label in labels:
            assert isinstance(label, QLabel) and label.textFormat() == Qt.TextFormat.PlainText
            assert label.toolTip() == "", "a tooltip has no format switch: none carries plan text"
            assert not label.openExternalLinks()


def test_plan_text_is_normalised_and_clipped(card: View3DInfoCard) -> None:
    long_name = "bed " * 200
    forged = "Rose\r\n2026-10-12 ERROR forged line\u202e"
    shown(card, item=item(long_name, ObjectType.SHRUB, plant_species=forged), has_model=False,
          record=record("SHRUB", shape="circle", params={"radius_cm": 9.0}, parent="p"),
          parent_name="x\ny" * 100, problem="a\nb" * 400)
    assert len(card.title()) <= 81 and card.title().endswith("…")
    species = card.value("Species:")
    assert species is not None and "\n" not in species and "\r" not in species
    assert "\u202e" not in species
    assert "\n" not in (card.value("Bed:") or "") and len(card.value("Bed:") or "") <= 81
    assert "\n" not in card.note() and len(card.note()) <= 240


# ── the action, and coming and going ─────────────────────────────────────────


def test_show_in_plan_names_the_item(card: View3DInfoCard, qtbot) -> None:
    asked: list[str] = []
    card.show_in_plan.connect(asked.append)
    card._button.click()  # noqa: SLF001
    assert asked == [], "nothing shown: nothing to show in the plan"
    shown(card, item=item("bed"), record=record(), has_model=True)
    card._button.click()  # noqa: SLF001
    assert asked == ["it"] and card._button.text() == "Show in Plan"  # noqa: SLF001


def test_clear_hides_and_forgets(card: View3DInfoCard) -> None:
    shown(card, item=item("bed"), record=record(), has_model=True)
    card.clear()
    assert card.isHidden() and card.item_id == "" and card.rows() == [] and card.text() == ""


def test_asking_for_the_same_lines_again_keeps_the_labels(card: View3DInfoCard) -> None:
    """Every pass of the pipeline refreshes the card while an item is selected: the same
    lines must not rebuild ten widgets a tick."""
    subject = CardSubject(item("bed"), record(), True)
    card.show_subject(subject)
    before = [id(label) for label in card.labels()]
    card.show_subject(subject)
    assert [id(label) for label in card.labels()] == before
    card.show_subject(CardSubject(item("bed"), record(height=55.0), True))
    assert card.value("Height:") == "55.0 cm"
    assert [id(label) for label in card.labels()] != before
