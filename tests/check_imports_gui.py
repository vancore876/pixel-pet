"""GUI review/save/cancel with real worker threads and scripted source readers."""
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer, qInstallMessageHandler
from PySide6.QtWidgets import QApplication
from notes import NoteService, NoteStore
from notepad_window import NotepadWindow
from settings import AppSettings

qInstallMessageHandler(lambda kind, context, text:
    print(text, file=sys.stderr) if 'This plugin does not support' not in text else None)
app = QApplication([])
temp = tempfile.TemporaryDirectory()
root = Path(temp.name)
preview = Path(os.environ.get('BUDDY_QA_DIR', root))
preview.mkdir(parents=True, exist_ok=True)
store = NoteStore(root / 'notes.json')
settings = AppSettings(root / 'settings.json')
service = NoteService(store, settings)
window = NotepadWindow(service, settings)
errors = []
gate = threading.Event()
state = {'heartbeats': 0, 'stage': 'pdf'}
pdf = {'kind': 'pdf', 'title': 'Clinic appointment',
    'text': 'Bring your ID.\n\nAppointment on October 8 at 9 AM.', 'url': '',
    'sections': [{'title': 'Clinic appointment', 'body': 'Bring your ID.\n\nAppointment on October 8 at 9 AM.'}],
    'sources': [{'title': 'Clinic appointment.pdf', 'url': ''}], 'truncated': False}
web = {'kind': 'web', 'title': 'Travel checklist',
    'text': 'Pack a charger and check the departure time.',
    'url': 'https://example.org/travel',
    'sections': [{'title': 'Travel checklist', 'body': 'Pack a charger and check the departure time.'}],
    'sources': [{'title': 'Travel checklist', 'url': 'https://example.org/travel'}], 'truncated': True}


def read_pdf(path, cancel):
    gate.wait(3)
    return pdf


def read_web(url, cancel):
    return web


patch_pdf = patch('content_intake.extract_pdf', read_pdf)
patch_web = patch('content_intake.extract_web', read_web)
patch_pdf.start()
patch_web.start()
heartbeat = QTimer()
heartbeat.setInterval(25)
heartbeat.timeout.connect(lambda: state.update(heartbeats=state['heartbeats'] + 1))
heartbeat.start()


def begin():
    try:
        original = service.save_note(None, 'Writing draft', 'Original saved text', 0)
        state['original_id'] = original['id']
        window.load_note(original['id'])
        window.body.setPlainText('Do not replace my unsaved writing draft.')
        window.show_section('import')
        window.show()
        window.import_pdf(str(root / 'clinic.pdf'))
        assert window.intake.busy and window.import_progress.isVisible()
        assert window.cancel_import_button.isVisible()
        QTimer.singleShot(300, release_pdf)
    except Exception as exc:
        errors.append(repr(exc)); app.quit()


def release_pdf():
    try:
        assert state['heartbeats'] >= 5, 'Document reading blocked the GUI event loop'
        assert len(store.notes) == 1, 'Reader saved before review'
        gate.set()
    except Exception as exc:
        errors.append(repr(exc)); app.quit()


def received(result):
    QTimer.singleShot(50, reviewed)


def reviewed():
    try:
        if state['stage'] == 'pdf':
            assert window.save_import_button.isEnabled()
            window.import_review.setPlainText('Bring a driving licence. Appointment on October 8 at 9 AM.')
            assert window.save_import()
            assert 'driving licence' in store.notes[-1]['body']
            assert window.editing_id == state['original_id']
            assert window.dirty and window.body.toPlainText().startswith('Do not replace')
            assert store.notes[-1]['next_due'] is None
            window.grab().save(str(preview / 'pdf-import.png'))
            state['stage'] = 'web'
            window.web_address.setText('https://example.org/travel')
            window.read_web_button.click()
        elif state['stage'] == 'web':
            assert 'Only part' in window.import_status.text()
            assert 'https://example.org/travel' in window.import_sources.text()
            assert window.save_import()
            assert store.notes[-1]['document_source'] == 'https://example.org/travel'
            assert len(NoteStore(store.path).notes) == 3
            window.grab().save(str(preview / 'web-import.png'))
            state['stage'] = 'cancel'
            gate.clear()
            window.import_pdf(str(root / 'discarded.pdf'))
            window.cancel_import_button.click()
            gate.set()
            QTimer.singleShot(400, finish)
        else:
            raise AssertionError('A cancelled worker replaced the reviewed source')
    except Exception as exc:
        errors.append(repr(exc)); app.quit()


def finish():
    try:
        assert len(store.notes) == 3
        assert window.import_result['kind'] == 'web'
        assert not window.intake.busy
        assert window.body.toPlainText().startswith('Do not replace')
        window.shutdown()
        window.dirty = False
        window.close()
        app.quit()
    except Exception as exc:
        errors.append(repr(exc)); app.quit()


window.intake.completed.connect(received)
QTimer.singleShot(100, begin)
QTimer.singleShot(8000, lambda: (errors.append('GUI import check timed out'), app.quit()))
app.exec()
gate.set()
window.shutdown()
service.stop()
patch_pdf.stop()
patch_web.stop()
temp.cleanup()
if errors:
    raise AssertionError('; '.join(errors))
print('PASS: asynchronous PDF and web review, edited import save, intact writing draft, responsive progress/cancel, source attribution, no timed schedule, persistence, stale result cancellation, and shutdown. Source reads were scripted.')
