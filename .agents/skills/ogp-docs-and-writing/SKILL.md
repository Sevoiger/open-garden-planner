---
name: ogp-docs-and-writing
description: Locate and update the OGP document of record when behavior or technical contracts change.
---

# Documentation of record

Update the document that owns a changed behavior or contract. Keep AGENTS.md a
short operating guide and docs/fork/PROGRESSION.md the single fork progress log.
A small edit does not require an ADR, new FR or updates to every document.

| Change | Relevant home |
| --- | --- |
| Personal fork result / pending acceptance | docs/fork/PROGRESSION.md |
| Owner instructions / drawing and catalog use | docs/fork/UTILISATION_JARDIN.md, docs/fork/PLANTES_ET_DESSIN.md |
| Component responsibilities | docs/05-building-block-view/ |
| Runtime interaction | docs/06-runtime-view/ |
| Shared technical pattern / test technique | docs/08-crosscutting-concepts/ |
| Durable architecture choice / non-obvious constraint | docs/09-architecture-decisions/ |
| User capability contract | docs/functional-requirements.md |
| Reusable failure lesson | docs/11-risks-and-technical-debt/ |
| Domain vocabulary | docs/12-glossary/ |

Keep technical upstream documents in English; owner-facing fork guides can use
French. Use UTF-8. Cite concrete behavior and evidence, date volatile measurements,
and describe limitations. Link to history rather than repeating incident narratives
in skills, root instructions and progress tables.

AGENTS.md is the single root instruction file. Skills live in .agents/skills.
After changing agent tooling, run its focused checks; no mirrored library is maintained.

Wiki sources are in docs/wiki/. Validate with scripts/sync_wiki.py --check if
those sources change; this fork does not publish the live wiki. No automatic
upstream publication is implied by writing documentation.

## References

Owner workflow and audit: docs/fork/AGENT_WORKFLOW.md.
Active test and delivery policy: AGENTS.md.
