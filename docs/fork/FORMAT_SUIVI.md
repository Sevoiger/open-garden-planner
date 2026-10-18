# Perennial tracking format and compatibility

The canonical source of truth remains the `.ogp` JSON document, file format 1.4.
No database or companion server is introduced. See ADR-059. New keys are additive:

- `objects[].metadata.garden_tracking` contains `kind` (`individual`, `group`,
  `zone`), `state`, `period`, optional `quantity`, `zone_id`, `depth_cm`, media and
  provenance. Existing object UUIDs and shape geometry are reused. Names remain
  `objects[].name`; notes and cultivar reuse `metadata.plant_instance.notes` and
  `variety_cultivar`. There is no second editable store for those fields.
- `garden_memory = {"version":1,"events":{event_id:event},"requests":{key:receipt}}`
  is the only intervention store. Existing positional journal notes remain notes,
  not duplicated interventions. Recording a relocation is explicit; moving a
  drawing does not create one. Existing automatic `parent_bed_id` remains the
  geometry/container relationship. `garden_tracking.zone_id` is the explicit
  business association and never changes Qt parenting or child movement.

A period is `{precision,value,label}`. Precisions are `day` (`YYYY-MM-DD`),
`month` (`YYYY-MM`), `season` (`YYYY-spring/summer/autumn/winter`), `year` (`YYYY`),
or `unknown` (empty value). The original label is retained. Exact dates also
populate the legacy `plant_instance.planting_date`; partial/unknown edits remove
that exact field. Legacy projects without tracking read their exact field if
present and otherwise remain unknown. Loading never stamps today.

Search boundaries are explicitly conventional northern-hemisphere calendar
periods: spring March–May, summer June–August, autumn September–November, winter
December–February of the following year. Date filters use inclusive overlap;
unknown dates match unbounded searches only. Results sort by conventional start,
then stable event ID; unknown dates sort last. No derived boundary is persisted
as an exact occurrence date. Seasons/years permit 0001–9998; exact days/months
use the ISO calendar supported by Python. These are software intervals, not local
phenology or weather observations.

States are `planned`, `in_place`, `dormant`, `uncertain`, `confirmed_dead`,
`removed_moved`. Dormancy never implies death. Archival by state retains the
object and its identity. Each event has its stable ID, UTC creation timestamp,
occurrence period, type, status (`planned`/`done`), nonempty description and
one or more `{id,name}` target references. Names are historical readable fallbacks
if targets are deleted; renaming does not break UUID links. Quantity, historical
treated area (`area_m2`) and depth (`depth_cm`) are optional finite nonnegative
numbers. Null/missing is distinct from zero; comma decimals are accepted on input.
Materials, units, application and project-relative media are optional. No event
recomputes laboratory measurements or derives mass from volume.

First explicit card/event write activates perennial continuity (a nonempty
versioned store). Copying a season in this mode carries every object ID, note,
intervention, receipt and history. Ordinary clipboard copies create ordinary OGP
object identities; they do not duplicate intervention entries. An explicitly
copied planting retains its zone reference until reassigned. Legacy projects
without tracking retain upstream New Season behaviour.

All GUI and MCP writes use `GardenMemoryService` and `GardenEditCommand` on the
Qt thread, participating in ordinary dirty state and global undo/redo. Creation
receipts persist a canonical content fingerprint and actual event ID. A replay
with changed content conflicts; deleting an event keeps its receipt, so a retry
cannot recreate it. Undo/redo restores event snapshots but deliberately retains protocol receipts.
A late retry after undo cannot resurrect the intervention; an intentional fresh
addition uses a new request key. Creation time and event identity cannot be
patched. New media references must resolve to files inside the saved project.

JSON export is the complete canonical snapshot plus `garden_export_version:1`.
CSV is UTF-8 with BOM, one card/event per row, with precision/value/label kept
separate, stable target IDs/names and optional quantities left empty rather than
zero. It is a consultation export, not an import/edit synchronisation format.

Portable ZIPs contain `garden.ogp` and content-addressed media. Background PNGs
are already embedded by OGP. Restore refuses existing destinations, traversal,
symlinks, duplicate paths and archives over 2,000 entries or 1 GB uncompressed.
Attachment imports are copied to `<project>.assets/`; cancelling edits never
silently deletes them. Save As/perennial copies retain relative media references
and copy their files; conflicting destination contents are refused. Archives
can relocate legacy external media by explicitly copying the referenced files.

Before first enrichment of an existing file, save creates an exclusive numbered
`.pre-jardin.ogp` backup. Validation still precedes scene replacement and atomic
file writes. The fork additionally retains opaque unknown top-level keys and
metadata fields through a load/save round trip; the obsolete upstream
`paper_layouts` omission remains deliberate. This does not claim support for
unknown future object types or file versions.

The fork reads older `.ogp` files. Unmodified upstream 1.30.10 ignores and drops
`garden_memory` when resaving: it does not know intervention or idempotence data.
It may retain opaque item metadata, but cannot keep partial and exact date edits
coherent. Do not round-trip a tracked project through upstream as a preservation
workflow. Keep the `.pre-jardin` original and use this fork plus portable backups.
