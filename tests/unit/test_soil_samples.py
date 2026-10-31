"""Laboratory points keep source facts, coordinates and unrelated soil separate."""
from copy import deepcopy

import pytest

from open_garden_planner.services.document_operations import prepare_operations


def _document():
    return {"canvas": {"width": 1000, "height": 600}, "objects": [],
            "soil_tests": {"global": {"target_id": "global", "records": [{"id": "old", "date": "2024-01-01", "ph": 7.1}]}}}


def test_full_report_is_detached_and_preserved_without_kit_conversion():
    original = _document()
    report = {"laboratory": "Labo fictif", "reference": "test-01", "sample_depth": "0–30 cm",
              "source_text": "Texte de laboratoire et commentaires.\n", "readings": [
                  {"parameter": "P₂O₅", "value": "0.07", "unit": "g/kg", "method": "Olsen"},
                  {"parameter": "Na₂O", "value": "< 0.01", "unit": "g/kg", "reference": "< 0.08"},
                  {"parameter": "CEC", "value": "59", "unit": "méq/kg", "method": "Metson"},
              ]}
    operations = [{"operation": "record_soil_sample", "fields": {
        "test_date": "2025-05-07", "ph": 8.2, "position": {"x_cm": 200, "y_cm": 300}, "report": report}}]
    result, rows = prepare_operations(original, operations)
    point = rows[0]["target_id"]
    record = result["soil_tests"][point]["records"][0]
    assert record["laboratory_report"] == report
    assert record["sample_position"] == {"x_cm": 200, "y_cm": 300}
    assert record["ph"] == 8.2 and record["mode"] == "lab"
    assert "p_ppm" not in record and "p_level" not in record
    assert result["soil_tests"]["global"] == original["soil_tests"]["global"]
    assert len(original["soil_tests"]) == 1
    report["readings"][0]["value"] = "changed"
    assert record["laboratory_report"]["readings"][0]["value"] == "0.07"


def test_edit_delete_and_unknown_extension_are_preserved():
    document, rows = prepare_operations(_document(), [{"operation": "record_soil_sample", "fields": {
        "test_date": "2025-01-02", "ph": 6.4, "position": {"x_cm": 100, "y_cm": 200}}}])
    target, identity = rows[0]["target_id"], rows[0]["record_id"]
    document["soil_tests"][target]["records"][0]["other_extension"] = {"keep": True}
    updated, _ = prepare_operations(document, [{"operation": "edit_soil_sample", "target_id": target,
        "fields": {"record_id": identity, "position": {"x_cm": 300, "y_cm": 400}}}])
    record = updated["soil_tests"][target]["records"][0]
    assert record["ph"] == 6.4 and record["other_extension"] == {"keep": True}
    assert record["sample_position"] == {"x_cm": 300, "y_cm": 400}
    deleted, _ = prepare_operations(updated, [{"operation": "delete_soil_sample", "target_id": target,
                                               "fields": {"record_id": identity}}])
    assert not deleted["soil_tests"][target]["records"]
    assert document["soil_tests"][target]["records"][0]["sample_position"]["x_cm"] == 100


@pytest.mark.parametrize("fields", [
    {"ph": 7, "position": {"x_cm": 10, "y_cm": 10}},
    {"test_date": "2025-01-01", "ph": float("nan"), "position": {"x_cm": 10, "y_cm": 10}},
    {"test_date": "2025-01-01", "ph": 7, "position": {"x_cm": 1001, "y_cm": 10}},
    {"test_date": "2025-01-01", "report": {"readings": [{"parameter": "P", "value": 3}]},
     "position": {"x_cm": 10, "y_cm": 10}},
    {"test_date": "2025-01-01", "position": {"x_cm": 10, "y_cm": 10}},
])
def test_invalid_sample_rejects_whole_preparation(fields):
    document = _document()
    before = deepcopy(document)
    with pytest.raises(ValueError):
        prepare_operations(document, [{"operation": "record_soil_sample", "fields": fields}])
    assert document == before


def test_edit_preserves_nested_extensions_and_delete_rejects_extra_fields():
    document, rows = prepare_operations(_document(), [{"operation": "record_soil_sample", "fields": {
        "test_date": "2025-01-02", "ph": 6.123456789, "position": {"x_cm": 100, "y_cm": 200},
        "report": {"source_text": "Fictif", "readings": [{"parameter": "P", "value": "< 1"}]}}}])
    target, identity = rows[0]["target_id"], rows[0]["record_id"]
    report = document["soil_tests"][target]["records"][0]["laboratory_report"]
    report["accreditation"] = {"keep": True}
    report["readings"][0]["uncertainty"] = "± 1"
    updated, _ = prepare_operations(document, [{"operation": "edit_soil_sample", "target_id": target,
        "fields": {"record_id": identity, "report": {"source_text": "Fictif", "readings": [
            {"parameter": "P", "value": "< 2", "previous_index": 0}]}}}])
    result = updated["soil_tests"][target]["records"][0]
    assert result["ph"] == 6.123456789
    assert result["laboratory_report"]["accreditation"] == {"keep": True}
    assert result["laboratory_report"]["readings"][0]["uncertainty"] == "± 1"
    with pytest.raises(ValueError, match="Unknown soil sample field"):
        prepare_operations(document, [{"operation": "delete_soil_sample", "target_id": target,
                                      "fields": {"record_id": identity, "ph": 3}}])


