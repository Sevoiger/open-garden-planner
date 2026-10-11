---
name: ogp-3d-renderer
description: "Measured facts and runbook for Open Garden Planner's Qt Quick 3D renderer (Phase 17, ADR-048; spike in src/open_garden_planner/spike_q3d/, production package ui/view3d/quick3d/ from L1.2). Load when: writing or debugging anything that imports QtQuick3D/QtQuick/QtQml/QtQuickWidgets; feeding numpy geometry or textures to the engine; placing the sun, sky, shadows or camera; choosing render presets or post effects; hosting a View3D in a QQuickWidget or QQuickView; picking; taking screenshots or offscreen renders; rendering in CI (xvfb + Mesa, Windows WARP); packaging Qt Quick 3D in the PyInstaller exe; or when a 3D frame is black, white, mirrored, washed out, slow or seems to hang. Only facts measured in this repo, each with where and how it was measured."
---

# Qt Quick 3D renderer — measured facts and runbook

**Status:** v0, written from the Package L0 spike (ADR-048, *Accepted — GO 2026-10-05*). Every number below
was measured in this repo; the machine is named because software rasterisers (Mesa llvmpipe,
Windows WARP) say nothing about absolute GPU speed — only about correctness, ratios and
relative cost. Art direction lives in `ogp-lush-cinematic`; this skill is the engine.
Since L1.2 the production package `ui/view3d/quick3d/` exists: its measured facts and commands
are §12, and rows it added to the tables below say "(L1.2)".

## 1. Boundary rules (pinned by tests)

- The spike is **dormant**: dispatched from `main()` on `--spike-q3d`, never imported at app
  start (`tests/unit/test_spike_q3d_isolation.py`).
- **One engine module.** Only `spike_q3d/quick.py` may import `PyQt6.QtQuick3D`, `QtQuick`,
  `QtQml` or `QtQuickWidgets` (AST scan in the same test). The production package inherits the
  rule as `ui/view3d/quick3d/`.
- **One engine package (L1.2).** In the product, only `ui/view3d/quick3d/` imports those
  bindings — and inside it only `geometry.py` and `host.py` do; `selftest.py` names them as
  strings to import them one by one. Pinned over the WHOLE tree, imports by name included
  (`__import__("PyQt6.QtQuick3D")` is an import), by `tests/unit/test_quick3d_boundary.py`,
  which also pins that app start loads neither the bindings nor the package and that the
  package never uses a `QQuickWidget`.
- **QML carries no user-visible strings** — `pylupdate6` does not extract `qsTr`, so any text
  goes through a QWidget overlay with `self.tr()` (§8.3).
- `spike_q3d/meshes.py` is **Qt-free numpy** (it graduates into `core/`).
- **`core/scene3d/` is Qt-free** (standard library + numpy; AST scan in
  `tests/unit/test_scene3d_contract.py`). That is a rule about its source — no module there uses
  Qt — not about the interpreter: importing the package runs `core/__init__.py`, which loads
  PyQt6 for other `core` modules. It is the production side of the engine boundary since
  L1.1 (ADR-054, §8.26.1): an engine adapter implements `core/scene3d/sink.py::EngineSink` and is
  driven by `SceneSync`; builders are tested against `RecordingSink` with no GPU.
- **`EngineSink` has ten methods, and `clear()` is the one an adapter gets wrong.** After
  `clear()` the engine holds nothing — no items, no sun, no ground, no open transaction — and it
  must work at any time: inside a transaction (abort it), on an empty scene, and after one of the
  adapter's own calls failed half way. `SceneSync.reset()` recovers through it and through
  nothing else. Test an adapter's recovery the way `tests/unit/test_scene3d_sync.py` tests the
  double: fail a call in mid-transaction, `reset()`, apply, and compare with a from-scratch
  apply (`sync.expected_state()`).
- **A record's `footprints` are outer rings only; its `holes` are cut out of them.** A closed
  fence is one outer ring and one hole. Anything that gives a footprint area — a mesh, the ground
  bake, a pick shape — iterates `record.regions`. `verify_builder` refuses a solid built over a
  hole.

## 2. PyQt6 6.11 binding facts

