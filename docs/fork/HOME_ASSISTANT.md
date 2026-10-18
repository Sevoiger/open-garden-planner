# Future Home Assistant boundary

No Home Assistant connection, credentials, REST/MQTT service, web interface or
actuation is implemented in tranche 1. `.ogp` remains the source of truth for
plantings, zones, explicit associations and interventions.

Potential read data: stable zone/planting IDs, labels, partial dates, condition,
zone membership and separately planned/completed interventions. The existing
embedded MCP server is loopback-only and requires the application to be open.
Reads use a Qt-thread snapshot; writes use the same validation and commands as
the GUI, require the existing write opt-in/token, and remain in memory until Save.
The tracking tools are `get_garden_record`, `list_zone_plantings`,
`search_garden_interventions`, `set_garden_card`, `add_garden_intervention`,
`edit_garden_intervention`, `delete_garden_intervention`. Actual IDs are returned;
`persisted:false` never claims a disk save. Durable request keys protect additions.

A later persistent companion could expose an explicitly authenticated REST or
MQTT boundary without requiring a GUI. That is a separate architecture decision,
including file ownership/locking and a single writer: it must not independently
rewrite a plan while OGP edits it. A safe first read-only prototype could consume
an intentional JSON export. Server operation and deployment are separate scope.

Future sensor associations should reference zone UUIDs, with an explicit mapping
from sensor identity to zone and measurement type/unit. Store timestamped raw
measurements separately from interpretations and manual interventions. A sensor
reading, recommendation or automation request does not prove that watering or a
soil application occurred. No irrigation control is authorised by this note.
