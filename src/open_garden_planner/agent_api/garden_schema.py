"""Discoverable input contracts; the shared garden service remains authoritative."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt

EventType = Literal[
    "planting", "relocation", "amendment", "fertilisation", "mulching", "sowing",
    "pruning", "watering", "observation", "removal_loss",
]
EventStatus = Literal["planned", "done"]
Nonnegative = Annotated[StrictFloat | StrictInt, Field(ge=0, allow_inf_nan=False)]


class ClosedInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PeriodInput(ClosedInput):
    precision: Literal["day", "month", "season", "year", "unknown"]
    value: str = Field(default="", description="YYYY-MM-DD / YYYY-MM / YYYY-season / YYYY; empty for unknown.")
    label: str | None = Field(default=None, description="Original human label; omitted preserves the calendar value.")

    def as_period(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


Occurrence = PeriodInput | str | None


class GardenCardInput(ClosedInput):
    name: str | None = None
    kind: Literal["individual", "group", "zone"] | None = None
    state: Literal["planned", "in_place", "dormant", "uncertain", "confirmed_dead", "removed_moved"] | None = None
    period: Occurrence = None
    quantity: Nonnegative | None = None
    zone_id: str | None = None
    notes: str | None = None
    botanical_name: str | None = None
    cultivar: str | None = None
    provenance: str | None = None
    depth_cm: Nonnegative | None = None
    media: list[str] | None = None


class InterventionDetails(ClosedInput):
    material: str | None = None
    quantity: Nonnegative | None = Field(default=None, description="Absent/null means unknown; 0 means measured zero.")
    unit: str | None = Field(default=None, description="Explicit unit label, e.g. L, kg, g, pcs; no automatic conversion.")
    application: str | None = None
    area_m2: Nonnegative | None = None
    depth_cm: Nonnegative | None = None
    media: list[str] | None = None


class EventTarget(ClosedInput):
    id: str
    name: str


class InterventionPatch(InterventionDetails):
    period: Occurrence = None
    type: EventType | None = None
    status: EventStatus | None = None
    description: str | None = None
    targets: list[EventTarget] | None = None


def input_fields(value: ClosedInput) -> dict[str, Any]:
    """Keep omitted patch fields omitted and let the shared service validate nulls."""
    result = value.model_dump(exclude_unset=True)
    period_value = getattr(value, "period", None)
    if isinstance(period_value, PeriodInput):
        result["period"] = period_value.as_period()
    return result
