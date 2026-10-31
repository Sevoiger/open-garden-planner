"""Acquisitions remain distinct, conflict-safe and recoverable."""
import json

import pytest

from open_garden_planner.core.personal_store import (
    PersonalStoreConflictError,
    PersonalStoreLoadError,
)
from open_garden_planner.services.acquisition_inventory import AcquisitionInventory


def test_acquisitions_round_trip_edit_and_conflict(tmp_path):
    path = tmp_path / "acquisitions.json"
    store = AcquisitionInventory(path)
    first = store.put({"name": "Compost fictif", "kind": "Compost", "supplier": "Fournisseur fictif",
        "unit_price": 7.25, "currency": "EUR", "purchased_quantity": 4.0,
        "remaining_quantity": 3.0, "unit": "sac", "purchase_date": "2025-02-01"})
    raw = json.loads(path.read_text())
    raw[0]["extension"] = {"keep": True}
    path.write_text(json.dumps(raw))
    with pytest.raises(PersonalStoreConflictError):
        store.put({"remaining_quantity": 2.0}, first["id"])
    assert store.all()[0]["remaining_quantity"] == 3.0
    store.reload()
    store.put({"remaining_quantity": 2.0}, first["id"])
    reopened = AcquisitionInventory(path).all()[0]
    assert reopened["extension"] == {"keep": True}
    assert reopened["purchased_quantity"] == 4.0 and reopened["remaining_quantity"] == 2.0
    assert reopened["unit_price"] == 7.25 and reopened["supplier"] == "Fournisseur fictif"
    store.delete(first["id"])
    assert AcquisitionInventory(path).all() == []


def test_invalid_store_and_failed_write_do_not_destroy_data(tmp_path, monkeypatch):
    path = tmp_path / "acquisitions.json"
    path.write_text("{ broken")
    before = path.read_bytes()
    with pytest.raises(PersonalStoreLoadError):
        AcquisitionInventory(path)
    assert path.read_bytes() == before
    path.write_text("null")
    with pytest.raises(PersonalStoreLoadError):
        AcquisitionInventory(path)
    assert path.read_text() == "null"
    path.unlink()
    store = AcquisitionInventory(path)
    import open_garden_planner.services.acquisition_inventory as module
    save = module.write_json
    monkeypatch.setattr(module, "write_json", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("failure")))
    with pytest.raises(OSError):
        store.put({"name": "Outil", "kind": "Outil"})
    assert store.all() == [] and not path.exists()
    monkeypatch.setattr(module, "write_json", save)
    store.put({"name": "Outil", "kind": "Outil"})
    assert len(AcquisitionInventory(path).all()) == 1


def test_invalid_quantities_are_rejected(tmp_path):
    store = AcquisitionInventory(tmp_path / "acquisitions.json")
    with pytest.raises(ValueError):
        store.put({"name": "Compost", "kind": "Compost", "purchased_quantity": 2, "remaining_quantity": 3})
    with pytest.raises(ValueError):
        store.put({"name": "Compost", "kind": "Compost", "unit_price": float("nan")})
    assert not store.path.exists()


def test_extreme_json_number_refuses_loading_without_changing_file(tmp_path):
    path = tmp_path / "acquisitions.json"
    path.write_text('[{"id":"x","name":"tool","kind":"tool","unit_price":' + '9' * 1000 + '}]')
    before = path.read_bytes()
    with pytest.raises(PersonalStoreLoadError):
        AcquisitionInventory(path)
    assert path.read_bytes() == before
