"""Qt adapter contract for the pure shared command-history engine."""

import pytest

from open_garden_planner.core.commands import Command, CommandManager


class _Command(Command):
    def __init__(self, name: str, *, fail_execute_on: int | None = None, fail_undo: bool = False) -> None:
        self.name = name
        self.execute_calls = 0
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
        if self.fail_undo:
            raise RuntimeError(f"undo failed: {self.name}")


@pytest.fixture
def manager(qtbot: object) -> CommandManager:
    return CommandManager()


def _capture_signals(manager: CommandManager) -> list[tuple[object, ...]]:
    events: list[tuple[object, ...]] = []
    manager.can_undo_changed.connect(lambda value: events.append(("can_undo", value)))
    manager.can_redo_changed.connect(lambda value: events.append(("can_redo", value)))
    manager.command_executed.connect(lambda text: events.append(("command_executed", text)))
    manager.stack_changed.connect(lambda: events.append(("stack_changed",)))
    return events


def test_manager_preserves_signal_order_and_clear_dirty_contract(manager: CommandManager) -> None:
    events = _capture_signals(manager)
    command = _Command("first")

    manager.execute(command)
    assert events == [
        ("can_undo", True),
        ("command_executed", "first"),
        ("stack_changed",),
    ]
    assert manager.undo_depth == 1
    assert manager.undo_description == "first"

    events.clear()
    manager.undo()
    assert events == [
        ("can_undo", False),
        ("can_redo", True),
        ("stack_changed",),
    ]

    events.clear()
    manager.redo()
    assert events == [
        ("can_undo", True),
        ("can_redo", False),
        ("stack_changed",),
    ]

    events.clear()
    manager.clear()
    assert events == [("can_undo", False), ("can_redo", False)]
    assert manager.undo_depth == 0 and manager.redo_depth == 0


def test_register_applied_uses_same_stack_and_notifications(manager: CommandManager) -> None:
    events = _capture_signals(manager)
    command = _Command("already applied")

    manager.register_applied(command)

    assert command.execute_calls == 0
    assert manager._undo_stack is manager._history.undo_stack
    assert manager._redo_stack is manager._history.redo_stack
    assert manager._undo_stack == [command]
    assert events == [
        ("can_undo", True),
        ("command_executed", "already applied"),
        ("stack_changed",),
    ]


def test_failed_callbacks_keep_stacks_and_emit_no_signals(manager: CommandManager) -> None:
    events = _capture_signals(manager)
    with pytest.raises(RuntimeError, match="execute failed"):
        manager.execute(_Command("refused", fail_execute_on=1))
    assert events == []
    assert manager.undo_depth == manager.redo_depth == 0

    command = _Command("undo refusal", fail_undo=True)
    manager.register_applied(command)
    events.clear()
    with pytest.raises(RuntimeError, match="undo failed"):
        manager.undo()
    assert events == []
    assert manager._undo_stack == [command]
    assert manager._redo_stack == []


def test_failed_redo_remains_redoable_and_emits_no_signals(manager: CommandManager) -> None:
    events = _capture_signals(manager)
    command = _Command("redo refusal", fail_execute_on=2)
    manager.execute(command)
    manager.undo()
    events.clear()
    undo_before = manager._undo_stack.copy()
    redo_before = manager._redo_stack.copy()

    with pytest.raises(RuntimeError, match="execute failed"):
        manager.redo()

    assert events == []
    assert manager._undo_stack == undo_before
    assert manager._redo_stack == redo_before
