"""One call order of the host's lifecycle, in a process of its own — not a test module.

    python tests/integration/quick3d_lifecycle_child.py <order> [<qml dir>]

Why a process per order: the failure this matrix exists for is not an exception.
A Qt Quick window grabbed at the wrong moment returns a perfectly good image, and
the PROCESS dies in the next pass of the event loop (abort, access violation or
heap corruption — ADR-048 addendum L1.2, fix round 1). A test in the pytest
process would take the whole run down with it; here a death is an exit code.

Each order drives a real ``Quick3DHost`` and records what every step answered —
a red-pixel count for a frame, the host's state, or the exception a step raised.
Then the event loop runs for half a second (that is when a doomed process dies),
the observations go to stdout as one ``RESULT {json}`` line, and ``END`` is the
last line of a process that survived. ``test_quick3d_lifecycle.py`` judges.

Two orders are CONTROLS (``unguarded_…``): they reach past the host's guard to the
raw ``QQuickView`` and do what the guard forbids. They must NOT reach ``END`` —
that is what shows the matrix can see the failure it guards against.
"""

from __future__ import annotations

import faulthandler
import json
import logging
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if __name__ == "__main__":  # run as a script: make this checkout's code importable
    for entry in (str(REPO / "src"), str(REPO)):
        if entry not in sys.path:
            sys.path.insert(0, entry)

RING = ((-50.0, -50.0), (50.0, -50.0), (50.0, 50.0), (-50.0, 50.0))
SIZE = (320, 240)
#: A 100 x 100 cm red box seen from above at 0.5 px/cm covers 50 x 50 = 2,500 pixels.
BOX_PIXELS = 2500

#: Every public call of the host, for the "closed host" rows: name → how to call it.
HOST_CALLS: dict[str, Callable[[Any, Any], Any]] = {}


def _register_calls() -> None:
    from open_garden_planner.core.scene3d import EnginePose, engine_buffers, prism_mesh
    from open_garden_planner.ui.view3d.quick3d.adapter import GroundPlacement, PartSpec
    from open_garden_planner.ui.view3d.quick3d.look import NEUTRAL_DAY
    from open_garden_planner.ui.view3d.quick3d.shots import TopDownShot

    pose = EnginePose((0.0, 0.0, 0.0), 0.0)
    spec = PartSpec("x", 0, "vc", engine_buffers(prism_mesh([RING], 10.0)), True)
    HOST_CALLS.update({
        "create_part": lambda host, _part: host.create_part(spec, pose, (255, 255, 255, 255)),
        "pose_part": lambda host, part: host.pose_part(part, pose),
        "tint_part": lambda host, part: host.tint_part(part, (1, 2, 3, 255)),
        "set_sun": lambda host, _part: host.set_sun((0.0, -1.0, 0.0)),
        "set_ground": lambda host, _part: host.set_ground(GroundPlacement((0.0, 0.0, 0.0), 10.0, 10.0)),
        "request_frame": lambda host, _part: host.request_frame(),
        "container": lambda host, _part: host.container(),
        "show": lambda host, _part: host.show(),
        "hide": lambda host, _part: host.hide(),
        "wait_ready": lambda host, _part: host.wait_ready(0.2),
        "grab": lambda host, _part: host.grab("closed"),
        "graphics_api": lambda host, _part: host.graphics_api(),
        "renders_3d": lambda host, _part: host.renders_3d(),
        "device_pixel_ratio": lambda host, _part: host.device_pixel_ratio(),
        "view_size": lambda host, _part: host.view_size(),
        "set_shot": lambda host, _part: host.set_shot(TopDownShot((0.0, 0.0), 0.5)),
        "set_camera": lambda host, _part: host.set_camera((0.0, -5.0, 5.0), (0.0, 0.0, 0.0)),
        "set_top_down": lambda host, _part: host.set_top_down((0.0, 0.0), 0.5),
        "set_look": lambda host, _part: host.set_look(NEUTRAL_DAY),
        "pick": lambda host, _part: host.pick(1.0, 1.0),
        "project": lambda host, _part: host.project(0.0, 0.0, 0.0),
        "engine_parts": lambda host, _part: host.engine_parts(),
        "scene_property": lambda host, _part: host.scene_property("sunUp"),
        "watch_messages": lambda host, _part: host.watch_messages(),
        # the three releases: legal on a closed host, and they do nothing there
        "destroy_part": lambda host, part: host.destroy_part(part),
        "clear": lambda host, _part: host.clear(),
        "close": lambda host, _part: host.close(),
    })


