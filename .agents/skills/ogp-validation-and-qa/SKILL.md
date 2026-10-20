---
name: ogp-validation-and-qa
description: Choose meaningful OGP checks and regression coverage according to the changed behavior and risk.
---

# Validation proportional to risk

Use the owner policy in AGENTS.md. Select checks for behavior the diff can affect.
No test-count target, new test per edit, compulsory second test layer or automatic
full rerun. Existing passing checks need repeating only after a relevant change
or when an unresolved concern requires it.

## Select evidence

- Documentation/process only: links, context validity and applicable script checks.
- UI/catalog/art: existing affected workflows plus rendering/interaction checks
  where relevant. pytest-macos.ini provides a small optional starting set.
- Persistence/undo/core: round trips, old/additive data, failed-write atomicity,
  command history and the wider affected integration suites; full suite when
  appropriate. Dependency/platform work and audits warrant broader validation.
- MCP: real authenticated and refused writes, undo, replay and save/reopen when
  the changed boundary affects them. Use isolated data and loopback only.
- Text changes: source and compiled translation coverage for all registered locales.

Add or extend a regression for a meaningful uncovered failure. Favor observable
behavior over private implementation and avoid cosmetic Cartesian products that
exercise no distinct contract. Existing tests can be consolidated or removed only
with evidence of duplication or obsolescence, not merely to lower a count.

## Test mechanics worth preserving

Qt tests use qtbot for initialization and teardown. Existing fixtures isolate app
data, settings and network workers; preserve isolation in child interpreters too.
Never run against the owner's actual libraries or plan.

For real drag-handle/event dispatch, use QTest on the viewport; direct tool calls
are appropriate for tool logic but do not prove Qt delivery. Assert the actual
scene coordinates. Geometry changes need representative rotated cases as well
as the identity angle. Reuse the existing golden plant-parent matrix when a
shared bed/container action changes instead of duplicating it in a new suite.

State what passed and what was omitted. A headless render does not establish
native visual acceptance. Provide only the short manual checks automation cannot
establish. Pre-existing failures stay visible; do not loosen assertions to hide them.

## References

Practical fork selection: docs/fork/AGENT_WORKFLOW.md.
Qt behavior: ogp-qt-cad-reference. Upstream test history and fixtures:
docs/08-crosscutting-concepts/ section 8.10, subject to the owner's fork policy.
