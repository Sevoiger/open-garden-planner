"""Visible, selectable diagnostics with an explicit clipboard action."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class CopyableErrorDialog(QDialog):
    def __init__(self, title, message, parent=None, *, code="CATALOG_ERROR", details="", source_error=None, url=""):
        super().__init__(parent)
        self.error_info = None
        if source_error is not None:
            from open_garden_planner.services.plant_source_errors import plant_source_error_info

            self.error_info = plant_source_error_info(source_error, url=url)
            info = self.error_info
            code, details, url = info.code, info.message, info.url
            message = self._source_message(info)
        self.code = code
        self.setWindowTitle(title)
        self.resize(760, 420)
        layout = QVBoxLayout(self)
        self.summary = QLabel(message)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        layout.addWidget(self.summary)
        lines = [self.tr("Error code: {code}").format(code=code)]
        if self.error_info is not None and self.error_info.http_status is not None:
            lines.append(self.tr("HTTP status: {status}").format(status=self.error_info.http_status))
        if url:
            lines.append(self.tr("Source URL: {url}").format(url=url))
        if self.error_info is not None and self.error_info.stage:
            lines.append(self.tr("Step: {step}").format(step=self.error_info.stage))
        if details:
            lines.extend(("", details))
        self.details = QPlainTextEdit("\n".join(lines))
        self.details.setReadOnly(True)
        self.details.setAccessibleName(self.tr("Error details"))
        layout.addWidget(self.details, 1)
        self.report = "\n\n".join((title, message, self.details.toPlainText()))
        buttons = QDialogButtonBox()
        self.copy_button = QPushButton(self.tr("Copy details"))
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.report))
        buttons.addButton(self.copy_button, QDialogButtonBox.ButtonRole.ActionRole)
        close = QPushButton(self.tr("Close"))
        close.clicked.connect(self.accept)
        buttons.addButton(close, QDialogButtonBox.ButtonRole.AcceptRole)
        layout.addWidget(buttons)

    def _source_message(self, info):
        summaries = {
            "SOURCE_UNSUPPORTED_URL": self.tr("This source URL is not supported."),
            "SOURCE_ROBOTS_DENIED": self.tr("The source does not allow automated retrieval."),
            "SOURCE_HTTP_ERROR": self.tr("The source returned HTTP {status}.").format(status=info.http_status),
            "SOURCE_TIMEOUT": self.tr("The source did not respond in time."),
            "SOURCE_NETWORK_ERROR": self.tr("The source could not be reached. Check the network connection."),
            "SOURCE_TOO_LARGE": self.tr("The source exceeds the supported size limit."),
            "SOURCE_PRODUCT_AMBIGUOUS": self.tr("Several plants were found, but the main plant could not be identified reliably."),
            "SOURCE_PARSE_ERROR": self.tr("The source format could not be read."),
            "SOURCE_IDENTITY_MISMATCH": self.tr("The downloaded page does not match the requested plant."),
        }
        summary = summaries.get(info.code, self.tr("The plant source could not be read."))
        if info.code == "SOURCE_UNSUPPORTED_URL":
            action = self.tr("Enter an HTTPS Promesse de Fleurs product URL. Your record has not been changed.")
        elif info.code == "SOURCE_TIMEOUT" or info.retryable:
            action = self.tr("You can retry later. Your record has not been changed.")
        elif info.code == "SOURCE_ROBOTS_DENIED" or info.http_status in (401, 403):
            action = self.tr("Use a local file you already have. Your record has not been changed.")
        else:
            action = self.tr("Check the source URL. No changes were saved; the diagnostic below can be copied.")
        return summary + "\n\n" + action


def show_copyable_error(parent, title, message, *, code="CATALOG_ERROR", details=""):
    return CopyableErrorDialog(title, message, parent, code=code, details=details).exec()
