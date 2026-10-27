"""Large read payloads stay paged, media-free by default and revision-pinned."""

from __future__ import annotations

import json

import pytest

from open_garden_planner.agent_api.read_projection import (
    MAX_DOCUMENT_STATE_BYTES,
    project_document_state,
    project_library_page,
    project_library_snapshot,
    read_document_media,
)


def test_document_projection_pages_business_data_and_omits_geometry_and_images() -> None:
    snapshot = {
        "version": "1.4",
        "canvas": {"width": 400, "height": 300},
        "objects": [
            {"item_id": "object-a", "type": "background_image", "x": 20, "y": 30,
             "width": 200, "height": 100, "image_data": "a" * 100_000,
             "metadata": {"plant_species": {"scientific_name": "Plantus a", "image_url": "https://example.test/a.png"}}},
            {"item_id": "object-b", "type": "plant", "x": 40, "y": 50,
             "width": 8, "metadata": {"image_url": "data:image/png;base64,SECRET", "planting_date": "2026"}},
        ],
        "manual_tasks": {f"task-{index}": {"title": f"Task {index}"} for index in range(4)},
    }

    page = project_document_state(
        snapshot, revision="r1", mode="autonomous", domains=["objects", "tasks"],
        offset=1, limit=1,
    )

    assert page["document"]["objects"] == [
        {"item_id": "object-b", "type": "plant", "metadata": {"planting_date": "2026"}}
    ]
    assert page["pagination"]["objects"]["next_offset"] is None
    assert page["document"]["manual_tasks"] == {"task-1": {"title": "Task 1"}}
    assert page["pagination"]["manual_tasks"]["next_offset"] == 2
    assert page["media"]["total"] == 1
    assert page["media"]["items"][0]["path"] == "objects/1/metadata/image_url"
    assert "SECRET" not in str(page)
    assert "image_data" not in str(page)
    assert "width" not in page["document"]["objects"][0]


def test_document_media_manifest_uses_original_list_index_and_revision() -> None:
    first, second = "A" * 128, "B" * 128
    snapshot = {
        "version": "1.4",
        "objects": [
            {"item_id": "first", "image_data": first},
            {"item_id": "second", "image_data": second},
        ],
    }
    page = project_document_state(
        snapshot, revision="revision-1", mode="live", domains=["objects"],
        offset=1, limit=1,
    )
    media = page["media"]["items"][0]
    assert media["path"] == "objects/1/image_data"
    assert media["media_id"] != project_document_state(
        snapshot, revision="revision-1", mode="live", domains=["objects"], offset=0, limit=1,
    )["media"]["items"][0]["media_id"]

    chunk = read_document_media(
        snapshot, media_id=media["media_id"], revision="revision-1",
        expected_revision="revision-1", offset=16, max_bytes=12,
    )
    assert chunk["data"] == second[16:28]
    assert chunk["offset_unit"].startswith("UTF-8 bytes")
    with pytest.raises(ValueError, match="revision changed"):
        read_document_media(
            snapshot, media_id=media["media_id"], revision="revision-2",
            expected_revision="revision-1",
        )


def test_document_revision_pins_pages_and_state_response_has_an_absolute_cap() -> None:
    snapshot = {
        "version": "1.4",
        "manual_tasks": {str(index): {"notes": "synthetic " * 1_000} for index in range(60)},
    }
    with pytest.raises(ValueError, match="fewer domains or a smaller limit"):
        project_document_state(snapshot, revision="r1", mode="autonomous", domains=["tasks"], limit=100)
    with pytest.raises(ValueError, match="revision changed"):
        project_document_state(
            snapshot, revision="r2", mode="autonomous", domains=["tasks"],
            expected_revision="r1",
        )


def test_document_media_chunks_do_not_split_utf8_characters() -> None:
    source = "éa" * 8
    snapshot = {"objects": [{"image_data": source}]}
    manifest = project_document_state(
        snapshot, revision="r1", mode="autonomous", domains=["objects"],
    )["media"]["items"][0]
    first = read_document_media(
        snapshot, media_id=manifest["media_id"], revision="r1", expected_revision="r1",
        offset=0, max_bytes=3,
    )
    assert first["data"] == "éa"
    assert first["next_offset"] == 3
    with pytest.raises(ValueError, match="UTF-8 byte boundary"):
        read_document_media(
            snapshot, media_id=manifest["media_id"], revision="r1", expected_revision="r1",
            offset=1,
        )


def test_data_uri_payloads_are_removed_from_known_and_arbitrary_media_fields() -> None:
    data_uri = "data:image/png;base64,PRIVATE"
    snapshot = {
        "objects": [{
            "item_id": "object-a",
            "metadata": {
                "image_url": data_uri,
                "thumbnail_url": data_uri,
                "raw_data": {"owner_extension": data_uri},
            },
        }],
    }
    state = project_document_state(
        snapshot, revision="r1", mode="autonomous", domains=["objects"],
    )
    assert "PRIVATE" not in str(state["document"])
    assert state["media"]["total"] == 3
    for item in state["media"]["items"]:
        chunk = read_document_media(
            snapshot, media_id=item["media_id"], revision="r1", expected_revision="r1",
        )
        assert chunk["data"] == data_uri