| Need | What works | Measured trap |
|---|---|---|
| Custom geometry | `QQuick3DGeometry` subclass, `setVertexData`/`setIndexData` | — |
| Textures from Python | `QQuick3DTextureData`, `Format.RGBA8` | detached from its `Texture` and attached again, the same object draws the surface **untextured (white)**; `update()` does not help, `setTextureData(textureData())` + `update()` (or a fresh object) does — §5 |
| Instancing | not bound → merge meshes (static batching) | — |
| Geometry lifetime | one `QQuick3DGeometry` per Model lifetime | once its Model is destroyed, the same geometry given to a new Model **renders and picks nothing** (frame = empty scene, 0/20 picks); `update()` does not help, a full re-upload (`set_mesh`) does |
| `Repeater3D` over a JS array | fine for a fixed set | **any** change of the array destroys and recreates **every** delegate — combined with the row above, every model came back blank; use per-item creation or a list model with incremental inserts |
| Pick result | `View3D.pick()` inside a QML helper that returns a plain JS object | the return value arrives as **`QJSValue`**: call `.toVariant()` before `dict()` |
| Surface format | — | `QQuick3D.idealSurfaceFormat()` **before** `QGuiApplication` exists **segfaults** (Linux) |
| Models from Python (L1.2) | a QML function (`createPart`) that calls `Component.createObject(parentNode, {...})`, invoked with `QMetaObject.invokeMethod(root, "createPart", Qt.ConnectionType.DirectConnection, Q_RETURN_ARG("QVariant"), Q_ARG("QVariant", …))` | the Model arrives as a `QQuick3DObject`; `setProperty("position" / "eulerRotation", QVector3D)` works; pass the geometry as an ARGUMENT (QML converts it), keep the Python reference as long as the Model lives |
| Destroying a Model (L1.2) | `retired = true` (a property of ours), `visible = false`, `pickable = false`, `geometry = null`, then `destroy()` | `destroy()` is deferred: a frame in between would still draw the Model. A listing must skip it by `retired`, never by `visible` — a Model hidden on purpose is still the engine's |
| Writing a scene property from Python (L1.2) | `metaObject().indexOfProperty(name) >= 0` first, then `setProperty` and its result (`host._set`) | every `setProperty` result was ignored at first; a property renamed in `Garden.qml` is now an `EngineError` that names it, at host construction (Qt documents that an undeclared name becomes a dynamic property and returns False — silently) |
| The graphics API's name (L1.2) | `rendererInterface().graphicsApi()` through a table keyed by the enum MEMBER (`host.GRAPHICS_API_NAMES`: `Direct3D11`, `OpenGL`, …) | PyQt6 6.11 names the members `OpenGL` but `Direct3D11Rhi`, `VulkanRhi`, `MetalRhi`, `NullRhi`, and `Direct3D12`: a list of strings typed on Windows (`OpenGLRhi`) failed the first Linux run |
| A vertex shader that sways (L1.2) | multiply the displacement by a factor that is exactly 0 at rest: `smoothstep(0.0, 2.0, uTime)` | the spike's `sin(phase) × strength × weight` is not 0 at time 0: boxes with full wind weight, in a wind 40 × the look's, were drawn 10.2 px and 18.9 px (20 cm, 38 cm) beside their meshes, with no message |
| Exposure (L1.2) | `ExtendedSceneEnvironment.exposure` (`QtQuick3D.Helpers`) | the plain `SceneEnvironment` has no `exposure`: a QML load error |
| Fill light without a probe (L1.2) | a second `DirectionalLight { brightness: 0; ambientColor: … }` | — (measured: it still contributes its ambient term, so the fill stays when the sun is switched off) |
| `baseColor` vs a shader uniform (L1.2) | `PrincipledMaterial.baseColor` takes sRGB and is linearised by the engine; a `vector3d` uniform of a `CustomMaterial` is passed as it is | the engine's sRGB curve is an approximation: the same tint read 171 through `baseColor` and 169 through an exact linear uniform |
| Vertex colour in a fragment-only custom shader (L1.2) | `VAR_COLOR` | — (D3D11 on GPU and WARP; OpenGL unverified) |

## 3. Frame and geometry

- **Frame mapping, exactly once:** scene (E, N, up) → engine (x = E, y = up, z = −N);
  determinant +1, so triangle winding survives. Done in `quick.py`, never in QML.
  The production mapping is `core/scene3d/frame.py` (`scene_to_engine_points` / `_vectors` and
  their inverses, exact column selection). Meshes there are in the **item-local** frame and a
  record's transform poses them: `engine_pose(transform)` gives the Model position
  `(east_cm, base_cm, −north_cm)` and a turn of `+rotation_deg` about the engine's +Y — proven
  equal to posing in the scene and mapping the result (`tests/unit/test_scene3d_frame.py`), and
  **measured on the engine in L1.2**: `eulerRotation.y = +rotation_deg` is the
  counter-clockwise turn seen from above — a 240 × 60 cm bar matched the CPU prediction with
  mask IoU 0.9994 at 17° and 0.9895 at 213.5°, the opposite sense with 0.288 and 0.157
  (`tests/integration/test_quick3d_render.py`).
- **The byte layout is Qt-free since L1.2:** `core/scene3d/interleave.py::engine_buffers(mesh)`
  builds the vertex and index bytes (and maps the frame, once);
  `ui/view3d/quick3d/geometry.py::BufferGeometry` only hands them to Qt — one upload per
  object, no re-upload method.
- **The top-down view is linear (L1.2).** With `TonemapModeNone` a pixel is linear radiance,
  so it can be computed: a lit horizontal face reads `255 × tint × (brightness × sin(elevation)
  × sun colour + fill)`, all in linear light — predicted 167.2, measured 171.
- **Vertex layout, stride 48 bytes:** position f32×3 @0, normal f32×3 @12, colour f32×4 @24
  (**linear**, not sRGB), uv f32×2 @40 (spike convention: u = wind weight, v = phase), indices
  U32; `setBounds` + `update()`.
- **Update cost** of replacing a 100k-vertex geometry (`NumpyGeometry.set_mesh`: frame mapping +
  interleave + copy): median **10.5 ms** (n = 10, max 25 ms; cloud container CPU,
  `--update-bench`), 14–27 ms on Windows runner instances. GO criterion 6 says ≤ 10 ms —
  borderline; measure on the owner box before optimising, and look at the interleave copy
  first.

## 4. Light, sky, shadows

- A `DirectionalLight` shines down its **local −Z**. Orient it with
  `QQuaternion.rotationTo(QVector3D(0, 0, -1), travel)`; `lookAt` exists on **cameras only**
  (QML: "lookAt is not a function"). Light travel = −sun vector from `core/solar` (ADR-037).
- **Property names differ:** Models use `castsShadows` / `receivesShadows`; Lights use
  `castsShadow`. The wrong one is a QML load error, not a warning.
