"""Native table and curve drawing for AYFX frames."""

from dataclasses import replace

from PySide6.QtCore import (
    QAbstractTableModel, QItemSelectionModel, QMimeData, QModelIndex,
    QRect, QRegularExpression, Qt, QTimer, Signal,
)
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHeaderView, QLineEdit,
    QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableView,
)

from ayfx import BankError, Frame, decode_effect, effect_from_frames

HEADERS = ("Pos", "T", "N", "Per", "Ns", "V", "Period (linear)", "Noise", "Volume")
PARAMETERS = ("tone", "noise", "volume")
LIMITS = (0xFFF, 0x1F, 0xF)
DIGITS = (3, 2, 1)
BAR_START = 6
FRAME_MIME = "application/x-ayfx-workbench-frames"
BLANK = Frame(False, False, 0, 0, 0)
Role = Qt.ItemDataRole
Flags = Qt.ItemFlag


class FrameModel(QAbstractTableModel):
    edited = Signal(object, str)

    def __init__(self, parent):
        super().__init__(parent)
        self.frames = []
        self.editable = False

    def show_frames(self, frames, editable, reset=False):
        frames = list(frames)
        if reset or self.frames != frames or editable != self.editable:
            self.beginResetModel()
            self.frames = frames
            self.editable = editable
            self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.frames)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section, orientation, role=Role.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Role.DisplayRole:
            return HEADERS[section]
        return None

    def data(self, index, role=Role.DisplayRole):
        if not index.isValid():
            return None
        column = index.column()
        frame = self.frames[index.row()]
        if role == Role.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignCenter
        if role == Role.CheckStateRole and column in (1, 2):
            enabled = frame.tone_on if column == 1 else frame.noise_on
            return Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked
        if role == Role.UserRole and column >= BAR_START:
            return getattr(frame, PARAMETERS[column - BAR_START])
        if role in (Role.DisplayRole, Role.EditRole):
            if column == 0:
                return f"{index.row():03d}"
            if 3 <= column <= 5:
                parameter = column - 3
                return f"{getattr(frame, PARAMETERS[parameter]):0{DIGITS[parameter]}X}"
        return None

    def flags(self, index):
        flags = super().flags(index)
        if self.editable and index.isValid():
            if index.column() in (1, 2):
                flags |= Flags.ItemIsUserCheckable
            if 3 <= index.column() <= 5:
                flags |= Flags.ItemIsEditable
        return flags

    def setData(self, index, value, role=Role.EditRole):
        if not self.editable or not index.isValid():
            return False
        row, column = index.row(), index.column()
        if role == Role.CheckStateRole and column in (1, 2):
            parameter = "tone_on" if column == 1 else "noise_on"
            value = Qt.CheckState(value) == Qt.CheckState.Checked
        elif role == Role.EditRole and 3 <= column <= 5:
            parameter = PARAMETERS[column - 3]
            try:
                value = int(value, 16)
            except (TypeError, ValueError):
                return False
            if not 0 <= value <= LIMITS[column - 3]:
                return False
        else:
            return False
        frame = replace(self.frames[row], **{parameter: value})
        if frame != self.frames[row]:
            self.frames[row] = frame
            self.dataChanged.emit(self.index(row, 1), self.index(row, 8))
            self.edited.emit(tuple(self.frames), "Edit frame")
        return True

    def draw_values(self, parameter, values):
        for row, value in values:
            self.frames[row] = replace(self.frames[row], **{PARAMETERS[parameter]: value})
        first, last = sorted((values[0][0], values[-1][0]))
        self.dataChanged.emit(self.index(first, parameter + 3), self.index(last, parameter + BAR_START))


class FrameDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        if 3 <= index.column() <= 5:
            editor = QLineEdit(parent)
            parameter = index.column() - 3
            patterns = ("[0-9A-Fa-f]{1,3}", "[01]?[0-9A-Fa-f]", "[0-9A-Fa-f]")
            editor.setValidator(QRegularExpressionValidator(QRegularExpression(patterns[parameter]), editor))
            editor.setMaxLength(DIGITS[parameter])
            return editor
        return super().createEditor(parent, option, index)

    def paint(self, painter, option, index):
        if index.column() < BAR_START:
            super().paint(painter, option, index)
            return
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, option.widget)
        bar = option.rect.adjusted(4, 3, -4, -3)
        value = index.data(Role.UserRole)
        palette = option.palette
        painter.save()
        painter.fillRect(bar, palette.base())
        fill = QRect(bar)
        fill.setWidth(round(bar.width() * value / LIMITS[index.column() - BAR_START]))
        painter.fillRect(fill, palette.highlight())
        painter.setPen(palette.mid().color())
        painter.drawRect(bar.adjusted(0, 0, -1, -1))
        painter.restore()


