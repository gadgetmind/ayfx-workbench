"""Native AYFX bank browser and workbench."""

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import (
    QBuffer, QByteArray, QIODevice, QMimeData, QObject, QRunnable, QSaveFile,
    QRect, QSize, Qt, QThreadPool, Signal, Slot,
)
from PySide6.QtGui import QAction, QDrag, QKeySequence, QPalette, QUndoCommand, QUndoStack
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QHBoxLayout,
    QInputDialog, QLabel, QListView, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPushButton, QRubberBand, QSplitter, QTabBar,
    QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTextBrowser, QVBoxLayout, QWidget,
)

from ayfx import BankError, MAX_EFFECTS, decode_bank, effect_from_frames, encode_bank
from frames import BLANK, FrameTable
from synth import FRAME_RATE, SAMPLE_RATE, render_pcm

ROOT = Path(__file__).resolve().parent
SAMPLE_LABELS = {
    "tests.afb": "Test Effects",
    "zxgames.afb": "ZX Games",
    "zxmsx.afb": "ZX MSX Demos",
    "streets.afb": "Streets of Rage 2",
}
MIME = "application/x-ayfx-workbench-effect"
BANK_FILTER = "AYFX banks (*.afb *.AFB);;All files (*)"


class WorkbenchArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_help(sys.stderr)
        self.exit(2, f"\n{self.prog}: error: {message}\n")


def discover_samples(directory):
    """Keep the bundled order, followed by additional banks sorted by path."""
    bundled_order = {name: index for index, name in enumerate(SAMPLE_LABELS)}
    entries = []
    for path in directory.rglob("*"):
        if path.suffix.lower() == ".afb" and path.is_file():
            relative = path.relative_to(directory)
            key = relative.as_posix()
            order = bundled_order.get(key.lower(), len(bundled_order))
            label = SAMPLE_LABELS.get(key.lower(), relative.with_suffix("").as_posix())
            entries.append((order, key.casefold(), key, label, path))
    return [(label, path) for _, _, _, label, path in sorted(entries)]


class RenderSignals(QObject):
    finished = Signal(int, bytes)


class RenderJob(QRunnable):
    def __init__(self, token, frames, signals):
        super().__init__()
        self.token = token
        self.frames = frames
        self.signals = signals

    def run(self):
        self.signals.finished.emit(self.token, render_pcm(self.frames))


class Player(QObject):
    failed = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.token = 0
        self.sink = self.buffer = None
        self.signals = RenderSignals(self)
        self.signals.finished.connect(self.start_audio)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)

    def play(self, effect):
        self.stop()
        if effect.frames:
            self.pool.start(RenderJob(self.token, effect.frames, self.signals))

    def stop(self):
        self.token += 1
        self.pool.clear()
        sink, buffer = self.sink, self.buffer
        self.sink = self.buffer = None
        if sink:
            sink.stop()
            sink.deleteLater()
            buffer.close()
            buffer.deleteLater()

    @Slot(int, bytes)
    def start_audio(self, token, pcm):
        if token != self.token:
            return
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            self.failed.emit("No audio output device is available.")
            return
        audio_format = QAudioFormat()
        audio_format.setSampleRate(SAMPLE_RATE)
        audio_format.setChannelCount(1)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(audio_format):
            self.failed.emit("The default audio output does not support 44.1 kHz mono PCM.")
            return
        self.buffer = QBuffer(self)
        self.buffer.setData(QByteArray(pcm))
        self.buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.sink = QAudioSink(device, audio_format, self)
        self.sink.setVolume(0.65)
        self.sink.stateChanged.connect(self.audio_state)
        self.sink.start(self.buffer)

    @Slot(QAudio.State)
    def audio_state(self, state):
        if self.sink and state == QAudio.State.StoppedState:
            if self.sink.error() != QAudio.Error.NoError:
                self.failed.emit("Qt could not play audio on the default output device.")


