# Test-suite latency baseline — issue #509

Measured 17 October 2026 against `132f6715` (v1.30.9) on the development box:
Windows, **Python 3.12.9**, PyQt6 6.11.0 (Qt runtime 6.11.0), 20 logical cores,
63.8 GB RAM, `QT_QPA_PLATFORM=offscreen`. CI differs on both axes that matter:
**Python 3.11.9** on `ubuntu-latest` (**4 vCPU**). The two environments are not
directly comparable in either direction, which is why the local and CI figures
below are reported side by side rather than merged.

## Why this document exists

Issue #509 was filed with the claim that the battery "takes ~15.5 minutes and runs
serially on one core". Two of its four proposed remedies were costs, not speedups, and
its central number had never been decomposed. This records the decomposition, so the
next change to the suite is chosen from measurement instead of guessed.

Reproduce:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
venv/Scripts/python.exe -m pytest tests/ -q --durations=0 --timeout=180 > durations.txt
venv/Scripts/python.exe scripts/measure_test_suite_latency.py durations.txt
```

The CI `Test` job now writes the same report and uploads it as the
`test-durations-linux` artifact, so the baseline refreshes on every push instead of
drifting until someone remembers to re-measure.

## The run

| Fact | Value |
|---|---|
| Collected tests | **8 548** |
| Result | `1 failed, 8506 passed, 41 skipped in 912.60s (0:15:12)` |
| Collection overhead alone | 3.4 s |
| Local wall clock, unit layer only | **192.6 s** (3 min 12 s) |

**The one failure is not a product defect and it is not intermittent.**
`tests/unit/test_distribution_inventory.py::test_real_frozen_bundle_passes_the_inventory_check`
asserts `interpreter == "3.11"` ("run frozen verification in the locked builder
interpreter"). This box's venv is Python 3.12.9, so the test fails by design on a
non-locked interpreter. The practical consequence for the development loop is worth
stating plainly: **on this box the full suite is never green**, independent of the
code under test, which erodes the signal the run is supposed to carry.

`--durations=0` reports every phase of 5 ms or more; 20 872 sub-5 ms phases are
hidden. That does not weaken the conclusions below — the visible phases account for
868 s of the 912 s wall clock — but a per-test total from this report is a lower
bound, not the whole cost.

## Phase split

| Phase | Seconds | Share of wall clock |
|---|---|---|
| `call` (test bodies) | 620.6 s | 68 % |
| `teardown` | 128.1 s | 14 % |
| `setup` | 119.4 s | 13 % |
| collection, reporting, hidden sub-5 ms phases | ~44 s | 5 % |

**27 % of the run is pytest's own per-test fixture overhead** across 8 548 tests —
cost proportional to test *count*, not to what any test asserts. That is a different
lever from the one below and should not be attributed to it.

## Where the test time actually is

Measured over the 1 830 tests with a `call` phase above the 5 ms threshold:

| Layer | Tests timed | Seconds | Share | Mean |
|---|---|---|---|---|
| integration | 958 | 415.8 s | 67.0 % | 0.434 s |
| unit | 797 | 166.8 s | 26.9 % | 0.209 s |
| ui | 89 | 27.4 s | 4.4 % | 0.308 s |
| perf | 5 | 10.5 s | 1.7 % | 2.100 s |

### The time is concentrated, not spread

| Concentration metric | Value |
|---|---|
| Median test | **0.030 s** |
| Mean test | 0.339 s (**11.3× the median**) |
| Slowest 1 test | 14.9 s |
| Slowest 10 tests | 21.1 % of measured call time |
| Slowest 50 tests | **50.9 %** |
| Slowest 200 tests | 72.4 % |
| Top decile of files | **71.8 %** |

Ten files out of 228 hold **57 %** of all measured test time:

| Seconds | Share | File |
|---|---|---|
| 96.7 s | 15.6 % | `tests/integration/test_iconography_3c.py` |
| 41.9 s | 6.8 % | `tests/integration/test_theme_switch_chrome.py` |
| 37.2 s | 6.0 % | `tests/integration/test_icon_system.py` |
| 29.7 s | 4.8 % | `tests/unit/test_retracted_figures.py` |
| 28.9 s | 4.7 % | `tests/integration/test_agent_api_default_on.py` |
| 25.8 s | 4.2 % | `tests/unit/test_gate_commands.py` |
| 24.6 s | 4.0 % | `tests/unit/test_texture_forge_conformance.py` |
| 24.5 s | 3.9 % | `tests/integration/test_agent_api_writes.py` |
| 23.5 s | 3.8 % | `tests/integration/test_idle_scene_quiescence.py` |
| 22.1 s | 3.6 % | `tests/ui/test_main_window.py` |

**This is the fork in the road that issue #509 left open, and it now has an answer.**
A suite whose cost is spread evenly across 8 548 independent tests is a suite that
parallel execution fixes. A suite where half the time sits in fifty tests is not —
that is a suite where those fifty tests are doing something expensive, and the right
first move is to find out what.

## Three mechanisms, at three different levels of confidence

### 1. A global theme switch, called in a loop — measured, and the largest single cause

`tests/integration/test_iconography_3c.py` (96.7 s, the most expensive file in the
suite) constructs **no full application**. It builds individual widgets
(`LayerListItem`, `ConstraintListItem`, `PlantSearchPanel`, `CollapsiblePanel`,
`SunSimToolbar`) and calls `apply_theme(app, ThemeMode.LIGHT)` /
`ThemeMode.DARK` — two or three times per test, the third restoring the light theme.

What each of those calls costs, from the source:

- `src/open_garden_planner/ui/icons.py:52` registers
  `lambda _colors: clear_cache()` as a theme listener, so **every** `apply_theme()`
  empties `_icon_cache` and `_pixmap_cache` (`icons.py:42-49`);
- `apply_theme()` then walks `app.topLevelWidgets()` and, via
  `_propagate_theme_colors()`, `app.allWidgets()`, calling `apply_theme_colors()`
  and `refresh_theme_icons()` on every widget that opts in;
- each widget then re-requests its icons, and `_render_svg` puts each one through
  `QSvgRenderer` again (`icons.py:81`), because the cache that would have held it
  was just cleared;
- plus `generate_stylesheet()` (`theme.py:317`, a ~900-line generator) and a
  process-wide `app.setStyleSheet()`.

So one `apply_theme()` is **O(number of live widgets) and cache-destroying**. There
are 48 call sites across 7 test files
(`test_iconography_3c.py`, `test_icon_system.py`, `test_theme_switch_chrome.py`,
`test_icon_provider.py`, `test_theme_tokens.py`, `test_logging.py`,
`test_sun_mismatch.py`); the three most expensive files alone are **28 %** of the
suite's measured test time.

Measured: the call sites and the cache invalidation. **Hypothesis, not yet measured:**
that ~2-3 s per call is dominated by the icon re-render rather than the stylesheet.
The decisive experiment is a one-line ablation — replace the `clear_cache` listener
with a no-op on an identical widget set and re-time — because if that alone collapses
the cost, the fix is scoped to one module and belongs with the product-performance
work in #461 rather than here.

### 2. Full application boots — measured

`tests/ui/test_main_window.py` (22.1 s) and `tests/ui/test_language_menu.py`
construct a real `GardenPlannerApp` per test; 34 test files construct one in total.
`pyproject.toml` already records the cost: "the slowest legitimate tests build the
full app in ~6 s". Sharing one app across tests would remove the repeated boot, but a
shared `GardenPlannerApp` is shared mutable state, and this repo's §11.4 record is
full of exactly that failure mode. Not attempted here; recorded as a known cost.

### 3. Live loopback servers — measured

`test_agent_api_default_on.py` (28.9 s), `test_agent_api_writes.py` (24.5 s) and
friends start a real `AgentApiServer` per test. Each does a real bind plus a
shutdown. These are the tests that make parallel execution non-trivial, see below.

## Corrections to issue #509's stated facts

Recorded rather than silently amended, because each changes what the work is worth:

1. **"Every push costs about 31 minutes of wall clock" was wrong.** The `test` and
   `coverage` jobs have no `needs:` between them, so they run concurrently on
   separate runners: wall clock is `max(Test, Coverage)`, not their sum. What doubles
   is **runner-minutes**.
2. **The suite is 8 548 tests, not 8 551.** The issue's `1 + 8513 + 37` matched a
   run on the #503 branch; `master` has drifted since.
3. **"CI's Test job is the faster environment" was confounded** by the interpreter
   (3.11.9 vs 3.12.9 here) and the core count (4 vCPU vs 20).
4. **The `QSettings` sharing risk is already mitigated.** `tests/conftest.py` derives
   `TEST_APPLICATION` from `os.getpid()`, added on 2026-08-17 (§11.4) precisely so two
   concurrent pytest processes cannot share one store. Parallel workers are separate
   processes, so each gets its own key by construction.
5. **The port risk is smaller than stated.** Every socket test uses `_free_port()`
   (`bind(("127.0.0.1", 0))`, then reads the OS-assigned port). No test binds a fixed
   port; `8765` appears only in settings-default assertions and dialog URL strings,
   and `tests/integration/conftest.py` records that no test ever binds it. The real
   residual is a TOCTOU window, since `_free_port()` closes before the server re-binds.
6. **Criterion 2 (`-m "not perf"`) is worth ~0.5 %.** `tests/perf` is 6.35 s of a
   1 362 s run. It has independent merit — a plain run becomes deterministic, per
   §11.4's #354 entry about not letting timing assertions share a runner with
   correctness tests — but it is not a speed lever.
7. **`Coverage` is not a required check.** Branch protection requires eight contexts
   (`Agent context parity`, `Lint`, `Test`, `Security`, `Dependencies` ×2, `Types` ×2).
   De-duplicating the two runs therefore buys runner-minutes, not gate latency — and
   any restructuring must keep a check literally named `Test`.

## Consequences for the choices ahead

- **The suite's shape suits parallel execution.** Only two session-scoped autouse
  fixtures and three module-scoped ones exist in the whole tree; everything else is
  function-scoped and independent.
- **But `--dist load` (xdist's default) is the wrong setting here.** It splits a
  module across workers, which rebuilds module-scoped fixtures such as
  `tests/unit/test_spike_q3d_board.py::plan` (the 500-object reference plan plus two
  boards) once per worker. `--dist loadfile` keeps a module whole.
- **`pytest-timeout` changes meaning.** The 180 s guard must stay (#448, the
  2026-08-17 stall), but today a Windows timeout dumps stacks and aborts the run;
  under xdist it kills one worker, which is restarted, and the run continues. A hang
  would present as a crashed worker rather than a clean abort.
- **A parallel run's first visible failure mode would be coverage, not tests.**
  `quality/coverage-floors.json` holds exact integer floors per package
  (`covered * 100 // statements`) plus a per-file floor on
  `core/tools/constraint_tool.py`. Losing a single line fails that job, and since
  `Coverage` is not required, the red would sit there unblocking merges while looking
  authoritative.
- **The honest instrument for the concentrated half is a fix, not a scheduler.** Per
  the Amdahl-style projection in the script, perfect `-n 8` would divide the 620 s of
  call time into 78 s. That projection is real but it is also a floor: it ignores
  worker start-up, duplicated collection (3.4 s × N), and duplicated session fixtures.