def _pump(app: Any, ms: int = 300) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.005)


def _red(image: Any) -> int:
    from open_garden_planner.ui.view3d.quick3d.offscreen import image_rgba

    pixels = image_rgba(image).astype(int)
    return int(((pixels[..., 0] > 150) & (pixels[..., 1] < 70) & (pixels[..., 2] < 70)).sum())


def _attempt(call: Callable[[], Any]) -> Any:
    """What a step answered: its value (JSON-able), or ``"<ErrorType>: message"``."""
    try:
        value = call()
    except Exception as error:  # noqa: BLE001 - the matrix records every outcome
        return f"{type(error).__name__}: {error}"
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if hasattr(value, "item_id"):  # a PickHit
        return {"hit": value.item_id, "point": [round(v, 2) for v in value.point]}
    if hasattr(value, "width"):  # a QImage: red DEVICE pixels, and the ratio to judge them by
        return {"red": _red(value), "dpr": float(value.devicePixelRatio())}
    return repr(value)[:80]


def _host(size: tuple[int, int] = SIZE, qml_dir: Path | None = None) -> tuple[Any, Any, Any]:
    """A host with one red box under a sun, seen from above; its sink and sync."""
    from open_garden_planner.core.scene3d import (
        Material,
        Params,
        Record,
        SceneSync,
        SunState,
        Transform,
    )
    from open_garden_planner.ui.view3d.quick3d.adapter import Quick3DSink
    from open_garden_planner.ui.view3d.quick3d.host import Quick3DHost
    from open_garden_planner.ui.view3d.quick3d.shots import TopDownShot

    host = Quick3DHost(size, qml_dir=qml_dir)
    sink = Quick3DSink(host)
    sync = SceneSync(sink)
    sync.apply({"w": Record(item_id="w", kind="SHED", shape="rectangle", footprints=(RING,),
                            transform=Transform(), material=Material("SHED", fill_rgba=(230, 20, 20, 255)),
                            height_cm=20.0, params=Params({"seed": "w"}))})
    sync.set_sun(SunState(45.0, 225.0))
    host.set_shot(TopDownShot((0.0, 0.0), 0.5))
    return host, sink, sync


def _grab(host: Any) -> Any:
    return _attempt(lambda: host.grab("matrix"))


def _centre_pick(host: Any) -> Any:
    width, height = host.view_size()
    return _attempt(lambda: host.pick(width / 2.0, height / 2.0))


# ── the orders ───────────────────────────────────────────────────────────────


def show_grab(app: Any, _qml: Path | None = None) -> dict[str, Any]:
    """The reviewer's first killer: show(), then grab() before the first expose."""
    host, _sink, _sync = _host()
    host.show()
    out = {"state_after_show": host.state.value, "grab_at_once": _grab(host),
           "pick_at_once": _centre_pick(host)}
    out["ready"] = host.wait_ready(10.0)
    out.update(state_ready=host.state.value, grab_ready=_grab(host), pick_ready=_centre_pick(host))
    _pump(app, 500)
    out["grab_later"] = _grab(host)
    host.close()
    return out


def grab_show(app: Any, _qml: Path | None = None) -> dict[str, Any]:
    """The second killer: the one offscreen frame, then show() — what the old error
    message recommended."""
    host, _sink, _sync = _host()
    out = {"state_new": host.state.value, "offscreen": _grab(host), "state_after": host.state.value,
           "show": _attempt(host.show), "container": _attempt(host.container),
           "second_grab": _grab(host), "wait_ready": _attempt(lambda: host.wait_ready(0.3)),
           "hide": _attempt(host.hide)}
    _pump(app, 500)
    out["state_end"] = host.state.value
    host.close()
    return out