- `ProceduralSkyTextureData`: **`sunLongitude = azimuth + 90°`** (sky probe, disc read 25°
  off-centre: max 0.85° on OpenGL, 1.4° on D3D11 — including up to ~0.6° of the probe's own
  error before `probes.pixel_bearing` inverted the camera's 14° pitch; a centred disc only
  proves the direction). The environment pre-filters the light probe **once per Texture object** and ignores
  later `textureData` changes, so build a fresh sky `Texture` on every sun change
  (`createObject(view.scene)`, destroy the old one — parenting to `view.scene` avoids the "not
  placed in the graphics scene" warning).
- **`ProceduralSkyTextureData` regenerates the whole sky synchronously, on the GUI thread, on
  every input change** (~190 ms each at `SkyTextureQualityHigh`, llvmpipe). Never bind its
  inputs live, and build no sky before the first sun is known: the spike's live-bound sky
  regenerated for each look and sun input during an open, and its initial one was thrown away.
  Build it once per sun change — create the data at `SkyTextureQualityLow`, set every input,
  raise the quality last (one full generation). Measured once (llvmpipe, 640×360, golden hour;
  commit a275da2, the number every record cites): QML load 1709 → 70 ms, `set_look` 791 →
  0.1 ms, `set_sun` 2192 → 344 ms, a mood + sun change 3110 → 365 ms; frames bit-identical.
- If the image-based light out-shines the sun, the whole frame reads flat and blue —
  `probeExposure` 0.35–0.55 against sun brightness 1.7–2.1 (rigs in `ogp-lush-cinematic` §3).
- **Shadow truth** (box 100 × 100 × 200 cm, azimuth 225°, IoU of the engine's shadow-map
  footprint vs the analytic 2D shadow, top-down orthographic, `--iou`):

  | Backend / machine | Size | 15° | 35° | 60° |
  |---|---|---|---|---|
  | OpenGL, Mesa llvmpipe (container) | 1280×720 | 0.959 | 0.950 | 0.920 |
  | OpenGL, Mesa llvmpipe (container) | 960×540 | 0.980 | 0.985 | 0.964 |
  | Direct3D 11, WARP (windows-latest) | 960×540 | 0.980 | 0.984 | 0.963 |

  Settings behind those numbers: `shadowBias` 5, `shadowMapFar` 9000, `lockShadowmapTexels`,
  the High preset's quality and PCF — but **no cascades**: the orthographic probe view forces
  `csmNumSplits: 0`, so the cascaded configuration of the beauty views is not covered yet.
  The value depends on frame size (pixel grid), not on the backend. Low preset (VeryHigh map
  since creator round 4, one cascade, bias 15; the probe still runs without cascades):
  0.953 / 0.959 / 0.922 at 1280×720, 0.985 / 0.983 / 0.967 at 640×360 (High map before:
  0.963 / 0.968 / 0.937 at 1280×720).
- **Shadow bias at grazing light:** 1024-texel maps (low, medium) at `shadowBias` 5 left a
  sun-grazed roof acned and stair-stepped (33–40 % of the slope); 15 measured clean with the IoU
  gate unchanged. VeryHigh quality alone: 33 %; one cascade split: 8 %; 32-bit map: 15 %.
- **Fog vs sky:** the skybox is drawn × `probeExposure`, `Fog` is not — fog = sky horizon ×
  probe exposure × 0.8 (linear) meets the sky within ±1.3 luma. The sky's
  `groundHorizonColor` shows in a sliver under a far-clipped ground plane: give it the sky
  horizon colour, not the fog colour (that drew a dark line). That alone still left a
  1–3 px dark line (dip 9–10 luma, llvmpipe): `ProceduralSkyTextureData`'s default
  `groundCurve` 0.02 is ~32 % of the way to `groundBottomColor` 0.7° below the horizon —
  about one texel of the sky texture — so the filtered horizon row darkens. `groundCurve`
  0.1 with `groundEnergy` 1.0 measured 0.1–0.2 (the brighter lower sky lifts shaded walls
  ~5 luma; ground shadows unchanged).
- **PrincipledMaterial and water:** its grazing sky reflection ignores `specularAmount`,
  `fresnelScale` and `fresnelPower`, and its image-light diffuse is scaled by
  (1 − `specularAmount`). A custom material may sample the probe with Qt's
  `qt_sampleDiffuse` / `qt_sampleGlossy` — but only inside `#if QSSG_ENABLE_LIGHT_PROBE`,
  or the shader fails to compile in views without a probe (OpenGL verified; D3D11 is the
  Windows evidence run's job).
- **Double-sided leaves:** with `cullMode: NoCulling` back faces get the flipped normal — a
  front-only light term turns grass into black stubble; light both sides.
- **Sharpening is on or off:** `sharpnessAmount` 0.08 overshot into near-black, red-less pixels
  (1,500–3,100 per daytime frame at 1280×720: teal-black slits in picket fences, black stubble
  on lawns); 0.03 is nearly as bad (noon 1,502 vs 1,554), and only 0.0 is clean. Leave it at
  0 (edge energy −16–29 %, an owner taste question), and keep the render tier's speckle gate.
- **Anti-aliasing at low:** `fxaaEnabled` lives on `ExtendedSceneEnvironment`,
  `specularAAEnabled` on `SceneEnvironment`; keep FXAA off in probe views (it softens the
  measured pixels).
- **Deterministic boards:** stop the wind and reset `windTime` to 0 — otherwise every frame
  after an fps measurement freezes foliage at a run-dependent sway.
- **SSGI renders a black frame on Mesa llvmpipe** (`--ssgi`; isolated by toggling SSGI and SSR
  separately) → opt-in until verified on real GPUs. SSR renders fine there.

## 5. Ground texture

- A `QImage` → `QQuick3DTextureData` (RGBA8) on a `#Rectangle` needs **`flipV: true`**: texture
  rows are north-up. Orientation probe (`--orient`): NCC identity 0.94 vs flip_v −0.10.
- **Re-attach renders white** (the texture twin of the geometry rule in §2): the IoU and
  orientation probes set `groundTexture` to null, which detaches the `QQuick3DTextureData`;
  setting the same object back left the plan ground white until the next reload (mean luma
  difference 13.1 on the frame, all of it in the ground half; llvmpipe, 640×360).
  `preserved_state()` re-uploads a detached ground. It was caught only because
  `--second-window` compared frames, then misdiagnosed once as a stale reference by a run
  without `--iou`; check a restore in pixels (`probe_restore_frame_diff` < 1), not by object
  identity.
- `QGraphicsScene.render()` always paints the canvas background — the spike swaps that exact
  colour for meadow; the production bake paints records directly (plan L1.5).
- **Ownership:** a texture created in Python is owned by Python, so something must hold it
  while QML shows it — exactly the one shown. An append-only keep-alive list grew RSS by one
  baked ground (2400×1600 RGBA ≈ 15 MB + GPU copy) per project reload, linearly; holding only
  the shown texture and releasing the old one *after* the scene shows the new one settled
  (A/B, 10 reloads, llvmpipe). Judge leaks by `leak_slope_mb_per_reload` (Theil–Sen over the
  second half of the reloads), never by total growth — the first reload adds ~70 MB either way
  — in committed memory (private bytes on Windows: its working set is trimmed and regrown, so
  v7 could not judge a leak at all), over **20 reloads**: at 10, five points could not resolve
  10 MB/reload against ±30–60 MB swings (WARP v8; llvmpipe once read 14.3 at ±35 MB). Trust
  the gate only with its positive control on the same machine (`--soak-leak-mb 25` must fail
  it). Private bytes do not see VRAM: on a discrete GPU the gate is blind to GPU-side leaks.
  The gate answers "a trend over the last ten reloads?", not "did memory grow?". On WARP every
  run (v9–v19) first dips 94–146 MB (consistent with memory the probes held: the probe-less
  controls, at a smaller window, never dip), then rises 5–10 MB per reload; the tail gate read
  1.5–9.0 (1.8 and 9.0 for identical code), and the controls read 28–34 MB/reload for the 25
  MB they hold. Longer soaks (v13–v19; v15–v19 ran 100 reloads): identical code rose +196 and
  +62 MB by reload 50, and over reloads 51–100 the five 100-reload runs (the same soak path)
  had slopes of 0.21, 2.03, 3.05, 0.08 and 3.50 MB/reload, the 0.08 an interval that includes
  zero. Over reloads 11–20 llvmpipe RSS read 0.00–0.46 MB/reload where WARP read 1.5–9.0, so
  over that window it is not Python-side growth of WARP's size (no Linux soak runs past reload
  20); a D3D11 resource retained per reload is not ruled out (WARP keeps GPU memory in private
  bytes; a discrete GPU keeps it in VRAM, unseen). Cause unidentified; locate it with an A/B
  soak, not a longer one. Record the whole curve (`leak_curve_mb`) with the verdict, and write
  numbers, not shapes: "ceiling", "slowing" and "threefold" each overreached on these curves.

## 6. Hosts

| | `QQuickView` (+ window container) | `QQuickWidget` |
|---|---|---|
| Frame signal | `frameSwapped` | **none** — count `quickWindow().afterRendering` |
| Request a frame | `view.update()` | **`quickWindow().update()`**; `widget.update()` only re-composites the old texture |
| "Is it on screen?" | `isExposed()` | `quickWindow().isExposed()` is **always False** (offscreen) → `isVisible()` |
| Screenshot | `grabWindow()` | `grabFramebuffer()` |

- **A `QQuickWidget` in a window moves the whole top-level window onto RHI composition.**
  2D canvas pan step, median: 5.5 ms without → 7.9 ms with a 3D widget beside it (**×1.42**,
  llvmpipe) and 2.8 → 15.9 ms (**×5.7**, Windows WARP, frozen) — `--pan-bench`; GO criterion 3
  asks ≤ ×1.3 on owner hardware. A synchronous
  `repaint()` does **not** include the flush/composition (it measured 0.08 ms "with 3D") —
  measure pan cost with `processEvents()`.
- **A second 3D window is not faster:** with the same view it took as long to its first
  finished readback as the first window (`--second-window`; WARP: 63.7 s vs 63.0 s) —
  consistent with per-window pipeline state (inferred, not verified). Keep one host alive and
  hide/show it: re-entry (show → next frame) median **7 ms** on llvmpipe, 31–53 ms on WARP
  (`--soak`; measured on a fully rendered scene — before the re-attach fix in §2, a soak that
  re-added models measured an empty garden).
- **The window container keeps the main window a raster surface (L1.2).** With
  `QWidget.createWindowContainer(view)` beside the 2D canvas, the top-level window's
  `surfaceType()` stays `RasterSurface` and a 2D pan step costs the same: median 2.56 ms with
  the container vs 2.63 ms without (×0.97; WARP ×0.92). The control — a `QQuickWidget` in the
  same place — turns the surface into `Direct3DSurface`. The surface type is the direct test;
  the timing only says what it would cost. A container belongs to its parent widget: when
  that widget is destroyed Qt destroys the view too, and a Python wrapper used afterwards
  raises "wrapped C/C++ object … has been deleted". `Quick3DHost` asks `sip.isdeleted` on
  every call: such a host reads `closed`, its three releases (`destroy_part`, `clear`,
  `close`) do nothing and every other call raises `HostStateError`.
- **A window without a scene graph can be grabbed exactly once — and only if it is never
  shown (L1.2).** `create()` + `grabWindow()` on a never-shown `QQuickView` gives a correct
  frame once (pixel-identical to a shown grab); the second such grab is the bare clear colour —
  every Model gone, `#Rectangle` included (mean abs difference 161; GPU and WARP). `pick()`
  finds nothing on such a window. Offscreen rendering is therefore one host per frame
  (0.4–0.5 s with one material, 1.1–1.3 s with six).
- **`show()`, then `grabWindow()` before the first expose, kills the process (L1.2, review
  round 1).** The grab RETURNS A CORRECT IMAGE; the process dies in the next passes of the
  event loop — exit 3, `0xC0000005` or `0xC0000374` (3 of 3 on the GPU and 3 of 3 on WARP
  here; the review: 5 of 5, 3 of 3, and 4 of 4 embedded in a splitter). The same death: one
  hidden grab followed by `show()` (3 of 3 on each adapter), and an embedded, never-shown view
  grabbed and then shown by its parent (2 of 2, 1 of 1 on WARP). NOT when the window is
  destroyed before the event loop runs again (6 of 6 survived) — a test whose host goes out of
  scope proves nothing. The condition is `isSceneGraphInitialized()`, never `isVisible()`.
- **Qt's window predicates, measured as (visible, exposed, scene graph initialised) on
  Direct3D 11 (L1.2, review round 1):** new F F F · after `show()` T F F · after one
  `processEvents()` T T T · right after `hide()` F **T** T, then F F T · shown again T F T ·
  minimised **T** F T. Under `QT_QPA_PLATFORM=offscreen` the scene graph reports initialised
  from the window's birth and draws no 3D. `Quick3DHost.state` derives new / starting / live /
  hidden / spent / closed from these on every call (§12); there is no "was shown" flag, because
  the parent of an embedded container shows and hides it without asking.
- **Harmless, also measured:** `show()` + `hide()` before any event, then the one offscreen grab
  (the scene graph never came up); a grab while hidden WITH a scene graph (a correct frame, 2
  of 2 — the host refuses it all the same: unmeasured on OpenGL); wrapping a view that is
  live, starting or hidden in a window container.
- **Do not wait for `frameSwapped` to be sure of a frame (L1.2).** On Windows, Qt 6.11
  delivers update requests from a thread that waits for the display's vertical blank. With the
  monitor asleep, a visible, exposed, active window presented its first frame and then none
  (1 frame after 40 `update()` calls; `QT_D3D_NO_VBLANK_THREAD=1` → about 4 per second; on WARP
  they flow). `grabWindow()` renders and reads back synchronously regardless (1–9 ms): it is
  the synchronisation point. A `QQuickWidget`'s window composition waited 262 ms per 2D pan
  step on that machine.

## 7. Picking

`pickAt(x, y)` in QML → `{hit, id, x, y, z}` (engine frame). Models must set
`pickable: true`. `--pick` checks it in a top-down orthographic view against a CPU oracle (the
topmost triangle under a vertical ray over every pickable mesh, merged per item):

- **Easy targets** (each item's highest unoccluded triangle): **20/20**, 0.15–0.24 ms per pick
  (llvmpipe); 20/20 in the frozen Windows D3D11 exe. Easy targets alone prove little — a picker
  that only tests bounding boxes scores 18/20 on them.
- **Adversarial targets** (points inside a tall item's bounding box but off its mesh, where a
  box picker names the wrong item): **10/10** on llvmpipe at 6f0c4f4.
- **Independent projection:** the click pixel comes from the engine's `mapFrom3DScene`, so a
  wrong engine projection could aim and pick consistently wrong. An orthographic projection of
  our own (`measure.top_down_pixel`) agreed with it to **0.0 px**, and the picked point lay
  **0.0 cm** from the target (llvmpipe, 6f0c4f4).
- **Re-attach:** 20/20 again after a detach/re-attach cycle, and the frame is pixel-identical
  (mean abs diff 0.0) — 0/20 and a blank frame without the re-upload rule in §2.

## 8. Screenshots and CI rendering

- **Open time** (criterion 5) runs from the user's request to the first *finished readback*:
  ground bake, model build, QML load, scene apply, show → grab (`open_ms`,
  `open_breakdown_ms`). Never stop the clock at `afterRendering`/`frameSwapped`: that marks
  submission, and on a software rasteriser the real cost lands in the next grab.
- **Cold vs warm must be recorded, not assumed.** Qt keeps `q3dshadercache-*`,
  `qtpipelinecache-*` and `qmlcache` in the app's cache folder (`~/.cache/<argv0 basename>/`,
  `__main__.py` for `python -m`) and `qtshadercache-*` one level up; Mesa keeps
  `mesa_shader_cache`. The spike lists what it found (`shader_caches.found_before_run`).
  `--cold` sets `QT_DISABLE_SHADER_DISK_CACHE`, `QSG_RHI_DISABLE_DISK_CACHE`,
  `QT_QUICK3D_NO_SHADER_CACHE_LOAD`, `QML_DISABLE_DISK_CACHE` and `MESA_SHADER_CACHE_DISABLE`
  (all in the 6.11 runtime): nothing is loaded **or written**. GPU drivers keep their own
  shader caches, which no flag reaches. On llvmpipe cold vs warm is noise (first frame's
  render cost dominates); the GPU decides.
- **A shader that does not compile is only a warning.** `QSpirvCompiler: Failed to parse
  shader` and `Failed to compile fragment shader` arrive as `QtWarningMsg`, and the frame
  still renders (measured: one renamed call in `water.frag` changed 449 pond pixels' colour,
  exit 0, `status: ok`). Record every Qt message
  (`spike_q3d/qt_messages.py`: `qInstallMessageHandler`, into the log and `metrics.json`) and
  fail on shader/QML errors — a frozen exe has no stderr to read them from. A clean llvmpipe
  run emits no Qt messages at all, so the gate has no false alarms there.
- **Aim probes at the actual view size** (`SpikeRenderer.view_size()`), not the requested
  one: a window larger than the screen is clamped.
- **Which device rendered? (L1.2)** `rendererInterface().graphicsApi()` names the API, not the
  adapter. Switch on Qt's `qt.rhi.general` debug output
  (`QLoggingCategory.setFilterRules("qt.rhi.general.debug=true")`) and read it from the message
  handler: Direct3D lists every adapter and prints `using this adapter` after the chosen one;
  OpenGL prints the context's `RENDERER`. `quick3d/qt_messages.py::graphics_adapter` does it.
  **Force the software rasteriser on Windows with `QSG_RHI_PREFER_SOFTWARE_RENDERER=1`** — the
  log then says `Microsoft Basic Render Driver` (WARP), which is what a GPU-less runner has.
- **A recorder that replaces the Qt message handler must forward (L1.2).**
  `qInstallMessageHandler` keeps ONE handler: a recorder installed for one frame hid a shader
  error from the recorder of the whole run. `qt_messages.install` chains to the handler it
  replaced. Warnings that belong to the machine are a class of their own, listed exactly —
  so far one: `qt.qpa.screen: "Unable to open monitor interface to …"` (display asleep).
- **Classify a Qt message by its level first (L1.2, review round 1).** Only a WARNING is
  promoted to an error by its text; debug and info lines are information whatever they say.
  With `qt.rhi.general.debug=true` switched on, the first version ran the error patterns over
  debug output — the wording of a Qt debug line could have failed a release. And switch that
  rule off again: `QLoggingCategory.setFilterRules` holds ONE rule set per process and has no
  getter; `qt_messages.graphics_info()` is a context manager that nests.

- **Linux:** the `offscreen` QPA selects the *software* scene graph, which cannot render 3D.
  Use `xvfb-run` + `QT_QPA_PLATFORM=xcb` + `QSG_RHI_BACKEND=opengl` (+ `LIBGL_ALWAYS_SOFTWARE=1`
  in a container). Packages: `libegl1` (the QtQuick3D binding links libEGL), `libxcb-cursor0`
  (xcb plugin). As root, Qt WebEngine needs `QTWEBENGINE_DISABLE_SANDBOX=1`.
- **Windows runner** (windows-latest, no GPU): `Direct3D11Rhi` on WARP (the spike printed
  PyQt's enum member name; the production host says `Direct3D11` — §2). Frozen exe: QML load
  1.0–2.7 s, first *presented* frame 2.0–3.0 s, 960×540 low 11.5–14.2 fps / high 4.3–4.8 fps
  (runs v3/v4, `docs/09-architecture-decisions/adr-048-evidence/`). `frameSwapped` marks
  submission, not completion — on a software rasteriser the real cost lands in the next
  readback, so time open-to-ready with a grab. **Each new sky light probe costs 40–85 s
  once** on WARP (in the frames or the grab right after a sun change); later
  grabs of the same sky take 20–500 ms, and `QSG_RENDER_LOOP=basic` changes nothing. Never
  rebuild the probe per frame; on software rendering drop image-based light. Evidence run
  v1's 56-minute "hang" was this cost, times many shots, with no log.
- **A frozen GUI exe has no stdout** (`print` is a no-op; #291 is the precedent). The spike
  writes `<out>/spike.log` (flushed per line), rewrites `metrics.json` after every phase, and
  `--watchdog-s N` arms `faulthandler` to dump every thread's stack into the log and exit
  non-zero. Use the same pattern for any headless render path.
- Commands (Linux render tier, opt-in):

  ```bash
  OGP_RENDER3D=1 QSG_RHI_BACKEND=opengl QT_QPA_PLATFORM=xcb LIBGL_ALWAYS_SOFTWARE=1 \
    xvfb-run -a -s "-screen 0 1920x1080x24" \
    venv/bin/python -m pytest tests/integration/test_spike_q3d_render.py
  ```

## 9. Packaging (PyInstaller)

- The spec needs a data loop for the QML + shader files and hidden imports for
  `PyQt6.QtQuick`, `QtQml`, `QtQuickWidgets`, `QtQuick3D`.
- **The Qt Quick 3D runtime already ships in today's bundle.** Windows dist, branch vs master
  built in the same job: 626.2 MB vs 624.5 MB → **+1.7 MB** (only `QtQuick3D.pyd` and the QML
  files are new). Quick3D + ShaderTools files: 21.1 MB (Windows), ~23 MB (Linux); the Qt3D DLLs
  this replaces: 5.8 MB.
- Frozen Windows exe with the spike bundled: the unchanged `--selftest` gate still exits 0
  (ogp-change-control §2.8 is its home).
- **L1.2:** the production package adds its five QML and shader files (a data loop in
  `installer/ogp.spec`) and eleven modules: **+79,008 bytes** (650,815,562 vs 650,736,554,
  same machine, same day). `--selftest` now also RENDERS one hidden 160 × 120 frame from the
  bundle's QML and shaders and judges its pixels (§12); frozen exe 3.0–4.1 s on an RTX 4070
  and 3.0–3.1 s on WARP for the whole command (6.1–7.2 s on a busy machine).

## 10. CPU-side baselines (not engine)

`bench_small.ogp` (99 items): plan load 0.1–0.5 s, ground bake 0.1–0.3 s, mesh build ~0.5 s
for 185k triangles (Linux and Windows alike). `bench_large.ogp` (425 items): 2.28 s for 1.46M
triangles (`scripts/bench_view3d.py`).

## 11. Symptom → cause (spike chronicle)

| Symptom | Cause | Fix |
|---|---|---|
| Segfault at start | `idealSurfaceFormat()` before `QGuiApplication` | call nothing Quick-3D before the app object |
| Frame renders nothing 3D under CI | `offscreen` QPA → software scene graph | xvfb + xcb + `QSG_RHI_BACKEND=opengl` |
| QML: non-existent property `castsShadow` | Model vs Light naming | `castsShadows` on Models |
| QML: `lookAt is not a function` | only cameras have it | quaternion from Python |
| Whole garden pale blue, no shadows | the light was never oriented | `rotationTo((0,0,-1), travel)` |
| Lawn white, gravel around the beds | ground texture upside down | `flipV: true` |
| Sky sun stuck after a sun change | probe cached per Texture object | new Texture per change |
| Ultra preset black | SSGI on Mesa | SSGI opt-in |
| `'QJSValue' object is not iterable` | QML function returned a JS object | `.toVariant()` |
| QQuickWidget never reports frames | it has no `frameSwapped` | `quickWindow().afterRendering` |
| QQuickWidget renders only once | `widget.update()` re-composites | `quickWindow().update()` |
| Windows run "hangs" for an hour | `grabWindow()` ~100 s on WARP, no output | time every grab; log + watchdog |
| Frozen spike fails in 1 s | default plan path is the source tree | pass `--plan` explicitly |
| Models blank and unpickable after a list change | geometry outlived its Model; array `Repeater3D` recreates all delegates | re-upload on re-attach; one geometry per Model lifetime |
| A sun change stalls for a minute (WARP) | new sky light probe is prefiltered | rebuild only on noticeable sun moves; no IBL on software |
| Windows RSS reads `None` | ctypes default `int` restype truncates the process pseudo-handle | declare `HANDLE` restype/argtypes |
| `--iou` crashes / `--orient` says mirrored at 150 % display scale | pixel grid built from the logical size | build it from the grabbed image and `devicePixelRatio()` |
| A measurement changes when the flag order changes | a probe left camera/preset/ground/sun behind | run probes inside `SpikeRenderer.preserved_state()` |
| "Open time 2 s" but the first grab takes a minute (WARP) | `frameSwapped` marks submission | time request → first finished readback (`open_ms`) |
| A custom material renders the wrong colour, run "ok" | shader compile error is a `QtWarningMsg` | record Qt messages; fail on shader/QML errors |
| "Cold" open time on the second run of the day | Qt/Mesa disk caches from the first | `--cold`, and read `shader_caches.found_before_run` |
| RSS climbs ~30 MB per project reload | append-only keep-alive list of ground textures | hold only the shown texture; judge by the tail slope |
| Plan ground white after a probe; the second window "differs" by ~10 luma | the same `QQuick3DTextureData` re-attached after being detached | re-upload it on re-attach; assert `probe_restore_frame_diff` < 1 |
| The second offscreen frame is an empty sky (L1.2) | a never-shown window renders one frame, then its scene is gone | one host per offscreen frame |
| A wait for frames times out although the window is visible (L1.2) | display asleep: no vertical blank, no update requests | never wait for presented frames; `grab()` |
| QML: Cannot assign to non-existent property "exposure" (L1.2) | plain `SceneEnvironment` | `ExtendedSceneEnvironment` |
| A broken shader raises, but the run's summary counts 0 errors (L1.2) | a nested message recorder replaced the outer one | recorders forward to the handler they replaced |
| `--selftest` fails with `RenderError: Qt Quick runs on the Software backend` (L1.2) | it ran under `QT_QPA_PLATFORM=offscreen` | run it with a real platform |
| "wrapped C/C++ object of type QQuickView has been deleted" on close (L1.2) | the widget that parented the window container was destroyed first | the host asks `sip.isdeleted`: it reads `closed`, the releases do nothing, everything else raises `HostStateError` |
| The process dies a moment AFTER a grab that returned a good image (L1.2) | the window was shown but not exposed yet (no scene graph), or was shown after its one offscreen grab | `wait_ready()` (check its answer) before `grab()`; a spent host is replaced, never shown |
| `TypeError` / `ValueError` / "not a live part" from a sink's `clear()` after the window closed (L1.2) | two owners of Model lifetime: the sink destroyed part by part with handles the dead host no longer knew | the host owns every Model; `clear()` asks it once and forgets in a `finally` |
| A file of 30+ engine tests reports only skips (L1.2) | its canary ran product code, and the product was broken | a canary of bare Qt; a positive control that a broken scene FAILS the file |
| `'OpenGL'` is not in a list that says `'OpenGLRhi'` (L1.2) | PyQt's enum member names are inconsistent | map the enum member, not a string |
| Foliage drawn beside its mesh although the clock stands still (L1.2) | the wind shader's sway is not 0 at time 0 | a rest factor that is exactly 0 at time 0 |
| `io.UnsupportedOperation: fileno` when a watchdog is armed (L1.2) | `faulthandler` writes to a descriptor; stderr was captured or replaced | dump into the log's own handle, else into the null device — the exit still happens |
| A CI step prints its verdict and nothing after it (L1.2) | `Write-Error` under GitHub's pwsh (`$ErrorActionPreference = 'stop'`) ends the step | `Write-Host "::error::…"`, the log in `try`/`catch`, `exit 1` at the end |
| Two identical frames around an edit (L1.2) | the edited item was hidden behind another | edit something the camera sees |

## 12. The production package — `ui/view3d/quick3d/` (L1.2)

Concept and module map: §8.26.2 of `docs/08-crosscutting-concepts/README.md`; the decision and
every measurement: ADR-048, addendum L1.2. What you need at the keyboard:

- **Sink and host.** `adapter.Quick3DSink(host)` is the `EngineSink`; `host.Quick3DHost(size)`
  is the `QQuickView` and implements the sink's `EngineScene` — it OWNS every Model.
  `SceneSync(Quick3DSink(host)).apply(records, sun=…, ground=…)` is the whole wiring.
  `host.container()` embeds it; `host.pick(x, y)` returns a `PickHit` in the SCENE frame;
  `host.engine_parts()` reads the Models back from the QML scene (what the engine holds, not
  what the sink believes).
- **The host's states — which call is legal when** (`host.state`, derived from Qt on every
  call; anything else raises `HostStateError` with what to do instead):

  | State | Qt says | `grab()` | `pick()` | `show()` | `container()` |
  |---|---|---|---|---|---|
  | `new` | no scene graph, not visible | once (offscreen), then `spent`; not when embedded | no | yes | yes |
  | `starting` | visible, no scene graph yet | **no — fatal** | no | yes | yes |
  | `live` | scene graph, visible, exposed | yes | yes | yes | yes |
  | `hidden` | scene graph, not both | no | no | yes | yes |
  | `spent` | its one offscreen frame is taken | no | no | **no — fatal** | no |
  | `closed` | `close()`, or Qt destroyed the view | no | no | no | no |

  After `show()`: `if not host.wait_ready(): …` — it pumps the event loop to the first expose,
  bounded, and answers; `grab()` never pumps. On a closed host `destroy_part`, `clear` and
  `close` do nothing, `state` / `is_closed` / `is_visible` answer, everything else raises.
  Under the software scene graph `host.renders_3d()` is False and `grab` / `pick` raise
  `EngineError`.
- **Recovery.** After a sink call raised: `sync.reset()` (it calls `Quick3DSink.clear()`, which
  is legal in a half-applied transaction and on a dead host), then `sync.apply(...)`. When the
  host itself died: `sink.attach(fresh_host)` in between. Check with
  `sink.state() == sync.expected_state()`.
- **Errors in a SHOWN view** raise nothing (a broken shader is a Qt warning and the frame still
  renders, without that material): `host.watch_messages()` once, then poll
  `host.engine_errors()` on the GUI thread; warnings and errors also go to the app's log, from
  Qt's render thread.
- **One frame without a window:** `offscreen.render_records(records, shot=TopDownShot(...),
  sun=SunState(...), ground=GroundSpec(...), size=(w, h))` → `OffscreenFrame` (`.image`,
  `.rgba()`, `.open_ms`, `.build_failures`, `.adapter` with `graphics_info=True`). A QML or
  shader error, or a backend without 3D, raises `RenderError`. For processes that own
  themselves — a tool, a test, the self-test — NOT for the running app: it blocks 0.4–1.3 s
  and builds and destroys a graphics device per call. Silent unless `log=` is passed.
- **The engine boundary without a GPU.** Under the offscreen platform `Garden.qml` loads and
  Models are created, posed and read back — no pixels, but every sink call, the state rules,
  the teardown orders and the recovery are checkable: `tests/ui/test_quick3d_host.py` (default
  tier). Its canary is bare Qt with inline QML — NO product code — so a broken scene file
  fails the tests instead of skipping them; `render_records` raises `RenderError` naming the
  `Software` backend there.
- **The render tier** (opt-in, opens windows):

  ```bash
  # Windows (Git Bash) — the real GPU, then WARP
  OGP_RENDER3D=1 venv/Scripts/python.exe -m pytest tests/integration/test_quick3d_render.py \
      tests/integration/test_quick3d_engine.py tests/integration/test_quick3d_selftest.py \
      tests/integration/test_quick3d_lifecycle.py
  OGP_RENDER3D=1 QSG_RHI_PREFER_SOFTWARE_RENDERER=1 venv/Scripts/python.exe -m pytest \
      tests/integration/test_quick3d_render.py tests/integration/test_quick3d_lifecycle.py
  # Linux
  OGP_RENDER3D=1 QSG_RHI_BACKEND=opengl LIBGL_ALWAYS_SOFTWARE=1 \
    xvfb-run -a -s "-screen 0 1920x1080x24" venv/bin/python -m pytest \
    tests/integration/test_quick3d_render.py tests/integration/test_quick3d_engine.py
  ```

  The driver alone, to look at frames (`<out>/*.png`, `result.json`, `render.log`):
  `venv/Scripts/python.exe tests/integration/quick3d_render_driver.py --out <out> --scenarios
  truth,materials,rest,states,edits,recovery,pick,sun,integration,embed`. One call order of the
  host's lifecycle, in a process of its own: `venv/Scripts/python.exe
  tests/integration/quick3d_lifecycle_child.py show_grab` (it lists the orders when called
  without one). From a git worktree set `PYTHONPATH` to that checkout's `src` first for
  anything that is not pytest.
- **The self-test's 3D block:** `quick3d/selftest.py::run_selftest()`; the log is
  `OpenGardenPlanner-selftest-3d.log` in the app's log directory (beside `app.log`), or
  `OGP_SELFTEST_LOG`. The whole `--selftest` runs under ONE watchdog (300 s, the reason in the
  log before the exit) — `faulthandler` has a single timer per process, so never nest
  `runlog.watchdog`. Never run `--selftest` under `QT_QPA_PLATFORM=offscreen`.
- **Numbers** (RTX 4070 / WARP, 2026-10-11): open of `bench_small.ogp` (99 records, 87 Models)
  to the first finished readback 1.11 s / 0.17 s; edit → finished readback median 7.1 ms
  (p95 17.9) / 23.2 ms; pick 0.01–0.2 ms; shadow IoU vs the analytic 2D shadow 0.976–0.978
  at 35° and 480 × 360; engine import 17 ms on top of 17 ms for `core/scene3d`.
- **Not measured:** on Linux everything added in review round 1 — the lifecycle matrix above
  all (the first three render files ran once on GitHub's ubuntu runner: 47 passed, 4 skipped
  by design, 1 failed on the API name); `frozen-selftest.yml` on any runner; an integrated
  GPU; a display scale other than 100 %.