class BankList(QListWidget):
    dropped = Signal(object, int, int)

    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.marker = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)

    def startDrag(self, supported_actions):
        if self.currentRow() < 0:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(MIME, b"drag")
        drag.setMimeData(mime)
        # The drop handler updates both the bank model and its view.
        drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, Qt.DropAction.CopyAction)

    def dragEnterEvent(self, event):
        self.accept_bank_drag(event)

    def dragMoveEvent(self, event):
        self.accept_bank_drag(event)
        if event.isAccepted():
            row = self.drop_row(event.position().toPoint())
            item = self.item(row) or self.item(row - 1)
            bounds = self.visualItemRect(item) if item else QRect()
            y = bounds.top() if row < self.count() else bounds.bottom() + 1
            self.marker.setGeometry(2, max(0, y - 1), self.viewport().width() - 4, 3)
            self.marker.show()
        else:
            self.marker.hide()

    def dragLeaveEvent(self, event):
        self.marker.hide()
        super().dragLeaveEvent(event)

    def drop_row(self, point):
        item = self.itemAt(point)
        if item is None:
            return self.count()
        return self.row(item) + (point.y() > self.visualItemRect(item).center().y())

    def accept_bank_drag(self, event):
        source = event.source()
        if isinstance(source, BankList) and event.mimeData().hasFormat(MIME) and source.currentRow() >= 0:
            action = Qt.DropAction.MoveAction if source is self else Qt.DropAction.CopyAction
            event.setDropAction(action)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        self.marker.hide()
        self.accept_bank_drag(event)
        if event.isAccepted():
            source = event.source()
            self.dropped.emit(source, source.currentRow(), self.drop_row(event.position().toPoint()))


class BankRowDelegate(QStyledItemDelegate):
    def __init__(self, parent):
        super().__init__(parent)
        self.byte_width = 0

    def paint(self, painter, option, index):
        position, size, name = index.data(Qt.ItemDataRole.UserRole)
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        style = styled.widget.style()
        text = styled.rect.adjusted(4, 0, -4, 0)
        styled.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, styled.widget)

        metrics = styled.fontMetrics
        digit_width = max(metrics.horizontalAdvance(digit) for digit in "0123456789")
        gap = max(8, metrics.horizontalAdvance("  "))
        number_width = 3 * digit_width + 4
        size_left = text.left() + number_width + gap
        name_left = size_left + self.byte_width + 2 * gap
        name_width = max(0, text.right() - name_left + 1)
        display_name = metrics.elidedText(name, Qt.TextElideMode.ElideRight, name_width)
        fields = (
            (QRect(text.left(), text.top(), number_width, text.height()), position, Qt.AlignmentFlag.AlignLeft),
            (QRect(size_left, text.top(), self.byte_width, text.height()), size, Qt.AlignmentFlag.AlignRight),
            (QRect(name_left, text.top(), name_width, text.height()), display_name, Qt.AlignmentFlag.AlignLeft),
        )
        selected = bool(styled.state & QStyle.StateFlag.State_Selected)
        enabled = bool(styled.state & QStyle.StateFlag.State_Enabled)
        role = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        painter.save()
        painter.setClipRect(text)
        painter.setFont(styled.font)
        for rect, value, alignment in fields:
            style.drawItemText(painter, rect, alignment | Qt.AlignmentFlag.AlignVCenter,
                               styled.palette, enabled, value, role)
        painter.restore()


class BankChange(QUndoCommand):
    def __init__(self, window, label, effects, row):
        super().__init__(label)
        self.window = window
        self.before = (window.effects, window.bank.currentRow())
        self.after = (effects, row)

    def redo(self):
        self.window.show_bank(*self.after)

    def undo(self):
        self.window.show_bank(*self.before)


class FrameChange(QUndoCommand):
    def __init__(self, window, row, after, label):
        super().__init__(label)
        self.window = window
        self.row = row
        self.before = window.effects[row]
        self.after = after

    def redo(self):
        self.window.apply_frames(self.row, self.after)

    def undo(self):
        self.window.apply_frames(self.row, self.before)


