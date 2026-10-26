"""Mathematical invariants of translations, independent of Qt and the solver."""

import pytest

from open_garden_planner.agent_api.constraint_policy import (
    CLASSIFIED_TYPES,
    TRANSLATION_FREE_TYPES,
    translation_preserves_constraint,
)


def relation(kind, a="a", b="b", c="c"):
    return {"constraint_type": kind, "anchor_a": {"item_id": a},
            "anchor_b": {"item_id": b}, "anchor_c": {"item_id": c}}


def test_classification_covers_the_actual_enum():
    from open_garden_planner.core.constraints import ConstraintType

    assert {kind.name for kind in ConstraintType} == CLASSIFIED_TYPES


@pytest.mark.parametrize("kind", sorted(TRANSLATION_FREE_TYPES))
def test_size_and_orientation_are_independent_of_translation(kind):
    assert translation_preserves_constraint(relation(kind), {"a": (40, -25)})
    assert translation_preserves_constraint(relation(kind), {"a": (40, -25), "b": (-7, 12)})


@pytest.mark.parametrize("kind", ["DISTANCE", "EDGE_LENGTH", "COINCIDENT", "POINT_ON_CIRCLE"])
def test_pair_relations_need_a_common_translation_but_local_dimensions_are_free(kind):
    assert not translation_preserves_constraint(relation(kind), {"a": (40, 25)})
    assert translation_preserves_constraint(relation(kind), {"a": (40, 25), "b": (40, 25)})
    assert translation_preserves_constraint(relation(kind, a="a", b="a"), {"a": (40, 25)})


@pytest.mark.parametrize("kind", ["ANGLE", "POINT_ON_EDGE", "TANGENT"])
def test_third_anchor_is_not_forgotten(kind):
    assert not translation_preserves_constraint(relation(kind), {"a": (40, 25), "b": (40, 25)})
    assert translation_preserves_constraint(relation(kind), dict.fromkeys("abc", (40, 25)))
    assert translation_preserves_constraint(relation(kind, a="a", b="a", c="a"), {"a": (40, 25)})
    malformed = relation(kind)
    malformed.pop("anchor_c")
    assert not translation_preserves_constraint(malformed, dict.fromkeys("abc", (40, 25)))


@pytest.mark.parametrize(("kind", "free", "blocked"), [
    ("HORIZONTAL", (5, 0), (0, 5)),
    ("VERTICAL_DISTANCE", (5, 0), (0, 5)),
    ("VERTICAL", (0, 5), (5, 0)),
    ("HORIZONTAL_DISTANCE", (0, 5), (5, 0)),
])
def test_only_the_constrained_axis_is_protected(kind, free, blocked):
    assert translation_preserves_constraint(relation(kind), {"a": free})
    assert not translation_preserves_constraint(relation(kind), {"a": blocked})


@pytest.mark.parametrize(("kind", "free", "blocked", "mirror"), [
    ("SYMMETRY_HORIZONTAL", (5, 0), (0, 5), ((7, 3), (7, -3))),
    ("SYMMETRY_VERTICAL", (0, 5), (5, 0), ((3, 7), (-3, 7))),
])
def test_symmetry_keeps_its_world_axis(kind, free, blocked, mirror):
    assert translation_preserves_constraint(relation(kind), {"a": free, "b": free})
    assert not translation_preserves_constraint(relation(kind), {"a": blocked, "b": blocked})
    assert translation_preserves_constraint(relation(kind), {"a": mirror[0], "b": mirror[1]})


def test_fixed_and_axis_guards_have_no_tolerance_band_for_incremental_creep():
    for tiny in (1e-12, 1e-7, 0.001):
        assert not translation_preserves_constraint(relation("FIXED"), {"a": (tiny, 0)})
        assert not translation_preserves_constraint(relation("HORIZONTAL"), {"a": (0, tiny)})
    assert translation_preserves_constraint(relation("FIXED"), {"b": (5, 7)})


def test_unknown_or_nonfinite_motion_fails_closed():
    assert not translation_preserves_constraint(relation("FUTURE_TYPE"), {"a": (5, 7)})
    for invalid in (float("nan"), float("inf"), -float("inf")):
        assert not translation_preserves_constraint(relation("EDGE_LENGTH"), {"a": (invalid, 0)})