class FrameTable(QTableView):
    audition_requested = Signal()
    edited = Signal(object, str)
    message = Signal(str)

    def __init__(self):
        super().__init__()
        self.frame_model = FrameModel(self)
        self.frame_model.edited.connect(self.edited)
        self.setModel(self.frame_model)
        self.setItemDelegate(FrameDelegate(self))
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(24)
        header = self.horizontalHeader()
        header.setMinimumSectionSize(24)
        for column, width in enumerate((53, 36, 36, 55, 48, 38)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.setColumnWidth(column, width)
        for column in range(BAR_START, len(HEADERS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        self.stroke = None
        self.scroll_point = None
        self.stroke_scroll = QTimer(self)
        self.stroke_scroll.setInterval(70)
        self.stroke_scroll.timeout.connect(self.scroll_stroke)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        available = self.viewport().width() - sum(self.columnWidth(i) for i in range(BAR_START))
        side = max(70, available // 4)
        for column, width in enumerate((max(130, available - 2 * side), side, side), BAR_START):
            self.setColumnWidth(column, width)

    def show_frames(self, frames, editable, new_effect):
        if new_effect:
            self.stroke_scroll.stop()
            self.stroke = None
            self.clearSelection()
            self.scrollToTop()
        scroll = self.verticalScrollBar().value()
        row = self.currentIndex().row()
        column = self.currentIndex().column()
        self.frame_model.show_frames(frames, editable, new_effect)
        if not new_effect and 0 <= row < len(frames):
            self.setCurrentIndex(self.frame_model.index(row, column))
            self.verticalScrollBar().setValue(scroll)

    def mousePressEvent(self, event):
        if self.frame_model.editable and event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return
        point = event.position().toPoint()
        index = self.indexAt(point)
        if (event.button() == Qt.MouseButton.LeftButton and self.frame_model.editable
                and index.isValid() and index.column() >= BAR_START):
            self.setFocus()
            self.setCurrentIndex(index)
            self.stroke = (index.column(), None, tuple(self.frame_model.frames))
            self.draw_to(point)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.stroke is None:
            super().mouseMoveEvent(event)
        else:
            self.scroll_point = event.position().toPoint()
            self.draw_to(self.scroll_point)
            if self.scroll_direction():
                if not self.stroke_scroll.isActive():
                    self.stroke_scroll.start()
            else:
                self.stroke_scroll.stop()
            event.accept()

    def scroll_direction(self):
        y = self.scroll_point.y()
        if y < 10:
            return -1
        if y >= self.viewport().height() - 10:
            return 1
        return 0

    def scroll_stroke(self):
        scrollbar = self.verticalScrollBar()
        before = scrollbar.value()
        scrollbar.setValue(before + self.scroll_direction())
        if scrollbar.value() != before:
            self.draw_to(self.scroll_point)

    def mouseReleaseEvent(self, event):
        if self.frame_model.editable and event.button() == Qt.MouseButton.RightButton:
            self.audition_requested.emit()
            event.accept()
            return
        if self.stroke is None or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        self.stroke_scroll.stop()
        self.draw_to(event.position().toPoint())
        original = self.stroke[2]
        self.stroke = None
        frames = tuple(self.frame_model.frames)
        if frames != original:
            self.edited.emit(frames, "Draw frame curve")
        event.accept()

    def draw_to(self, point):
        column, previous, original = self.stroke
        y = max(0, min(point.y(), self.viewport().height() - 1))
        row = self.rowAt(y)
        if row < 0:
            row = self.frame_model.rowCount() - 1
        parameter = column - BAR_START
        rect = self.visualRect(self.frame_model.index(row, column)).adjusted(4, 0, -4, 0)
        fraction = (point.x() - rect.left()) / max(1, rect.width() - 1)
        value = round(LIMITS[parameter] * max(0, min(fraction, 1)))
        values = [(row, value)]
        if previous is not None and previous[0] != row:
            start, start_value = previous
            step = 1 if row > start else -1
            values = [
                (i, round(start_value + (value - start_value) * (i - start) / (row - start)))
                for i in range(start + step, row + step, step)
            ]
        self.frame_model.draw_values(parameter, values)
        self.stroke = (column, (row, value), original)

    def selected_rows(self):
        return sorted(index.row() for index in self.selectionModel().selectedRows())

    def copy_frames(self):
        rows = self.selected_rows()
        if rows:
            frames = tuple(self.frame_model.frames[row] for row in rows)
            mime = QMimeData()
            mime.setData(FRAME_MIME, effect_from_frames(frames).stream)
            QApplication.clipboard().setMimeData(mime)
            self.message.emit(f"Copied {len(frames)} frame(s).")

    def paste_frames(self):
        if not self.frame_model.editable:
            return
        mime = QApplication.clipboard().mimeData()
        if mime is None or not mime.hasFormat(FRAME_MIME):
            self.message.emit("The clipboard has no AYFX frames.")
            return
        try:
            frames = decode_effect(bytes(mime.data(FRAME_MIME))).frames
        except BankError as error:
            self.message.emit(str(error))
            return
        self.insert_frames(frames, "Paste frames")

    def insert_blank(self):
        self.insert_frames((BLANK,), "Insert frame")

    def append_blank(self):
        self.insert_frames((BLANK,), "Append frame", len(self.frame_model.frames))

    def insert_frames(self, frames, label, row=None):
        if not self.frame_model.editable or not frames:
            return
        current = self.frame_model.frames
        if row is None:
            row = self.currentIndex().row()
            if row < 0:
                row = len(current)
        self.edited.emit(tuple(current[:row]) + frames + tuple(current[row:]), label)
        self.selectionModel().setCurrentIndex(
            self.frame_model.index(row, 0),
            QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
        )
        self.scrollTo(self.currentIndex())

    def delete_frames(self):
        rows = set(self.selected_rows())
        if self.frame_model.editable and rows:
            self.edited.emit(
                tuple(frame for row, frame in enumerate(self.frame_model.frames) if row not in rows),
                "Delete frames",
            )
