"""Create a fictional perennial garden .ogp without touching application settings."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def create_demo(destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite: {destination}")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtGui import QColor, QImage, QPainter, QPen
    from PyQt6.QtWidgets import QApplication

    from open_garden_planner.core.commands import CommandManager
    from open_garden_planner.core.garden_memory import GardenMemoryService
    from open_garden_planner.core.object_types import ObjectType
    from open_garden_planner.core.plan_io import write_plan
    from open_garden_planner.core.project import ProjectManager
    from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
    from open_garden_planner.ui.canvas.items import CircleItem, RectangleItem
    from open_garden_planner.ui.canvas.items.background_image_item import BackgroundImageItem

    application = QApplication.instance() or QApplication([])
    scene = CanvasScene(width_cm=1600, height_cm=1000)
    project = ProjectManager()
    commands = CommandManager()
    service = GardenMemoryService(project, commands, scene)
    layer_id = scene.layers[0].id
    zone = RectangleItem(200, 200, 700, 400, object_type=ObjectType.GARDEN_BED, layer_id=layer_id)
    tree = CircleItem(480, 420, 110, object_type=ObjectType.TREE, layer_id=layer_id)
    perennial = CircleItem(300, 300, 25, object_type=ObjectType.PERENNIAL, layer_id=layer_id)
    bulbs_a = CircleItem(480, 420, 40, object_type=ObjectType.PERENNIAL, layer_id=layer_id)
    bulbs_b = CircleItem(750, 320, 45, object_type=ObjectType.PERENNIAL, layer_id=layer_id)
    for item in (zone, tree, perennial, bulbs_a, bulbs_b):
        scene.addItem(item)
    for item, name, kind, state, when, count in [
        (zone, "ZONE FICTIVE — démonstration", "zone", "in_place", None, None),
        (tree, "Arbre fictif", "individual", "in_place", "2022-11-12", None),
        (perennial, "Vivace fictive", "individual", "in_place", "2024-04", None),
        (bulbs_a, "Alliums fictifs A — sous l’arbre", "group", "dormant", "Automne 2023", 30),
        (bulbs_b, "Bulbes fictifs B — à identifier", "group", "uncertain", None, None),
    ]:
        card = {"name": name, "kind": kind, "state": state, "period": when,
                "notes": "DÉMONSTRATION FICTIVE. Aucune donnée d’un jardin réel."}
        if item is not zone:
            card["zone_id"] = str(zone.item_id)
        if count is not None:
            card["quantity"] = count
        service.set_card(str(item.item_id), card)
    for index, target, kind, status, when, description, details in [
        (1, bulbs_a, "planting", "done", "Automne 2023", "Plantation fictive d’un groupe de bulbes", {}),
        (2, bulbs_b, "observation", "done", None, "Présence fictive, date et nombre inconnus", {}),
        (3, zone, "mulching", "done", "2025-11", "Paillage fictif sur une partie du massif",
         {"area_m2": 4.5, "quantity": 20, "unit": "L", "material": "Matériau fictif"}),
        (4, zone, "amendment", "planned", "2027-03", "Projet fictif, apport non réalisé",
         {"area_m2": 4.5, "material": "Compost fictif"}),
        (5, bulbs_a, "observation", "done", "2026", "Dormance fictive, aucune mort confirmée", {}),
    ]:
        service.add_event({"type": kind, "status": status, "period": when, "description": description,
                           "targets": [{"id": str(target.item_id), "name": target.name}], **details},
                          f"demo-event-{index}")
    # Programmatic reference grid, exactly 100 cm per cell; no real garden plan.
    image = QImage(1600, 1000, QImage.Format.Format_RGB32)
    image.fill(QColor("#f4f4e8"))
    painter = QPainter(image)
    painter.setPen(QPen(QColor("#dce1d4"), 1))
    for x in range(0, 1600, 100):
        painter.drawLine(x, 0, x, 1000)
    for y in range(0, 1000, 100):
        painter.drawLine(0, y, 1600, y)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    scene.addItem(BackgroundImageItem("fond-fictif.png", _pixmap_data=bytes(buffer.data())))
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_plan(destination, project.snapshot_dict(scene))
    # Keep the QApplication alive through the scene's C++ teardown.
    scene.clear()
    del scene, application


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    create_demo(args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
