"""Personal facts prepare immutable patches and reject unknown source shapes."""
from copy import deepcopy

import pytest

from open_garden_planner.services.plant_specimens import (
    add_specimen,
    prepare_personal_specimen_fields,
)


@pytest.mark.parametrize("fields", [
    {"acquisition_price": "-1"}, {"acquisition_price": "NaN"},
    {"acquisition_receipt_media_id": "unattached"}, {"acquisition_period": "next year perhaps"},
])
def test_invalid_personal_fact_leaves_document_untouched(fields):
    document, identity = add_specimen({"objects": []}, "rose", "Rose")
    before = deepcopy(document)
    with pytest.raises((ValueError, TypeError)):
        prepare_personal_specimen_fields(document, identity, fields)
    assert document == before


def test_unknown_existing_field_format_cannot_be_replaced_silently():
    document, identity = add_specimen({"objects": []}, "rose", "Rose")
    document["plant_specimens"][identity]["metadata"]["plant_instance"]["substrate"] = {"future": "typed record"}
    with pytest.raises(ValueError, match="unrecognized format"):
        prepare_personal_specimen_fields(document, identity, {"substrate": "replacement"})
    updated = prepare_personal_specimen_fields(document, identity, {"notes": "unrelated"})
    assert updated["plant_specimens"][identity]["metadata"]["plant_instance"]["substrate"] == {"future": "typed record"}


def test_clearing_known_acquisition_date_or_price_means_unknown_not_zero():
    document, identity = add_specimen({"objects": []}, "rose", "Rose")
    document = prepare_personal_specimen_fields(document, identity,
                                                {"acquisition_period": "2024-04-01", "acquisition_price": "0"})
    instance = document["plant_specimens"][identity]["metadata"]["plant_instance"]
    assert instance["acquisition_date"] == "2024-04-01"
    assert instance["acquisition_price"] == 0
    updated = prepare_personal_specimen_fields(document, identity,
                                               {"acquisition_period": "", "acquisition_price": ""})
    instance = updated["plant_specimens"][identity]["metadata"]["plant_instance"]
    assert "acquisition_date" not in instance
    assert instance["acquisition_period"]["precision"] == "unknown"
    assert instance["acquisition_price"] is None