def show_wait_grab(app: Any, _qml: Path | None = None) -> dict[str, Any]:
    host, _sink, _sync = _host()
    host.show()
    out = {"ready": host.wait_ready(10.0), "state": host.state.value, "grab": _grab(host),
           "pick": _centre_pick(host), "renders_3d": host.renders_3d(), "api": host.graphics_api()}
    _pump(app, 300)
    out["second_grab"] = _grab(host)
    host.close()
    return out


def hide_grab(app: Any, _qml: Path | None = None) -> dict[str, Any]:
    """Shown, live, hidden: a hidden host is asked for a frame."""
    host, _sink, _sync = _host()
    host.show()
    out = {"ready": host.wait_ready(10.0), "live_grab": _grab(host)}
    host.hide()
    _pump(app, 100)
    out.update(state_hidden=host.state.value, grab_hidden=_grab(host), pick_hidden=_centre_pick(host),
               wait_hidden=_attempt(lambda: host.wait_ready(0.3)))
    host.close()
    return out


def show_hide_show_grab(app: Any, _qml: Path | None = None) -> dict[str, Any]:
    host, _sink, _sync = _host()
    host.show()
    out = {"ready": host.wait_ready(10.0), "first": _grab(host)}
    host.hide()
    _pump(app, 100)
    host.show()
    out.update(state_reshown=host.state.value, grab_at_once=_grab(host))
    out["ready_again"] = host.wait_ready(10.0)
    out.update(state=host.state.value, grab=_grab(host), pick=_centre_pick(host))
    host.close()
    return out


def show_hide_before_expose_grab(app: Any, _qml: Path | None = None) -> dict[str, Any]:
    """Shown and hidden again with no event in between: the window never got a scene
    graph, so the host is still new and has its one offscreen frame."""
    host, _sink, _sync = _host()
    host.show()
    host.hide()
    out = {"state": host.state.value, "offscreen": _grab(host), "state_after": host.state.value,
           "show": _attempt(host.show)}
    _pump(app, 300)
    host.close()
    return out


def grab_twice_hidden(app: Any, _qml: Path | None = None) -> dict[str, Any]:
    host, _sink, _sync = _host()
    out = {"first": _grab(host), "second": _grab(host), "third": _grab(host)}
    _pump(app, 300)
    host.close()
    return out


def _every_call(host: Any, part: Any) -> dict[str, Any]:
    return {name: _attempt(lambda call=call: call(host, part)) for name, call in HOST_CALLS.items()}


def close_anything(app: Any, _qml: Path | None = None) -> dict[str, Any]:
    from open_garden_planner.core.scene3d import EnginePose, engine_buffers, prism_mesh
    from open_garden_planner.ui.view3d.quick3d.adapter import PartSpec

    host, _sink, _sync = _host()
    host.show()
    ready = host.wait_ready(10.0)
    part = host.create_part(PartSpec("p", 0, "vc", engine_buffers(prism_mesh([RING], 10.0)), True),
                            EnginePose((0.0, 0.0, 0.0), 0.0), (255, 255, 255, 255))
    host.close()
    out = {"ready": ready, "state": host.state.value, "is_closed": host.is_closed,
           "is_visible": host.is_visible(), "calls": _every_call(host, part)}
    _pump(app, 300)
    out["calls_after_events"] = _every_call(host, part)
    return out


def parent_destroyed_anything(app: Any, _qml: Path | None = None) -> dict[str, Any]:
    from PyQt6 import sip
    from PyQt6.QtWidgets import QWidget

    from open_garden_planner.core.scene3d import EnginePose, engine_buffers, prism_mesh
    from open_garden_planner.ui.view3d.quick3d.adapter import PartSpec

    host, _sink, _sync = _host()
    parent = QWidget()
    parent.resize(400, 300)
    host.container(parent)
    parent.show()
    ready = host.wait_ready(10.0)
    part = host.create_part(PartSpec("p", 0, "vc", engine_buffers(prism_mesh([RING], 10.0)), True),
                            EnginePose((0.0, 0.0, 0.0), 0.0), (255, 255, 255, 255))
    sip.delete(parent)  # what closing the main window does to the workspace
    out = {"ready": ready, "state": host.state.value, "is_closed": host.is_closed,
           "is_visible": host.is_visible(), "calls": _every_call(host, part)}
    _pump(app, 300)
    return out


