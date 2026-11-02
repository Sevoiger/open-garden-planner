"""Escaped report content from canonical botanical and personal data."""
from html import escape

from open_garden_planner.models.botanical_calendar import (
    CORE_CALENDAR_ACTIVITIES,
    SPECIALIZED_CALENDAR_ACTIVITIES,
)
from open_garden_planner.services.botanical_calendar import calendar_for_species
from open_garden_planner.services.botanical_profile import trait
from open_garden_planner.services.garden_queries import garden_objects, garden_record


def catalog_report_html(entries, labels, *, export_date, snapshot=None, selected_photos=None):
    """Return print content and explicit image references; never fetch a URL."""
    images = []
    def text(value):
        return escape(str(value)).replace("\n", "<br>")

    def heading(key, level=2):
        style = ' style="page-break-before:always"' if key == "personal" else ""
        return f"<h{level}{style}>{text(labels[key])}</h{level}>"

    def rows(values):
        lines = []
        for label, value in values:
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                continue
            if value is None or value == "" or value == "unknown":
                continue
            lines.append(f'<tr><td width="33%"><b>{text(label)}</b></td><td>{text(value)}</td></tr>')
        return '<table width="100%" cellspacing="0" cellpadding="5">' + "".join(lines) + "</table>" if lines else ""

    def photo(asset, owner="", scope="project"):
        if not isinstance(asset, dict) or not asset.get("photo_path"):
            return ""
        uri = f"ogp-photo:{len(images)}"
        images.append((uri, asset["photo_path"], scope))
        caption = " | ".join(str(asset.get(key) or "") for key in ("caption", "credit", "taken_at")
                             if asset.get(key))
        if owner:
            caption = owner + " | " + caption
        return f'<p><img src="{uri}" width="280"><br><i>{text(caption)}</i></p>'

    pages = []
    for identity, plant in entries:
        content = [f'<h1>{text(plant.common_name or plant.scientific_name)}</h1>',
                   f'<p class="scientific">{text(plant.scientific_name)}</p>',
                   f'<p class="muted">{text(labels["exported"])} {text(export_date)}</p>',
                   heading("botanical")]
        from open_garden_planner.services.botanical_media import primary_reference

        reference = primary_reference(plant)
        if reference is not None:
            content.append(photo(reference, scope="library"))
        values = [(labels[key], getattr(plant, key)) for key in
                  ("family", "genus", "min_height_cm", "max_height_cm", "min_spread_cm", "max_spread_cm",
                   "soil_type", "ph_min", "ph_max", "hardiness_zone_min", "hardiness_zone_max", "flower_color", "foliage_color")]
        for key in ("cycle", "growth_rate", "sun_requirement", "water_needs"):
            value = getattr(plant, key).value
            if value != "unknown":
                values.append((labels[key], labels.get(value, value)))
        values.extend((labels[key], trait(plant, key)) for key in labels.get("trait_keys", ()))
        content.append(rows(values))
        if plant.description:
            content.append(f'<p>{text(plant.description)}</p>')
        content.append(heading("calendar"))
        calendar = calendar_for_species(plant)
        activities = CORE_CALENDAR_ACTIVITIES + tuple(
            key for key in SPECIALIZED_CALENDAR_ACTIVITIES
            if getattr(calendar, key).months is not None or getattr(calendar, key).source_text
        )
        table = ['<table width="100%" cellspacing="2" cellpadding="4"><tr><th></th>']
        table.extend(f'<th>{month}</th>' for month in range(1, 13))
        table.append('</tr>')
        for key in activities:
            period = getattr(calendar, key)
            table.append(f'<tr><td><b>{text(labels[key])}</b></td>')
            for month in range(1, 13):
                color = "#347657" if month in (period.months or ()) else "#e7ebe5"
                table.append(f'<td bgcolor="{color}" align="center"><font color="{color}">.</font></td>')
            table.append('</tr>')
        table.append('</table>')
        content.append("".join(table))
        for key in activities:
            period = getattr(calendar, key)
            note = period.source_text or labels["unknown"]
            if period.manually_edited:
                note += " | " + labels["corrected"]
            content.append(f'<p class="muted">{text(labels[key])}: {text(note)}</p>')
        offsets = []
        for key in ("indoor_sow", "direct_sow", "transplant", "harvest"):
            start, end = getattr(plant, key + "_start"), getattr(plant, key + "_end")
            if start is not None or end is not None:
                value = f"{start if start is not None else labels['unknown']} / {end if end is not None else labels['unknown']}"
                offsets.append((labels["frost_" + key], value))
        if offsets:
            content.append(f'<p><b>{text(labels["frost_relative"])}</b></p>')
            content.append(rows(offsets))
        content.append(heading("sources"))
        sources = []
        for provider, url_key in (("local_sheet", "url"), ("promesse_de_fleurs", "source_url")):
            source = plant.raw_data.get(provider, {})
            if isinstance(source, dict) and source:
                sources.extend(((labels["provider"], provider), (labels["source_url"], source.get(url_key)),
                                (labels["source_file"], source.get("source_file"))))
        imported = plant.raw_data.get("catalog_import", {})
        if isinstance(imported, dict):
            sources.append((labels["retrieved"], imported.get("retrieved_at")))
            sources.append((labels["verified_at"], imported.get("verified_at")))
        sources.extend(((labels["source_id"], plant.source_id), (labels["reference_url"], plant.image_url)))
        content.append(rows(sources))
        if snapshot is not None:
            specimens = [record for record in garden_objects(snapshot)
                         if record.get("metadata", {}).get("plant_instance", {}).get("library_plant_id") == identity]
            if specimens:
                content.append(heading("personal"))
            for index, record in enumerate(specimens):
                instance = record.get("metadata", {}).get("plant_instance", {})
                assets = instance.get("media_assets", [])
                selected_assets = [asset for asset in assets if isinstance(asset, dict)
                                   and asset.get("kind") == "personal" and selected_photos is not None
                                   and (record["item_id"], asset.get("id")) in selected_photos] if isinstance(assets, list) else []
                style = ' style="page-break-before:always"' if index and selected_assets else ""
                content.append(f'<h3{style}>{text(record.get("name", ""))}</h3>')
                tracking = record.get("metadata", {}).get("garden_tracking", {})
                history = garden_record(snapshot, record["item_id"])
                values = [(labels[key], instance.get(key)) for key in
                          ("cultivation_mode", "location", "acquisition_supplier", "acquisition_price", "container",
                           "substrate", "old_number", "alias", "nfc_tag", "notes")]
                if isinstance(instance.get("acquisition_currency"), str) and instance.get("acquisition_price") is not None:
                    values[3] = (labels["acquisition_price"], str(instance["acquisition_price"]) + " " + instance["acquisition_currency"])
                from open_garden_planner.models.garden_memory import period

                try:
                    acquisition = period(instance.get("acquisition_period", instance.get("acquisition_date")))
                except (TypeError, ValueError):
                    acquisition = period()
                values.append((labels["acquisition_period"], acquisition["label"] or acquisition["value"]))
                planting_period = history["card"]["period"]
                values.append((labels["planting_date"], planting_period["label"] or planting_period["value"]))
                if isinstance(instance.get("cultivation_mode"), str):
                    values[0] = (labels["cultivation_mode"], labels.get(instance["cultivation_mode"], instance["cultivation_mode"]))
                state = tracking.get("state")
                values.append((labels["state"], labels.get(state, state) if isinstance(state, str) else None))
                content.append(rows(values))
                for measurement in history["measurements"]:
                    content.append(rows([(labels["measured"], measurement["date"]),
                                         (labels["height"], measurement.get("height_cm")),
                                         (labels["spread"], measurement.get("spread_cm")),
                                         (labels["notes"], measurement.get("note"))]))
                for event in history["direct_events"]:
                    occurrence = event["period"]["label"] or event["period"]["value"] or labels["unknown"]
                    content.append(f'<p><b>{text(occurrence)}</b> - {text(event["description"])}</p>')
                for asset in selected_assets:
                    content.append(photo(asset, owner=record.get("name", "")))
        pages.append("".join(content))
    style = ('body { font-family: "Helvetica"; font-size: 10pt; color: #253d30; } '
             'h1 { font-size: 23pt; color: #286248; margin-bottom: 5pt; } '
             'h2 { font-size: 14pt; color: #286248; margin-top: 16pt; } '
             'h3 { font-size: 12pt; margin-top: 13pt; } '
             '.scientific { font-style: italic; font-size: 13pt; } '
             '.muted { color: #5c665d; font-size: 9pt; } '
             'td { border-bottom: 1px solid #e7ebe5; vertical-align: top; }')
    return '<html><head><style>' + style + '</style></head><body>' + '<p style="page-break-before:always"></p>'.join(pages) + '</body></html>', images
