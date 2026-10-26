"""File dialogs must finish before verifying synthetic import/export results."""

import base64
import csv
import io
import xml.etree.ElementTree as ET

from PIL import Image

from tests.integration.test_agent_ui_control import ui_window  # noqa: F401
from tests.integration.test_mcp_extended_workflows import modal_title, reopen
from tests.integration.test_mcp_horticultural_cycle import run_cycle


def test_discovered_creation_roster_roundtrip_delete_and_undo_over_mcp(ui_window, qtbot, tmp_path):  # noqa: F811
    window, _ = ui_window

    async def body(d):
        catalog = (await d.call("list_creatable_types"))["result"]
        count = 0
        for spec in catalog:
            if not spec["creatable"]:
                assert spec["reason"]
                continue
            await d.call("new_plan", force=True)
            args = {"object_type": spec["object_type"], "name": f"Audit {spec['object_type']}"}
            if spec["shape"] == "polyline":
                args["points"] = [[300, 300], [500, 400]]
            elif spec["shape"] == "polygon":
                args["points"] = [[300, 300], [500, 300], [400, 500]]
            else:
                args.update(x=400, y=400)
                if spec["shape"] == "circle":
                    args["radius"] = 50
                elif spec["shape"] in {"rectangle", "ellipse"}:
                    args.update(width=200, height=100)
                elif spec["shape"] == "callout":
                    args["text"] = "Annotation synthétique"
                else:
                    raise AssertionError(("Uncovered creation family", spec))
            created = await d.call("create_object", **args)
            identity = created["item_id"]
            geometry = await d.call("get_geometry", item_id=identity)
            raw = await d.raw()
            restored = await reopen(d, tmp_path / "roster.ogp")
            assert restored["objects"] == raw["objects"], spec["object_type"]
            assert await d.call("get_geometry", item_id=identity) == geometry, spec["object_type"]
            await d.call("delete_object", item_id=identity)
            assert not (await d.raw())["objects"], spec["object_type"]
            await d.call("undo")
            assert await d.call("get_geometry", item_id=identity) == geometry
            await d.call("redo")
            assert not (await d.raw())["objects"]
            count += 1
        print(f"MCP creation/roundtrip/delete/undo: {count} creatable types")

    run_cycle(window, qtbot, body)


def test_background_import_is_undoable_and_embedded_over_mcp(ui_window, qtbot, tmp_path):  # noqa: F811
    window, _ = ui_window
    image = tmp_path / "synthetic.png"
    Image.new("RGB", (60, 40), "green").save(image)

    async def body(d):
        await d.call("save_plan", file_path=str(tmp_path / "background.ogp"))
        _, receipt = await d.open_action("Import Background Image")
        await d.wait(await d.file_dialog(image, "Open"))
        await d.wait(receipt)
        imported = next(o for o in (await d.raw())["objects"] if o["type"] == "background_image")
        assert (await d.call("get_plan_summary"))["is_dirty"]
        assert (await d.call("get_history"))["undo_depth"] == 1
        await d.call("undo")
        assert not (await d.raw())["objects"]
        await d.call("redo")
        raw = await reopen(d, tmp_path / "background.ogp")
        assert raw["objects"][0] == imported
        with Image.open(io.BytesIO(base64.b64decode(imported["image_data"]))) as decoded:
            assert decoded.size == (60, 40) and decoded.getpixel((0, 0))[:3] == (0, 128, 0)

    run_cycle(window, qtbot, body)


def test_dxf_import_export_and_png_svg_csv_dialogs_over_mcp(ui_window, qtbot, tmp_path):  # noqa: F811
    import ezdxf

    window, _ = ui_window
    source = tmp_path / "synthetic.dxf"
    doc = ezdxf.new()
    doc.modelspace().add_line((100, 100), (200, 100))
    doc.saveas(source)

    async def body(d):
        _, receipt = await d.open_action("Import DXF")
        await d.wait(await d.file_dialog(source, "Open"))
        form = await modal_title(d, "Import DXF")
        await d.field(form, "Scale (DXF units → cm):", 2, "QDoubleSpinBox")
        await d.button(form, "OK")
        await d.wait(receipt)
        objects = (await d.call("list_objects"))["result"]
        assert len(objects) == 1
        geometry = await d.call("get_geometry", item_id=objects[0]["item_id"])
        assert geometry["vertices"] == [{"x_cm": 200, "y_cm": 200}, {"x_cm": 400, "y_cm": 200}]
        await d.call("undo")
        assert not (await d.raw())["objects"]
        await d.call("redo")
        restored = await reopen(d, tmp_path / "import.ogp")
        assert len(restored["objects"]) == 1
        await d.call("create_object", object_type="PERENNIAL", species="Tomato", name="Tomate exportée", x=400, y=300)
        export = tmp_path / "roundtrip.dxf"
        await d.call("export_dxf", file_path=str(export))
        assert len(ezdxf.readfile(export).modelspace()) >= 2
        svg = tmp_path / "plan.svg"
        _, receipt = await d.open_action("Export as SVG")
        await d.wait(await d.file_dialog(svg, "Save"))
        await d.wait(receipt)
        assert ET.parse(svg).getroot().tag.endswith("svg")
        png = tmp_path / "plan.png"
        form, receipt = await d.open_action("Export as PNG")
        radio = d.one(form, **{"class": "QRadioButton", "text": "72 DPI (Screen)"})
        await d.run("interact_ui", widget_id=radio["id"], action="click")
        await d.button(form, "Save")
        await d.wait(await d.file_dialog(png, "Save"))
        await d.wait(receipt)
        with Image.open(png) as result:
            assert abs(result.width - 29.7 / 2.54 * 72) < 1 and result.height > 0
            assert len(result.convert("RGB").getcolors(result.width * result.height)) > 1
        plants = tmp_path / "plants.csv"
        _, receipt = await d.open_action("Export Plant List")
        await d.wait(await d.file_dialog(plants, "Save"))
        await d.wait(receipt)
        with plants.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        assert len(rows) == 2 and "Tomato" in str(rows)

    run_cycle(window, qtbot, body)
