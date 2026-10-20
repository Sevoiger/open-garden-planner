---
name: debug-verbose
description: Diagnose an unresolved OGP bug with targeted instrumentation when existing evidence is insufficient.
---

# Focused investigation

Reproduce the symptom and identify the unresolved competing explanations. Add temporary tagged logging or a stack trace at the boundary that distinguishes them. Run the failing path with isolated data, fix the demonstrated cause, remove instrumentation and verify that path. A case-study write-up is useful only for a reusable finding.

Search [technical history](references/technical-history.md) for the relevant symbol
or symptom and read only that section. The reference preserves upstream evidence,
not current delivery obligations. Apply root AGENTS.md for tests and delivery.
