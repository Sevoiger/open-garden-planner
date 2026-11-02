"""Choose which workspace tabs are shown and how they are ordered."""

from collections.abc import Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class TabConfigurationDialog(QDialog):
    """A small accessible editor for tab visibility and display order.

    Each entry is ``(stable_id, translated_label, visible)``. The stable ID is
    stored in ``UserRole`` so the application never persists translated text.
    The plan tab remains checked, guaranteeing a persistent route back into the
    workspace.
    """

    TAB_ID_ROLE = Qt.ItemDataRole.UserRole

    def __init__(
        self,
        entries: Sequence[tuple[str, str, bool]],
        *,
        permanent_tab_id: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._permanent_tab_id = permanent_tab_id
        self.setWindowTitle(self.tr("Configure Tabs"))
        self.setMinimumWidth(340)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(self.tr("Choose which tabs to show and arrange their order."), self)
        )

        self.tab_list = QListWidget(self)
        self.tab_list.setAccessibleName(self.tr("Workspace tabs"))
        self.tab_list.setAccessibleDescription(
            self.tr("Checked tabs are shown. Use Move Up and Move Down to change their order.")
        )
        for tab_id, label, visible in entries:
            item = QListWidgetItem(label)
            item.setData(self.TAB_ID_ROLE, tab_id)
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(
                Qt.CheckState.Checked
                if visible or tab_id == permanent_tab_id
                else Qt.CheckState.Unchecked
            )
            if tab_id == permanent_tab_id:
                item.setToolTip(self.tr("The Garden Plan tab is always shown."))
            self.tab_list.addItem(item)
        self.tab_list.currentRowChanged.connect(self._update_move_buttons)
        self.tab_list.itemChanged.connect(self._keep_permanent_tab_visible)
        layout.addWidget(self.tab_list)

        move_layout = QHBoxLayout()
        move_layout.addStretch(1)
        self.move_up_button = QPushButton(self.tr("Move Up"), self)
        self.move_up_button.setAccessibleName(self.tr("Move selected tab up"))
        self.move_up_button.clicked.connect(lambda: self._move_current(-1))
        move_layout.addWidget(self.move_up_button)
        self.move_down_button = QPushButton(self.tr("Move Down"), self)
        self.move_down_button.setAccessibleName(self.tr("Move selected tab down"))
        self.move_down_button.clicked.connect(lambda: self._move_current(1))
        move_layout.addWidget(self.move_down_button)
        layout.addLayout(move_layout)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)
        if self.tab_list.count():
            self.tab_list.setCurrentRow(0)
        else:
            self._update_move_buttons(-1)

    @property
    def tab_order(self) -> list[str]:
        return [
            str(self.tab_list.item(index).data(self.TAB_ID_ROLE))
            for index in range(self.tab_list.count())
        ]

    @property
    def visible_tab_ids(self) -> list[str]:
        return [
            str(self.tab_list.item(index).data(self.TAB_ID_ROLE))
            for index in range(self.tab_list.count())
            if self.tab_list.item(index).checkState() == Qt.CheckState.Checked
        ]

    def _keep_permanent_tab_visible(self, item: QListWidgetItem) -> None:
        if (
            item.data(self.TAB_ID_ROLE) == self._permanent_tab_id
            and item.checkState() != Qt.CheckState.Checked
        ):
            item.setCheckState(Qt.CheckState.Checked)

    def _move_current(self, offset: int) -> None:
        row = self.tab_list.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < self.tab_list.count():
            return
        item = self.tab_list.takeItem(row)
        self.tab_list.insertItem(target, item)
        self.tab_list.setCurrentRow(target)

    def _update_move_buttons(self, row: int) -> None:
        self.move_up_button.setEnabled(row > 0)
        self.move_down_button.setEnabled(0 <= row < self.tab_list.count() - 1)
