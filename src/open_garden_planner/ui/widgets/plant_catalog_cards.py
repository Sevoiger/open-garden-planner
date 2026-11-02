"""Readable catalogue cards with compact flowering and pruning periods."""
from PyQt6.QtCore import QLocale, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPalette
from PyQt6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

CARD_ROLE = int(Qt.ItemDataRole.UserRole) + 1


def _card_metrics(font):
    line = max(22, QFontMetrics(font).height() + 6)
    small = QFont(font)
    if small.pointSizeF() > 0:
        small.setPointSizeF(max(9, small.pointSizeF() - 1))
    elif small.pixelSize() > 0:
        small.setPixelSize(max(11, small.pixelSize() - 2))
    month_line = max(17, QFontMetrics(small).height() + 4)
    header = max(76, line * 3)
    conditions = 12 + header + 10
    months = conditions + line + 10
    bands = months + month_line + 4
    height = bands + 2 * (month_line + 4) + 12
    return line, small, month_line, header, conditions, months, bands, height


class PlantCatalogCardDelegate(QStyledItemDelegate):
    thumbnail_needed = pyqtSignal(str)

    def sizeHint(self, option, _index):
        return QSize(410, _card_metrics(option.font)[-1])

    def paint(self, painter, option, index):
        widget = option.widget
        # Retain the Python owner before allocations can trigger collection.
        _owner = widget.window() if widget is not None else None
        data = index.data(CARD_ROLE)
        if not isinstance(data, dict):
            return super().paint(painter, option, index)
        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        icon = icon if isinstance(icon, QIcon) else QIcon()
        style_option.text = ""
        style_option.icon = QIcon()
        style = widget.style() if widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, style_option, painter, widget)
        painter.save()
        painter.setClipRect(option.rect)
        line_height, small_font, month_height, _header, conditions, months, bands, _height = _card_metrics(option.font)
        rect = option.rect.adjusted(12, 12, -12, -12)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        color = option.palette.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text)
        painter.setPen(color)
        image_rect = QRect(rect.left(), rect.top(), 76, 76)
        if not icon.isNull():
            icon.paint(painter, image_rect, Qt.AlignmentFlag.AlignCenter)
        else:
            painter.fillRect(image_rect, QColor("#e5ede5"))
            painter.setPen(QColor("#347657"))
            painter.drawText(image_rect, Qt.AlignmentFlag.AlignCenter, self.tr("Photo"))
            painter.setPen(color)
            if data.get("thumbnail"):
                self.thumbnail_needed.emit(data["identity"])
        text_left = image_rect.right() + 14
        width = max(20, rect.right() - text_left)
        def line(text, top, *, bold=False, italic=False):
            font = QFont(option.font)
            font.setBold(bold)
            font.setItalic(italic)
            painter.setFont(font)
            painter.drawText(QRect(text_left, top, width, line_height), Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, width))
        line(data["name"], rect.top(), bold=True)
        line(data["botanical"], rect.top() + line_height, italic=True)
        line(data["badges"], rect.top() + line_height * 2)
        painter.setFont(option.font)
        painter.drawText(QRect(rect.left(), option.rect.top() + conditions, rect.width(), line_height),
                         Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(data["conditions"], Qt.TextElideMode.ElideRight, rect.width()))
        painter.setFont(small_font)
        label_width = max(QFontMetrics(small_font).horizontalAdvance(label) for label in
                          (self.tr("Flowering"), self.tr("Pruning"))) + 10
        label_width = min(label_width, rect.width() // 3)
        month_width = max(6, (rect.width() - label_width) // 12)
        month_left = rect.left() + label_width
        for month in range(1, 13):
            cell = QRect(month_left + (month - 1) * month_width, option.rect.top() + months, month_width, month_height)
            painter.drawText(cell, Qt.AlignmentFlag.AlignCenter, QLocale().monthName(month, QLocale.FormatType.ShortFormat)[:1])
        for row, (key, label) in enumerate((("flowering", self.tr("Flowering")), ("pruning", self.tr("Pruning")))):
            top = option.rect.top() + bands + row * (month_height + 4)
            painter.setPen(color)
            painter.drawText(QRect(rect.left(), top, label_width - 4, month_height), Qt.AlignmentFlag.AlignVCenter, label)
            for month in range(1, 13):
                active = month in (data["calendar"].get(key, {}).get("months") or ())
                cell = QRect(month_left + (month - 1) * month_width + 1, top + 3, month_width - 2, month_height - 6)
                painter.fillRect(cell, QColor("#347657") if active else QColor("#dfe5de"))
        painter.setPen(QColor("#cad5c8"))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        painter.restore()
