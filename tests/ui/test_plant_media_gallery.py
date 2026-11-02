"""Real asynchronous import, thumbnails and undoable associations in the gallery."""
from PIL import Image
from PyQt6.QtWidgets import QFileDialog, QGraphicsScene

from open_garden_planner.core.commands import CommandManager
from open_garden_planner.core.plant_specimens import SpecimenEditCommand
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.services.plant_specimens import add_specimen
from open_garden_planner.ui.widgets.plant_media_gallery import PlantMediaGallery


def test_gallery_background_import_primary_remove_undo_and_reopen(qtbot, tmp_path, monkeypatch):
    project, scene, commands = ProjectManager(), QGraphicsScene(), CommandManager()
    candidate, identity = add_specimen(project.snapshot_dict(scene), "rose", "Rose pot")
    commands.execute(SpecimenEditCommand(project, candidate))
    path = tmp_path / "garden.ogp"
    project.save(scene, path)
    photo = tmp_path / "photo.png"
    Image.new("RGB", (640, 480), "orange").save(photo)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a, **_kw: (str(photo), ""))
    gallery = PlantMediaGallery(project, scene, commands, identity)
    qtbot.addWidget(gallery)
    gallery.show()
    gallery.caption.setText("Rose en juin")
    gallery._import()
    qtbot.waitUntil(lambda: gallery.gallery.count() == 1 and not gallery._jobs)
    item = gallery.gallery.item(0)
    assert not item.icon().isNull()
    gallery.gallery.setCurrentRow(0)
    gallery._primary()
    qtbot.waitUntil(lambda: not gallery._jobs)
    asset = project.plant_specimens[identity]["metadata"]["plant_instance"]["media_assets"][0]
    original = path.parent / asset["photo_path"]
    assert original.read_bytes() == photo.read_bytes()
    gallery.gallery.setCurrentRow(0)
    gallery._remove()
    assert not project.plant_specimens[identity]["metadata"]["plant_instance"]["media_assets"]
    assert original.exists()
    commands.undo()
    project.save(scene, path)
    reopened = ProjectManager()
    reopened.load(QGraphicsScene(), path)
    assert reopened.plant_specimens == project.plant_specimens
    assert reopened.plant_specimens[identity]["metadata"]["plant_instance"]["primary_media_id"] == asset["id"]
    qtbot.waitUntil(lambda: not gallery._jobs)
