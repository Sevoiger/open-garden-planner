"""Mutation check for the 3D scene pipeline (Phase 17 L1.1, #385, ADR-054).

A green suite says the tests pass. It does not say they would FAIL if the code
were wrong — the first version of this pipeline passed every gate while turning
a closed fence into a slab over the whole garden. This script asks the second
question directly: it breaks ONE rule of the pipeline at a time and runs the
tests that are supposed to notice. Every mutant must be KILLED (the tests fail);
a SURVIVED mutant is a rule no test pins.

How it works: each mutant is one textual replacement in one source file. The
file is restored from an in-memory copy in a ``finally`` block, the script
refuses to start when ``src/`` has uncommitted changes (so a crash can never be
mistaken for your work, and ``git checkout -- src`` is always a safe way back),
and a pattern that no longer matches exactly once is reported as STALE and
counts as a failure — a mutant that silently stopped mutating proves nothing.

It is NOT part of the test suite (it rewrites source files and takes a few
minutes). Run it by hand after changing ``core/scene3d/``, ``ui/canvas/footprints.py``
or ``ui/view3d/snapshot.py``, or their tests::

    venv/Scripts/python.exe scripts/mutation_check_scene3d.py            # all mutants
    venv/Scripts/python.exe scripts/mutation_check_scene3d.py --list     # names only
    venv/Scripts/python.exe scripts/mutation_check_scene3d.py -k hole    # a subset

Exit code 0: every mutant killed. 1: a mutant survived or is stale. 2: refused.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = "src/open_garden_planner/"
U = "tests/unit/"
I = "tests/integration/"  # noqa: E741  (a path prefix, read next to U)

CONTRACT = U + "test_scene3d_contract.py"
FRAME = U + "test_scene3d_frame.py"
MESH = U + "test_scene3d_mesh.py"
POLYGON = U + "test_scene3d_polygon.py"
SYNC = U + "test_scene3d_sync.py"
SNAPSHOT = I + "test_scene3d_snapshot.py"
PIPELINE = I + "test_scene3d_pipeline.py"
EDGE_PLAN = I + "test_scene3d_edge_plan.py"
GOLDEN = I + "test_footprints_golden.py"


@dataclass(frozen=True)
class Mutant:
    """One broken rule: replace ``old`` with ``new`` in ``path``; ``tests`` must fail."""

    label: str
    path: str
    old: str
    new: str
    tests: tuple[str, ...]


_SNAPSHOT_LOOP = "    for item in reversed(items):\n        if not item.isVisible():\n            continue\n"

MUTANTS: tuple[Mutant, ...] = (
    # ── the record and its signatures ────────────────────────────────────────
    Mutant("a signature without the value check (a hash collision hides an edit)",
           SRC + "core/scene3d/record.py",
           "            self.geometry_sig == other.geometry_sig\n"
           "            and self.kind == other.kind\n"
           "            and self.shape == other.shape\n"
           "            and self.height_cm == other.height_cm\n"
           "            and self.path_width_cm == other.path_width_cm\n"
           "            and self.footprints == other.footprints\n"
           "            and self.holes == other.holes\n"
           "            and self.path == other.path\n"
           "            and self.params == other.params\n",
           "            self.geometry_sig == other.geometry_sig\n",
           (CONTRACT,)),
    Mutant("holes are not part of the geometry (a fence that closes keeps its open solid)",
           SRC + "core/scene3d/record.py",
           "            self.height_cm, self.params, self.holes,\n        )))",
           "            self.height_cm, self.params,\n        )))",
           (CONTRACT,)),
    *(Mutant(f"{name} is hashed but not compared",
             SRC + "core/scene3d/record.py",
             f"            and self.{name} == other.{name}\n", "",
             (CONTRACT,))
      for name in ("footprints", "holes", "path", "path_width_cm", "height_cm", "params")),
    Mutant("the transform is hashed but not compared (a move from -1 cm to -2 cm is lost)",
           SRC + "core/scene3d/record.py",
           "        return self.transform_sig == other.transform_sig and self.transform == other.transform\n",
           "        return self.transform_sig == other.transform_sig\n",
           (CONTRACT,)),
    Mutant("the material is hashed but not compared",
           SRC + "core/scene3d/record.py",
           "        return self.material_sig == other.material_sig and self.material == other.material\n",
           "        return self.material_sig == other.material_sig\n",
           (CONTRACT,)),
    Mutant("a clockwise ring is accepted as a footprint",
           SRC + "core/scene3d/record.py",
           "            if not ring_area(ring) > 0.0:\n", "            if False:\n",
           (CONTRACT,)),
    Mutant("a counter-clockwise ring is accepted as a hole",
           SRC + "core/scene3d/record.py",
           "                    if not ring_area(ring) < 0.0:\n", "                    if False:\n",
           (CONTRACT,)),
    Mutant("no length quantisation",
           SRC + "core/scene3d/record.py",
           "LENGTH_DECIMALS: Final = 7\n", "LENGTH_DECIMALS: Final = 15\n",
           (CONTRACT, FRAME, SNAPSHOT)),
    Mutant("no length quantisation: the float dust of a moved house's ridge reaches its geometry",
           SRC + "core/scene3d/record.py",
           "LENGTH_DECIMALS: Final = 7\n", "LENGTH_DECIMALS: Final = 15\n",
           (SNAPSHOT + "::TestHouseRidge::test_a_thousand_moves_never_leak_float_dust_into_the_geometry",)),
    Mutant("quantize_cm is the identity: undo of a move is not the same record",
           SRC + "core/scene3d/record.py",
           "    return round(value, LENGTH_DECIMALS) + 0.0\n", "    return value + 0.0\n",
           (SNAPSHOT + "::TestDiffMatrix::test_undo_dust_near_the_origin_is_absorbed_by_the_record_grid",)),
    # ── frames ───────────────────────────────────────────────────────────────
    Mutant("the engine frame is mirrored (z = +N)",
           SRC + "core/scene3d/frame.py",
           "    engine[..., 2] = -scene[..., 1]\n", "    engine[..., 2] = scene[..., 1]\n",
           (FRAME,)),
    Mutant("the rotation sense is flipped",
           SRC + "core/scene3d/frame.py",
           "    turn = math.degrees(math.atan2(m12, m11))\n",
           "    turn = math.degrees(math.atan2(-m12, m11))\n",
           (FRAME, SNAPSHOT)),
    Mutant("a non-rigid transform is not baked into the local geometry",
           SRC + "core/scene3d/frame.py",
           "        return (quantize_cm(self.m11 * u + self.m21 * v), quantize_cm(self.m12 * u + self.m22 * v))\n",
           "        return (quantize_cm(u), quantize_cm(v))\n",
           (FRAME, SNAPSHOT)),
    # ── meshes and builders ──────────────────────────────────────────────────
    Mutant("validate() without the winding rule",
           SRC + "core/scene3d/mesh.py",
           "        if check_winding and len(self.indices):\n", "        if False and len(self.indices):\n",
           (MESH, SYNC)),
    Mutant("validate() without the index-bounds rule",
           SRC + "core/scene3d/mesh.py",
           "        if len(self.indices) and int(self.indices.max()) >= count:\n",
           "        if False and len(self.indices) and int(self.indices.max()) >= count:\n",
           (MESH,)),
    Mutant("the default builder bakes the colour (a recolour would need a rebuild)",
           SRC + "core/scene3d/build.py",
           'return (MeshPart(mesh, "vc", casts_shadow=True, tinted=True, name="prism"),)',
           'return (MeshPart(mesh, "vc", casts_shadow=True, tinted=False, name="prism"),)',
           (MESH,)),
    Mutant("the default prism is 1 % too tall",
           SRC + "core/scene3d/build.py",
           "    mesh = prism_mesh(record.footprints, record.height_cm, record.holes)\n",
           "    mesh = prism_mesh(record.footprints, record.height_cm * 1.01, record.holes)\n",
           (MESH, SNAPSHOT)),
    Mutant("the default builder ignores the holes (review P1-1: the fence becomes a slab)",
           SRC + "core/scene3d/build.py",
           "    mesh = prism_mesh(record.footprints, record.height_cm, record.holes)\n",
           "    mesh = prism_mesh(record.footprints, record.height_cm)\n",
           (MESH, SNAPSHOT, EDGE_PLAN)),
    Mutant("the cap is triangulated without its holes",
           SRC + "core/scene3d/build.py",
           "        vertices, triangles = triangulate_region(outer, inner)\n",
           "        vertices, triangles = triangulate_region(outer, ())\n",
           (MESH,)),
    Mutant("a hole gets no walls",
           SRC + "core/scene3d/build.py",
           "        for hole in inner:\n            _walls(hole, height_cm, positions, normals)\n", "",
           (MESH,)),
    Mutant("a ring that crosses itself is extruded anyway (review P2-6)",
           SRC + "core/scene3d/build.py",
           "            if not is_simple_ring(ring):\n", "            if False:\n",
           (MESH + "::TestDefaultBuilder::test_a_bow_tie_is_refused_not_built_wrong",)),
    Mutant("verify_builder does not look at holes from above",
           SRC + "core/scene3d/build.py",
           "        if hole_cover_limit is not None and hole_cover > hole_cover_limit:\n",
           "        if False:\n",
           (MESH, SNAPSHOT)),
    Mutant("verify_builder does not look beyond the footprint",
           SRC + "core/scene3d/build.py",
           "        if plan_margin_cm is not None and beyond > max(PLAN_AREA_TOLERANCE * plan_area, 1.0):\n",
           "        if False:\n",
           (MESH,)),
    Mutant("switching the overreach bound off silently switches the hole bound off too",
           SRC + "core/scene3d/build.py",
           "    if (plan_margin_cm is not None or hole_cover_limit is not None) and parts and record.footprints:\n",
           "    if plan_margin_cm is not None and parts and record.footprints:\n",
           (MESH,)),
    Mutant("the hole bound cannot be tuned (the keyword is ignored)",
           SRC + "core/scene3d/build.py",
           "        if hole_cover_limit is not None and hole_cover > hole_cover_limit:\n",
           "        if hole_cover > HOLE_COVER_LIMIT:\n",
           (MESH,)),
    Mutant("verify_builder ignores the tolerance in centimetres",
           SRC + "core/scene3d/build.py",
           "        if height_tolerance_cm is not None:\n", "        if False:\n",
           (MESH,)),
    Mutant("the hole is sampled only at its centre (a half-covered hole reads all or nothing)",
           SRC + "core/scene3d/build.py",
           "    steps = (np.arange(24) + 0.5) / 24.0\n", "    steps = (np.arange(1) + 0.5) / 1.0\n",
           (MESH,)),
    # ── polygons ─────────────────────────────────────────────────────────────
    Mutant("a large ring is never tested for crossings",
           SRC + "core/scene3d/polygon.py",
           "        return not _edges_touch_numpy(ring)\n", "        return True\n",
           (POLYGON,)),
    Mutant("the array version of the simplicity test ignores edges that only touch",
           SRC + "core/scene3d/polygon.py",
           "        if bool(np.any(crossing | touching)):\n", "        if bool(np.any(crossing)):\n",
           (POLYGON,)),
    Mutant("the bounding-box prune drops edges whose boxes only touch",
           SRC + "core/scene3d/polygon.py",
           "                & (low[rows, None, 1] <= high[None, :, 1]) & (low[None, :, 1] <= high[rows, None, 1])\n",
           "                & (low[rows, None, 1] < high[None, :, 1]) & (low[None, :, 1] < high[rows, None, 1])\n",
           (POLYGON,)),
    Mutant("a hole joins the LARGEST outer ring around it",
           SRC + "core/scene3d/polygon.py",
           "                best < 0 or ring_area(outer) < ring_area(outers[best])\n",
           "                best < 0 or ring_area(outer) > ring_area(outers[best])\n",
           (POLYGON,)),
    # ── sink and sync ────────────────────────────────────────────────────────
    Mutant("a builder failure escapes (the transaction is half applied)",
           SRC + "core/scene3d/sync.py",
           "            except Exception as error:  # any builder defect is contained (module docstring)\n",
           "            except ZeroDivisionError as error:  # any builder defect is contained (module docstring)\n",
           (SYNC,)),
    Mutant("a move also calls the builder",
           SRC + "core/scene3d/sync.py",
           "        fresh = (*changes.added, *changes.geometry)\n",
           "        fresh = (*changes.added, *changes.geometry, *changes.transform)\n",
           (SYNC, PIPELINE)),
    Mutant("a sink error does not poison the sync",
           SRC + "core/scene3d/sync.py",
           "            send()\n            self._sink.commit()\n        except BaseException:\n"
           "            self._poisoned = True\n            raise\n",
           "            send()\n            self._sink.commit()\n        except BaseException:\n"
           "            raise\n",
           (SYNC,)),
    Mutant("reset() does not clear the sink (review P1-2)",
           SRC + "core/scene3d/sync.py",
           "        try:\n            self._sink.clear()\n", "        try:\n            pass\n",
           (SYNC, PIPELINE)),
    Mutant("reset() keeps the sun it deduplicates against",
           SRC + "core/scene3d/sync.py",
           "        self._failures = {}\n        self._sun = None\n        self._ground = None\n        try:\n",
           "        self._failures = {}\n        try:\n",
           (SYNC,)),
    Mutant("a clear() that raises leaves the sync usable",
           SRC + "core/scene3d/sync.py",
           "            self._sink.clear()\n        except BaseException:\n            self._poisoned = True\n            raise\n",
           "            self._sink.clear()\n        except BaseException:\n            raise\n",
           (SYNC,)),
    Mutant("the sun handed to apply() is dropped",
           SRC + "core/scene3d/sync.py",
           "        send_sun = sun is not None and sun != self._sun\n", "        send_sun = False\n",
           (SYNC, PIPELINE)),
    Mutant("a builder registered late is not noticed (review P2-1)",
           SRC + "core/scene3d/sync.py",
           "            and builder_for(new[item_id].kind) is not built_with\n",
           "            and False\n",
           (SYNC, PIPELINE)),
    Mutant("RecordingSink accepts a duplicate add",
           SRC + "core/scene3d/sink.py",
           "        if item_id in self._items:\n"
           "            raise SinkProtocolError(f\"add(): item {item_id!r} is already in the sink\")\n",
           "",
           (SYNC,)),
    Mutant("RecordingSink.clear() leaves the transaction open",
           SRC + "core/scene3d/sink.py",
           "        self._ground = None\n        self._open = False\n        self.calls.append(SinkCall(\"clear\", None, ()))\n",
           "        self._ground = None\n        self.calls.append(SinkCall(\"clear\", None, ()))\n",
           (SYNC,)),
    Mutant("RecordingSink.clear() keeps the sun",
           SRC + "core/scene3d/sink.py",
           "        self._items.clear()\n        self._sun = None\n", "        self._items.clear()\n",
           (SYNC,)),
    Mutant("the diff lists a reorder as geometry",
           SRC + "core/scene3d/scene_diff.py",
           "        geometry=tuple(geometry),\n",
           "        geometry=tuple(geometry) if not reordered else tuple(survivors),\n",
           (CONTRACT, SYNC)),
    # ── footprints and the snapshot ──────────────────────────────────────────
    Mutant("an ellipse is polygonised with 23 segments (the footprint changed)",
           SRC + "ui/canvas/footprints.py",
           "ELLIPSE_SEGMENTS = 24\n", "ELLIPSE_SEGMENTS = 23\n",
           (GOLDEN,)),
    Mutant("a rectangle footprint starts at another corner (same polygon, other numbers)",
           SRC + "ui/canvas/footprints.py",
           "        corners = (rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft())\n",
           "        corners = (rect.topRight(), rect.bottomRight(), rect.bottomLeft(), rect.topLeft())\n",
           (GOLDEN,)),
    Mutant("circles carry their item's rotation",
           SRC + "ui/view3d/snapshot.py",
           "rotatable=shape != OUTLINE_CIRCLE)", "rotatable=True)",
           (SNAPSHOT,)),
    Mutant("invisible items are snapshotted",
           SRC + "ui/view3d/snapshot.py",
           _SNAPSHOT_LOOP, "    for item in reversed(items):\n",
           (SNAPSHOT,)),
    Mutant("the snapshot is ordered top to bottom",
           SRC + "ui/view3d/snapshot.py",
           _SNAPSHOT_LOOP, _SNAPSHOT_LOOP.replace("reversed(items)", "items"),
           (SNAPSHOT,)),
    Mutant("the snapshot drops the holes of a stroke",
           SRC + "ui/view3d/snapshot.py",
           "        holes=holes if any(holes) else (),\n", "        holes=(),\n",
           (SNAPSHOT, EDGE_PLAN)),
    Mutant("a polygon that crosses itself is handed on as it was drawn",
           SRC + "ui/view3d/snapshot.py",
           "    if is_simple_ring(ring):\n        return ((ccw(ring), ()),)\n",
           "    if True:\n        return ((ccw(ring), ()),)\n",
           (SNAPSHOT,)),
    Mutant("one bad item raises out of the snapshot (review P2-3)",
           SRC + "ui/view3d/snapshot.py",
           "        except Exception as error:  # one bad item must not blank the view (docstring)\n",
           "        except ZeroDivisionError as error:  # one bad item must not blank the view (docstring)\n",
           (SNAPSHOT,)),
    Mutant("a bad item is logged on every snapshot",
           SRC + "ui/view3d/snapshot.py",
           "    if key in _reported:\n        return\n", "",
           (SNAPSHOT,)),
    Mutant("a roof ridge gets a record of its own (review P2-4)",
           SRC + "ui/view3d/snapshot.py",
           '_NO_RECORD_KINDS = frozenset({"ROOF_RIDGE"})\n', "_NO_RECORD_KINDS: frozenset[str] = frozenset()\n",
           (SNAPSHOT, PIPELINE, EDGE_PLAN)),
    Mutant("of two items with one id the upper one wins, silently (review P2-5)",
           SRC + "ui/view3d/snapshot.py",
           "        if item_id in records:\n"
           "            _report(SkippedItem(item_id, kind, \"another item already has this id\"), skipped)\n"
           "            continue\n",
           "",
           (SNAPSHOT,)),
    Mutant("a skipped item is logged but never handed to the caller's list",
           SRC + "ui/view3d/snapshot.py",
           "    if into is not None:\n        into.append(skipped)\n", "",
           (SNAPSHOT,)),
)


def run(mutant: Mutant) -> tuple[str, str]:
    """``(verdict, detail)`` — KILLED, SURVIVED or STALE."""
    path = REPO / mutant.path
    original = path.read_bytes()
    text = original.decode("utf-8").replace("\r\n", "\n")
    if text.count(mutant.old) != 1:
        return "STALE", f"pattern found {text.count(mutant.old)}x in {mutant.path}"
    try:
        path.write_text(text.replace(mutant.old, mutant.new), encoding="utf-8", newline="")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *mutant.tests, "-q", "-x", "-p", "no:cacheprovider"],
            cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=1800, check=False,
        )
    finally:
        path.write_bytes(original)
    lines = proc.stdout.strip().splitlines()
    tail = lines[-1] if lines else proc.stderr.strip()[-200:]
    if proc.returncode == 0:
        return "SURVIVED", tail
    failed = next((line for line in lines if line.startswith(("FAILED tests", "ERROR tests"))), tail)
    return "KILLED", failed[:160]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--list", action="store_true", help="print the mutants and exit")
    parser.add_argument("-k", metavar="TEXT", default="", help="only mutants whose label contains TEXT")
    args = parser.parse_args(argv)
    chosen = [m for m in MUTANTS if args.k.lower() in m.label.lower()]
    if args.list:
        for mutant in chosen:
            print(f"{mutant.label}  [{mutant.path}]")
        print(f"{len(chosen)} mutants")
        return 0
    dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--", "src"],
                           capture_output=True, text=True, check=False).stdout.strip()
    if dirty:
        print("refusing to run: src/ has uncommitted changes (this script rewrites source files)\n"
              + dirty)
        return 2
    not_killed = 0
    for mutant in chosen:
        verdict, detail = run(mutant)
        not_killed += verdict != "KILLED"
        print(f"{verdict:<8} {mutant.label}  [{detail}]", flush=True)
    print(f"{len(chosen) - not_killed}/{len(chosen)} mutants killed")
    return 1 if not_killed else 0


if __name__ == "__main__":
    raise SystemExit(main())
