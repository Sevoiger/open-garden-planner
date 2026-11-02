"""Monthly botanical periods retain provenance and stay frost-independent."""

import json
from copy import deepcopy

from open_garden_planner.models.botanical_calendar import BotanicalCalendar, MonthlyPeriod
from open_garden_planner.models.plant_data import PlantSpeciesData
from open_garden_planner.services.botanical_calendar import (
    botanical_calendar_from_raw_data,
    calendar_for_species,
    parse_months,
)
from open_garden_planner.services.local_plant_import import read_sheets
from open_garden_planner.services.local_plant_sources import read_local_plant_sources


def test_parse_month_ranges_lists_and_year_wrap() -> None:
    assert parse_months("Avril à Juin") == (4, 5, 6)
    assert parse_months("entre Mars et Mai") == (3, 4, 5)
    assert parse_months("décembre à février") == (12, 1, 2)
    assert parse_months("entre Mars et Mai et Septembre") is None
    assert parse_months("March and April") == (3, 4)
    assert parse_months("Mars à Mai et Septembre à Octobre") == (3, 4, 5, 9, 10)


def test_unrecognized_season_text_is_preserved_without_guessing_months() -> None:
    raw_data = {
        "promesse_de_fleurs": {
            "fields": {"Période de floraison": ["Au printemps"]}
        }
    }

    calendar = botanical_calendar_from_raw_data(raw_data)

    assert calendar.flowering.months is None
    assert calendar.flowering.source_text == "Au printemps"


def test_legacy_sheet_data_is_read_separately_from_frost_offsets() -> None:
    plant = PlantSpeciesData(
        scientific_name="Testus example",
        common_name="Plante exemple",
        transplant_start=-4,
        raw_data={
            "local_sheet": {
                "characteristics": {
                    "Période raisonnable de plantation": "Mars à Mai",
                    "Période de floraison": "Juillet à Août",
                    "Période de taille": "Après floraison",
                },
                "plantation_mois": [3, 4, 5],
                "floraison_mois": [7, 8],
            }
        },
    )

    calendar = calendar_for_species(plant)

    assert calendar.planting.months == (3, 4, 5)
    assert calendar.planting.source_text == "Mars à Mai"
    assert calendar.flowering.months == (7, 8)
    assert calendar.pruning.months is None
    assert calendar.pruning.source_text == "Après floraison"
    assert plant.transplant_start == -4
    assert "botanical_calendar" not in plant.raw_data


def test_conflicting_month_list_and_source_text_are_left_unknown() -> None:
    calendar = botanical_calendar_from_raw_data({
        "local_sheet": {
            "characteristics": {"Période de floraison": "Juillet à Août"},
            "floraison_mois": [6, 7],
        }
    })

    assert calendar.flowering.months is None
    assert calendar.flowering.source_text == "Juillet à Août"


def test_conflicting_explicit_month_lists_cannot_be_overridden_by_text() -> None:
    calendar = botanical_calendar_from_raw_data({
        "local_sheet": {
            "characteristics": {"Période de floraison": "Juillet à Août"},
            "floraison_mois": [7, 8],
            "flowering_months": [6, 7],
        }
    })

    assert calendar.flowering.months is None
    assert calendar.flowering.source_text == "Juillet à Août"


def test_html_source_shape_and_saved_manual_edits_keep_source_text() -> None:
    plant = PlantSpeciesData(
        scientific_name="Testus example",
        common_name="Plante exemple",
        raw_data={
            "promesse_de_fleurs": {
                "fields": {"Période de floraison": ["Août à Octobre"]}
            }
        },
    )
    extracted = calendar_for_species(plant)
    edited = extracted.flowering.with_months((7, 8, 9))
    edited_calendar = BotanicalCalendar(
        planting=extracted.planting,
        flowering=edited,
        pruning=extracted.pruning,
    )
    plant.raw_data["botanical_calendar"] = edited_calendar.to_dict()

    restored = calendar_for_species(PlantSpeciesData.from_dict(plant.to_dict()))

    assert restored.flowering.months == (7, 8, 9)
    assert restored.flowering.source_text == "Août à Octobre"
    assert restored.flowering.manually_edited is True
    assert MonthlyPeriod.from_dict(restored.flowering.to_dict()) == restored.flowering

    cleared = edited.with_months(())
    assert cleared.months == ()
    assert cleared.source_text == "Août à Octobre"
    assert cleared.manually_edited is True


