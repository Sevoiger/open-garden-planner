"""Audit every custom dialog under a constrained desktop, without user data or IO."""

import ast
import importlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QGroupBox,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStyle,
    QStyleOptionButton,
    QTabWidget,
    QWidget,
)

from open_garden_planner.core.commands import CommandManager
from open_garden_planner.core.garden_memory import GardenMemoryService
from open_garden_planner.core.i18n import load_translator
from open_garden_planner.core.project import ProjectManager
from open_garden_planner.services.companion_planting_service import CompanionPlantingService
from open_garden_planner.services.guild_library import GuildLibrary
from open_garden_planner.services.plant_api import PlantAPIManager
from open_garden_planner.services.shopping_list_service import ShoppingListService
from open_garden_planner.services.soil_service import SoilService
from open_garden_planner.ui.canvas.canvas_scene import CanvasScene
from open_garden_planner.ui.canvas.items import RectangleItem
from open_garden_planner.ui.theme import ThemeMode, apply_theme
from open_garden_planner.ui.widgets.responsive_dialog import bounded_dialog_rect

# WebEngine must be imported before pytest-qt creates QApplication; the view
# itself is replaced below, so this does not start Chromium.
importlib.import_module("open_garden_planner.ui.dialogs.map_picker_dialog")

DIALOGS = Path(__file__).resolve().parents[2] / 'src/open_garden_planner/ui/dialogs'
CASES = [
    (p.stem, node.name)
    for p in sorted(DIALOGS.glob('*.py'))
    for node in ast.walk(ast.parse(p.read_text()))
    if isinstance(node, ast.ClassDef) and any(
        isinstance(base, ast.Name) and base.id == 'ResponsiveDialog' for base in node.bases
    )
] + [('companion_panel', 'CompatibleSetDialog')]


class DummyWebView(QWidget):
    """Exercise the actual map dialog layout without Chromium or network calls."""

    def page(self):
        return MagicMock()

    def settings(self):
        return MagicMock()

    def setUrl(self, url):
        pass


def make_dialog(module_name, class_name, monkeypatch, tmp_path):
    prefix = 'panels' if module_name == 'companion_panel' else 'dialogs'
    module = importlib.import_module(f'open_garden_planner.ui.{prefix}.{module_name}')
    cls = getattr(module, class_name)
    scene = CanvasScene()
    pm = ProjectManager()
    services = (scene, pm)  # keep QObject dependencies alive through the dialog's lifetime
    kwargs = {}
    if class_name == 'AddPlantingRecordDialog':
        kwargs = {'area_id': 'layout-audit'}
    elif class_name == 'CalibrationDialog':
        pixmap = QPixmap(300, 200)
        pixmap.fill()
        kwargs = {'image_pixmap': pixmap}
    elif class_name == 'CompanionCheckDialog':
        kwargs = {'beneficial': [], 'antagonistic': [], 'service': CompanionPlantingService()}
    elif class_name == 'ConnectAiAssistantDialog':
        kwargs = {'server_url': None}
    elif class_name == 'ConstraintConflictDialog':
        kwargs = {'conflicts': []}
    elif class_name == 'DxfImportDialog':
        import ezdxf
        path = tmp_path / 'audit.dxf'
        ezdxf.new().saveas(path)
        kwargs = {'file_path': path}
    elif class_name in ('ExportPngDialog', 'PrintOptionsDialog'):
        kwargs = {'canvas_width_cm': 1000, 'canvas_height_cm': 800}
        if class_name == 'PrintOptionsDialog':
            kwargs.update(grid_visible=True, labels_visible=True)
    elif class_name == 'EventEditor':
        kwargs = {'targets': []}
    elif class_name == 'GardenMemoryDialog':
        commands = CommandManager()
        kwargs = {'service': GardenMemoryService(pm, commands, scene)}
        services += (commands,)
    elif class_name == 'GuildTemplateEditor':
        kwargs = {'library': GuildLibrary(user_dir=tmp_path / 'guilds')}
    elif class_name == 'InsertGuildDialog':
        kwargs = {'scene': scene, 'library': GuildLibrary(user_dir=tmp_path / 'guilds')}
    elif class_name == 'MapPickerDialog':
        monkeypatch.setattr(module, 'QWebEngineView', DummyWebView)
        kwargs = {'api_key': 'layout-test-placeholder'}
    elif class_name == 'PlantSearchDialog':
        kwargs = {'api_manager': PlantAPIManager()}
    elif class_name == 'PropertiesDialog':
        kwargs = {'item': RectangleItem(0, 0, 100, 80)}
    elif class_name == 'SeasonManagerDialog':
        kwargs = {'project_manager': pm}
    elif class_name == 'ShoppingListDialog':
        kwargs = {'service': ShoppingListService(scene, SoilService(pm), pm)}
    elif class_name == '_EntryDialog':
        kwargs = {'parent': None, 'segments': module.resolve_season_segments(2026, None)[0]}
    elif class_name == 'CompatibleSetDialog':
        kwargs = {'sets': [], 'existing_plants': []}
    dialog = cls(**kwargs)
    dialog._audit_dependencies = (services, kwargs)
    return dialog


