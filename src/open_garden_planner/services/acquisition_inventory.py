"""Cross-project acquisition lots, with atomic, conflict-checked publication."""
from __future__ import annotations

import datetime
import math
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from open_garden_planner.core.personal_store import (
    PersonalStoreLoadError,
    PersonalStoreLoadState,
    read_json,
    write_json,
)

_FIELDS = {"id", "name", "kind", "supplier", "purchase_date", "unit_price", "currency",
           "purchased_quantity", "remaining_quantity", "unit", "initial_condition", "notes"}
_TEXT = {"name", "kind", "supplier", "currency", "unit", "initial_condition", "notes"}
_NUMBERS = {"unit_price", "purchased_quantity", "remaining_quantity"}


def validate_acquisition(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("id"), str) or not value["id"].strip():
        raise ValueError("Acquisition requires an identifier")
    result = deepcopy(value)
    for key in _TEXT:
        if key in value and (not isinstance(value[key], str) or len(value[key]) > 20_000):
            raise ValueError(f"Invalid {key}")
    if not value.get("name", "").strip() or not value.get("kind", "").strip():
        raise ValueError("Name and type are required")
    for key in _NUMBERS:
        number = value.get(key)
        if number is not None:
            try:
                valid = (not isinstance(number, bool) and isinstance(number, (int, float))
                         and math.isfinite(number) and number >= 0)
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError(f"Invalid {key}")
    bought, left = value.get("purchased_quantity"), value.get("remaining_quantity")
    if bought is not None and left is not None and left > bought:
        raise ValueError("Remaining quantity cannot exceed purchased quantity")
    date = value.get("purchase_date", "")
    if not isinstance(date, str):
        raise ValueError("Invalid purchase date")
    if date:
        try:
            parsed = datetime.date.fromisoformat(date)
        except ValueError as exc:
            raise ValueError("Invalid purchase date") from exc
        if parsed.isoformat() != date or parsed > datetime.date.today():
            raise ValueError("Invalid purchase date")
    return result


class AcquisitionInventory:
    """An explicit file-backed store; failed writes never replace the loaded state."""

    FILENAME = "acquisitions.json"

    def __init__(self, path: Path):
        self.path = Path(path)
        self._records: list[dict[str, Any]] = []
        self._fingerprint: str | None = None
        self.reload()

    def reload(self) -> None:
        loaded = read_json(self.path)
        raw = [] if loaded.state == PersonalStoreLoadState.ABSENT else loaded.value
        try:
            if not isinstance(raw, list):
                raise ValueError("Inventory must be an array")
            records = [validate_acquisition(item) for item in raw]
            if len({item["id"] for item in records}) != len(records):
                raise ValueError("Duplicate acquisition identifier")
        except (TypeError, ValueError) as exc:
            raise PersonalStoreLoadError(f"Invalid acquisition inventory: {exc}") from exc
        self._records = records
        self._fingerprint = loaded.fingerprint

    def all(self) -> list[dict[str, Any]]:
        return deepcopy(self._records)

    def put(self, fields: dict[str, Any], identity: str | None = None) -> dict[str, Any]:
        if set(fields) - (_FIELDS - {"id"}):
            raise ValueError("Unknown acquisition field")
        if identity:
            index = next((i for i, record in enumerate(self._records) if record["id"] == identity), None)
            if index is None:
                raise ValueError("Acquisition not found")
            record = {**self._records[index], **deepcopy(fields)}
        else:
            index = None
            record = {**deepcopy(fields), "id": str(uuid4())}
        record = validate_acquisition(record)
        candidate = deepcopy(self._records)
        if index is None:
            candidate.append(record)
        else:
            candidate[index] = record
        fingerprint = write_json(self.path, candidate, expected_fingerprint=self._fingerprint)
        self._records, self._fingerprint = candidate, fingerprint
        return deepcopy(record)

    def delete(self, identity: str) -> None:
        candidate = [r for r in self._records if r["id"] != identity]
        if len(candidate) == len(self._records):
            raise ValueError("Acquisition not found")
        fingerprint = write_json(self.path, candidate, expected_fingerprint=self._fingerprint)
        self._records, self._fingerprint = candidate, fingerprint