def test_duplicate_lab_parameters_never_lose_or_copy_unknown_extensions():
    doc, rows = prepare_operations(_document(), [{"operation": "record_soil_sample", "fields": {
        "test_date": "2025-01-02", "position": {"x_cm": 100, "y_cm": 200}, "report": {"readings": [
            {"parameter": "pH", "method": "H2O", "value": "6"},
            {"parameter": "pH", "method": "KCl", "value": "5"}]}}}])
    target, record_id = rows[0]["target_id"], rows[0]["record_id"]
    old = doc["soil_tests"][target]["records"][0]["laboratory_report"]["readings"]
    old[0]["uncertainty"] = "± 0.1"
    old[1]["uncertainty"] = "± 0.2"
    operation = {"operation": "edit_soil_sample", "target_id": target, "fields": {
        "record_id": record_id, "report": {"readings": [
            {"parameter": "pH", "method": "H2O", "value": "6"},
            {"parameter": "pH", "method": "KCl", "value": "5"}]}}}
    with pytest.raises(ValueError, match="Identify or explicitly remove"):
        prepare_operations(doc, [operation])
    operation["fields"]["report"]["readings"][0]["previous_index"] = 0
    operation["fields"]["report"]["readings"][1]["previous_index"] = 1
    operation["fields"]["report"]["readings"].append({"parameter": "pH", "method": "new", "value": "7"})
    result, _ = prepare_operations(doc, [operation])
    readings = result["soil_tests"][target]["records"][0]["laboratory_report"]["readings"]
    assert readings[0]["uncertainty"] == "± 0.1" and readings[1]["uncertainty"] == "± 0.2"
    assert "uncertainty" not in readings[2] and "previous_index" not in readings[0]
    operation["fields"]["report"]["readings"].pop(0)
    operation["fields"]["report"]["removed_reading_indices"] = [0]
    result, _ = prepare_operations(doc, [operation])
    assert result["soil_tests"][target]["records"][0]["laboratory_report"]["readings"][0]["uncertainty"] == "± 0.2"


def test_full_analysis_accepts_up_to_fifteen_locations_but_simple_ph_does_not():
    positions = [{"x_cm": 10 * i, "y_cm": 40} for i in range(15)]
    fields = {"test_date": "2025-01-02", "positions": positions,
              "report": {"readings": [{"parameter": "Sable", "value": "40", "unit": "%"}]}}
    result, rows = prepare_operations(_document(), [{"operation": "record_soil_sample", "fields": fields}])
    record = result["soil_tests"][rows[0]["target_id"]]["records"][0]
    assert record["sample_positions"] == positions and record["sample_position"] == positions[0]
    with pytest.raises(ValueError, match="1 to 15"):
        prepare_operations(_document(), [{"operation": "record_soil_sample",
            "fields": {**fields, "positions": positions + [positions[0]]}}])
    with pytest.raises(ValueError, match="require a laboratory"):
        prepare_operations(_document(), [{"operation": "record_soil_sample", "fields": {
            "test_date": "2025-01-02", "positions": positions, "ph": 7}}])


def test_previous_parameter_code_is_not_lost_when_editing_reading():
    document, rows = prepare_operations(_document(), [{"operation": "record_soil_sample", "fields": {
        "test_date": "2025-01-02", "position": {"x_cm": 100, "y_cm": 200},
        "report": {"readings": [{"parameter": "Azote", "parameter_code": "nitrogen_total", "value": "1"}]}}}])
    target, identity = rows[0]["target_id"], rows[0]["record_id"]
    updated, _ = prepare_operations(document, [{"operation": "edit_soil_sample", "target_id": target,
        "fields": {"record_id": identity, "report": {"readings": [
            {"parameter": "Azote", "value": "2", "previous_index": 0}]}}}])
    assert updated["soil_tests"][target]["records"][0]["laboratory_report"]["readings"][0]["parameter_code"] == "nitrogen_total"


def test_standard_parameter_cannot_be_renamed_through_mcp():
    document, rows = prepare_operations(_document(), [{"operation": "record_soil_sample", "fields": {
        "test_date": "2025-01-02", "position": {"x_cm": 100, "y_cm": 200},
        "report": {"readings": [{"parameter": "Azote", "parameter_code": "nitrogen_total", "value": "1"}]}}}])
    with pytest.raises(ValueError, match="Cannot rename"):
        prepare_operations(document, [{"operation": "edit_soil_sample", "target_id": rows[0]["target_id"],
            "fields": {"record_id": rows[0]["record_id"], "report": {"readings": [
                {"parameter": "Potassium", "value": "2", "previous_index": 0}]}}}])