def assert_readable_controls(dialog, class_name):
    for group in dialog.findChildren(QGroupBox):
        if group.isVisibleTo(dialog):
            assert group.height() >= group.minimumSizeHint().height(), group.title()
    for field in [*dialog.findChildren(QLineEdit), *dialog.findChildren(QAbstractSpinBox), *dialog.findChildren(QComboBox)]:
        if isinstance(field, QLineEdit) and isinstance(field.parentWidget(), (QAbstractSpinBox, QComboBox)):
            continue
        if field.isVisibleTo(dialog):
            assert field.height() >= field.minimumSizeHint().height(), (class_name, field.objectName())
    # A clipped button was the measured Preferences regression: 0–12 px high
    # for 17 px of glyphs. Check actual styled content rectangles, not just hints.
    for button in dialog.findChildren(QPushButton):
        assert button.parentWidget() is not dialog, (class_name, button.text(), "orphaned action")
        if not button.isVisibleTo(dialog) or not button.text():
            continue
        option = QStyleOptionButton()
        option.initFrom(button)
        contents = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
        assert contents.height() >= button.fontMetrics().height(), (
            class_name, button.text(), contents, button.fontMetrics().height()
        )
        assert contents.width() >= button.fontMetrics().horizontalAdvance(button.text().replace('&', '')), (
            class_name, button.text(), contents, button.sizeHint()
        )


@pytest.mark.parametrize('module_name,class_name', CASES, ids=[c[1] for c in CASES])
@pytest.mark.parametrize('font_size,theme,language', [(13, ThemeMode.DARK, 'en'), (18, ThemeMode.LIGHT, 'de'), (18, ThemeMode.LIGHT, 'fr')])
def test_forms_keep_controls_readable_on_small_desktop(
    qtbot, qapp, monkeypatch, tmp_path, module_name, class_name, font_size, theme, language,
):
    old_font = qapp.font()
    qapp.setFont(QFont('Helvetica', font_size))
    apply_theme(qapp, theme)
    assert load_translator(qapp, language)
    try:
        dialog = make_dialog(module_name, class_name, monkeypatch, tmp_path)
        qtbot.addWidget(dialog)
        available = QRect(40, 40, 600, 440)
        screen = MagicMock()
        screen.availableGeometry.return_value = available
        monkeypatch.setattr(dialog, 'screen', lambda: screen)
        dialog.resize(600, 440)
        dialog.show()
        qapp.processEvents()
        qtbot.waitUntil(lambda: available.contains(dialog.frameGeometry()))
        qtbot.wait(20)
        scroll = dialog._responsive_scroll
        assert scroll.widget().height() >= scroll.widget().minimumSizeHint().height()
        assert scroll.widget().width() >= scroll.widget().minimumSizeHint().width()
        assert_readable_controls(dialog, class_name)
        for tabs in dialog.findChildren(QTabWidget):
            for index in range(tabs.count()):
                tabs.setCurrentIndex(index)
                qapp.processEvents()
                assert_readable_controls(dialog, class_name)
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        scroll.horizontalScrollBar().setValue(scroll.horizontalScrollBar().maximum())
        qapp.processEvents()
        # Showing twice must not reparent widgets again or duplicate wrappers.
        dialog.hide()
        dialog.show()
        assert dialog._responsive_scroll is scroll
        dialog.close()
    finally:
        load_translator(qapp, "en")
        qapp.setFont(old_font)
        apply_theme(qapp, ThemeMode.DARK)


