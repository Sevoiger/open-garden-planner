"""Error diagnostics remain selectable and explicitly copyable, including on Qt workers."""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication

from open_garden_planner.services.plant_source_errors import PlantSourceError
from open_garden_planner.ui.dialogs.copyable_error_dialog import CopyableErrorDialog


def test_error_details_can_be_selected_and_copied(qtbot):
    error = PlantSourceError("Forbidden: échec du fournisseur", code="SOURCE_HTTP_ERROR",
        url="https://www.promessedefleurs.com/rose.html?token=private", http_status=403, stage="fetch")
    dialog = CopyableErrorDialog("Source", "", source_error=error)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.details.setFocus()
    dialog.details.selectAll()
    qtbot.keySequence(dialog.details, QKeySequence.StandardKey.Copy)
    copied = QApplication.clipboard().text()
    assert "SOURCE_HTTP_ERROR" in copied
    assert "403" in copied
    assert "échec du fournisseur" in copied
    assert "private" not in copied
    assert dialog.details.textCursor().hasSelection()
    assert dialog.summary.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    qtbot.mouseClick(dialog.copy_button, Qt.MouseButton.LeftButton)
    assert QApplication.clipboard().text() == dialog.report
    assert "Source" in dialog.report
    assert "HTTP 403" in dialog.report


def test_ambiguous_product_error_explains_failure_and_copies_candidates(qtbot):
    error = PlantSourceError("HTML contains multiple product records: download.html; Rose A; Rose B",
                            code="SOURCE_PRODUCT_AMBIGUOUS", stage="parse")
    dialog = CopyableErrorDialog("Source", "", source_error=error)
    qtbot.addWidget(dialog)
    assert "main plant could not be identified" in dialog.summary.text()
    assert "Rose A; Rose B" in dialog.details.toPlainText()
    assert "No changes were saved" in dialog.summary.text()


def test_network_error_does_not_invent_an_http_status(qtbot):
    dialog = CopyableErrorDialog("Source", "", source_error=PlantSourceError(
        "offline", code="SOURCE_NETWORK_ERROR", stage="fetch", retryable=True))
    qtbot.addWidget(dialog)
    assert "network connection" in dialog.summary.text()
    assert "HTTP" not in dialog.report
