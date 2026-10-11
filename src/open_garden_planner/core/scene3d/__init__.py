"""Qt-free 3D scene core (Phase 17 L1.1, #385, ADR-054; concept: §8.26).

The pipeline::

    live canvas items ──snapshot_records()──▶ {item_id: Record}      (ui/view3d/snapshot.py)
                                                   │ diff() against the last snapshot
                                                   ▼
                              SceneSync.apply ──▶ builders (added + geometry only)
                                                   │ MeshPart, item-local frame
                                                   ▼
                                               EngineSink   (Qt Quick 3D: L1.2 · RecordingSink: tests)

Modules: ``record`` (the contract: ``Record``, ``Transform``, ``Material``,
``Params``, three signatures), ``scene_diff`` (the diff), ``frame`` (scene ↔ engine, local ↔
scene), ``mesh``, ``sink``, ``build`` (builder seam + default prism builder),
``sync``, ``color``. Nothing here imports Qt (pinned by an AST scan); numpy and
the standard library only.

``legacy`` is the pre-L1.1 contract (the former ``core/scene3d.py``, moved
here when the package was made, less the two functions contract v2 shares with
it): the ``Scene3DRecord`` collector types, ``extrude_footprint`` and the scalar
``to_engine_frame``. It feeds the shipped Qt 3D window and the dormant spike,
and L1.10 deletes it with them. Every name the old module had is still exported
here, so no importer changed.

Since the review of L1.1: ``polygon`` (plane polygons — orientation, outer rings
and holes, triangulation; it also holds ``triangulate_polygon``, which outlives
``legacy``), and ``frame`` holds ``sun_direction_scene``. The module of ``diff``
is ``scene_diff``, so that ``core.scene3d.diff`` is one thing: the function.

"Imports no Qt" is a statement about this package's SOURCE. Importing it still
runs ``core/__init__.py``, which loads the command system and with it PyQt6.
"""

from __future__ import annotations

from .build import (
    BUILT_HEIGHT_TOLERANCE,
    Builder,
    BuilderContractError,
    BuilderRegistry,
    default_builder,
    item_seed,
    plan_overreach,
    prism_mesh,
    verify_builder,
)
from .color import Rgba, srgb8_to_linear, srgb_channel_to_linear, srgb_to_linear
from .frame import (
    ENGINE_TO_SCENE,
    SCENE_TO_ENGINE,
    EnginePose,
    LocalFrame,
    engine_model_matrix,
    engine_pose,
    engine_to_scene_points,
    engine_to_scene_vectors,
    local_frame,
    pose_point,
    pose_points,
    pose_ring,
    scene_to_engine_points,
    scene_to_engine_vectors,
    sun_direction_scene,
)
from .legacy import (
    DECAL_LIFT_CM,
    DECAL_MAX_LIFT_CM,
    DECAL_STACK_STEP_CM,
    FLAT_THICKNESS_CM,
    Scene3DRecord,
    extrude_footprint,
    records_from_raw,
    to_engine_frame,
)
from .mesh import (
    BENT_NORMAL_KINDS,
    FLAT_NORMAL_MIN_DOT,
    MATERIAL_KINDS,
    Mesh,
    MeshError,
    MeshPart,
    normal_vs_winding,
)
from .polygon import (
    Region,
    ccw,
    cut_region,
    cw,
    group_rings,
    is_buildable_region,
    is_flat_ring,
    is_simple_ring,
    point_in_regions,
    point_in_ring,
    regions_area,
    ring_area,
    signed_area,
    triangulate_polygon,
    triangulate_region,
)
from .record import (
    ANGLE_DECIMALS,
    EMPTY_PARAMS,
    FALLBACK_RGBA,
    GEOMETRY_FIELDS,
    LENGTH_DECIMALS,
    MATERIAL_FIELDS,
    MAX_LENGTH_CM,
    PARAM_CONTAINER_MATERIAL,
    PARAM_OUTLINE_RESOLVED,
    PARAM_PATH_STYLE,
    PARAM_PLANT_CATEGORY,
    PARAM_RADIUS_CM,
    PARAM_RIDGE,
    PARAM_SEED,
    PARAM_SPECIES_KEY,
    PARAM_SPECIES_NAME,
    SHAPE_CIRCLE,
    SHAPE_ELLIPSE,
    SHAPE_POLYGON,
    SHAPE_POLYLINE,
    SHAPE_RECTANGLE,
    SHAPES,
    SIGNATURE_FIELDS,
    TRANSFORM_FIELDS,
    UNSIGNED_FIELDS,
    Material,
    Params,
    ParamValue,
    Point,
    Record,
    Ring,
    Transform,
    quantize_cm,
    quantize_deg,
)
from .scene_diff import SceneDiff, diff
from .sink import (
    EngineSink,
    GroundSpec,
    RecordingSink,
    SinkCall,
    SinkItem,
    SinkProtocolError,
    SinkState,
    SunState,
)
from .sync import BuildFailure, SceneSync, SyncStateError

