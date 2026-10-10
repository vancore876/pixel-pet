"""Exercise the renewed entry points and preserve drafts through navigation."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QMessageBox
from main import BuddyApp
from settings import AppSettings
from ui_style import DARK, LIGHT

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)
if APP.platformName() == 'offscreen' and Path('C:/Windows/Fonts/segoeui.ttf').is_file():
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeuib.ttf')


class Credentials:
    def get(self): return ''


with tempfile.TemporaryDirectory(prefix='jeffery-home-integration-') as directory:
    root = Path(directory)
    settings = AppSettings(root / 'settings.json')
    settings.save({'desktop_enabled': False, 'mouse_mode': 'off', 'ai_autonomy': False,
                   'memory_ai': False, 'business_greetings': False, 'home_on_start': False,
                   'semantic_memory_enabled': False})
    with patch('main.CredentialStore', return_value=Credentials()):
        buddy = BuddyApp(APP, settings, show_tray=False)
    errors = []

    def review():
        try:
            buddy.note_service.stop()
            buddy.menu.actions()[0].trigger()
            assert buddy.home.isVisible()
            buddy.home.create_buttons['note'].click()
            window = buddy.notepad
            assert window.isVisible() and window.kind.currentData() == 'note'
            window.title.setText('Counter writing')
            window.body.setPlainText('Personal text stays in this Windows profile.')
            assert window.save_note()
            identifier = window.editing_id
            assert buddy.home.recent.item(0).data(Qt.UserRole) == identifier
            window.body.setPlainText('This unsaved writing must survive a cancelled switch.')
            with patch('notepad_window.QMessageBox.question', return_value=QMessageBox.Cancel):
                buddy.home.create_buttons['order'].click()
            assert window.editing_id == identifier and window.dirty
            assert 'unsaved writing' in window.body.toPlainText()
            with patch('notepad_window.QMessageBox.question', return_value=QMessageBox.Discard):
                window.reload_saved()
            buddy.home.create_buttons['order'].click()
            window.title.setText('Order draft')
            window.order_notes.setPlainText('Check this part before promising pickup.')
            assert not window.save_note(), 'Offline business save must not become a local success'
            assert window.dirty and 'Sign in' in window.status.text()
            assert len(buddy.notes_store.notes) == 1
            buddy.home.nav_buttons['coworkers'].click()
            assert buddy.work_chat.isVisible()
            buddy.work_chat.home_requested.emit()
            assert buddy.home.isVisible() and window.dirty
            buddy.business_sync.status_changed.emit('Shared with coworkers · preview', True)
            assert buddy.home.connected
            buddy.work_chat.sign_out()
            assert not buddy.home.connected
            assert buddy.notes_store.find(identifier)['kind'] == 'note'
            buddy.note_service.modify(identifier, pinned=True)
            sticky = buddy.stickies[identifier]
            buddy.apply_settings({'interface_appearance': 'system'})
            for palette in (DARK, LIGHT):
                with patch('ui_style.ui_palette', return_value=palette):
                    buddy.interface_scheme_changed()
                for surface in (buddy.home, buddy.note_popup, sticky, buddy.desktop.text_window):
                    assert palette['bg'] in surface.styleSheet()
                assert window.dirty and window.title.text() == 'Order draft'
            buddy.apply_settings({'interface_appearance': 'dark'})
            assert buddy.home.settings['interface_appearance'] == 'dark'
            assert '#111e28' in buddy.home.styleSheet()
            buddy.dialog.home_requested.emit()
            buddy.process_window.home_requested.emit()
            buddy.tray.open_requested.emit()
            buddy.pet.overlay_requested.emit()
            assert buddy.home.isVisible()
            preview = Path(os.environ.get('BUDDY_QA_DIR', root))
            preview.mkdir(parents=True, exist_ok=True)
            buddy.home.resize(800, 600)
            APP.processEvents()
            assert buddy.home.scroll.horizontalScrollBar().maximum() == 0
            buddy.home.grab().save(str(preview / 'home-integration-dark.png'))
            buddy.home.focus_start.click()
            assert buddy.focus.clock.state == 'running'
            buddy.shutdown()
            assert not buddy.home.isVisible() and not buddy.thread.isRunning()
            assert not buddy.focus.timer.isActive()
            APP.quit()
        except Exception as error:
            errors.append(repr(error))
            APP.quit()

    QTimer.singleShot(200, review)
    QTimer.singleShot(12000, lambda: (errors.append('Home integration timed out'), APP.quit()))
    APP.exec()
    buddy.shutdown()
    if errors:
        raise AssertionError('; '.join(errors))
    print('PASS: Home entry points, scoped navigation, local writing, preserved drafts, blocked offline business save, login status, live appearance, and shutdown.')
