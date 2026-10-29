"""Deletion boundaries: last two runs, fallback chains, foreign data, publication."""

import json
from pathlib import Path

import pytest

from open_garden_planner.services import weather_collection as wc
from open_garden_planner.services.weather_retention import MARKER, prune_archives


def run_name(index):
    return f"202610{index:02d}T080000000000Z"


def make_run(root, index, reference=None):
    name = run_name(index)
    path = root / "runs" / name
    path.mkdir(parents=True)
    (path / "data.json").write_text("{}")
    data = {
        "schema_version": 2,
        "attempted_at": f"2026-10-{index:02d}T08:00:00+00:00",
        "config_sha256": "a" * 64,
        "sources": {"test": {"raw": {"path": f"runs/{run_name(reference or index)}/data.json"}}},
    }
    (path / "manifest.json").write_text(json.dumps(data))
    return path, data


def publish(root, data):
    (root / "latest.json").write_text(json.dumps(data))


def test_keep_n_and_n_minus_one_and_prune_owned_interrupted_runs(tmp_path):
    for i in [1, 2, 3]:
        make_run(tmp_path, i)
    current, data = make_run(tmp_path, 4)
    publish(tmp_path, data)
    interrupted = tmp_path / "runs" / run_name(0)
    interrupted.mkdir()
    (interrupted / MARKER).write_text("owned")
    foreign = tmp_path / "runs" / "personal-notes"
    foreign.mkdir()
    unmarked = tmp_path / "runs" / "20260930T080000000000Z"
    unmarked.mkdir()
    result = prune_archives(tmp_path, current)
    assert set(result["retained_runs"]) == {run_name(3), run_name(4)}
    assert set(result["removed_runs"]) == {run_name(0), run_name(1), run_name(2)}
    assert foreign.exists() and unmarked.exists()
    assert (current / "data.json").exists()


def test_previous_run_and_transitive_fallback_references_survive(tmp_path):
    make_run(tmp_path, 1)
    make_run(tmp_path, 2, reference=1)
    make_run(tmp_path, 3, reference=2)
    current, data = make_run(tmp_path, 4)
    publish(tmp_path, data)
    result = prune_archives(tmp_path, current)
    assert result["removed_runs"] == []
    assert len(result["protected_runs"]) == 4
    assert (tmp_path / "runs" / run_name(1) / "data.json").exists()


def test_symlinks_and_invalid_paths_cannot_expand_deletion(tmp_path):
    old, _ = make_run(tmp_path, 1)
    current, data = make_run(tmp_path, 2)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    (tmp_path / "runs" / run_name(0)).symlink_to(outside, target_is_directory=True)
    data["sources"]["test"]["raw"]["path"] = "../outside/keep"
    publish(tmp_path, data)
    with pytest.raises(ValueError, match="Unsafe archive reference"):
        prune_archives(tmp_path, current)
    assert old.exists() and (outside / "keep").exists()


def test_bad_historical_manifest_aborts_all_pruning(tmp_path):
    old, _ = make_run(tmp_path, 1)
    current, data = make_run(tmp_path, 2)
    publish(tmp_path, data)
    (old / "manifest.json").write_text("{bad")
    with pytest.raises(ValueError):
        prune_archives(tmp_path, current)
    assert old.exists()


def test_publication_failure_never_starts_cleanup(tmp_path, monkeypatch):
    config = json.loads((Path(__file__).resolve().parents[2] / "weather/config.json").read_text())
    config["models"] = {}
    config["ensembles"] = {}
    config["meteociel"]["enabled"] = False
    old, _ = make_run(tmp_path, 1)
    seen = []
    actual = wc.atomic_json

    def fail_latest(path, data):
        if path.name == "latest.json":
            raise OSError("disk full")
        actual(path, data)

    monkeypatch.setattr(wc, "atomic_json", fail_latest)
    monkeypatch.setattr(wc, "prune_archives", lambda *_: seen.append(True))
    with pytest.raises(OSError, match="disk full"):
        wc.collect(config, tmp_path)
    assert not seen and old.exists()


def test_collect_invokes_retention_only_after_complete_publication(tmp_path, monkeypatch):
    config = json.loads((Path(__file__).resolve().parents[2] / "weather/config.json").read_text())
    config["models"] = {}
    config["ensembles"] = {}
    config["meteociel"]["enabled"] = False
    for i in [1, 2, 3]:
        make_run(tmp_path, i)
    wc.collect(config, tmp_path)
    report = json.loads((tmp_path / "retention.json").read_text())
    assert report["errors"] == []
    assert len(list((tmp_path / "runs").iterdir())) == 2
    assert not (tmp_path / "runs" / run_name(2)).exists()
    assert (tmp_path / "runs" / run_name(3)).exists()


def test_unsafe_reference_in_unprotected_history_prevents_any_deletion(tmp_path):
    old, data = make_run(tmp_path, 1)
    data["sources"]["test"]["raw"]["path"] = "../unsafe"
    (old / "manifest.json").write_text(json.dumps(data))
    second, _ = make_run(tmp_path, 2)
    make_run(tmp_path, 3)
    current, data = make_run(tmp_path, 4)
    publish(tmp_path, data)
    with pytest.raises(ValueError, match="Unsafe archive reference"):
        prune_archives(tmp_path, current)
    assert old.exists() and second.exists()


def test_historical_timestamp_requires_timezone_before_pruning(tmp_path):
    old, data = make_run(tmp_path, 1)
    data["attempted_at"] = "2026-10-01T08:00:00"
    (old / "manifest.json").write_text(json.dumps(data))
    current, data = make_run(tmp_path, 2)
    publish(tmp_path, data)
    with pytest.raises(ValueError, match="timezone"):
        prune_archives(tmp_path, current)
    assert old.exists()
