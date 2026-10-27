"""Qt-free command contract and undo/redo history engine.

The engine owns the only undo and redo stacks. GUI applications can wrap it
with a QObject to publish framework-specific notifications, while headless
sessions can use the same transitions directly.
"""

from abc import ABC, abstractmethod


class Command(ABC):
    """Abstract base class for an undoable operation."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of the command."""

    @abstractmethod
    def execute(self) -> None:
        """Apply the command, including when redoing it."""

    @abstractmethod
    def undo(self) -> None:
        """Reverse the command."""


class CommandHistory:
    """Own one session's undo and redo stacks without a Qt dependency.

    A failed command callback leaves both stacks unchanged. Callers are
    responsible for publishing notifications only after a method returns
    successfully.
    """

    def __init__(self) -> None:
        self._undo_stack: list[Command] = []
        self._redo_stack: list[Command] = []

    @property
    def undo_stack(self) -> list[Command]:
        """The canonical undo stack (exposed for compatible adapters)."""
        return self._undo_stack

    @property
    def redo_stack(self) -> list[Command]:
        """The canonical redo stack (exposed for compatible adapters)."""
        return self._redo_stack

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    @property
    def undo_depth(self) -> int:
        return len(self._undo_stack)

    @property
    def redo_depth(self) -> int:
        return len(self._redo_stack)

    @property
    def undo_description(self) -> str | None:
        if self._undo_stack:
            return self._undo_stack[-1].description
        return None

    @property
    def redo_description(self) -> str | None:
        if self._redo_stack:
            return self._redo_stack[-1].description
        return None

    def execute(self, command: Command) -> None:
        """Run *command*, record it, and discard the abandoned redo branch."""
        command.execute()
        self._undo_stack.append(command)
        self._redo_stack.clear()

    def register_applied(self, command: Command) -> None:
        """Record an already-applied command without running it again."""
        self._undo_stack.append(command)
        self._redo_stack.clear()

    def undo(self) -> Command | None:
        """Undo the latest command and return it, if one exists.

        The command remains on the undo stack if its callback raises.
        """
        if not self._undo_stack:
            return None

        command = self._undo_stack[-1]
        command.undo()
        # Append first so even an allocation failure cannot lose the command
        # from both stacks. These transitions are otherwise non-throwing.
        self._redo_stack.append(command)
        self._undo_stack.pop()
        return command

    def redo(self) -> Command | None:
        """Re-execute the latest undone command and return it, if any.

        The command remains on the redo stack if its callback raises.
        """
        if not self._redo_stack:
            return None

        command = self._redo_stack[-1]
        command.execute()
        self._undo_stack.append(command)
        self._redo_stack.pop()
        return command

    def clear(self) -> None:
        """Discard both stacks."""
        self._undo_stack.clear()
        self._redo_stack.clear()


__all__ = ["Command", "CommandHistory"]