class Workbench(QMainWindow):
    def __init__(self, samples_dir=None):
        super().__init__()
        self.path = None
        self.effects = []
        self.sources = []
        self.preview_key = None
        self.help_dialog = None
        self.player = Player(self)
        self.player.failed.connect(self.statusBar().showMessage)
        self.history = QUndoStack(self)
        self.history.cleanChanged.connect(self.update_title)

        self.bank = BankList()
        self.bank_delegate = BankRowDelegate(self.bank)
        self.bank.setItemDelegate(self.bank_delegate)
        self.bank_summary = QLabel()
        self.bank_summary.setToolTip("Saved AFB size, including the effect count and offset table.")
        self.update_bank_summary()
        self.bank.setAlternatingRowColors(True)
        self.bank.currentRowChanged.connect(self.bank_selection)
        self.bank.itemClicked.connect(self.audition_bank)
        self.bank.dropped.connect(self.drop_effect)

        self.source = BankList()
        self.source.setViewMode(QListView.ViewMode.IconMode)
        self.source.setMovement(QListView.Movement.Static)
        self.source.setResizeMode(QListView.ResizeMode.Adjust)
        self.source.setGridSize(QSize(170, 37))
        self.source.setUniformItemSizes(True)
        self.source.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.source.setDragEnabled(True)
        self.source.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.source.setAcceptDrops(False)
        self.source.currentRowChanged.connect(self.source_selection)
        self.source.itemClicked.connect(lambda _: self.play_source())

        self.collection = QComboBox()
        if samples_dir is None:
            samples_dir = ROOT / "samples"
        for label, path in discover_samples(samples_dir):
            self.collection.addItem(label, path)
        self.collection.currentIndexChanged.connect(self.load_collection)

        self.preview_title = QLabel("Select an effect to see its frames")
        self.preview_title.setTextFormat(Qt.TextFormat.PlainText)
        self.preview_tabs = QTabBar()
        self.preview_tabs.setExpanding(False)
        self.preview_tabs.addTab("My Bank · Edit")
        self.preview_tabs.addTab("Source · Read only")
        self.preview_tabs.currentChanged.connect(self.refresh_preview)
        self.frames = FrameTable()
        self.frames.audition_requested.connect(self.play_bank)
        self.frames.edited.connect(self.commit_frames)
        self.frames.message.connect(lambda message: self.statusBar().showMessage(message, 4000))
        self.frame_buttons = self.buttons(
            ("Insert frame", self.frames.insert_blank), ("Append frame", self.frames.append_blank),
            ("Delete frames", self.frames.delete_frames),
        )
        self.frame_buttons.layout().addWidget(QLabel("Drag bars to draw · Double-click hex values to type"))

        bank_buttons = self.buttons(
            ("Play working", self.play_bank), ("Rename…", self.rename), ("Move up", lambda: self.move_selected(-1)),
            ("Move down", lambda: self.move_selected(1)), ("Delete", self.delete),
        )
        source_buttons = self.buttons(
            ("Open source…", self.open_source), ("Play source", self.play_source),
            ("Add to My Bank", self.add_source),
        )
        clipboard_buttons = self.buttons(
            ("New effect", self.new_effect), ("Copy", self.copy),
            ("Paste", self.paste), ("Duplicate", self.duplicate),
        )
        bank_panel = self.panel("My Bank", clipboard_buttons, bank_buttons, self.bank_summary, self.bank)
        source_panel = self.panel("Source Banks", self.collection, source_buttons, self.source)
        preview_panel = self.panel(
            "Frames", self.preview_tabs, self.preview_title, self.frame_buttons, self.frames,
        )
        upper = QSplitter(Qt.Orientation.Horizontal)
        upper.addWidget(bank_panel)
        upper.addWidget(source_panel)
        upper.setSizes([465, 565])
        outer = QSplitter(Qt.Orientation.Vertical)
        outer.addWidget(upper)
        outer.addWidget(preview_panel)
        outer.setSizes([365, 335])
        self.setCentralWidget(outer)

        self.build_menus()
        self.resize(1050, 740)
        self.update_title()
        self.load_collection(self.collection.currentIndex())
        self.refresh_preview()
        self.statusBar().showMessage("Click an effect to audition it. Space plays My Bank.")

    @staticmethod
    def buttons(*actions):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        for title, callback in actions:
            button = QPushButton(title)
            button.clicked.connect(callback)
            row.addWidget(button)
        row.addStretch()
        return widget

    @staticmethod
    def panel(title, *widgets):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        label = QLabel(title)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        layout.addWidget(label)
        for widget in widgets:
            layout.addWidget(widget)
        return panel

    def action(self, menu, title, handler, shortcut=None, scope=None):
        action = QAction(title, self)
        action.triggered.connect(handler)
        if shortcut is not None:
            action.setShortcut(shortcut)
        if scope is not None:
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            scope.addAction(action)
        menu.addAction(action)
        return action

    def build_menus(self):
        file = self.menuBar().addMenu("&File")
        self.action(file, "&New bank", self.new_bank, QKeySequence.StandardKey.New)
        self.action(file, "&Open working bank…", self.open_bank, QKeySequence.StandardKey.Open)
        self.action(file, "&Save bank", self.save, QKeySequence.StandardKey.Save)
        self.action(file, "Save bank &as…", self.save_as, QKeySequence.StandardKey.SaveAs)
        file.addSeparator()
        self.action(file, "Open s&ource bank…", self.open_source)
        self.action(file, "&Quit", self.close, QKeySequence.StandardKey.Quit)

        edit = self.menuBar().addMenu("&Bank")
        undo = self.history.createUndoAction(self, "&Undo")
        undo.setShortcuts(QKeySequence.StandardKey.Undo)
        edit.addAction(undo)
        redo = self.history.createRedoAction(self, "&Redo")
        redo.setShortcuts(QKeySequence.StandardKey.Redo)
        edit.addAction(redo)
        edit.addSeparator()
        self.action(edit, "&New effect", self.new_effect)
        self.action(edit, "&Copy effect", self.copy, QKeySequence.StandardKey.Copy, self.bank)
        self.action(edit, "&Paste effect", self.paste, QKeySequence.StandardKey.Paste, self.bank)
        self.action(edit, "D&uplicate effect", self.duplicate, QKeySequence("Ctrl+D"), self.bank)
        self.action(edit, "&Rename effect…", self.rename, QKeySequence("F2"), self.bank)
        self.action(edit, "&Delete effect", self.delete, QKeySequence("Delete"), self.bank)

        frames = self.menuBar().addMenu("F&rames")
        self.action(frames, "&Copy frames", self.frames.copy_frames, QKeySequence.StandardKey.Copy, self.frames)
        self.action(frames, "&Paste frames", self.frames.paste_frames, QKeySequence.StandardKey.Paste, self.frames)
        self.action(frames, "&Insert frame", self.frames.insert_blank, QKeySequence("Insert"), self.frames)
        self.action(frames, "&Append frame", self.frames.append_blank)
        self.action(frames, "&Delete frames", self.frames.delete_frames, QKeySequence("Delete"), self.frames)

        play = self.menuBar().addMenu("&Playback")
        self.action(play, "Play &working effect", self.play_bank, QKeySequence("Space"))
        self.action(play, "Play &source effect", self.play_source)
        self.action(play, "&Stop", self.player.stop, QKeySequence("Escape"))

        help_menu = self.menuBar().addMenu("&Help")
        self.action(help_menu, "&Using AYFX Workbench", self.show_help, QKeySequence("F1"))

    def show_help(self):
        if self.help_dialog is None:
            try:
                guide = (ROOT / "docs" / "USAGE.md").read_text(encoding="utf-8")
            except OSError as error:
                self.show_error("Could not open Help", error)
                return
            dialog = QDialog(self)
            dialog.setWindowTitle("Using AYFX Workbench")
            dialog.resize(660, 570)
            browser = QTextBrowser(dialog)
            browser.setMarkdown(guide)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
            buttons.rejected.connect(dialog.close)
            layout = QVBoxLayout(dialog)
            layout.addWidget(browser)
            layout.addWidget(buttons)
            self.help_dialog = dialog
        self.help_dialog.show()
        self.help_dialog.raise_()
        self.help_dialog.activateWindow()

    @staticmethod
    def effect_label(row, effect):
        return f"{row:03d}  {effect.title or '(unnamed)'}"

    @staticmethod
    def set_bank_item(item, row, effect):
        position, size, name = f"{row:03d}", f"{effect.bank_bytes:,} B", effect.title or "(unnamed)"
        item.setText(f"{position}  ·  {size}  ·  {name}")
        item.setData(Qt.ItemDataRole.UserRole, (position, size, name))
        item.setToolTip(
            f"{len(effect.stream):,} bytes packed frames and end marker"
            f"\n{effect.name_bytes:,} bytes for name"
            "\n2 bytes for offset"
        )

    def update_bank_summary(self):
        count = len(self.effects)
        size = 1 + sum(effect.bank_bytes for effect in self.effects) if count else 0
        label = "effect" if count == 1 else "effects"
        self.bank_summary.setText(f"{count} {label}  ·  {size:,} bytes")
        metrics = self.bank.fontMetrics()
        self.bank_delegate.byte_width = max(
            (metrics.horizontalAdvance(f"{effect.bank_bytes:,} B") + 4 for effect in self.effects),
            default=0,
        )
        self.bank.viewport().update()

    def show_bank(self, effects, row):
        self.effects = effects
        self.bank.blockSignals(True)
        self.bank.clear()
        for index, effect in enumerate(effects):
            item = QListWidgetItem()
            self.set_bank_item(item, index, effect)
            self.bank.addItem(item)
        self.update_bank_summary()
        self.bank.setCurrentRow(min(row, len(effects) - 1))
        self.bank.blockSignals(False)
        self.bank_selection(self.bank.currentRow())
        self.update_title()

    def update_title(self, *_):
        title = self.path.name if self.path else "Untitled"
        modified = " *" if not self.history.isClean() else ""
        self.setWindowTitle(f"{title}{modified} — AYFX Workbench")

    def load_collection(self, index):
        if index < 0:
            return
        path = self.collection.itemData(index)
        self.show_source(self.read_bank(path))

    def show_source(self, effects):
        if effects is None:
            return
        self.sources = effects
        self.source.clear()
        for row, effect in enumerate(effects):
            item = QListWidgetItem(self.effect_label(row, effect))
            item.setToolTip(item.text())
            item.setSizeHint(QSize(165, 32))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.source.addItem(item)
        if self.source.count():
            self.source.setCurrentRow(0)

    def bank_selection(self, row):
        self.select_preview(0)

    def source_selection(self, row):
        self.select_preview(1)

    def select_preview(self, tab):
        if self.preview_tabs.currentIndex() == tab:
            self.refresh_preview()
        else:
            self.preview_tabs.setCurrentIndex(tab)

    def refresh_preview(self):
        working = self.preview_tabs.currentIndex() == 0
        effects, listing = (self.effects, self.bank) if working else (self.sources, self.source)
        row = listing.currentRow()
        effect = effects[row] if 0 <= row < len(effects) else None
        key = (working, row)
        new_effect = key != self.preview_key
        self.preview_key = key
        editable = working and effect is not None
        self.frame_buttons.setVisible(editable)
        self.frames.show_frames(effect.frames if effect else (), editable, new_effect)
        if effect is None:
            self.preview_title.setText("Select an effect in My Bank" if working else "Select a source effect")
            return
        count = len(effect.frames)
        self.preview_title.setText(
            f"{self.effect_label(row, effect)}    ·    {count} frames    ·    {count / FRAME_RATE:.2f} s"
        )

    def commit_frames(self, frames, label):
        row = self.bank.currentRow()
        if self.preview_tabs.currentIndex() == 0 and 0 <= row < len(self.effects):
            before = self.effects[row]
            if frames != before.frames:
                after = effect_from_frames(frames, before.name)
                self.history.push(FrameChange(self, row, after, label))

    def apply_frames(self, row, effect):
        # BankChange retains earlier lists for undo, so replace the list as well.
        self.effects = self.effects.copy()
        self.effects[row] = effect
        self.set_bank_item(self.bank.item(row), row, effect)
        self.update_bank_summary()
        if self.preview_tabs.currentIndex() == 0:
            self.bank.setCurrentRow(row)
            self.refresh_preview()

    def audition_bank(self, _):
        self.select_preview(0)
        self.play_bank()

    def play_bank(self):
        row = self.bank.currentRow()
        if row >= 0:
            self.player.play(self.effects[row])

    def play_source(self):
        row = self.source.currentRow()
        if row >= 0:
            self.select_preview(1)
            self.player.play(self.sources[row])

    def new_effect(self):
        title, accepted = QInputDialog.getText(self, "New effect", "Effect name", text="New effect")
        if accepted:
            name = title.replace("\0", "").encode("utf-8")
            self.insert(effect_from_frames((BLANK,), name), len(self.effects), "New effect")

    def add_source(self):
        row = self.source.currentRow()
        if row >= 0:
            self.insert(self.sources[row], len(self.effects), "Add source effect")

    def insert(self, effect, target, label):
        if len(self.effects) >= MAX_EFFECTS:
            self.statusBar().showMessage("The bank already contains 256 effects.", 5000)
            return
        effects = self.effects.copy()
        effects.insert(target, effect)
        self.history.push(BankChange(self, label, effects, target))
        self.bank.setFocus()

    def drop_effect(self, source, row, target):
        if source is self.source:
            self.insert(self.sources[row], target, "Add source effect")
        elif source is self.bank:
            self.move(row, target - (target > row))

    def move(self, row, target):
        if 0 <= row < len(self.effects) and 0 <= target < len(self.effects) and row != target:
            effects = self.effects.copy()
            effects.insert(target, effects.pop(row))
            self.history.push(BankChange(self, "Move effect", effects, target))

    def move_selected(self, direction):
        row = self.bank.currentRow()
        self.move(row, row + direction)

    def rename(self):
        row = self.bank.currentRow()
        if row < 0:
            return
        effect = self.effects[row]
        title, accepted = QInputDialog.getText(self, "Rename effect", "Effect name", text=effect.title)
        if accepted and title != effect.title:
            effects = self.effects.copy()
            effects[row] = effect.renamed(title.replace("\0", ""))
            self.history.push(BankChange(self, "Rename effect", effects, row))

    def delete(self):
        row = self.bank.currentRow()
        if row >= 0:
            effects = self.effects.copy()
            effects.pop(row)
            self.history.push(BankChange(self, "Delete effect", effects, row))

    def duplicate(self):
        row = self.bank.currentRow()
        if row >= 0:
            self.insert(self.effects[row], row + 1, "Duplicate effect")

    def copy(self):
        row = self.bank.currentRow()
        if row >= 0:
            data = QMimeData()
            data.setData(MIME, encode_bank([self.effects[row]]))
            QApplication.clipboard().setMimeData(data)
            self.statusBar().showMessage("Effect copied.", 2500)

    def paste(self):
        data = QApplication.clipboard().mimeData()
        if data is None or not data.hasFormat(MIME):
            self.statusBar().showMessage("The clipboard has no AYFX effect.", 3000)
            return
        try:
            effects = decode_bank(bytes(data.data(MIME)))
            if len(effects) != 1:
                raise BankError("The clipboard must contain one AYFX effect.")
        except BankError as error:
            self.show_error("Paste effect", error)
            return
        self.insert(effects[0], self.bank.currentRow() + 1, "Paste effect")

    def show_error(self, title, error):
        QMessageBox.warning(self, title, str(error))

    def read_bank(self, path):
        try:
            return decode_bank(path.read_bytes())
        except (OSError, BankError) as error:
            self.show_error("Could not open bank", f"{path}\n\n{error}")
            return None

    def choose_bank(self, title):
        filename, _ = QFileDialog.getOpenFileName(self, title, "", BANK_FILTER)
        return Path(filename) if filename else None

    def open_source(self):
        path = self.choose_bank("Open source bank")
        if path is None:
            return
        effects = self.read_bank(path)
        if effects is None:
            return
        index = self.collection.findData(path)
        self.collection.blockSignals(True)
        if index < 0:
            self.collection.addItem(path.name, path)
            index = self.collection.count() - 1
            self.collection.setItemData(index, str(path), Qt.ItemDataRole.ToolTipRole)
        self.collection.setCurrentIndex(index)
        self.collection.blockSignals(False)
        self.show_source(effects)

    def replace_bank(self, path, effects):
        self.player.stop()
        self.path = path
        self.history.clear()
        self.show_bank(effects, 0)

    def new_bank(self):
        if self.may_discard():
            self.replace_bank(None, [])

    def open_bank(self):
        path = self.choose_bank("Open working bank")
        if path is None:
            return
        effects = self.read_bank(path)
        if effects is None or not self.may_discard():
            return
        # The prompt can save the current bank to the path being opened.
        if self.path == path:
            effects = self.read_bank(path)
        if effects is not None:
            self.replace_bank(path, effects)

    def save(self):
        return self.save_as() if self.path is None else self.write_bank(self.path)

    def save_as(self):
        initial = str(self.path) if self.path else "My Effects.afb"
        filename, _ = QFileDialog.getSaveFileName(self, "Save working bank", initial, BANK_FILTER)
        if not filename:
            return False
        path = Path(filename)
        if path.suffix.lower() != ".afb":
            path = path.with_name(path.name + ".afb")
            if path.exists() and QMessageBox.question(
                self, "Replace bank?", f"Replace {path}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return False
        return self.write_bank(path)

    def write_bank(self, path):
        try:
            data = encode_bank(self.effects)
            file = QSaveFile(str(path))
            if not file.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(file.errorString())
            if file.write(data) != len(data) or not file.commit():
                raise OSError(file.errorString())
        except (OSError, BankError) as error:
            self.show_error("Could not save bank", error)
            return False
        self.path = path
        self.history.setClean()
        self.update_title()
        self.statusBar().showMessage(f"Saved {path.name}", 4000)
        return True

    def may_discard(self):
        if self.history.isClean():
            return True
        save = QMessageBox.StandardButton.Save
        discard = QMessageBox.StandardButton.Discard
        cancel = QMessageBox.StandardButton.Cancel
        choice = QMessageBox.question(
            self, "Unsaved changes", "Save changes to My Bank?",
            save | discard | cancel, save,
        )
        return choice == discard or (choice == save and self.save())

    def closeEvent(self, event):
        if self.may_discard():
            self.player.stop()
            event.accept()
        else:
            event.ignore()


def main(argv=None):
    parser = WorkbenchArgumentParser(
        description="Audition and edit AYFX sound banks.",
        add_help=False,
        allow_abbrev=False,
    )
    parser.add_argument("-h", "-?", "--help", action="help", help="show this help and exit")
    parser.add_argument("--bank", type=Path, metavar="FILE", help="open FILE in My Bank at startup")
    parser.add_argument("--samples", type=Path, metavar="DIR",
                        help="scan DIR and its subfolders for source banks")
    parser.add_argument("--maximised", "--maximized", action="store_true",
                        help="start with a maximised window")
    args = parser.parse_args(argv)
    if args.samples is not None:
        args.samples = args.samples.absolute()
        if not args.samples.is_dir():
            parser.error(f"sample folder is not a directory: {args.samples}")
    if args.bank is not None:
        args.bank = args.bank.absolute()
        try:
            effects = decode_bank(args.bank.read_bytes())
        except (OSError, BankError) as error:
            parser.error(f"cannot open {args.bank}: {error}")

    app = QApplication([sys.argv[0]])
    app.setApplicationName("AYFX Workbench")
    window = Workbench(samples_dir=args.samples)
    if args.bank is not None:
        window.replace_bank(args.bank, effects)
    if args.maximised:
        window.showMaximized()
    else:
        window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
