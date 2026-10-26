"""Soil-test facade over project storage and localized presentation.

Soil ratings, amendment recommendations and mismatch judgements live in the
Qt-free :mod:`open_garden_planner.services.soil_assessment` module. This class
keeps the historical API used by the canvas and dialogs while adapting the
pure results to the live ``ProjectManager`` and Qt translations.
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from PyQt6.QtCore import QCoreApplication

from open_garden_planner.models.amendment import AmendmentRecommendation
from open_garden_planner.models.soil_test import SoilTestHistory, SoilTestRecord
from open_garden_planner.services.amendment_loader import AmendmentLoader
from open_garden_planner.services.soil_assessment import (
    _NUTRIENT_KINDS,
    ALL_PARAMS,
    GLOBAL_TARGET_ID,
    MISMATCH_K_HIGH_DEMAND,
    MISMATCH_N_HIGH_DEMAND,
    MISMATCH_P_HIGH_DEMAND,
    MISMATCH_PH_HIGH,
    MISMATCH_PH_LOW,
    MISMATCH_REASON_CODES,
    PARAM_K,
    PARAM_N,
    PARAM_OVERALL,
    PARAM_P,
    PARAM_PH,
    HealthLevel,
    assess_mismatches,
    calculate_amendments,
    health_level,
    is_test_overdue,
    overlay_rgba,
)

if TYPE_CHECKING:
    from open_garden_planner.core.project import ProjectManager
    from open_garden_planner.models.plant_data import PlantSpeciesData


class SoilService:
    """Read/write access to project soil-test history and assessment facade."""

    def __init__(self, project_manager: ProjectManager) -> None:
        self._pm = project_manager

    def get_history(self, target_id: str) -> SoilTestHistory:
        """Return the history for ``target_id`` (empty if none recorded)."""
        raw = self._pm.soil_tests.get(target_id)
        if raw is None:
            return SoilTestHistory(target_id=target_id)
        return SoilTestHistory.from_dict(raw)

    def get_effective_record(self, bed_id: str) -> SoilTestRecord | None:
        """Resolve the bed's latest record, then the project's global default."""
        return self.get_effective_record_with_source(bed_id)[0]

    def get_effective_record_with_source(
        self, bed_id: str
    ) -> tuple[SoilTestRecord | None, str, SoilTestHistory]:
        """Return effective record, source and history used for staleness."""
        bed_history = self.get_history(bed_id)
        if bed_history.latest is not None:
            source = "global" if bed_id == GLOBAL_TARGET_ID else "bed"
            return bed_history.latest, source, bed_history
        global_history = self.get_history(GLOBAL_TARGET_ID)
        if global_history.latest is not None:
            return global_history.latest, "global", global_history
        return None, "none", bed_history

    def add_record(self, target_id: str, record: SoilTestRecord) -> None:
        """Append a test and mark the project dirty through its storage API."""
        history = self.get_history(target_id)
        history.records.append(record)
        self._pm.set_soil_test_history(target_id, history)

    @staticmethod
    def health_level(record: SoilTestRecord | None, parameter: str) -> HealthLevel:
        """Rate ``record`` using the shared Qt-free assessment rules."""
        return health_level(record, parameter)

    @staticmethod
    def overlay_rgba(level: HealthLevel) -> tuple[int, int, int, int] | None:
        """Return the tint for a known health level."""
        return overlay_rgba(level)

    @staticmethod
    def calculate_amendments(
        record: SoilTestRecord | None,
        target_ph: float = 6.5,
        target_n: int = 3,
        target_p: int = 3,
        target_k: int = 3,
        bed_area_m2: float = 0.0,
        loader: AmendmentLoader | None = None,
        enabled_ids: set[str] | None = None,
        prefer_organic: bool = True,
    ) -> list[AmendmentRecommendation]:
        """Use the shared pure amendment calculator."""
        return calculate_amendments(
            record,
            target_ph=target_ph,
            target_n=target_n,
            target_p=target_p,
            target_k=target_k,
            bed_area_m2=bed_area_m2,
            loader=loader,
            enabled_ids=enabled_ids,
            prefer_organic=prefer_organic,
        )

    @staticmethod
    def get_mismatched_plants(
        record: SoilTestRecord | None,
        plant_specs: list[PlantSpeciesData],
    ) -> list[tuple[PlantSpeciesData, list[str]]]:
        """Return localized reasons for plants mismatched with the soil test."""
        return [
            (spec, [text for _code, text in reasons])
            for spec, reasons in SoilService.get_mismatch_details(record, plant_specs)
        ]

    @staticmethod
    def get_mismatch_details(
        record: SoilTestRecord | None,
        plant_specs: list[PlantSpeciesData],
    ) -> list[tuple[PlantSpeciesData, list[tuple[str, str]]]]:
        """Render the pure mismatch codes with the installed Qt translator."""
        results: list[tuple[PlantSpeciesData, list[tuple[str, str]]]] = []
        for spec, reasons in assess_mismatches(record, plant_specs):
            rendered: list[tuple[str, str]] = []
            for reason in reasons:
                arguments = reason.argument_dict()
                if not arguments.get("name"):
                    arguments["name"] = QCoreApplication.translate("SoilService", "Plant")
                message = QCoreApplication.translate(
                    "SoilService", reason.translation_key
                )
                rendered.append((reason.code, message.format(**arguments)))
            results.append((spec, rendered))
        return results

    @staticmethod
    def is_test_overdue(history: SoilTestHistory | None, today: date) -> bool:
        """Delegate seasonal staleness to the same pure service calculation."""
        return is_test_overdue(history, today)


__all__ = [
    "ALL_PARAMS",
    "GLOBAL_TARGET_ID",
    "HealthLevel",
    "_NUTRIENT_KINDS",
    "MISMATCH_K_HIGH_DEMAND",
    "MISMATCH_N_HIGH_DEMAND",
    "MISMATCH_PH_HIGH",
    "MISMATCH_PH_LOW",
    "MISMATCH_P_HIGH_DEMAND",
    "MISMATCH_REASON_CODES",
    "PARAM_K",
    "PARAM_N",
    "PARAM_OVERALL",
    "PARAM_P",
    "PARAM_PH",
    "SoilService",
]
