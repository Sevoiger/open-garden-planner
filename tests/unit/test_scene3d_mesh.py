"""Mesh container, mesh parts and the builder seam (Phase 17 L1.1, #385, gate G7).

Qt-free. ``Mesh.validate()`` must catch each defect class with one failing case
each — including the L0 "wrong roof slope lit" bug (a flat face storing a normal
that is not its winding normal) — and pass everything the default builder emits.
The meshes of REAL canvas items are validated in
``tests/integration/test_scene3d_snapshot.py``.
"""

from __future__ import annotations

import dataclasses
import math
import re
from pathlib import Path

import numpy as np
import pytest

from open_garden_planner.core.scene3d import (
    BENT_NORMAL_KINDS,
    HOLE_COVER_LIMIT,
    MATERIAL_KINDS,
    PLAN_MARGIN_CM,
    BuilderContractError,
    BuilderRegistry,
    Material,
    Mesh,
    MeshError,
    MeshPart,
    Params,
    Record,
    Transform,
    default_builder,
    item_seed,
    normal_vs_winding,
    plan_overreach,
    point_in_regions,
    prism_mesh,
    srgb_to_linear,
    verify_builder,
)

SQUARE = ((-50.0, -50.0), (50.0, -50.0), (50.0, 50.0), (-50.0, 50.0))
L_SHAPE = ((0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (50.0, 50.0), (50.0, 100.0), (0.0, 100.0))
BOW_TIE = ((0.0, 0.0), (400.0, 300.0), (400.0, 0.0), (0.0, 300.0))
# a frame 10 m x 8 m with a 10 cm rim — the plan view of a closed fence
FRAME = ((-505.0, -405.0), (505.0, -405.0), (505.0, 405.0), (-505.0, 405.0))
FRAME_HOLE = ((-495.0, -395.0), (-495.0, 395.0), (495.0, 395.0), (495.0, -395.0))  # clockwise
FRAME_AREA = 1010.0 * 810.0 - 990.0 * 790.0  # 36,000 cm²
REPO = Path(__file__).resolve().parents[2]


def cap_area(mesh: Mesh) -> float:
    """Plan-view area of the upward-facing triangles of a mesh, in cm²."""
    p = mesh.positions.astype(np.float64)
    a, b, c = (p[mesh.indices[:, k]] for k in range(3))
    z = np.cross(b - a, c - a)[:, 2]
    return float(z[z > 1e-9].sum() / 2.0)


def assert_walls_face_away_from_the_solid(mesh: Mesh, regions) -> None:
    """Step a hair along each wall normal: outside the footprint. A hair back:
    inside. True for outer walls and for the walls around a hole alike."""
    p = mesh.positions.astype(np.float64)
    n = mesh.normals.astype(np.float64)
    walls = 0
    for tri in mesh.indices:
        normal = n[tri[0]]
        if abs(normal[2]) > 0.5:
            continue  # the cap
        mid = p[tri].mean(axis=0)
        assert not point_in_regions(mid[0] + 0.01 * normal[0], mid[1] + 0.01 * normal[1], regions)
        assert point_in_regions(mid[0] - 0.01 * normal[0], mid[1] - 0.01 * normal[1], regions)
        walls += 1
    assert walls > 0


def triangle(normal: tuple[float, float, float] = (0.0, 0.0, 1.0)) -> Mesh:
    """One CCW-from-above triangle in the ground plane (winding normal = +up)."""
    return Mesh.from_arrays(
        positions=[[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 10.0, 0.0]],
        normals=[normal] * 3,
        colors=[[1.0, 1.0, 1.0, 1.0]] * 3,
        uvs=[[0.0, 0.0]] * 3,
        indices=[0, 1, 2],
    )


def record(**changes: object) -> Record:
    base = Record(
        item_id="11111111-2222-3333-4444-555555555555",
        kind="RAISED_BED",
        shape="rectangle",
        footprints=(SQUARE,),
        transform=Transform(100.0, 200.0, 30.0),
        material=Material("RAISED_BED", fill_rgba=(120, 80, 40, 255)),
        height_cm=40.0,
        params=Params({"seed": "11111111-2222-3333-4444-555555555555"}),
        name="Bed",
    )
    return dataclasses.replace(base, **changes) if changes else base


# ── Mesh ──────────────────────────────────────────────────────────────────────


class TestMesh:
    def test_from_arrays_coerces_dtypes_and_shapes(self) -> None:
        mesh = triangle()
        assert mesh.positions.dtype == np.float32 and mesh.positions.shape == (3, 3)
        assert mesh.normals.dtype == np.float32 and mesh.normals.shape == (3, 3)
        assert mesh.colors.dtype == np.float32 and mesh.colors.shape == (3, 4)
        assert mesh.uvs.dtype == np.float32 and mesh.uvs.shape == (3, 2)
        assert mesh.indices.dtype == np.uint32 and mesh.indices.shape == (1, 3)
        assert (mesh.vertex_count, mesh.triangle_count) == (3, 1)
        mesh.validate()

    def test_flat_and_triple_indices_are_both_accepted(self) -> None:
        """The spike's ``MeshData`` keeps indices flat (M,); contract v2 stores
        (m, 3). ``from_arrays`` takes either, so a ported builder needs no reshape."""
        flat = Mesh.from_arrays([[0, 0, 0]] * 6, [[0, 0, 1]] * 6, [[1, 1, 1, 1]] * 6,
                                [[0, 0]] * 6, [0, 1, 2, 3, 4, 5])
        triples = Mesh.from_arrays([[0, 0, 0]] * 6, [[0, 0, 1]] * 6, [[1, 1, 1, 1]] * 6,
                                   [[0, 0]] * 6, [[0, 1, 2], [3, 4, 5]])
        assert flat == triples and flat.indices.shape == (2, 3)

    def test_arrays_are_read_only(self) -> None:
        mesh = triangle()
        with pytest.raises(ValueError):
            mesh.positions[0, 0] = 99.0
        with pytest.raises(dataclasses.FrozenInstanceError):
            mesh.positions = mesh.normals  # type: ignore[misc]

    def test_empty(self) -> None:
        empty = Mesh.empty()
        assert (empty.vertex_count, empty.triangle_count) == (0, 0)
        empty.validate()
        assert empty.indices.shape == (0, 3)
        with pytest.raises(MeshError, match="empty"):
            empty.bounds()

    def test_bounds(self) -> None:
        low, high = prism_mesh((SQUARE,), 40.0).bounds()
        assert low.tolist() == [-50.0, -50.0, 0.0] and high.tolist() == [50.0, 50.0, 40.0]

    def test_equality_is_by_content(self) -> None:
        assert triangle() == triangle()
        assert triangle() != triangle((0.0, 1.0, 0.0))
        assert triangle() != "a mesh"
        with pytest.raises(TypeError):
            hash(triangle())

    def test_merge_offsets_indices_and_skips_empties(self) -> None:
        a, b = triangle(), prism_mesh((SQUARE,), 10.0)
        merged = Mesh.merge([a, Mesh.empty(), b])
        assert merged.vertex_count == a.vertex_count + b.vertex_count
        assert merged.triangle_count == a.triangle_count + b.triangle_count
        np.testing.assert_array_equal(merged.indices[:1], a.indices)
        np.testing.assert_array_equal(merged.indices[1:], b.indices + a.vertex_count)
        np.testing.assert_array_equal(merged.positions[a.vertex_count:], b.positions)
        merged.validate()
        assert Mesh.merge([]) == Mesh.empty() and Mesh.merge([Mesh.empty()]) == Mesh.empty()

    def test_translated_moves_positions_only(self) -> None:
        moved = triangle().translated(5.0, -3.0, 2.0)
        assert moved.positions[0].tolist() == [5.0, -3.0, 2.0]
        np.testing.assert_array_equal(moved.normals, triangle().normals)
        moved.validate()


# one failing case per defect class the brief names ----------------------------


def _replace(mesh: Mesh, **arrays: np.ndarray) -> Mesh:
    return dataclasses.replace(mesh, **arrays)


class TestValidate:
    def test_non_finite_position(self) -> None:
        positions = triangle().positions.copy()
        positions[1, 2] = np.nan
        with pytest.raises(MeshError, match="non-finite.*positions"):
            _replace(triangle(), positions=positions).validate()

    @pytest.mark.parametrize("name", ["normals", "colors", "uvs"])
    def test_non_finite_attribute(self, name: str) -> None:
        array = getattr(triangle(), name).copy()
        array[0, 0] = np.inf
        with pytest.raises(MeshError, match=f"non-finite.*{name}"):
            _replace(triangle(), **{name: array}).validate()

    def test_index_out_of_bounds(self) -> None:
        with pytest.raises(MeshError, match="index 3 .* 3 vertices"):
            _replace(triangle(), indices=np.array([[0, 1, 3]], np.uint32)).validate()

    def test_wrong_dtype(self) -> None:
        with pytest.raises(MeshError, match="positions.*float32.*float64"):
            _replace(triangle(), positions=triangle().positions.astype(np.float64)).validate()
        with pytest.raises(MeshError, match="indices.*uint32.*int64"):
            _replace(triangle(), indices=np.array([[0, 1, 2]], np.int64)).validate()

    def test_wrong_shape(self) -> None:
        with pytest.raises(MeshError, match=r"colors.*\(3, 4\).*\(3, 3\)"):
            _replace(triangle(), colors=np.ones((3, 3), np.float32)).validate()
        with pytest.raises(MeshError, match=r"indices.*\(m, 3\)"):
            _replace(triangle(), indices=np.array([0, 1, 2], np.uint32)).validate()

    def test_attribute_count_mismatch(self) -> None:
        with pytest.raises(MeshError, match=r"normals.*\(3, 3\).*\(2, 3\)"):
            _replace(triangle(), normals=np.tile([0.0, 0.0, 1.0], (2, 1)).astype(np.float32)).validate()

    def test_non_unit_normal(self) -> None:
        with pytest.raises(MeshError, match="unit length.*2.0"):
            triangle((0.0, 0.0, 2.0)).validate()
        with pytest.raises(MeshError, match="unit length"):
            triangle((0.0, 0.0, 0.0)).validate()

    def test_flat_face_lit_from_the_wrong_side(self) -> None:
        """The L0 roof bug: the face is wound to look up, its stored normal looks
        down (or sideways) — the sun would light the wrong face."""
        with pytest.raises(MeshError, match=r"flat face.*-1\.0"):
            triangle((0.0, 0.0, -1.0)).validate()
        tilted = (0.0, 0.2, 0.9797959)  # 11.5° off: dot = 0.9798 < 0.99
        with pytest.raises(MeshError, match="flat face"):
            triangle(tilted).validate()
        almost = (0.0, 0.1, 0.99498744)  # 5.7° off: dot = 0.995 — inside the gate
        triangle(almost).validate()

    def test_bent_normal_kinds_are_exempt_from_the_winding_rule(self) -> None:
        """Foliage and grass shade with spherized normals by design
        (``ogp-lush-cinematic`` §2): their flat faces need not store the winding normal."""
        leaf = triangle((0.0, 0.0, -1.0))
        leaf.validate(check_winding=False)
        MeshPart(leaf, "foliage").validate()
        MeshPart(leaf, "grass").validate()
        with pytest.raises(MeshError, match="flat face"):
            MeshPart(leaf, "vc").validate()
        assert frozenset({"foliage", "grass"}) == BENT_NORMAL_KINDS <= MATERIAL_KINDS

    def test_degenerate_triangles_are_ignored_by_the_winding_rule(self) -> None:
        sliver = Mesh.from_arrays(
            [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [20.0, 0.0, 0.0]], [[0.0, 0.0, -1.0]] * 3,
            [[1, 1, 1, 1]] * 3, [[0, 0]] * 3, [0, 1, 2])
        sliver.validate()

    def test_smooth_faces_are_not_judged_as_flat(self) -> None:
        normals = np.array([[0.0, 0.6, 0.8], [0.6, 0.0, 0.8], [0.0, 0.0, 1.0]], np.float32)
        smooth = _replace(triangle(), normals=normals)
        dots, flat = normal_vs_winding(smooth)
        assert not flat.any() and dots[0] > 0.9
        smooth.validate()


# ── MeshPart ──────────────────────────────────────────────────────────────────


class TestMeshPart:
    def test_defaults(self) -> None:
        part = MeshPart(triangle())
        assert (part.material_kind, part.casts_shadow, part.tinted, part.name) == ("vc", True, False, "")

    def test_unknown_material_kind_is_refused(self) -> None:
        with pytest.raises(ValueError, match="material_kind 'marble'"):
            MeshPart(triangle(), "marble")

    def test_material_kinds_are_the_spike_s(self) -> None:
        """Drift guard (dies with the spike in L1.10): the kinds the L0 scene file
        switches on, plus its default ``vc``; ``white`` is the IoU probe's, not a
        builder's."""
        qml = (REPO / "src/open_garden_planner/spike_q3d/qml/GardenSpike.qml").read_text("utf-8")
        switched = set(re.findall(r'modelData\.kind === "(\w+)"', qml))
        assert switched - {"white"} | {"vc"} == MATERIAL_KINDS
        assert frozenset({"vc", "roof", "foliage", "grass", "glass", "water"}) == MATERIAL_KINDS

    def test_parts_compare_by_content(self) -> None:
        assert MeshPart(triangle(), "roof", False) == MeshPart(triangle(), "roof", False)
        assert MeshPart(triangle(), "roof") != MeshPart(triangle(), "vc")


# ── ports from the spike: one rule each, until L1.10 ──────────────────────────


class TestSpikeDriftGuards:
    def test_srgb_to_linear(self) -> None:
        from open_garden_planner.spike_q3d import meshes as spike

        for colour in ("#000000", "#ffffff", "#b4553d", "#487f34", "#0a0b0c", "#4d92c5"):
            np.testing.assert_array_equal(srgb_to_linear(colour), spike.srgb_to_linear(colour))
        with pytest.raises(ValueError):
            srgb_to_linear("b4553d")

    def test_item_seed(self) -> None:
        from open_garden_planner.spike_q3d import meshes as spike

        for item_id in ("11111111-2222-3333-4444-555555555555", "", "x"):
            for salt in ("", "leaves"):
                assert item_seed(item_id, salt) == spike.item_seed(item_id, salt)
        assert item_seed("a") != item_seed("b") and item_seed("a") != item_seed("a", "s")

    def test_normal_vs_winding(self) -> None:
        from open_garden_planner.spike_q3d import meshes as spike

        ours = prism_mesh((L_SHAPE,), 120.0)
        theirs = spike.prism(list(L_SHAPE), 120.0, 0.0, "#ffffff")
        dots, flat = normal_vs_winding(ours)
        spike_dots, spike_flat = spike.normal_vs_winding(theirs)
        np.testing.assert_array_equal(dots, spike_dots)
        np.testing.assert_array_equal(flat, spike_flat)
        np.testing.assert_array_equal(ours.positions, theirs.positions)
        np.testing.assert_array_equal(ours.normals, theirs.normals)


# ── the default builder (honest v0) ───────────────────────────────────────────


class TestDefaultBuilder:
    @pytest.mark.parametrize("height", [40.0, 0.1, 123.456789, 450.0, 1e-3, 33.3])
    def test_prism_top_is_exactly_the_height(self, height: float) -> None:
        (part,) = default_builder(record(height_cm=height))
        low, high = part.mesh.bounds()
        assert float(high[2]) == float(np.float32(height))  # exact, in the float32 the engine gets
        assert float(low[2]) == 0.0  # the item's own ground plane: base_cm is the transform's
        part.validate()

    def test_footprint_is_the_local_footprint(self) -> None:
        (part,) = default_builder(record(footprints=(L_SHAPE,)))
        low, high = part.mesh.bounds()
        assert low[:2].tolist() == [0.0, 0.0] and high[:2].tolist() == [100.0, 100.0]
        # 6 walls x 2 triangles + 4 cap triangles (n - 2 for a simple polygon)
        assert part.mesh.triangle_count == 16
        dots, flat = normal_vs_winding(part.mesh)
        assert flat.all() and dots.min() >= 0.99

    def test_prism_mesh_takes_a_clockwise_ring_and_still_faces_out(self) -> None:
        """``Record`` refuses a clockwise outer ring; the geometry helper under it
        stays tolerant, as the Qt 3D extrusion it mirrors is."""
        mesh = prism_mesh((tuple(reversed(L_SHAPE)),), 40.0)
        mesh.validate()
        assert mesh.triangle_count == prism_mesh((L_SHAPE,), 40.0).triangle_count == 16
        assert cap_area(mesh) == pytest.approx(7500.0)

    def test_part_is_neutral_and_tinted_by_the_sink(self) -> None:
        """A pure colour change must not rebuild geometry: the mesh carries no
        colour of the item, and the part asks the sink to tint it."""
        (part,) = default_builder(record())
        assert part.tinted is True and part.material_kind == "vc" and part.casts_shadow is True
        assert np.all(part.mesh.colors == 1.0)
        recoloured = record(material=Material("RAISED_BED", fill_rgba=(1, 2, 3, 255)))
        assert default_builder(recoloured) == (part,)

    def test_no_height_means_no_parts(self) -> None:
        """A lawn, a path, a rain barrel: the resolver gives no height, so there is
        no solid — the ground bake (L1.5) paints flat surfaces."""
        assert default_builder(record(height_cm=None)) == ()

    def test_several_rings_become_one_part(self) -> None:
        other = tuple((x + 300.0, y) for x, y in SQUARE)
        (part,) = default_builder(record(shape="polyline", footprints=(SQUARE, other),
                                         path=((-50.0, 0.0), (350.0, 0.0)), path_width_cm=100.0))
        assert part.mesh.triangle_count == 2 * (8 + 2)
        part.validate()

    def test_degenerate_footprints_give_no_parts(self) -> None:
        assert default_builder(record(footprints=())) == ()
        assert prism_mesh((SQUARE[:2],), 40.0) == Mesh.empty()
        assert prism_mesh((((0.0, 0.0), (5.0, 0.0), (9.0, 0.0)),), 40.0) == Mesh.empty()
        assert prism_mesh((SQUARE,), 0.0) == Mesh.empty()

    # ── holes (fix round 1, P1-1) ─────────────────────────────────────────────

    def test_a_hole_is_cut_out_of_the_cap(self) -> None:
        """The closed fence: 3.6 m² of stroke, not the 80 m² it encloses."""
        (part,) = default_builder(record(footprints=(FRAME,), holes=((FRAME_HOLE,),)))
        part.validate()
        assert cap_area(part.mesh) == pytest.approx(FRAME_AREA, rel=1e-6)
        assert cap_area(part.mesh) < 0.05 * 1010.0 * 810.0
        # 4 outer walls + 4 hole walls, two triangles each, + a cap of 4 + 4 + 2 - 2
        assert part.mesh.triangle_count == 16 + 8
        assert float(part.mesh.bounds()[1][2]) == 40.0

    def test_walls_face_away_from_the_solid_on_both_sides(self) -> None:
        holed = record(footprints=(FRAME,), holes=((FRAME_HOLE,),))
        (part,) = default_builder(holed)
        assert_walls_face_away_from_the_solid(part.mesh, holed.regions)
        (plain,) = default_builder(record(footprints=(L_SHAPE,)))
        assert_walls_face_away_from_the_solid(plain.mesh, record(footprints=(L_SHAPE,)).regions)

    def test_prism_mesh_without_the_holes_argument_fills_them(self) -> None:
        """Why ``holes`` must be passed along: the outer ring alone IS the slab."""
        slab = prism_mesh((FRAME,), 40.0)
        assert cap_area(slab) == pytest.approx(1010.0 * 810.0, rel=1e-6)
        with pytest.raises(ValueError, match="2 hole groups for 1 footprint"):
            prism_mesh((FRAME,), 40.0, ((FRAME_HOLE,), ()))

    def test_a_hole_free_ring_is_still_the_qt3d_prism_vertex_for_vertex(self) -> None:
        from open_garden_planner.core.scene3d import extrude_footprint

        positions, normals = extrude_footprint(list(L_SHAPE), 120.0)
        mesh = prism_mesh((L_SHAPE,), 120.0)
        np.testing.assert_array_equal(mesh.positions, np.array(positions, np.float32).reshape(-1, 3))
        np.testing.assert_array_equal(mesh.normals, np.array(normals, np.float32).reshape(-1, 3))

    # ── self-crossing rings (fix round 1, P2-6) ───────────────────────────────

    def test_a_bow_tie_is_refused_not_built_wrong(self) -> None:
        """Before: the ear clipper produced a cap wound the wrong way, validate()
        failed and production (validate=False) shipped it. Now the builder says no."""
        with pytest.raises(ValueError, match="not a simple polygon"):
            prism_mesh((BOW_TIE,), 40.0)
        lopsided = ((0.0, 0.0), (400.0, 300.0), (400.0, 0.0), (0.0, 200.0))[::-1]
        with pytest.raises(ValueError, match="not a simple polygon"):
            default_builder(record(shape="polygon", footprints=(lopsided,)))

    def test_output_does_not_depend_on_pose_material_or_name(self) -> None:
        verify_builder(default_builder, record())
        verify_builder(default_builder, record(height_cm=None))


# ── registry ──────────────────────────────────────────────────────────────────


def _roof_builder(rec: Record) -> tuple[MeshPart, ...]:
    return (MeshPart(prism_mesh(rec.footprints, 10.0), "roof"),)


class TestRegistry:
    def test_unknown_kind_falls_back_to_the_default(self) -> None:
        registry = BuilderRegistry()
        assert registry.builder_for("ANYTHING") is default_builder
        assert registry.build(record()) == default_builder(record())
        assert registry.kinds() == frozenset()

    def test_registered_builder_wins_for_its_kind_only(self) -> None:
        registry = BuilderRegistry()
        registry.register("HOUSE", _roof_builder)
        assert registry.builder_for("HOUSE") is _roof_builder
        assert registry.builder_for("TOOL_SHED") is default_builder
        assert registry.build(record(kind="HOUSE"))[0].material_kind == "roof"
        assert registry.kinds() == frozenset({"HOUSE"})

    def test_double_registration_is_an_error_unless_asked_for(self) -> None:
        registry = BuilderRegistry()
        registry.register("HOUSE", _roof_builder)
        with pytest.raises(ValueError, match="HOUSE"):
            registry.register("HOUSE", default_builder)
        registry.register("HOUSE", default_builder, replace=True)
        assert registry.builder_for("HOUSE") is default_builder

    def test_register_many_and_custom_default(self) -> None:
        registry = BuilderRegistry(default=_roof_builder)
        registry.register_many(("RAISED_BED", "CONTAINER"), default_builder)
        assert registry.builder_for("TREE") is _roof_builder
        assert registry.builder_for("CONTAINER") is default_builder


# ── verify_builder: the test instrument L1.6 / L1.7 use ───────────────────────


class TestVerifyBuilder:
    def test_returns_the_parts(self) -> None:
        assert verify_builder(default_builder, record()) == default_builder(record())

    def test_a_builder_reading_the_material_is_caught(self) -> None:
        def bad(rec: Record) -> tuple[MeshPart, ...]:
            height = 10.0 if rec.material.fill_rgba == (120, 80, 40, 255) else 20.0
            return (MeshPart(prism_mesh(rec.footprints, height)),)

        with pytest.raises(BuilderContractError, match="material"):
            verify_builder(bad, record())

    def test_a_builder_reading_the_transform_is_caught(self) -> None:
        def bad(rec: Record) -> tuple[MeshPart, ...]:
            mesh = prism_mesh(rec.footprints, 10.0).translated(rec.transform.east_cm, 0.0, 0.0)
            return (MeshPart(mesh),)

        with pytest.raises(BuilderContractError, match="transform"):
            verify_builder(bad, record())

    @pytest.mark.parametrize("field", ["item_id", "name", "parent_id"])
    def test_a_builder_reading_an_unsigned_field_is_caught(self, field: str) -> None:
        def bad(rec: Record) -> tuple[MeshPart, ...]:
            return (MeshPart(prism_mesh(rec.footprints, 10.0 + len(str(getattr(rec, field))))),)

        with pytest.raises(BuilderContractError, match=field):
            verify_builder(bad, record())

    def test_a_non_deterministic_builder_is_caught(self) -> None:
        counter = iter(range(1, 100))

        def bad(rec: Record) -> tuple[MeshPart, ...]:
            return (MeshPart(prism_mesh(rec.footprints, float(next(counter)))),)

        with pytest.raises(BuilderContractError, match="deterministic"):
            verify_builder(bad, record())

    def test_an_invalid_mesh_is_caught(self) -> None:
        def bad(_rec: Record) -> tuple[MeshPart, ...]:
            return (MeshPart(triangle((0.0, 0.0, -1.0))),)

        with pytest.raises(BuilderContractError, match="flat face"):
            verify_builder(bad, record())

    def test_a_wrong_return_type_is_caught(self) -> None:
        with pytest.raises(BuilderContractError, match="tuple of MeshPart"):
            verify_builder(lambda _rec: [MeshPart(triangle())], record())  # type: ignore[arg-type,return-value]
        with pytest.raises(BuilderContractError, match="tuple of MeshPart"):
            verify_builder(lambda _rec: (triangle(),), record())  # type: ignore[arg-type,return-value]

    def test_a_solid_taller_than_the_plan_says_is_caught(self) -> None:
        """Truth gate "built heights" (``ogp-lush-cinematic`` §1): the top of a
        shadow-casting item is its resolved height — never above it."""
        def bad(rec: Record) -> tuple[MeshPart, ...]:
            return (MeshPart(prism_mesh(rec.footprints, 46.0)),)  # +15 % decoration on top

        with pytest.raises(BuilderContractError, match="46.*40"):
            verify_builder(bad, record())
        verify_builder(bad, record(), height_tolerance=0.2)  # an explicit allowance is the caller's

    def test_height_tolerance_in_centimetres(self) -> None:
        """Issue #385's L1.6 gate is ±0.5 cm ABSOLUTE: on a 450 cm house the default
        1 % would let 4.5 cm through."""
        def builder_off_by(delta: float):
            def build(rec: Record) -> tuple[MeshPart, ...]:
                assert rec.height_cm is not None
                return (MeshPart(prism_mesh(rec.footprints, rec.height_cm + delta)),)
            return build

        house = record(height_cm=450.0)
        verify_builder(builder_off_by(4.0), house)  # inside 1 %
        with pytest.raises(BuilderContractError, match="454.*450.*±0.5 cm"):
            verify_builder(builder_off_by(4.0), house, height_tolerance_cm=0.5)
        verify_builder(builder_off_by(0.4), house, height_tolerance_cm=0.5)
        with pytest.raises(BuilderContractError, match="449.4"):
            verify_builder(builder_off_by(-0.6), house, height_tolerance_cm=0.5)

    # ── the plan-view check (fix round 1, P1-1) ───────────────────────────────

    def test_a_builder_that_fills_a_hole_is_caught(self) -> None:
        """The pre-fix default builder, reconstructed: every ring a solid. It passed
        every gate; now it cannot."""
        def every_ring_a_solid(rec: Record) -> tuple[MeshPart, ...]:
            assert rec.height_cm is not None
            rings = [*rec.footprints, *(hole for group in rec.holes for hole in group)]
            return (MeshPart(prism_mesh(rings, rec.height_cm)),)

        fence = record(footprints=(FRAME,), holes=((FRAME_HOLE,),))
        with pytest.raises(BuilderContractError, match="covers 100% of a hole"):
            verify_builder(every_ring_a_solid, fence)

        def outer_ring_only(rec: Record) -> tuple[MeshPart, ...]:
            assert rec.height_cm is not None
            return (MeshPart(prism_mesh(rec.footprints, rec.height_cm)),)  # forgot rec.holes

        with pytest.raises(BuilderContractError, match="covers 100% of a hole"):
            verify_builder(outer_ring_only, fence)
        verify_builder(default_builder, fence)
        verify_builder(default_builder, fence, plan_margin_cm=0.0)
        assert HOLE_COVER_LIMIT == 0.5

    def test_a_small_hole_is_protected_too(self) -> None:
        """The limit is a share of the hole, not a distance: a fence around a
        60 cm planter is caught like one around a garden."""
        outer = ((-40.0, -40.0), (40.0, -40.0), (40.0, 40.0), (-40.0, 40.0))
        hole = ((-30.0, -30.0), (-30.0, 30.0), (30.0, 30.0), (30.0, -30.0))
        small = record(footprints=(outer,), holes=((hole,),))

        def filled(rec: Record) -> tuple[MeshPart, ...]:
            return (MeshPart(prism_mesh(rec.footprints, 40.0)),)

        with pytest.raises(BuilderContractError, match="of a hole"):
            verify_builder(filled, small)

    def test_a_hole_under_two_huge_triangles_is_seen(self) -> None:
        """The hole is sampled, not the triangles: a slab of two triangles a
        hundred times the size of the hole it covers has no sample point of its
        own inside that hole, and is caught all the same."""
        outer = ((-500.0, -500.0), (500.0, -500.0), (500.0, 500.0), (-500.0, 500.0))
        hole = ((80.0, 210.0), (80.0, 290.0), (160.0, 290.0), (160.0, 210.0))
        well = record(footprints=(outer,), holes=((hole,),))
        slab = (MeshPart(prism_mesh(well.footprints, 40.0)),)
        assert plan_overreach(slab, well.regions, 0.0)[1] == 1.0
        with pytest.raises(BuilderContractError, match="covers 100% of a hole"):
            verify_builder(lambda rec: (MeshPart(prism_mesh(rec.footprints, 40.0)),), well)
        verify_builder(default_builder, well)

    def test_half_a_hole_covered_is_half(self) -> None:
        outer = ((-100.0, -100.0), (100.0, -100.0), (100.0, 100.0), (-100.0, 100.0))
        hole = ((-50.0, -50.0), (-50.0, 50.0), (50.0, 50.0), (50.0, -50.0))
        half = record(footprints=(outer,), holes=((hole,),))
        west = ((-100.0, -100.0), (0.0, -100.0), (0.0, 100.0), (-100.0, 100.0))
        parts = (MeshPart(prism_mesh((west,), 40.0)),)
        assert plan_overreach(parts, half.regions, 0.0)[1] == pytest.approx(0.5, abs=0.03)

    def test_reaching_beyond_the_footprint_needs_saying(self) -> None:
        def with_eaves(overhang: float):
            def build(rec: Record) -> tuple[MeshPart, ...]:
                ring = tuple((x + math.copysign(overhang, x), y + math.copysign(overhang, y))
                             for x, y in rec.footprints[0])
                return (MeshPart(prism_mesh((ring,), 40.0)),)
            return build

        verify_builder(with_eaves(35.0), record())  # inside the default reach
        assert PLAN_MARGIN_CM == 50.0
        with pytest.raises(BuilderContractError, match="more than 50 cm outside"):
            verify_builder(with_eaves(120.0), record())
        with pytest.raises(BuilderContractError, match="more than 0 cm outside"):
            verify_builder(with_eaves(35.0), record(), plan_margin_cm=0.0)
        verify_builder(with_eaves(120.0), record(), plan_margin_cm=130.0)
        verify_builder(with_eaves(120.0), record(), plan_margin_cm=None)  # switched off

    def test_plan_overreach_numbers(self) -> None:
        fence = record(footprints=(FRAME,), holes=((FRAME_HOLE,),))
        good = default_builder(fence)
        assert plan_overreach(good, fence.regions, 0.0) == (0.0, 0.0)
        slab = (MeshPart(prism_mesh(fence.footprints, 40.0)),)
        beyond, cover = plan_overreach(slab, fence.regions, 0.0)
        assert cover == 1.0
        # ``beyond`` is the four-points-per-triangle estimate: the slab's cap is two
        # triangles whose every sample lands in the hole, so it reads the whole cap
        # (1010 x 810) where the truth is the hole (990 x 790) — a net for gross
        # errors, as its docstring says, and here it is on the safe side.
        assert 990.0 * 790.0 <= beyond <= 1010.0 * 810.0 + 1e-6
        assert plan_overreach((), fence.regions, 0.0) == (0.0, 0.0)
