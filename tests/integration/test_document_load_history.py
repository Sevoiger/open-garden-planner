"""A document replacement cannot retain commands bound to the old scene data."""

import pytest

from open_garden_planner.core.commands import CreateItemCommand
from open_garden_planner.core.plan_io import write_plan
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.canvas_view import CanvasView
from open_garden_planner.ui.canvas.items import RectangleItem


@pytest.mark.parametrize("failure", ["prevalidation", "publication", "none"])
def test_load_history_follows_destructive_boundary(qtbot, tmp_path, monkeypatch, failure):
    scene = CanvasScene()
    view = CanvasView(scene)
    qtbot.addWidget(view)
    manager = ProjectManager()
    history = view.command_manager
    item = RectangleItem(10, 20, 100, 50)
    history.execute(CreateItemCommand(scene, item))
    # Both undo and redo references must be discarded on replacement.
    history.execute(CreateItemCommand(scene, RectangleItem(200, 20, 100, 50)))
    history.undo()
    target = tmp_path / "replacement.ogp"
    write_plan(target, {"objects": [], "canvas": {"width": 1000, "height": 500}})
    if failure == "prevalidation":
        target.write_text("{invalid", encoding="utf-8")
    elif failure == "publication":
        def fail_after_replacement(_data):
            raise RuntimeError("metadata publication failed")

        monkeypatch.setattr(manager, "_restore_project_metadata", fail_after_replacement)
    try:
        if failure == "none":
            manager.load(scene, target)
        else:
            with pytest.raises((ValueError, RuntimeError)):
                manager.load(scene, target)
        if failure == "prevalidation":
            assert history.can_undo and history.can_redo
            assert item.scene() is scene
        else:
            assert not history.can_undo
            assert not history.can_redo
            assert item.scene() is None
    finally:
        manager.release_document_ownership()
