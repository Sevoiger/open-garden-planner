from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from open_garden_planner.app.application import (
    TAB_CALENDAR,
    TAB_HARVEST,
    TAB_PLAN,
    TAB_SEEDS,
    GardenPlannerApp,
)
from open_garden_planner.ui.dialogs.tab_configuration_dialog import (
    TabConfigurationDialog,
)


def _make_app(qtbot, monkeypatch):
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Discard,
    )
    monkeypatch.setattr(QApplication, "focusWidget", lambda: None)
    monkeypatch.setattr(GardenPlannerApp, "_show_welcome_dialog", lambda _self: None)
    window = GardenPlannerApp()
    qtbot.addWidget(window)
    return window


def test_tab_configuration_persists_order_visibility_and_navigation(qtbot, monkeypatch):
    window = _make_app(qtbot, monkeypatch)
    original_harvest = window.harvest_view
    assert window._tab_widget.tabText(window._tab_widget.indexOf(window.calendar_view)) == "Calendar"
    assert window._tab_widget.tabText(window._tab_widget.indexOf(window.seed_inventory_view)) == "Seeds"
    assert TAB_CALENDAR in window._tab_widgets_by_id and TAB_SEEDS in window._tab_widgets_by_id

    def accept_configuration(dialog):
        harvest_row = next(
            row
            for row in range(dialog.tab_list.count())
            if dialog.tab_list.item(row).data(dialog.TAB_ID_ROLE) == TAB_HARVEST
        )
        dialog.tab_list.item(harvest_row).setCheckState(Qt.CheckState.Unchecked)
        dialog.tab_list.setCurrentRow(harvest_row)
        dialog.move_up_button.click()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(TabConfigurationDialog, "exec", accept_configuration)
    window._on_configure_tabs()
    configured_order = window._tab_order_ids()
    assert list(window._tab_visibility_actions) == configured_order
    assert configured_order.index(TAB_HARVEST) == 4
    harvest_index = window._tab_widget.indexOf(original_harvest)
    assert not window._tab_widget.isTabVisible(harvest_index)
    assert window._tab_widget.widget(harvest_index) is original_harvest
    window.close()

    reopened = _make_app(qtbot, monkeypatch)
    assert reopened._tab_order_ids() == configured_order
    harvest_index = reopened._tab_widget.indexOf(reopened.harvest_view)
    assert not reopened._tab_widget.isTabVisible(harvest_index)
    assert reopened._tab_widget.widget(harvest_index) is reopened.harvest_view
    assert reopened._tab_widget.isTabVisible(reopened._tab_widget.indexOf(reopened._tab_widgets_by_id[TAB_PLAN]))

    def accept_unchanged_configuration(dialog):
        assert dialog.tab_order == configured_order
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(TabConfigurationDialog, "exec", accept_unchanged_configuration)
    reopened._on_configure_tabs()
    assert reopened._tab_order_ids() == configured_order

    visibility_action = reopened._tab_visibility_actions[TAB_HARVEST]
    visibility_action.trigger()
    assert reopened._tab_widget.isTabVisible(reopened._tab_widget.indexOf(reopened.harvest_view))
    visibility_action.trigger()
    assert not reopened._tab_widget.isTabVisible(reopened._tab_widget.indexOf(reopened.harvest_view))

    # Business navigation explicitly restores a hidden tab before selecting it.
    assert reopened._navigate_to_tab(TAB_HARVEST)
    harvest_index = reopened._tab_widget.indexOf(reopened.harvest_view)
    assert reopened._tab_widget.isTabVisible(harvest_index)
    assert reopened._tab_widget.currentWidget() is reopened.harvest_view
    visibility_action.trigger()
    assert not reopened._tab_widget.isTabVisible(harvest_index)
    assert reopened._tab_widget.currentWidget() is reopened._tab_widgets_by_id[TAB_PLAN]
    assert reopened._navigate_to_tab(TAB_HARVEST)
    reopened.close()


def test_all_default_tabs_fit_inside_a_wide_window(qtbot, monkeypatch):
    window = _make_app(qtbot, monkeypatch)
    window.resize(1100, 800)
    window.show()
    tab_bar = window._tab_widget.tabBar()
    qtbot.waitUntil(lambda: tab_bar.width() >= 1000)

    assert window._tab_widget.count() == 9
    assert all(
        window._tab_widget.isTabVisible(index)
        for index in range(window._tab_widget.count())
    )
    assert not window._tab_widget.usesScrollButtons()
    assert tab_bar.elideMode() == Qt.TextElideMode.ElideRight
    assert tab_bar.tabRect(window._tab_widget.count() - 1).right() < tab_bar.width()
    assert window._tab_widget.tabToolTip(
        window._tab_widget.indexOf(window.calendar_view)
    ) == "Planting Calendar"
    assert window._tab_widget.tabToolTip(
        window._tab_widget.indexOf(window.seed_inventory_view)
    ) == "Seed Inventory"
    window.close()