def test_personal_fill_pngs_are_paged_as_media_at_their_original_object_paths() -> None:
    # This is the persisted format written by ProjectManager._serialize_item_core:
    # PolygonItem.metadata contains the personal fill record consumed by
    # personal_pattern_brush(). Use synthetic base64 text so no user texture is read.
    blob_size = 700_000
    snapshot = {
        "version": "1.4",
        "objects": [
            {"item_id": f"object-{index}", "type": "polygon", "metadata": {
                "owner_note": f"keep-{index}",
                "custom_extension": {"png": f"business-value-{index}"},
                "personal_fill_pattern": {
                    "png": chr(ord("A") + index) * blob_size,
                    "width_cm": 256.0,
                },
            }}
            for index in range(6)
        ],
    }

    media_ids: set[str] = set()
    for object_index in range(2, 6):
        page = project_document_state(
            snapshot, revision="r1", mode="autonomous", domains=["objects"],
            offset=object_index, limit=1,
        )

        assert len(json.dumps(page).encode("utf-8")) <= MAX_DOCUMENT_STATE_BYTES
        selected = page["document"]["objects"][0]
        assert selected["metadata"]["owner_note"] == f"keep-{object_index}"
        assert selected["metadata"]["custom_extension"]["png"] == f"business-value-{object_index}"
        assert selected["metadata"]["personal_fill_pattern"] == {"width_cm": 256.0}
        assert chr(ord("A") + object_index) * 128 not in str(page["document"])

        manifest = page["media"]["items"]
        assert len(manifest) == 1
        asset = manifest[0]
        assert asset["path"] == f"objects/{object_index}/metadata/personal_fill_pattern/png"
        assert asset["field"] == "png"
        assert asset["encoding"] == "base64"
        assert asset["encoded_bytes"] == blob_size
        media_ids.add(asset["media_id"])

        chunk = read_document_media(
            snapshot, media_id=asset["media_id"], revision="r1", expected_revision="r1",
            offset=0, max_bytes=32,
        )
        assert chunk["data"] == chr(ord("A") + object_index) * 32
        assert chunk["encoded_bytes"] == blob_size
        assert chunk["next_offset"] == 32

    assert len(media_ids) == 4


def test_library_search_pages_exact_id_and_returns_media_separately() -> None:
    plants = {
        "plant-b": {"scientific_name": "Fictitia beta", "common_name": "Bêta"},
        "plant-a": {
            "scientific_name": "Fictitia alpha", "common_name": "Alpha",
            "raw_data": {
                "personal_visual_png": "A" * 128,
                "owner_extension": "data:image/png;base64,RAW_DATA_PRIVATE",
            },
            "image_url": "data:image/png;base64,DO_NOT_INLINE",
        },
        "plant-large": {"scientific_name": "Fictitia gamma", "description": "x" * 40_000},
    }
    listing = project_library_page(plants, revision="library-r1", query="fictitia", limit=1)
    assert listing["plants"]["plant-a"]["scientific_name"] == "Fictitia alpha"
    assert "personal_visual_png" not in str(listing["plants"])
    assert "DO_NOT_INLINE" not in str(listing["plants"])
    assert "RAW_DATA_PRIVATE" not in str(listing["plants"])
    assert listing["next_offset"] == 1

    selected = project_library_page(
        plants, revision="library-r1", plant_id="plant-a", include_media=True,
        media_limit_bytes=8, expected_revision="library-r1",
    )
    media = selected["media"][0]
    assert selected["found"] is True
    assert media["data"] == "A" * 8
    assert media["next_offset"] == 8
    assert media["offset_unit"].startswith("UTF-8 bytes")
    assert project_library_page(
        plants, revision="library-r1", plant_id="plant-large",
    )["truncated_records"] == ["plant-large"]
    with pytest.raises(ValueError, match="library changed"):
        project_library_page(
            plants, revision="library-r2", plant_id="plant-a", expected_revision="library-r1",
        )


def test_library_snapshot_projection_pins_revision_to_plant_content() -> None:
    raw = {"scope": "library", "plants": {"id-1": {"scientific_name": "Species uno"}}}
    first = project_library_snapshot(raw, limit=1)
    second = project_library_snapshot(raw, limit=1, expected_revision=first["revision"])
    assert second["revision"] == first["revision"]


