"""Exercise the source launcher as a subprocess, including paths with spaces."""

import os
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_launcher_uses_local_interpreter_and_forwards_arguments(tmp_path):
    folder = tmp_path / "Jardin avec espaces"
    folder.mkdir()
    launcher = folder / "Lancer-Jardin.command"
    shutil.copyfile(ROOT / launcher.name, launcher)
    binary = folder / ".venv/bin/python"
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/bash\nprintf "%s\\n" "$PWD" "$@"\nexit 0\n')
    binary.chmod(0o755)
    result = subprocess.run(["bash", str(launcher), "projet avec accents é.ogp"],
                            cwd=tmp_path, input="", capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    log = (folder / ".local/logs/launcher.log").read_text()
    assert log.splitlines() == [str(folder), "-m", "open_garden_planner", "projet avec accents é.ogp"]
    assert "projet avec accents é.ogp" in result.stdout  # Debug console shows live output.
    assert not (folder / ".tools").exists()  # Launch never bootstraps dependencies.


@pytest.mark.skipif(sys.platform != "darwin", reason="Native macOS launcher")
def test_app_launcher_runs_without_console_and_keeps_logs(tmp_path):
    folder = tmp_path / "Jardin avec espaces"
    folder.mkdir()
    shutil.copyfile(ROOT / "Lancer-Jardin.command", folder / "Lancer-Jardin.command")
    shutil.copytree(ROOT / "Jardin.app", folder / "Jardin.app")
    binary = folder / ".venv/bin/python"
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/bash\nprintf "%s\\n" "$PWD" "$@"\necho "diagnostic" >&2\n')
    binary.chmod(0o755)
    result = subprocess.run(
        [str(folder / "Jardin.app/Contents/MacOS/Jardin"), "plan privé.ogp"],
        cwd=tmp_path, input="", capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    log = (folder / ".local/logs/launcher.log").read_text()
    assert log.splitlines() == [
        str(folder), "-m", "open_garden_planner", "plan privé.ogp", "diagnostic",
    ]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS code signing")
def test_app_launcher_signature_is_valid():
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--strict", str(ROOT / "Jardin.app")],
        check=True, capture_output=True, text=True, timeout=10,
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="Native macOS launcher")
def test_app_launcher_can_be_moved_to_applications(tmp_path):
    source = tmp_path / "Sources jardin é"
    source.mkdir()
    shutil.copyfile(ROOT / "Lancer-Jardin.command", source / "Lancer-Jardin.command")
    binary = source / ".venv/bin/python"
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/bash\nprintf "%s\\n" "$PWD" "$@"\n')
    binary.chmod(0o755)
    app = tmp_path / "Applications/Jardin.app"
    shutil.copytree(ROOT / "Jardin.app", app)
    (app / "Contents/Resources/SourceRoot.txt").write_text(str(source))
    result = subprocess.run(
        [str(app / "Contents/MacOS/Jardin"), "plan é.ogp"],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    lines = (source / ".local/logs/launcher.log").read_text().splitlines()
    assert unicodedata.normalize("NFC", lines[0]) == str(source)
    assert lines[1:] == ["-m", "open_garden_planner", "plan é.ogp"]


def test_launcher_missing_environment_explains_recovery(tmp_path):
    launcher = tmp_path / "Lancer-Jardin.command"
    shutil.copyfile(ROOT / launcher.name, launcher)
    result = subprocess.run(["bash", str(launcher)], input="\n", capture_output=True,
                            text=True, timeout=10)
    assert result.returncode == 1
    assert "setup_macos.sh" in result.stdout
    assert "Environnement manquant" in result.stdout


def test_launcher_preserves_failure_and_does_not_restart(tmp_path):
    launcher = tmp_path / "Lancer-Jardin.command"
    shutil.copyfile(ROOT / launcher.name, launcher)
    binary = tmp_path / ".venv/bin/python"
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/bash\necho "startup failure"\nexit 7\n')
    binary.chmod(0o755)
    result = subprocess.run(["bash", str(launcher)], input="\n", capture_output=True,
                            text=True, timeout=10, env=dict(os.environ))
    assert result.returncode == 7
    assert "code 7" in result.stdout
    assert (tmp_path / ".local/logs/launcher.log").read_text().count("startup failure") == 1
