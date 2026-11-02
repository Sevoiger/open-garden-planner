"""Lightweight catalogue cards with three compact monthly recommendation bands."""
from PyQt6.QtCore import QLocale, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPalette
from PyQt6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

CARD_ROLE = int(Qt.ItemDataRole.UserRole) + 1


class PlantCatalogCardDelegate(QStyledItemDelegate):
    thumbnail_needed = pyqtSignal(str)

    def sizeHint(self, _option, _index):
        return QSize(390, 180)

    def paint(self, painter, option, index):
        widget = option.widget
        # Keep the owning Python window reachable before allocating any paint
        # data. Cyclic collection must not delete the active child viewport.
        _owner = widget.window() if widget is not None else None
        data = index.data(CARD_ROLE)
        if not isinstance(data, dict):
            return super().paint(painter, option, index)
        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        icon = icon if isinstance(icon, QIcon) else QIcon()
        style_option.text = ""
        style_option.icon = type(icon)()
        style = widget.style() if widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, style_option, painter, widget)
        painter.save()
        rect = option.rect.adjusted(9, 7, -9, -7)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        color = option.palette.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text)
        painter.setPen(color)
        image_rect = QRect(rect.left(), rect.top(), 68, 64)
        if not icon.isNull():
            icon.paint(painter, image_rect, Qt.AlignmentFlag.AlignCenter)
        else:
            painter.fillRect(image_rect, QColor("#e5ede5"))
            painter.setPen(QColor("#347657"))
            painter.drawText(image_rect, Qt.AlignmentFlag.AlignCenter, self.tr("Photo"))
            painter.setPen(color)
            if data.get("thumbnail"):
                self.thumbnail_needed.emit(data["identity"])
        text_left = image_rect.right() + 10
        width = max(30, rect.right() - text_left)
        def line(text, top, *, bold=False, italic=False):
            font = QFont(option.font)
            font.setBold(bold)
            font.setItalic(italic)
            painter.setFont(font)
            painter.drawText(QRect(text_left, top, width, 20), Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, width))
        line(data["name"], rect.top(), bold=True)
        line(data["botanical"], rect.top() + 21, italic=True)
        line(data["badges"], rect.top() + 42)
        painter.setFont(option.font)
        painter.drawText(QRect(rect.left(), rect.top() + 70, rect.width(), 20), Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(data["conditions"], Qt.TextElideMode.ElideRight, rect.width()))
        label_width = min(74, rect.width() // 4)
        month_width = max(8, (rect.width() - label_width) // 12)
        month_left = rect.left() + label_width
        font = QFont(option.font)
        font.setPointSizeF(max(7, font.pointSizeF() - 1))
        painter.setFont(font)
        for month in range(1, 13):
            cell = QRect(month_left + (month - 1) * month_width, rect.top() + 93, month_width, 13)
            painter.drawText(cell, Qt.AlignmentFlag.AlignCenter, QLocale().monthName(month, QLocale.FormatType.ShortFormat)[:1])
        for row, (key, label) in enumerate((("planting", self.tr("Planting")),
                                            ("flowering", self.tr("Flowering")), ("pruning", self.tr("Pruning")))):
            top = rect.top() + 109 + row * 17
            painter.setPen(color)
            painter.drawText(QRect(rect.left(), top, label_width - 4, 14), Qt.AlignmentFlag.AlignVCenter, label)
            for month in range(1, 13):
                active = month in (data["calendar"].get(key, {}).get("months") or ())
                cell = QRect(month_left + (month - 1) * month_width + 1, top + 2, month_width - 2, 10)
                painter.fillRect(cell, QColor("#347657") if active else QColor("#dfe5de"))
        painter.setPen(QColor("#cad5c8"))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        painter.restore()
