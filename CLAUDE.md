# Open Garden Planner — personal macOS fork

PyQt6 garden planner with CAD geometry. Target: native Apple Silicon macOS,
editable source execution. Python environment: `.venv/bin/python`.

## Scope and delivery

- Follow the current user request. `MISSION_AGENT_JARDIN.md`, when present, is
  private owner context; keep it and real garden data outside Git.
- Work on a feature branch. Preserve unrelated edits. Inspect `git status`,
  remotes and recent commits before Git operations.
- Push only to the personal fork (`origin`, Sevoiger/open-garden-planner).
  Finish changes in a draft PR, updating an existing one for the same branch.
  No merge, release, manual tag, upstream post or wiki publication in this fork.
- Keep published commit history stable. Use coherent commits; do not force-push
  or rewrite shared history without explicit authorization.
- Remove obsolete worktrees only after checking for uncommitted/untracked work
  and active use. Archive unique work locally before removal.
- Review non-trivial changes independently with `senior-reviewer`; scope it to
  the actual change. A temporary review worktree is optional. Resolve P0/P1 and
  review fixes. If unavailable, disclose the unmet review in the draft PR.
- Windows/Linux packaging and frozen-exe checks are outside acceptance scope.
  Historical upstream release instructions in skills do not authorize them.

## Run and verify

```bash
bash scripts/setup_macos.sh              # initial setup / dependency change
.venv/bin/python -m open_garden_planner   # or open Lancer-Jardin.command
.venv/bin/python -m pytest -c pytest-macos.ini  # small existing workflow set
.venv/bin/python -m pytest tests/path_to_affected_test.py
.venv/bin/python -m ruff check src/ tests/ scripts/
.venv/bin/python scripts/check_agent_context.py
```

Tests use temporary app-data/settings. Preserve that isolation, including child
interpreters; never test against the owner's library or plan. Local disposable
checks and fixes within the request can proceed without repeated confirmation.

## Tests proportional to risk — owner policy, 20 October 2026

- Ordinary UI, catalog, artwork and documentation work: run relevant existing
  tests and applicable static checks. Documentation alone needs no app suite.
- `pytest-macos.ini` is a small starting set, not a completeness claim. Add
  affected tests when the change reaches beyond it. Plain `pytest tests/`
  retains the complete inherited suite.
- Core/persistence/undo, dependency/platform changes or an explicit audit:
  broaden to the affected integration suites and full suite as appropriate;
  explain the scope and any platform exclusions.
- No quota of new tests and no integration test for every small change. Add or
  extend a regression when it protects meaningful behavior not already covered.
  Prefer a real workflow with save/reopen, undo or failure handling where relevant.
- Avoid tests of wording, private implementation details and exhaustive cosmetic
  combinations without distinct risk. Reuse fixtures and existing parametrization.
- Keep inherited regressions. Remove/consolidate a test only with evidence that
  it is redundant or obsolete; reducing a reported count is not evidence.
- Do not rerun passing broad suites without a new change or unresolved concern.
  Report actual results, omissions and pre-existing failures without hiding them.

## Data and implementation contracts

- Preserve `.ogp` compatibility, unknown fields, atomic writes and backups.
  Route mutations through existing services/commands and normal undo/redo.
- `ui/canvas/geometry_apply.py`: shared rect-backed resize path.
  `ui/canvas/arrange.py::build_arrange_command`: shared arrange path.
  `CanvasScene.clear()`: clears private tracking before Qt destroys items.
- Keep plant identity, recorded observations and inferred advice distinct.
  Unknown dates/traits stay unknown. Personal imports require backups and replay
  safety; user-storage imports require the app closed.
- MCP writes keep authentication, validation, idempotence and Qt-thread handling.
  Never print tokens, credentials or private garden data.
- UI strings use `self.tr`, `QCoreApplication.translate` or `QT_TR_NOOP`.
  After changing strings: update/complete registered catalogs, compile and run
  `scripts/check_translations.py`; see `docs/TRANSLATING.md`.

## Read documentation when relevant

| Need | Source |
| --- | --- |
| Personal fork status and manual acceptance | `docs/fork/PROGRESSION.md` |
| Drawing/catalog guide | `docs/fork/PLANTES_ET_DESSIN.md` |
| Upstream stories / requirements | `docs/roadmap.md`, `docs/functional-requirements.md` |
| Persistence, commands, module boundaries | `ogp-architecture-contract` skill |
| Canvas geometry / Qt event behavior | `ogp-qt-cad-reference` skill |
| 3D rendering / art review | `ogp-3d-renderer`, `ogp-lush-cinematic` skills |
| Known failures / diagnostic tools | `ogp-debugging-playbook`, `ogp-diagnostics-and-tooling` |
| Decisions / technical history | `docs/09-architecture-decisions/`, `docs/11-risks-and-technical-debt/` |

Skills are task-specific references. Select by relevance; do not load the whole
library before an edit. Their historical blanket test/build/release procedures
are superseded by this fork policy. For a bug, reproduce and gather evidence;
add temporary tagged logging or stack traces when they help, then remove it.

Update the relevant document when behavior or a contract changes. Record fork
progress in one place, `docs/fork/PROGRESSION.md`; do not grow a history here.
Keep `AGENTS.md` and `CLAUDE.md` identical, mirrored project skills aligned, and
native reviewer instructions equivalent. Check parity after changing them.
See `docs/fork/AGENT_WORKFLOW.md` for the documentation audit and test selection.
