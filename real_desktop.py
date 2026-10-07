"""Play attached to real Windows controls; explicit selection editing stays local."""
from __future__ import annotations
import ctypes
import random
import sys
import time
import unicodedata
from PySide6.QtCore import Qt, QTimer, QObject, Signal, QAbstractNativeEventFilter, QSize, QRectF
from PySide6.QtGui import QPainter, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QWidget, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem, QAbstractItemView)
from characters import draw_character
from desktop_bridge import DesktopBridge, checked_rect
from config import clamp_position
from notepad_window import notes_style


def graphemes(text):
    result = []
    for char in text:
        if result and (unicodedata.combining(char) or char in ('\ufe0f', '\u200d') or result[-1].endswith('\u200d') or (char == '\n' and result[-1] == '\r')):
            result[-1] += char
        else:
            result.append(char)
    return result


class LetterStrip(QListWidget):
    reordered = Signal()

    def __init__(self):
        super().__init__()
        self.setFlow(QListWidget.LeftToRight)
        self.setWrapping(True)
        self.setSpacing(4)
        self.setGridSize(QSize(40, 48))
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setMinimumHeight(170)

    def load_text(self, text):
        self.clear()
        for letter in graphemes(text):
            item = QListWidgetItem({' ': '␣', '\n': '↵', '\r': '↵', '\r\n': '↵', '\t': '⇥'}.get(letter, letter))
            item.setData(Qt.UserRole, letter)
            item.setTextAlignment(Qt.AlignCenter)
            item.setSizeHint(QSize(36, 44))
            self.addItem(item)

    def text(self):
        return ''.join(self.item(i).data(Qt.UserRole) for i in range(self.count()))

    def move_letter(self, source, target):
        if 0 <= source < self.count() and 0 <= target < self.count() and source != target:
            item = self.takeItem(source)
            self.insertItem(target, item)
            self.setCurrentRow(target)
            self.reordered.emit()

    def dropEvent(self, event):
        if event.source() is not self:
            event.ignore()
            return
        target = self.indexAt(event.position().toPoint()).row()
        self.move_letter(self.currentRow(), target if target >= 0 else self.count() - 1)
        event.setDropAction(Qt.MoveAction)
        event.accept()


class TextPlayWindow(QDialog):
    def __init__(self, desktop):
        super().__init__()
        self.desktop = desktop
        self.selection = None
        self.setWindowTitle('Jeffery · Selected letters')
        self.resize(620, 460)
        self.setMinimumSize(460, 360)
        layout = QVBoxLayout(self)
        title = QLabel('A little shuffle for your letters')
        title.setStyleSheet('font-size: 20px; font-weight: 600;')
        layout.addWidget(title)
        self.source = QLabel('Select text in your editor, then press Ctrl+Alt+J.')
        self.source.setWordWrap(True)
        self.source.setTextFormat(Qt.PlainText)
        layout.addWidget(self.source)
        self.letters = LetterStrip()
        self.letters.reordered.connect(self.refresh_preview)
        layout.addWidget(self.letters, 1)
        self.preview = QLabel()
        self.preview.setTextFormat(Qt.PlainText)
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        row = QHBoxLayout()
        for label, action in (('Reset', self.reset), ('Jeffery pushes', self.push), ('Walk on selection', self.walk)):
            button = QPushButton(label)
            button.clicked.connect(action)
            row.addWidget(button)
        self.apply_button = QPushButton('Apply to editor')
        self.apply_button.setObjectName('primary')
        self.apply_button.clicked.connect(self.apply)
        self.apply_button.setEnabled(False)
        row.addWidget(self.apply_button)
        layout.addLayout(row)
        self.status = QLabel('Drag a letter to move it. Apply updates the original editor selection.')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        self.configure()

    def configure(self):
        self.setStyleSheet(notes_style(self.desktop.settings) + '\nQListWidget::item { background: #26394c; border: 1px solid #476881; border-radius: 5px; font-size: 18px; }')

    def present(self, selection):
        if not isinstance(selection, dict) or not isinstance(selection.get('text'), str) or not 0 < len(selection['text']) <= 120:
            raise ValueError('Windows returned an incomplete text selection.')
        if type(selection.get('hwnd')) is not int or type(selection.get('pid')) is not int or not isinstance(selection.get('runtime'), str):
            raise ValueError('The original editor could not be identified.')
        if not isinstance(selection.get('rects'), list) or not selection['rects']:
            raise ValueError('The selection is not visible.')
        for rect in selection['rects']:
            checked_rect(rect)
        self.selection = dict(selection)
        self.letters.load_text(selection['text'])
        self.source.setText('Selected in ' + str(selection.get('app', 'your app')) + (' · editable' if selection.get('editable') is True else ' · read-only'))
        self.status.setText('Drag letters, then Apply to editor. Ctrl+Z in the editor undoes the change.' if selection.get('editable') is True else 'This app exposes read-only text. Jeffery can walk on it; Apply is unavailable.')
        self.refresh_preview()
        self.show()
        self.raise_()

    def refresh_preview(self):
        self.preview.setText(self.letters.text())
        self.apply_button.setEnabled(bool(self.selection and self.selection.get('editable') is True and self.letters.text() != self.selection['text']))

    def reset(self):
        if self.selection:
            self.letters.load_text(self.selection['text'])
            self.refresh_preview()

    def push(self):
        self.desktop.pet.perform('PUSH', 3)
        if self.letters.count() > 1:
            self.letters.move_letter(0, 1)

    def walk(self):
        if self.selection:
            self.desktop.walk_selection(self.selection)

    def apply(self):
        if not self.selection or not self.apply_button.isEnabled():
            return
        self.apply_button.setEnabled(False)
        self.status.setText('Checking the original selection…')
        data = {key: self.selection[key] for key in ('hwnd', 'pid', 'runtime')}
        data['capture_id'] = self.selection.get('capture_id', '')
        data.update(original=self.selection['text'], text=self.letters.text())
        self.desktop.bridge.request('replace_text', data, self.applied)

    def applied(self, result, error):
        if error or not isinstance(result, dict) or result.get('applied') is not True:
            self.status.setText(error or 'Windows did not confirm the edit. Capture the selection again.')
            return
        self.selection['text'] = self.letters.text()
        self.status.setText('Applied in your editor. Ctrl+Z there undoes this edit.')
        self.desktop.pet.perform('CELEBRATE', 3)