def _splitter_window() -> tuple[Any, Any]:
    from PyQt6.QtWidgets import QMainWindow, QSplitter, QWidget

    window = QMainWindow()
    splitter = QSplitter()
    splitter.addWidget(QWidget())
    window.setCentralWidget(splitter)
    window.resize(900, 500)
    return window, splitter


def embedded_splitter(app: Any, _qml: Path | None = None) -> dict[str, Any]:
    """L1.3's first open: the app is up, the host is created, embedded in a splitter
    beside a widget and shown — then asked for a frame at once."""
    window, splitter = _splitter_window()
    window.show()
    _pump(app, 300)
    host, _sink, _sync = _host((450, 500))
    splitter.addWidget(host.container(splitter))
    splitter.setSizes([450, 450])
    host.show()
    out = {"state_after_show": host.state.value, "grab_at_once": _grab(host)}
    out["ready"] = host.wait_ready(10.0)
    out.update(state=host.state.value, grab=_grab(host), pick=_centre_pick(host))
    # the PARENT hides and shows the container — host.hide() / show() are never called
    host.container().hide()
    _pump(app, 100)
    out.update(state_parent_hid=host.state.value, grab_parent_hid=_grab(host))
    host.container().show()
    out.update(grab_parent_showed_at_once=_grab(host), ready_again=host.wait_ready(10.0),
               grab_again=_grab(host))
    window.hide()
    _pump(app, 100)
    out.update(state_window_hidden=host.state.value, grab_window_hidden=_grab(host))
    window.show()
    out.update(ready_window=host.wait_ready(10.0), grab_window=_grab(host))
    _pump(app, 500)
    host.close()
    window.close()
    return out


def embedded_never_shown_grab(app: Any, _qml: Path | None = None) -> dict[str, Any]:
    """Embedded, parent not shown yet: an offscreen frame now, and the process would die
    when the parent shows the container. The host has none to give."""
    window, splitter = _splitter_window()
    host, _sink, _sync = _host((450, 500))
    splitter.addWidget(host.container(splitter))
    splitter.setSizes([450, 450])
    out = {"state": host.state.value, "grab_before_parent": _grab(host),
           "wait_before_parent": _attempt(lambda: host.wait_ready(0.3))}
    window.show()  # the parent shows the container: host.show() was never called
    out.update(state_parent_shown=host.state.value, grab_at_once=_grab(host),
               ready=host.wait_ready(10.0), grab=_grab(host), pick=_centre_pick(host))
    _pump(app, 500)
    host.close()
    window.close()
    return out


def embed_after_show(app: Any, _qml: Path | None = None) -> dict[str, Any]:
    """A host shown standalone first and embedded afterwards — legal: only a spent host
    cannot be embedded."""
    host, _sink, _sync = _host((450, 500))
    host.show()
    out = {"ready_standalone": host.wait_ready(10.0), "standalone": _grab(host)}
    window, splitter = _splitter_window()
    window.show()
    _pump(app, 200)
    splitter.addWidget(host.container(splitter))
    splitter.setSizes([450, 450])
    host.show()
    out.update(ready=host.wait_ready(10.0), grab=_grab(host), pick=_centre_pick(host))
    _pump(app, 500)
    host.close()
    window.close()
    return out


