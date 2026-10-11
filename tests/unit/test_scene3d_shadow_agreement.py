"""The built world's 3D shadows against the 2D shadow model (Phase 17 L1.6, review
round 1, P1-3).

``scripts/measure_built_world_shadows.py`` projects every built-world mesh onto the
ground along the sun ray and compares it with what the 2D sun & shade model casts
for the same item. This file runs that measurement and holds the builders to the
table the docs quote (ADR-054's builders addendum, §8.26.6, FR-SUN-09,
``ogp-lush-cinematic`` §1 "Shadows") — within ``TOLERANCE`` (0.05), so a builder
change that moves a kind by a tenth of an IoU fails here and takes the docs with it.

It also states the RULE the table is an instance of, kind by kind:

- where 3D builds the solid the 2D model casts — a bed, a box, a plain panel — the
  two shadows are the same (IoU >= 0.99): the truth gate's own case;
- a picket, metal or chain-link fence is mostly air: its 3D shadow lies INSIDE the
  2D one and is a fraction of it — the 2D sun study over-shades behind such a
  fence, and the 3D view will show sun where the hours-of-sun map shows shade;
- a wall, a stone-wall fence and a hedge fence have a nominal section the plan does
  not hold: their 3D shadow is the WIDER one, more so the higher the sun.

Before this round the ADR said of every line item that "the body is wider". For
three of the five fence styles the opposite is true.
"""

from __future__ import annotations

import importlib.util
from functools import cache
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "measure_built_world_shadows.py"
PRISMS = ("raised bed", "container (box)", "plain panel (unknown style)")
POROUS = ("chain-link fence", "metal fence", "picket fence")  # least solid first
NOMINAL_SECTION = ("wall", "stone-wall fence", "hedge fence")


@cache
def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("measure_built_world_shadows_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


@cache
def _rows() -> dict[str, object]:
    return {row.name: row for row in _script().measure()}


def test_the_measurement_matches_the_documented_table() -> None:
    script = _script()
    assert script.TOLERANCE == 0.05 and script.ELEVATIONS_DEG == (15.0, 35.0, 60.0)
    assert script.drift(tuple(_rows().values())) == []
    assert set(_rows()) == set(script.DOCUMENTED) and len(script.DOCUMENTED) == 16


def test_the_drift_check_can_fail() -> None:
    """A row that moved by a tenth is reported; so is a missing case."""
    script = _script()
    rows = list(_rows().values())
    moved = script.Row(rows[0].name, rows[0].why,
                       tuple(value + 0.1 for value in rows[0].ratio), rows[0].iou)
    assert script.drift([moved, *rows[1:]]) == [
        f"{rows[0].name} at {elevation:g} deg: area ratio {value + 0.1:.2f}, documented {documented:.2f}"
        for elevation, value, documented in zip(
            script.ELEVATIONS_DEG, rows[0].ratio, script.DOCUMENTED[rows[0].name][0], strict=True)
    ]
    assert script.drift(rows[1:])[0].startswith("cases differ")


@pytest.mark.parametrize("name", PRISMS)
def test_a_prism_casts_the_shadow_the_2d_model_casts(name: str) -> None:
    row = _rows()[name]
    assert min(row.iou) >= 0.99  # type: ignore[attr-defined]
    assert all(abs(value - 1.0) <= 0.01 for value in row.ratio)  # type: ignore[attr-defined]


@pytest.mark.parametrize("name", POROUS)
def test_a_porous_fence_casts_less_in_3d_than_the_2d_model_says(name: str) -> None:
    row = _rows()[name]
    assert max(row.ratio) < 0.75  # type: ignore[attr-defined]
    # its 3D shadow lies inside the 2D strip's: the IoU IS the area ratio (the posts,
    # wider than the stroke, stick out by a hair)
    for ratio, iou in zip(row.ratio, row.iou, strict=True):  # type: ignore[attr-defined]
        assert iou <= ratio + 1e-9 and ratio - iou <= 0.03


def test_the_porous_fences_in_order() -> None:
    chain, metal, picket = (_rows()[name] for name in POROUS)
    for index in range(3):
        assert chain.ratio[index] < metal.ratio[index] < picket.ratio[index]  # type: ignore[attr-defined]
    assert max(chain.ratio) < 0.25  # type: ignore[attr-defined]
    # the number an owner will notice: behind a chain-link fence the hours-of-sun map
    # shows full shade where the 3D fence lets more than four fifths of the sun through
    assert 1.0 - max(chain.ratio) > 0.8  # type: ignore[attr-defined]


@pytest.mark.parametrize("name", NOMINAL_SECTION)
def test_a_nominal_section_casts_a_wider_shadow_the_higher_the_sun(name: str) -> None:
    row = _rows()[name]
    assert row.ratio[0] < row.ratio[1] < row.ratio[2]  # type: ignore[attr-defined]
    assert min(row.ratio) > 1.0  # type: ignore[attr-defined]
    # over the box gate at a low sun, under it at a high one
    assert row.iou[0] >= BOX_GATE_IOU > row.iou[2]  # type: ignore[attr-defined]


#: The truth gate "Shadows" of ``ogp-lush-cinematic`` §1: IoU of a BOX caster's engine
#: shadow against the analytic 2D shadow.
BOX_GATE_IOU = 0.85


def test_which_kinds_meet_the_box_gate_at_every_elevation() -> None:
    """The truth gate (IoU >= 0.85) is defined on a solid box caster. Which built-world
    kinds would meet it at all three elevations is a fact the 3D reviewer needs before
    measuring: the prisms, the two hedges and the round tub — no building, no terracotta
    pot, no wall and no styled fence."""
    meets = {name for name, row in _rows().items() if min(row.iou) >= BOX_GATE_IOU}  # type: ignore[attr-defined]
    assert meets == {*PRISMS, "hedge (polygon)", "hedge (section)", "round tub"}
    below = {name: min(row.iou) for name, row in _rows().items() if name not in meets}  # type: ignore[attr-defined]
    assert max(below.values()) < BOX_GATE_IOU - 0.01  # none of the others is a borderline case


def test_the_gate_s_number_is_the_skill_s() -> None:
    """Both skill trees state the gate and now list the built-world kinds next to it."""
    root = SCRIPT.parents[1]
    for tree in (".claude", ".agents"):
        text = (root / tree / "skills" / "ogp-lush-cinematic" / "SKILL.md").read_text(encoding="utf-8")
        row = next(line for line in text.splitlines() if line.startswith("| Shadows |"))
        assert "IoU ≥ 0.85" in row and "box caster" in row
        assert "scripts/measure_built_world_shadows.py" in row
        for kind in ("picket", "chain-link", "wall", "house", "pot"):
            assert kind in row, (tree, kind)


def test_the_stroke_widths_are_the_2d_presets() -> None:
    """The script is Qt-free and repeats the preset stroke widths; this pins them."""
    from open_garden_planner.core.object_types import (
        PATH_FENCE_STYLES,
        ObjectType,
        PathFenceStyle,
        get_style,
    )

    script = _script()
    for name, width in script.STROKE_CM.items():
        if name == "WALL":
            assert get_style(ObjectType.WALL).stroke_width == width
        else:
            assert PATH_FENCE_STYLES[PathFenceStyle[name]].stroke_width == width
    cases = {case.name: case for case in script.cases()}
    assert cases["wall"].record.path_width_cm == 4.0
    assert cases["wall"].record.height_cm == 200.0 and cases["picket fence"].record.height_cm == 120.0
