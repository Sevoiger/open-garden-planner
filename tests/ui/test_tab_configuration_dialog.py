from PyQt6.QtCore import Qt

from open_garden_planner.ui.dialogs.tab_configuration_dialog import (
    TabConfigurationDialog,
)


def test_dialog_orders_tabs_and_keeps_garden_plan_visible(qtbot):
    dialog = TabConfigurationDialog(
        [
            ("garden_plan", "Garden Plan", True),
            ("calendar", "Calendar", True),
            ("harvest", "Harvest", False),
        ],
        permanent_tab_id="garden_plan",
    )
    qtbot.addWidget(dialog)

    plan = dialog.tab_list.item(0)
    plan.setCheckState(Qt.CheckState.Unchecked)
    assert plan.checkState() == Qt.CheckState.Checked

    dialog.tab_list.setCurrentRow(2)
    dialog.move_up_button.click()
    assert dialog.tab_order == ["garden_plan", "harvest", "calendar"]
    assert dialog.visible_tab_ids == ["garden_plan", "calendar"]