def sink_clear_orders(app: Any, _qml: Path | None = None) -> dict[str, Any]:
    """P1-3: the sink's teardown in every order relative to the host's death — and
    ``SceneSync.reset()`` alone on a dead engine, then a fresh host through the same
    sink and sync."""
    from PyQt6 import sip
    from PyQt6.QtWidgets import QWidget

    from open_garden_planner.core.scene3d import Material, Params, Record, SunState, Transform
    from open_garden_planner.ui.view3d.quick3d.host import Quick3DHost
    from open_garden_planner.ui.view3d.quick3d.shots import TopDownShot

    out: dict[str, Any] = {}

    def dead_host(kill: Callable[[Any, Any], None]) -> tuple[Any, Any, Any]:
        host, sink, sync = _host()
        parent = QWidget()
        parent.resize(400, 300)
        host.container(parent)
        parent.show()
        host.wait_ready(10.0)
        kill(host, parent)
        return host, sink, sync

    def teardown(label: str, kill: Callable[[Any, Any], None]) -> None:
        host, sink, sync = dead_host(kill)
        out[label] = {"first_clear": _attempt(sink.clear), "items": list(sink.item_ids()),
                      "in_transaction": sink.in_transaction, "second_clear": _attempt(sink.clear),
                      "host_close": _attempt(host.close), "third_clear": _attempt(sink.clear),
                      "reset": _attempt(sync.reset), "state": host.state.value}

    teardown("parent_destroyed_then_clear", lambda _unused, parent: sip.delete(parent))
    teardown("parent_destroyed_close_then_clear",
             lambda host, parent: (sip.delete(parent), host.close()))
    teardown("close_then_clear", lambda host, _parent: host.close())
    _pump(app, 200)

    # THE recovery, as L1.3 will run it: the main window took the engine with it, the
    # next tick's apply fails, and SceneSync.reset() ALONE (it calls the sink's clear())
    # must bring sink and sync back — then a fresh host, the same sink, the same sync.
    record = Record(item_id="w", kind="SHED", shape="rectangle", footprints=(RING,),
                    transform=Transform(), material=Material("SHED", fill_rgba=(230, 20, 20, 255)),
                    height_cm=20.0, params=Params({"seed": "w"}))
    moved = Record(item_id="w", kind="SHED", shape="rectangle", footprints=(RING,),
                   transform=Transform(east_cm=10.0), material=record.material,
                   height_cm=20.0, params=Params({"seed": "w"}))
    host, sink, sync = dead_host(lambda _unused, parent: sip.delete(parent))
    row: dict[str, Any] = {"apply_on_dead_engine": _attempt(lambda: sync.apply({"w": moved}))}
    row.update(poisoned=sync.poisoned, reset=_attempt(sync.reset))
    row.update(poisoned_after_reset=sync.poisoned, items=list(sink.item_ids()),
               in_transaction=sink.in_transaction, state=host.state.value)
    out["reset_alone_on_a_dead_engine"] = row

    fresh = Quick3DHost(SIZE)
    sink.attach(fresh)
    sync.apply({"w": record}, sun=SunState(45.0, 225.0))
    fresh.set_shot(TopDownShot((0.0, 0.0), 0.5))
    fresh.show()
    out["fresh"] = {"ready": fresh.wait_ready(10.0), "grab": _grab(fresh), "pick": _centre_pick(fresh),
                    "parts": len(fresh.engine_parts()), "items": list(sink.item_ids()),
                    "state_equals_expected": sink.state() == sync.expected_state()}
    _pump(app, 300)
    fresh.close()
    return out


def minimised_grab(app: Any, _qml: Path | None = None) -> dict[str, Any]:
    """A live window, minimised: visible to Qt, not exposed — the host reads HIDDEN."""
    host, _sink, _sync = _host()
    host.show()
    out = {"ready": host.wait_ready(10.0), "live": _grab(host)}
    host._view.showMinimized()  # noqa: SLF001 - the host has no minimise call: the user's button
    _pump(app, 300)
    out.update(state_minimised=host.state.value, is_visible=host.is_visible(),
               grab_minimised=_grab(host), pick_minimised=_centre_pick(host))
    host._view.showNormal()  # noqa: SLF001
    out.update(ready_again=host.wait_ready(10.0), grab_restored=_grab(host))
    host.close()
    return out