def test_library_media_manifest_selects_blob_and_multi_blob_resume_skips_finished() -> None:
    long_blob, short_blob = "L" * 20, "S" * 3
    plants = {
        "plant-a": {
            "scientific_name": "Fictitia alpha",
            "raw_data": {"long_base64": long_blob, "short_base64": short_blob},
        }
    }
    first_read = project_library_page(plants, revision="r1", plant_id="plant-a")
    assets = {item["field"]: item for item in first_read["media_manifest"]}
    assert set(assets) == {"long_base64", "short_base64"}
    assert "media" not in first_read

    first_chunks = project_library_page(
        plants, revision="r1", plant_id="plant-a", include_media=True,
        media_offset=0, media_limit_bytes=5,
    )["media"]
    assert {part["media_id"] for part in first_chunks} == {item["media_id"] for item in assets.values()}
    assert all(part["offset"] == 0 for part in first_chunks)

    resumed = project_library_page(
        plants, revision="r1", plant_id="plant-a", include_media=True,
        media_offset=5, media_limit_bytes=5,
    )["media"]
    by_id = {part["media_id"]: part for part in resumed}
    assert by_id[assets["long_base64"]["media_id"]]["data"] == long_blob[5:10]
    finished = by_id[assets["short_base64"]["media_id"]]
    assert finished["data"] == ""
    assert finished["finished"] is True
    assert finished["next_offset"] is None

    selected = project_library_page(
        plants, revision="r1", plant_id="plant-a", include_media=True,
        media_id=assets["long_base64"]["media_id"], media_offset=5,
        media_limit_bytes=5,
    )["media"]
    assert len(selected) == 1
    assert selected[0]["data"] == long_blob[5:10]
    with pytest.raises(ValueError, match="media_id requires include_media"):
        project_library_page(
            plants, revision="r1", plant_id="plant-a", media_id=assets["long_base64"]["media_id"],
        )


def test_light_page_never_visits_unselected_media_and_ascii_never_encodes():
    class NoEncode(str):
        def encode(self, *args, **kwargs):
            raise AssertionError("ASCII media should not allocate its byte representation")

    class Unvisited(dict):
        def items(self):
            raise AssertionError("Unselected page must not be visited")

    snapshot = {"objects": [{"item_id": "one", "metadata": {"image_data": NoEncode("A" * 1_000_000)}},
                            Unvisited({"image_data": "B"})], "manual_tasks": {"t": {"title": "Task"}}}
    tasks = project_document_state(snapshot, revision="r", mode="live", domains=["tasks"])
    assert tasks["media"]["total"] == 0
    page = project_document_state(snapshot, revision="r", mode="live", domains=["objects"], limit=1)
    assert page["media"]["items"][0]["encoded_bytes"] == 1_000_000
    # Returned business data is detached even though selection uses borrowed records.
    page["document"]["objects"][0]["metadata"]["new"] = True
    assert "new" not in snapshot["objects"][0]["metadata"]


def test_library_projection_source_opt_in_fields_and_omissions():
    plants = {"p": {"scientific_name": "Fictitia", "common_name": None, "height": 0,
                    "edible": False, "raw_data": {"html": "X" * 100_000}}}
    detail = project_library_page(plants, revision="r")
    assert detail["plants"]["p"] == {"scientific_name": "Fictitia", "common_name": None, "height": 0, "edible": False}
    assert detail["omitted_fields"]["p"] == ["raw_data"]
    selected = project_library_page(plants, revision="r", fields=["common_name", "height", "edible", "absent"])
    assert selected["plants"]["p"] == {"common_name": None, "height": 0, "edible": False}
    with pytest.raises(ValueError, match="include_source"):
        project_library_page(plants, revision="r", fields=["raw_data"])
    source = project_library_page({"p": {"raw_data": {"html": "short"}}}, revision="r", include_source=True)
    assert source["plants"]["p"]["raw_data"] == {"html": "short"}
    summary = project_library_page(plants, revision="r", view="summary")
    assert summary["next_offset"] is None and summary["plants"]["p"]["scientific_name"] == "Fictitia"


def test_oversized_object_can_always_progress_with_explicit_field_projection():
    raw = {"objects": [{"item_id": "big", "name": "Big", "type": "plant", "metadata": {"notes": "X" * 600_000}},
                       {"item_id": "next", "name": "Next"}]}
    with pytest.raises(ValueError, match="object_fields"):
        project_document_state(raw, revision="r", mode="live", limit=1)
    page = project_document_state(raw, revision="r", mode="live", limit=1, object_fields=["item_id", "name"])
    assert page["document"]["objects"] == [{"item_id": "big", "name": "Big"}]
    assert page["pagination"]["objects"]["next_offset"] == 1
    assert page["omitted_object_fields"]["big"] == ["metadata", "type"]
    next_page = project_document_state(raw, revision="r", mode="live", offset=1, limit=1,
                                       expected_revision="r", object_fields=["item_id"])
    assert next_page["document"]["objects"] == [{"item_id": "next"}]


def test_truncation_does_not_add_unrequested_fields():
    result = project_library_page({"p": {"scientific_name": "Fictitia", "description": "X" * 40_000}},
                                  revision="r", fields=["description"])
    assert result["plants"]["p"] == {}
    assert result["truncated_records"] == ["p"]
    assert result["omitted_fields"]["p"] == ["description", "scientific_name"]
