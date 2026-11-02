"""Persist and restore UI-only state.

Thin wrapper around the settings backend so that the application doesn't
sprinkle raw key strings throughout `application.py`. Keys live under the
`UiState/` group so they don't collide with app-domain settings handled by
`app/settings.py`. Tab layout uses stable widget IDs, never translated labels.

Note: per-panel collapse/expand state is intentionally NOT persisted. The
sidebar accordion always starts fully collapsed every session (US-226,
ADR-030), so the old ``save_panel_state`` / ``restore_panel_state`` helpers
were removed.
"""

import json
from collections.abc import Sequence

from PyQt6.QtWidgets import QMainWindow, QSplitter

from open_garden_planner.app import settings as app_settings


class UiStateStore:
    """Persist and restore UI-only state (window, splitter, and tab layout)."""

    GROUP = "UiState"

    def __init__(self) -> None:
        # Same backend as everything else in the app, via the one chokepoint
        # (issue #285, ADR-041) — this store used to build its own and so
        # escaped the test isolation layered on AppSettings (docs §11.4).
        # Called module-qualified on purpose: `app_settings.create_qsettings`
        # is then the single name a test can patch to redirect every consumer.
        self._settings = app_settings.create_qsettings()

    def save_geometry(self, window: QMainWindow) -> None:
        self._settings.setValue(f"{self.GROUP}/geometry", window.saveGeometry())
        self._settings.setValue(f"{self.GROUP}/window_state", window.saveState())

    def restore_geometry(self, window: QMainWindow) -> bool:
        geom = self._settings.value(f"{self.GROUP}/geometry")
        if geom is None:
            return False
        window.restoreGeometry(geom)
        state = self._settings.value(f"{self.GROUP}/window_state")
        if state is not None:
            window.restoreState(state)
        return True

    def save_splitter(self, name: str, splitter: QSplitter) -> None:
        self._settings.setValue(f"{self.GROUP}/splitter_{name}", splitter.saveState())

    def restore_splitter(self, name: str, splitter: QSplitter) -> bool:
        state = self._settings.value(f"{self.GROUP}/splitter_{name}")
        if state is None:
            return False
        splitter.restoreState(state)
        return True

    def save_tab_layout(self, order: Sequence[str], visible_ids: Sequence[str]) -> None:
        """Save tab IDs in display order and the subset that should be visible."""
        value = {"order": list(order), "visible": list(visible_ids)}
        self._settings.setValue(
            f"{self.GROUP}/tab_layout", json.dumps(value, separators=(",", ":"))
        )

    def restore_tab_layout(self) -> tuple[list[str], list[str]] | None:
        """Return the saved tab order and visible IDs, or ``None`` if unset/invalid.

        The application validates the IDs against the tabs it currently owns.
        This reader rejects malformed settings without making startup depend on
        user-editable preference data.
        """
        raw = self._settings.value(f"{self.GROUP}/tab_layout")
        if not isinstance(raw, str):
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(value, dict):
            return None
        order = value.get("order")
        visible = value.get("visible")
        if not isinstance(order, list) or not isinstance(visible, list):
            return None
        if not all(isinstance(tab_id, str) for tab_id in (*order, *visible)):
            return None
        return list(order), list(visible)
