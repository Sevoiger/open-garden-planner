"""Large-image reads must reuse encoded data, rather than time out in PNG compression."""

import base64

from PyQt6.QtGui import QColor, QPixmap

from open_garden_planner.core.project import ProjectManager
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.items.background_image_item import BackgroundImageItem
from tests.integration.test_agent_ui_control import ui_window  # noqa: F401
from tests.integration.test_mcp_horticultural_cycle import run_cycle


def test_embedded_background_reads_and_save_reopen_do_not_reencode(ui_window, qtbot, tmp_path, monkeypatch):  # noqa: F811
    window, _ = ui_window
    image_path = tmp_path / "synthetic.png"
    pixmap = QPixmap(64, 48)
    pixmap.fill(QColor("#316846"))
    assert pixmap.save(str(image_path), "PNG")
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    item = BackgroundImageItem.from_dict({"type": "background_image", "image_data": encoded})
    window.canvas_scene.addItem(item)

    def no_recompression(*_args, **_kwargs):
        raise AssertionError("Snapshot must not invoke PNG encoding")

    monkeypatch.setattr(QPixmap, "save", no_recompression)
    project = tmp_path / "background.ogp"

    async def body(d):
        for _ in range(2):
            assert (await d.call("get_plan_summary"))["is_dirty"] is False
            raw = await d.raw()
            assert raw["objects"][0]["image_data"] == encoded
        await d.call("save_plan", file_path=str(project))
        await d.call("open_plan", file_path=str(project))
        assert (await d.raw())["objects"][0]["image_data"] == encoded
    run_cycle(window, qtbot, body)


def test_background_encoding_cache_tracks_pixel_changes_and_live_geometry(qtbot, tmp_path, monkeypatch):
    image_path = tmp_path / "synthetic.png"
    pixmap = QPixmap(64, 48)
    pixmap.fill(QColor("#316846"))
    assert pixmap.save(str(image_path), "PNG")
    item = BackgroundImageItem(str(image_path))
    first = item.to_dict()
    original_save = QPixmap.save
    calls = []

    def counted(pixmap, *args, **kwargs):
        calls.append(pixmap.cacheKey())
        return original_save(pixmap, *args, **kwargs)

    monkeypatch.setattr(QPixmap, "save", counted)
    item.setPos(12, 34)
    item.opacity = 0.6
    moved = item.to_dict()
    assert moved["position"] == {"x": 12, "y": 34} and moved["opacity"] == 0.6
    assert moved["image_data"] == first["image_data"] and calls == []
    item._original_pixmap.fill(QColor("#ff2211"))
    second = item.to_dict()
    assert len(calls) == 1 and second["image_data"] != first["image_data"]
    assert item.to_dict() == second and len(calls) == 1
    scene, pm = CanvasScene(), ProjectManager()
    scene.addItem(item)
    target = tmp_path / "changed.ogp"
    pm.save(scene, target)
    restored, other = ProjectManager(), CanvasScene()
    restored.load(other, target)
    assert restored.snapshot_dict(other)["objects"][0]["image_data"] == second["image_data"]