def test_all_custom_dialogs_use_screen_policy():
    root = DIALOGS.parent
    unmanaged = []
    for path in root.rglob('*.py'):
        if path.name == 'responsive_dialog.py':
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ClassDef) and any(
                isinstance(base, ast.Name) and base.id == 'QDialog' for base in node.bases
            ):
                unmanaged.append((path.name, node.name))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'QDialog':
                unmanaged.append((path.name, 'ad hoc QDialog constructor'))
    assert not unmanaged
    assert len(CASES) == 38


@pytest.mark.parametrize('frame', [QRect(900, 900, 1000, 1000), QRect(-1500, -900, 250, 200)])
def test_bounds_include_negative_monitor_origins(frame):
    available = QRect(-1200, -200, 800, 600)
    assert available.contains(bounded_dialog_rect(frame, available))


def test_preferences_can_save_from_scrolled_form(qtbot, qapp):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QDialog

    from open_garden_planner.app.settings import get_settings
    from open_garden_planner.ui.dialogs.preferences_dialog import PreferencesDialog

    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)
    dialog.resize(550, 400)
    dialog.show()
    qtbot.wait(20)
    scroll = dialog._responsive_scroll
    field = dialog._permapeople_key_id._line_edit
    scroll.ensureWidgetVisible(field)
    qapp.processEvents()
    qtbot.keyClicks(field, 'layout-test-id')
    save = next(button for button in dialog.findChildren(QPushButton) if button.text() == 'Save')
    assert not scroll.isAncestorOf(save)
    actions = dialog.findChild(QScrollArea, 'dialogActionsScroll')
    assert actions is not None and actions.isAncestorOf(save)
    assert actions.geometry().bottom() <= dialog.rect().bottom()
    assert actions.geometry().top() >= scroll.geometry().bottom()
    assert actions.viewport().rect().contains(save.mapTo(actions.viewport(), save.rect().center()))
    qtbot.mouseClick(save, Qt.MouseButton.LeftButton)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert get_settings().permapeople_key_id == 'layout-test-id'
    restored = PreferencesDialog()
    qtbot.addWidget(restored)
    assert restored._permapeople_key_id.text() == 'layout-test-id'


def test_french_rotation_buttons_fit_their_text(qtbot, qapp):
    from open_garden_planner.services.crop_rotation_service import CropRotationService
    from open_garden_planner.ui.panels.crop_rotation_panel import CropRotationPanel

    apply_theme(qapp, ThemeMode.LIGHT)
    assert load_translator(qapp, 'fr')
    try:
        panel = CropRotationPanel(CropRotationService())
        qtbot.addWidget(panel)
        panel.resize(700, 800)
        panel.show()
        qapp.processEvents()
        for button in (panel._edit_record_btn, panel._delete_record_btn):
            option = QStyleOptionButton()
            option.initFrom(button)
            contents = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
            assert contents.width() >= button.fontMetrics().horizontalAdvance(button.text())
    finally:
        load_translator(qapp, 'en')
        apply_theme(qapp, ThemeMode.DARK)
