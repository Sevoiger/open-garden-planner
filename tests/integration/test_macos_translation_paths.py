"""Translation extraction survives macOS-style symlinked temporary roots."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_translation_gate_with_symlinked_temporary_directory(tmp_path):
    physical = tmp_path / "physical" / "deeper" / "temporary"
    physical.mkdir(parents=True)
    alias = tmp_path / "temporary-alias"
    alias.symlink_to(physical, target_is_directory=True)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_translations.py")],
        cwd=ROOT, env={**os.environ, "TMPDIR": str(alias)},
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
