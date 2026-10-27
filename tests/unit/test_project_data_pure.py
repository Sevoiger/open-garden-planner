"""Pure project-data serialization and compatibility checks."""

from __future__ import annotations

from open_garden_planner.core.plan_io import read_plan, write_plan
from open_garden_planner.core.project_data import (
    FILE_VERSION,
    ProjectData,
    _is_newer_file_version,
)


def test_project_data_round_trip_keeps_unknown_document_and_metadata_fields(
    tmp_path,
) -> None:
    raw = {
        "version": "1.3",
        "metadata": {"created": "legacy", "vendor_note": {"label": "preserve"}},
        "canvas": {"width": 2400, "height": 1200},
        "layers": [],
        "objects": [],
        "north_angle_deg": 45,
        "future_document_field": {"nested": [1, "two"]},
        "paper_layouts": [{"ignored": True}],
        "garden_memory": {},
    }

    data = ProjectData.from_dict(raw)
    assert data.canvas_width == 2400
    assert data.extra_fields["future_document_field"] == {"nested": [1, "two"]}
    assert "paper_layouts" not in data.extra_fields
    assert data.source_metadata == {
        "created": "legacy",
        "vendor_note": {"label": "preserve"},
    }

    target = tmp_path / "synthetic.ogp"
    write_plan(target, data.to_dict())
    reopened = read_plan(target)

    assert reopened["version"] == FILE_VERSION
    assert reopened["metadata"]["created"] == "legacy"
    assert reopened["metadata"]["vendor_note"] == {"label": "preserve"}
    assert reopened["future_document_field"] == {"nested": [1, "two"]}
    assert "paper_layouts" not in reopened
    assert reopened["canvas"] == {"width": 2400, "height": 1200}
    assert reopened["north_angle_deg"] == 45


def test_project_file_version_comparison_keeps_legacy_policy() -> None:
    assert FILE_VERSION == "1.4"
    assert not _is_newer_file_version("1.3", FILE_VERSION)
    assert _is_newer_file_version("2.0", FILE_VERSION)
    assert not _is_newer_file_version("legacy", FILE_VERSION)


def test_source_mappings_are_copied_before_round_trip() -> None:
    source = {
        "canvas": {"width": 800, "height": 600},
        "objects": [],
        "layers": [],
        "vendor": {"data": ["kept"]},
        "metadata": {"vendor": {"state": "kept"}},
    }
    data = ProjectData.from_dict(source)
    source["vendor"]["data"].append("mutated")
    source["metadata"]["vendor"]["state"] = "mutated"

    serialized = data.to_dict()
    assert serialized["vendor"] == {"data": ["kept"]}
    assert serialized["metadata"]["vendor"] == {"state": "kept"}
