"""Monthly botanical periods kept separate from frost-relative task dates."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class MonthlyPeriod:
    """A set of calendar months and the source wording that supports it.

    ``months=None`` means that the source did not give a safely interpretable
    period. An empty tuple can represent a manually cleared month selection.
    ``source_text`` remains the imported wording even after a user changes the
    month selection; ``manually_edited`` makes that distinction explicit.
    """

    months: tuple[int, ...] | None = None
    source_text: str = ""
    manually_edited: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.source_text, str):
            raise TypeError("source_text must be a string")
        if not isinstance(self.manually_edited, bool):
            raise TypeError("manually_edited must be a boolean")
        if self.months is None:
            return
        normalized = tuple(self.months)
        if any(type(month) is not int or month < 1 or month > 12 for month in normalized):
            raise ValueError("Months must be integers from 1 through 12")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Months must not contain duplicates")
        object.__setattr__(self, "months", normalized)

    def with_months(self, months: Iterable[int] | None) -> MonthlyPeriod:
        """Return a manual correction while retaining the imported source text."""
        return MonthlyPeriod(
            months=tuple(months) if months is not None else None,
            source_text=self.source_text,
            manually_edited=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "months": list(self.months) if self.months is not None else None,
            "source_text": self.source_text,
            "manually_edited": self.manually_edited,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MonthlyPeriod:
        if not isinstance(data, dict):
            raise TypeError("Monthly period must be an object")
        months = data.get("months")
        if months is not None and not isinstance(months, (list, tuple)):
            raise TypeError("months must be a list or null")
        source_text = data.get("source_text", "")
        manually_edited = data.get("manually_edited", False)
        return cls(
            months=tuple(months) if months is not None else None,
            source_text=source_text,
            manually_edited=manually_edited,
        )


@dataclass(frozen=True, slots=True)
class BotanicalCalendar:
    """Optional recommendations, separate from real dates and frost offsets."""

    planting: MonthlyPeriod = field(default_factory=MonthlyPeriod)
    flowering: MonthlyPeriod = field(default_factory=MonthlyPeriod)
    pruning: MonthlyPeriod = field(default_factory=MonthlyPeriod)
    planting_optimal: MonthlyPeriod = field(default_factory=MonthlyPeriod)
    harvest: MonthlyPeriod = field(default_factory=MonthlyPeriod)
    sowing: MonthlyPeriod = field(default_factory=MonthlyPeriod)
    propagation: MonthlyPeriod = field(default_factory=MonthlyPeriod)

    def to_dict(self) -> dict[str, Any]:
        return {
            "planting": self.planting.to_dict(),
            "flowering": self.flowering.to_dict(),
            "pruning": self.pruning.to_dict(),
            "planting_optimal": self.planting_optimal.to_dict(),
            "harvest": self.harvest.to_dict(),
            "sowing": self.sowing.to_dict(),
            "propagation": self.propagation.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BotanicalCalendar:
        if not isinstance(data, dict):
            raise TypeError("Botanical calendar must be an object")
        return cls(
            planting=MonthlyPeriod.from_dict(data.get("planting", {})),
            flowering=MonthlyPeriod.from_dict(data.get("flowering", {})),
            pruning=MonthlyPeriod.from_dict(data.get("pruning", {})),
            planting_optimal=MonthlyPeriod.from_dict(data.get("planting_optimal", {})),
            harvest=MonthlyPeriod.from_dict(data.get("harvest", {})),
            sowing=MonthlyPeriod.from_dict(data.get("sowing", {})),
            propagation=MonthlyPeriod.from_dict(data.get("propagation", {})),
        )


CORE_CALENDAR_ACTIVITIES = ("planting", "flowering", "pruning")
SPECIALIZED_CALENDAR_ACTIVITIES = ("planting_optimal", "harvest", "sowing", "propagation")
CALENDAR_ACTIVITIES = CORE_CALENDAR_ACTIVITIES + SPECIALIZED_CALENDAR_ACTIVITIES