def test_invalid_stored_band_falls_back_without_hiding_other_edits() -> None:
    raw_data = {
        "local_sheet": {
            "characteristics": {
                "Période raisonnable de plantation": "Mars à Avril",
                "Période de floraison": "Mai à Juin",
                "Période de taille": "Septembre à Octobre",
            }
        },
        "botanical_calendar": {
            "planting": {
                "months": [9, 10], "source_text": "Plantation d'origine",
                "manually_edited": True,
            },
            "flowering": {
                "months": [7, 8], "source_text": "Floraison d'origine",
                "manually_edited": True,
            },
            "pruning": None,
        },
    }
    original = deepcopy(raw_data)
    plant = PlantSpeciesData(
        scientific_name="Testus example",
        common_name="Plante exemple",
        raw_data=raw_data,
    )

    calendar = calendar_for_species(plant)

    assert calendar.planting.months == (9, 10)
    assert calendar.planting.manually_edited is True
    assert calendar.flowering.months == (7, 8)
    assert calendar.flowering.manually_edited is True
    assert calendar.pruning.months == (9, 10)
    assert calendar.pruning.source_text == "Septembre à Octobre"
    assert plant.raw_data == original


def test_local_json_sources_are_enriched_with_separate_calendar(tmp_path) -> None:
    path = tmp_path / "plant.json"
    path.write_text(json.dumps({
        "schema_version": "1.0",
        "url": "https://example.test/plante",
        "safe_name": "plante",
        "nom_botanique": "Testus example",
        "nom_commun": "Plante exemple",
        "characteristics": {
            "Période raisonnable de plantation": "Septembre à Octobre",
            "Période de floraison": "Mai à Juin",
        },
        "plantation_mois": [9, 10],
        "floraison_mois": [5, 6],
    }), encoding="utf-8")

    # Installed legacy records retain the source inside local_sheet; the reader
    # can recover it even before the new normalized key has been written.
    historical, = read_sheets(tmp_path)
    recovered = calendar_for_species(historical)
    assert recovered.planting.months == (9, 10)
    assert recovered.flowering.months == (5, 6)

    plant, = read_local_plant_sources(tmp_path)

    stored = BotanicalCalendar.from_dict(plant.raw_data["botanical_calendar"])
    assert stored.planting.months == (9, 10)
    assert stored.planting.source_text == "Septembre à Octobre"
    assert stored.flowering.months == (5, 6)
    assert stored.pruning.months is None


def test_specialized_source_periods_keep_disjoint_winter_ranges_and_climate():
    source = {"local_sheet": {"characteristics": {
        "Meilleure période de plantation": "Novembre à Février",
        "Période de récolte": "Juin et Septembre à Octobre",
        "Période de semis": "Deux semaines avant les dernières gelées",
        "Période de multiplication": "Mars à Avril",
        "Climat de préférence": "Océanique, hors sol gelé",
    }}}
    original = deepcopy(source)
    calendar = botanical_calendar_from_raw_data(source)
    assert calendar.planting_optimal.months == (11, 12, 1, 2)
    assert calendar.harvest.months == (6, 9, 10)
    assert calendar.sowing.months is None
    assert calendar.sowing.source_text == "Deux semaines avant les dernières gelées"
    assert calendar.propagation.months == (3, 4)
    assert source == original
    assert BotanicalCalendar.from_dict(calendar.to_dict()) == calendar


def test_specialized_manual_band_survives_other_invalid_bands_and_frost_offsets():
    plant = PlantSpeciesData("", "Plante", indoor_sow_start=-6, harvest_start=16, raw_data={
        "botanical_calendar": {
            "harvest": {"months": [8, 9], "source_text": "Juillet à Septembre", "manually_edited": True},
            "sowing": "future representation",
        },
        "local_sheet": {"semis_mois": [3, 4]},
    })
    calendar = calendar_for_species(plant)
    assert calendar.harvest.months == (8, 9)
    assert calendar.harvest.manually_edited
    assert calendar.sowing.months == (3, 4)
    assert plant.indoor_sow_start == -6
    assert plant.harvest_start == 16
    assert plant.raw_data["botanical_calendar"]["sowing"] == "future representation"