__all__ = [
    "ANGLE_DECIMALS",
    "BENT_NORMAL_KINDS",
    "BUILT_HEIGHT_TOLERANCE",
    "DECAL_LIFT_CM",
    "DECAL_MAX_LIFT_CM",
    "DECAL_STACK_STEP_CM",
    "EMPTY_PARAMS",
    "ENGINE_TO_SCENE",
    "FALLBACK_RGBA",
    "FLAT_NORMAL_MIN_DOT",
    "FLAT_THICKNESS_CM",
    "GEOMETRY_FIELDS",
    "LENGTH_DECIMALS",
    "MATERIAL_FIELDS",
    "MATERIAL_KINDS",
    "MAX_LENGTH_CM",
    "PARAM_CONTAINER_MATERIAL",
    "PARAM_OUTLINE_RESOLVED",
    "PARAM_PATH_STYLE",
    "PARAM_PLANT_CATEGORY",
    "PARAM_RADIUS_CM",
    "PARAM_RIDGE",
    "PARAM_SEED",
    "PARAM_SPECIES_KEY",
    "PARAM_SPECIES_NAME",
    "SCENE_TO_ENGINE",
    "SHAPES",
    "SHAPE_CIRCLE",
    "SHAPE_ELLIPSE",
    "SHAPE_POLYGON",
    "SHAPE_POLYLINE",
    "SHAPE_RECTANGLE",
    "SIGNATURE_FIELDS",
    "TRANSFORM_FIELDS",
    "UNSIGNED_FIELDS",
    "BuildFailure",
    "Builder",
    "BuilderContractError",
    "BuilderRegistry",
    "EnginePose",
    "EngineSink",
    "GroundSpec",
    "LocalFrame",
    "Material",
    "Mesh",
    "MeshError",
    "MeshPart",
    "ParamValue",
    "Params",
    "Point",
    "Record",
    "RecordingSink",
    "Region",
    "Rgba",
    "Ring",
    "Scene3DRecord",
    "SceneDiff",
    "SceneSync",
    "SinkCall",
    "SinkItem",
    "SinkProtocolError",
    "SinkState",
    "SunState",
    "SyncStateError",
    "Transform",
    "ccw",
    "cut_region",
    "cw",
    "default_builder",
    "diff",
    "engine_model_matrix",
    "engine_pose",
    "engine_to_scene_points",
    "engine_to_scene_vectors",
    "extrude_footprint",
    "group_rings",
    "is_buildable_region",
    "is_flat_ring",
    "is_simple_ring",
    "item_seed",
    "local_frame",
    "normal_vs_winding",
    "plan_overreach",
    "point_in_regions",
    "point_in_ring",
    "pose_point",
    "pose_points",
    "pose_ring",
    "prism_mesh",
    "quantize_cm",
    "quantize_deg",
    "records_from_raw",
    "regions_area",
    "ring_area",
    "scene_to_engine_points",
    "scene_to_engine_vectors",
    "signed_area",
    "srgb8_to_linear",
    "srgb_channel_to_linear",
    "srgb_to_linear",
    "sun_direction_scene",
    "to_engine_frame",
    "triangulate_polygon",
    "triangulate_region",
    "verify_builder",
]
