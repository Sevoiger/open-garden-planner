"""Qt-free contract and failure-safety tests for the shared command history."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from open_garden_planner.core.command_history import Command, CommandHistory


class _Command(Command):
    def __init__(self, name: str, *, fail_execute_on: int | None = None, fail_undo: bool = False) -> None:
        self.name = name
        self.execute_calls = 0
        self.undo_calls = 0
        self.fail_execute_on = fail_execute_on
        self.fail_undo = fail_undo

    @property
    def description(self) -> str:
        return self.name

    def execute(self) -> None:
        self.execute_calls += 1
        if self.execute_calls == self.fail_execute_on:
            raise RuntimeError(f"execute failed: {self.name}")

    def undo(self) -> None:
        self.undo_calls += 1
        if self.fail_undo:
            raise RuntimeError(f"undo failed: {self.name}")


def test_command_history_module_imports_without_qt() -> None:
    """Prove package import does not transitively load either Qt binding."""
    project_root = Path(__file__).resolve().parents[2]
    script = r'''
import importlib.abc
import sys

class BlockQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".")
               for name in ("PyQt6", "PySide", "PySide6")):
            raise AssertionError(f"Qt import attempted: {fullname}")
        return None

sys.meta_path.insert(0, BlockQt())
from open_garden_planner.core.command_history import Command, CommandHistory

class Noop(Command):
    @property
    def description(self):
        return "noop"
    def execute(self):
        pass
    def undo(self):
        pass

history = CommandHistory()
history.execute(Noop())
history.undo()
history.redo()
assert history.undo_depth == 1
assert not any(name == "PyQt6" or name.startswith(("PyQt6.", "PySide", "PySide6"))
               for name in sys.modules)
'''
    env = os.environ.copy()
    source_path = str(project_root / "src")
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_path, env.get("PYTHONPATH")) if part
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_execute_register_and_branch_transitions_share_one_history() -> None:
    history = CommandHistory()
    first = _Command("first")
    second = _Command("second")

    history.execute(first)
    history.undo()
    assert history.undo_depth == 0
    assert history.redo_depth == 1
    assert history.redo_description == "first"

    history.register_applied(second)
    assert second.execute_calls == 0
    assert history.undo_depth == 1
    assert history.redo_depth == 0
    assert history.undo_description == "second"
    assert history.undo_stack is history.undo_stack
    assert history.undo_stack == [second]

    history.clear()
    assert history.undo_stack == []
    assert history.redo_stack == []
    assert not history.can_undo and not history.can_redo


def test_failed_execute_preserves_both_stacks_including_redo_branch() -> None:
    history = CommandHistory()
    existing = _Command("existing")
    history.execute(existing)
    history.undo()
    undo_before = history.undo_stack.copy()
    redo_before = history.redo_stack.copy()

    with pytest.raises(RuntimeError, match="execute failed"):
        history.execute(_Command("refused", fail_execute_on=1))

    assert history.undo_stack == undo_before
    assert history.redo_stack == redo_before


def test_failed_undo_preserves_undo_and_redo_stacks() -> None:
    history = CommandHistory()
    command = _Command("cannot undo", fail_undo=True)
    history.register_applied(command)
    undo_before = history.undo_stack.copy()
    redo_before = history.redo_stack.copy()

    with pytest.raises(RuntimeError, match="undo failed"):
        history.undo()

    assert history.undo_stack == undo_before
    assert history.redo_stack == redo_before


def test_failed_redo_preserves_undo_and_redo_stacks() -> None:
    history = CommandHistory()
    command = _Command("cannot redo", fail_execute_on=2)
    history.execute(command)
    history.undo()
    undo_before = history.undo_stack.copy()
    redo_before = history.redo_stack.copy()

    with pytest.raises(RuntimeError, match="execute failed"):
        history.redo()

    assert history.undo_stack == undo_before
    assert history.redo_stack == redo_before