class PeekSprite(QWidget):
    def __init__(self, settings):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput)
        self.settings, self.frame = settings, 0
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle('Jeffery · Peek')
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.animate)

    def animate(self):
        self.frame += 1
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setClipRect(QRectF(0, 0, self.width(), self.height() * 0.66))
        draw_character(p, self.width(), frame=self.frame, state='PEEK', skin=self.settings['pet_palette'], character=self.settings['character'])

    def showEvent(self, event):
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)


class SelectionHotkey(QAbstractNativeEventFilter):
    identifier = 0x4A46

    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.registered = False
        if sys.platform == 'win32':
            # Ctrl+Alt+J. MOD_NOREPEAT avoids a capture queue while held.
            self.registered = bool(ctypes.windll.user32.RegisterHotKey(None, self.identifier, 0x4003, ord('J')))
            if self.registered:
                QApplication.instance().installNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):
        if sys.platform == 'win32':
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam == self.identifier:
                QTimer.singleShot(0, self.callback)
                return True, 0
        return False, 0

    def stop(self):
        if self.registered:
            ctypes.windll.user32.UnregisterHotKey(None, self.identifier)
            QApplication.instance().removeNativeEventFilter(self)
            self.registered = False


class DesktopPanel(QDialog):
    def __init__(self, desktop):
        super().__init__()
        self.desktop = desktop
        self.setWindowTitle('Jeffery · Real desktop')
        self.resize(610, 460)
        layout = QVBoxLayout(self)
        heading = QLabel('Places Jeffery can reach')
        heading.setStyleSheet('font-size: 20px; font-weight: 600;')
        layout.addWidget(heading)
        hint = QLabel('Keep a folder icon or tab visible. Jeffery follows its position when the window moves.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.targets = QListWidget()
        layout.addWidget(self.targets, 1)
        row = QHBoxLayout()
        for label, action in (('Refresh', desktop.bridge.scan), ('Jump / ride', self.visit), ('Select tab', self.select_tab), ('Come out', desktop.emerge)):
            button = QPushButton(label)
            button.clicked.connect(action)
            row.addWidget(button)
        layout.addLayout(row)
        text = QPushButton('Capture selected text in 3 seconds · Ctrl+Alt+J')
        text.clicked.connect(desktop.capture_countdown)
        layout.addWidget(text)
        self.status = QLabel(desktop.bridge.status)
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        desktop.bridge.changed.connect(self.refresh)
        desktop.bridge.status_changed.connect(self.status.setText)
        self.configure()

    def configure(self):
        self.setStyleSheet(notes_style(self.desktop.settings))

    def refresh(self):
        selected = self.targets.currentItem()
        identifier = selected.data(Qt.UserRole) if selected else ''
        self.targets.clear()
        for target in self.desktop.bridge.targets:
            item = QListWidgetItem(f"{target['kind'].capitalize()} · {target['name']}")
            item.setData(Qt.UserRole, target['id'])
            self.targets.addItem(item)
            if target['id'] == identifier:
                self.targets.setCurrentItem(item)
        if self.targets.currentRow() < 0 and self.targets.count():
            self.targets.setCurrentRow(0)

    def visit(self):
        item = self.targets.currentItem()
        if item:
            self.status.setText(self.desktop.visit(item.data(Qt.UserRole)))

    def select_tab(self):
        item = self.targets.currentItem()
        if item:
            self.status.setText(self.desktop.select_tab(item.data(Qt.UserRole)))


class RealDesktopPlay(QObject):
    def __init__(self, pet, settings, parent=None, bridge=None):
        super().__init__(parent)
        self.pet, self.settings = pet, settings
        self.bridge = bridge or DesktopBridge(settings, self)
        self.active_id, self.pending_id = '', ''
        self.activity_blocked = lambda: False
        self.leave_other = lambda: None
        self.anchor, self.origin = None, None
        self.peek_sprite = PeekSprite(settings)
        self.text_window = TextPlayWindow(self)
        self.panel = DesktopPanel(self)
        self.return_timer = QTimer(self)
        self.return_timer.setSingleShot(True)
        self.return_timer.timeout.connect(self.emerge)
        self.peek_timer = QTimer(self)
        self.peek_timer.setInterval(2600)
        self.peek_timer.timeout.connect(self.toggle_peek)
        self.follow_timer = QTimer(self)
        self.follow_timer.setInterval(300)
        self.follow_timer.timeout.connect(self.follow_window)
        self.random_timer = QTimer(self)
        self.random_timer.setInterval(30000)
        self.random_timer.timeout.connect(self.random_visit)
        self.random_timer.start()
        self.countdown = QTimer(self)
        self.countdown.setSingleShot(True)
        self.countdown.timeout.connect(self.capture_text)
        self.hotkey = SelectionHotkey(self.capture_text)
        pet.hop_completed.connect(self.arrived)
        self.bridge.changed.connect(self.follow_target)
        self.pet.drag_started.connect(lambda: self.emerge(silent=True))
        self.pet.drag_released.connect(self.drop_on_target)

    def configure(self):
        self.bridge.configure()
        self.panel.configure()
        self.text_window.configure()
        self.peek_sprite.update()
        if not self.settings['desktop_enabled'] or not self.settings['pet_enabled']:
            self.emerge(silent=True)

    def show_panel(self):
        self.panel.refresh()
        self.panel.show()
        self.panel.raise_()
        self.bridge.scan()

    def visit(self, identifier):
        target = self.bridge.target(identifier)
        if not target:
            return 'That desktop target is no longer visible. Refresh the list.'
        if not self.settings['desktop_enabled'] or not self.settings['pet_enabled']:
            return 'Enable the buddy and real desktop play in Settings first.'
        self.leave_other()
        self.emerge(silent=True)
        self.origin = [self.pet.x(), self.pet.y()]
        self.anchor = dict(target)
        self.pending_id = identifier
        rect = self.bridge.rect(target)
        y = rect.center().y() - self.pet.height() // 2 if target['kind'] == 'folder' else rect.top() - self.pet.height() + 22
        self.pet.hop_to([rect.center().x() - self.pet.width() // 2, y], 'desktop:' + identifier)
        return ('Hiding at ' if target['kind'] == 'folder' else 'Jumping onto ') + target['name'] + '.'

    def arrived(self, identifier):
        if identifier == 'screen-text':
            self.pet.perform('READ', 5)
            return
        if not identifier.startswith('desktop:') or identifier[8:] != self.pending_id:
            return
        target = self.bridge.target(self.pending_id)
        self.active_id, self.pending_id = self.pending_id, ''
        if not target:
            self.emerge(silent=True)
            return
        if target['kind'] == 'folder':
            self.pet.hide_in('desktop:' + self.active_id)
            self.peek_timer.start()
        else:
            self.pet.perform('BALANCE' if target['kind'] == 'tab' else 'HANG', self.settings['hide_seconds'] + 1)
        self.return_timer.start(self.settings['hide_seconds'] * 1000)
        if sys.platform == 'win32':
            self.follow_timer.start()

    def follow_window(self):
        if not self.active_id or not self.anchor or 'window_rect' not in self.anchor:
            return
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        hwnd = self.anchor['hwnd']
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = wintypes.RECT()
        if pid.value != self.anchor['pid'] or not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            self.emerge(silent=True)
            return
        base = self.anchor['window_rect']
        raw = self.anchor['rect']
        moved = {**self.anchor, 'rect': [raw[0] + rect.left - base[0], raw[1] + rect.top - base[1], raw[2], raw[3]]}
        self.follow_target(moved)

    def follow_target(self, moved=None):
        if not self.active_id:
            return
        target = moved or self.bridge.target(self.active_id)
        if not target:
            self.emerge(silent=True)
            return
        if moved is None:
            self.anchor = dict(target)
        rect = self.bridge.rect(target)
        if target['kind'] == 'folder':
            size = max(48, min(72, self.settings['pet_size']))
            self.peek_sprite.resize(size, size)
            self.peek_sprite.move(clamp_position([rect.center().x() - size // 2, rect.top() - size // 3], size, size, QApplication.screens()))
        elif not self.pet.drag_offset:
            point = clamp_position([rect.center().x() - self.pet.width() // 2, rect.top() - self.pet.height() + 22], self.pet.width(), self.pet.height(), QApplication.screens())
            self.pet.move(point)
            self.pet.real_x, self.pet.real_y = float(point.x()), float(point.y())
            self.pet.position_bubble()

    def toggle_peek(self):
        self.follow_target()
        if self.active_id and self.pet.hidden_in.startswith('desktop:'):
            self.peek_sprite.setVisible(not self.peek_sprite.isVisible())

    def peek(self):
        if self.active_id and self.pet.hidden_in.startswith('desktop:'):
            self.follow_target()
            self.peek_sprite.show()
            return 'Peeking out of the real folder icon.'
        return 'Jeffery is already out here.'

    def emerge(self, silent=False):
        self.return_timer.stop()
        self.peek_timer.stop()
        self.follow_timer.stop()
        self.peek_sprite.hide()
        was_hidden = self.pet.hidden_in.startswith('desktop:')
        active = bool(self.active_id or self.pending_id)
        self.active_id, self.pending_id = '', ''
        if was_hidden:
            self.pet.come_out([self.pet.x(), self.pet.y()])
            if not silent:
                self.pet.say('Boo! Found me!')
        elif active:
            self.pet.cancel_hop()
            self.pet.action_state = None
            if not silent:
                self.pet.begin_drop()
        self.anchor = None
        return 'Back on the desktop.'

    def drop_on_target(self, position):
        x, y = position
        target = next((t for t in self.bridge.targets if self.bridge.rect(t).adjusted(-14, -14, 14, 14).contains(x, y)), None)
        if target:
            self.visit(target['id'])

    def jump_path(self, path, position=None):
        from pathlib import Path
        target = next((t for t in self.bridge.targets if t['kind'] == 'folder' and str(Path(t['path'])).casefold() == str(Path(path)).casefold()), None)
        if target:
            return self.visit(target['id'])
        self.bridge.scan()
        self.pet.say('Keep that folder icon visible, then choose it in Real desktop.')
        self.show_panel()
        return 'Bring the real folder icon into view and refresh.'

    def random_visit(self):
        if not (self.settings['desktop_random'] and self.settings['playful'] and self.settings['desktop_enabled'] and self.settings['pet_enabled']):
            return
        if self.activity_blocked() or self.pet.paused or self.pet.hidden_in or self.pet.hop_motion or self.pet.drop_motion or self.pet.drag_offset or self.pet.action_state:
            return
        if self.bridge.targets and random.random() < 0.55:
            self.visit(random.choice(self.bridge.targets)['id'])

    def select_tab(self, identifier):
        target = self.bridge.target(identifier)
        if not target or target['kind'] != 'tab':
            return 'Choose a real tab in the desktop list first.'
        self.bridge.request('select_tab', {key: target[key] for key in ('hwnd', 'pid', 'runtime', 'name')},
                            lambda result, error: self.bridge.set_status(error or 'Selected the real tab.'))
        return 'Selecting the tab through Windows.'

    def capture_countdown(self):
        self.bridge.set_status('Select text in your editor now. Capturing in 3 seconds…')
        self.countdown.start(3000)

    def capture_text(self):
        self.countdown.stop()
        self.bridge.request('capture_text', {}, self.captured)

    def captured(self, selection, error):
        if error:
            self.bridge.set_status(error)
            self.show_panel()
            return
        try:
            self.text_window.present(selection)
            self.walk_selection(selection)
        except ValueError as exc:
            self.bridge.set_status(str(exc))

    def walk_selection(self, selection):
        from desktop_bridge import logical_rect
        self.leave_other()
        self.emerge(silent=True)
        rect = logical_rect(selection['rects'][0], self.bridge.monitors, QApplication.screens())
        self.pet.hop_to([rect.left(), rect.top() - self.pet.height() + 18], 'screen-text')

    def stop(self):
        self.countdown.stop()
        self.random_timer.stop()
        self.hotkey.stop()
        self.emerge(silent=True)
        self.bridge.stop()
        self.panel.hide()
        self.text_window.hide()
