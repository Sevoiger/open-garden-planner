"""Permission failures must not be mistaken for missing personal stores."""

from pathlib import Path

import pytest

from open_garden_planner.core.personal_store import PersonalStoreLoadError, PersonalStoreLoadState
from open_garden_planner.models.plant_lists import PlantListStore
from open_garden_planner.models.seed_inventory import SeedInventoryStore


@pytest.mark.parametrize("factory, content", [
    (PlantListStore, b'{"version":"1.0","lists":[]}'),
    (SeedInventoryStore, b'[{"id":"one","species_name":"Synthetic"}]'),
])
def test_denied_read_is_failed_blocks_save_and_allows_explicit_retry(tmp_path, monkeypatch, factory, content):
    path = tmp_path / "personal.json"
    path.write_bytes(content)
    original_read = Path.read_bytes

    def denied_read(current):
        if current == path:
            raise PermissionError(13, "Permission denied", str(path))
        return original_read(current)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "read_bytes", denied_read)
        store = factory(path)
        assert store.load_state == PersonalStoreLoadState.FAILED
        assert isinstance(store.load_error, PersonalStoreLoadError)
        with pytest.raises(PersonalStoreLoadError, match="Permission denied"):
            store.save()
        assert original_read(path) == content
        assert not path.with_name(path.name + ".writer.lock").exists()

    store.reload()
    assert store.load_state in {PersonalStoreLoadState.EMPTY, PersonalStoreLoadState.LOADED}
    assert path.read_bytes() == content