def watch(app: Any, qml: Path | None = None) -> dict[str, Any]:
    """P2-13: a SHOWN host that listens. With the shipped QML nothing is reported; with a
    broken shader (``qml`` points at a copy) the error reaches the log and the owner."""
    from open_garden_planner.core.scene3d import EnginePose, engine_buffers, prism_mesh
    from open_garden_planner.ui.view3d.quick3d.adapter import PartSpec
    from open_garden_planner.ui.view3d.quick3d.host import Quick3DHost
    from open_garden_planner.ui.view3d.quick3d.shots import TopDownShot

    logged: list[dict[str, Any]] = []
    gui_thread = threading.get_ident()

    class Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            logged.append({"level": record.levelname, "message": record.getMessage()[:300],
                           "gui_thread": threading.get_ident() == gui_thread})

    logger = logging.getLogger("ogp.tests.quick3d.watch")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(Keep())

    host = Quick3DHost(SIZE, qml_dir=qml)
    out: dict[str, Any] = {"errors_without_a_watch": _attempt(host.engine_errors)}
    watching = host.watch_messages(logger)
    out["watch_is_idempotent"] = host.watch_messages(logger) is watching
    host.create_part(PartSpec("leaf", 0, "foliage", engine_buffers(prism_mesh([RING], 20.0)), True),
                     EnginePose((0.0, 0.0, 0.0), 0.0), (230, 20, 20, 255))
    host.set_sun((0.0, -1.0, 0.0))  # straight down, engine frame
    host.set_shot(TopDownShot((0.0, 0.0), 0.5))
    host.show()
    out.update(ready=host.wait_ready(10.0), grab=_grab(host))
    _pump(app, 300)
    out.update(engine_errors=list(host.engine_errors()), counts=dict(watching.counts),
               logged=list(logged), second_grab=_grab(host))
    host.close()
    out["errors_after_close"] = _attempt(lambda: len(host.engine_errors()))
    return out


def unguarded_show_grab(app: Any, _qml: Path | None = None) -> dict[str, Any]:
    """CONTROL — what the guard forbids, done on the raw view: shown, grabbed before the
    first expose. The image is fine; the process dies in the event loop that follows."""
    host, _sink, _sync = _host()
    host.show()
    image = host._view.grabWindow()  # noqa: SLF001 - the control reaches past the guard
    out = {"state": host.state.value, "grab": {"red": _red(image)}}
    print("GRABBED " + json.dumps(out), flush=True)  # the last sign of life, if the control works
    _pump(app, 1000)  # with the window ALIVE: a host that went out of scope would hide the death
    host.close()
    return out


def unguarded_grab_show(app: Any, _qml: Path | None = None) -> dict[str, Any]:
    """CONTROL — the one offscreen frame, then the raw view is shown (what the old
    error message recommended)."""
    host, _sink, _sync = _host()
    image = host.grab("offscreen")
    host._view.show()  # noqa: SLF001 - the control reaches past the guard
    out = {"state": host.state.value, "grab": {"red": _red(image)}}
    print("GRABBED " + json.dumps(out), flush=True)
    _pump(app, 1000)
    host.close()
    return out


ORDERS: dict[str, Callable[[Any, Path | None], dict[str, Any]]] = {
    "show_grab": show_grab,
    "grab_show": grab_show,
    "show_wait_grab": show_wait_grab,
    "hide_grab": hide_grab,
    "show_hide_show_grab": show_hide_show_grab,
    "show_hide_before_expose_grab": show_hide_before_expose_grab,
    "grab_twice_hidden": grab_twice_hidden,
    "close_anything": close_anything,
    "parent_destroyed_anything": parent_destroyed_anything,
    "embedded_splitter": embedded_splitter,
    "embedded_never_shown_grab": embedded_never_shown_grab,
    "embed_after_show": embed_after_show,
    "sink_clear_orders": sink_clear_orders,
    "minimised_grab": minimised_grab,
    "watch": watch,
    "unguarded_show_grab": unguarded_show_grab,
    "unguarded_grab_show": unguarded_grab_show,
}


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2) or argv[0] not in ORDERS:
        print(f"usage: quick3d_lifecycle_child.py <{'|'.join(ORDERS)}> [<qml dir>]",
              file=sys.stderr)
        return 2
    qml_dir = Path(argv[1]).resolve() if len(argv) == 2 else None
    faulthandler.enable(all_threads=True)
    faulthandler.dump_traceback_later(90, exit=True)  # a hang is a failure too
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    _register_calls()
    result = ORDERS[argv[0]](app, qml_dir)
    _pump(app, 500)  # a process doomed by a bad grab dies HERE, in the next event-loop passes
    print("RESULT " + json.dumps(result, default=str), flush=True)
    print("END", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
