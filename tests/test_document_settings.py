"""OCR controls and local semantic-model settings survive validated saving."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from notes import NoteStore, NoteService
from notepad_window import NotepadWindow
from settings import AppSettings
from settings_window import SettingsWindow

app = QApplication.instance() or QApplication([])


class DocumentSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = AppSettings(self.root / 'settings.json')

    def tearDown(self):
        self.temp.cleanup()

    def test_local_paths_and_languages_roundtrip_without_invalid_controls(self):
        self.settings.save({'pdf_ocr': True, 'ocr_language': 'eng+fra',
            'tesseract_path': r'C:\Program Files\Tesseract-OCR\tesseract.exe',
            'semantic_memory_enabled': True, 'semantic_model_path': r'C:\Jeffery\models'})
        reloaded = AppSettings(self.settings.path)
        self.assertTrue(reloaded['pdf_ocr'])
        self.assertTrue(reloaded['semantic_memory_enabled'])
        self.assertEqual(reloaded['semantic_model_path'], r'C:\Jeffery\models')
        self.assertEqual(reloaded['ocr_language'], 'eng+fra')
        self.settings.save({'ocr_language': 'eng --config evil', 'tesseract_path': 'bad\x00.exe',
            'semantic_model_path': 100, 'semantic_memory_enabled': 'true'})
        self.assertEqual(self.settings['ocr_language'], 'eng')
        self.assertEqual(self.settings['tesseract_path'], '')
        self.assertEqual(self.settings['semantic_model_path'], '')
        self.assertFalse(self.settings['semantic_memory_enabled'])

    def test_settings_dialog_applies_new_controls(self):
        window = SettingsWindow(self.settings)
        try:
            values = []
            window.apply_requested.connect(values.append)
            window.refresh()
            window.controls['semantic_memory_enabled'].setChecked(True)
            window.controls['semantic_model_path'].setText(str(self.root / 'model'))
            window.controls['ocr_language'].setText('eng+deu')
            window.apply()
            self.assertEqual(values[0]['ocr_language'], 'eng+deu')
            self.assertTrue(values[0]['semantic_memory_enabled'])
            self.assertEqual(values[0]['semantic_model_path'], str(self.root / 'model'))
        finally:
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_pdf_import_uses_per_import_ocr_choice_and_saved_language(self):
        self.settings.save({'ocr_language': 'eng+fra', 'tesseract_path': 'chosen-tesseract.exe'})
        store = NoteStore(self.root / 'notes.json')
        service = NoteService(store, self.settings, lambda: False)
        window = NotepadWindow(service, self.settings)
        try:
            window.import_ocr.setChecked(True)
            with patch.object(window.intake, 'import_pdf', return_value=True) as importer:
                window.import_pdf('scan.pdf')
                importer.assert_called_once_with('scan.pdf', engine='auto', ocr=True, ocr_language='eng+fra', tesseract_path='chosen-tesseract.exe')
            self.assertFalse(window.import_ocr.isEnabled())
            window.set_import_busy(False)
            self.assertTrue(window.import_ocr.isEnabled())
        finally:
            service.stop()
            window.shutdown()
            window.close()
            window.deleteLater()
            service.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_search_storage_errors_do_not_destroy_unsaved_draft(self):
        store = NoteStore(self.root / 'notes.json')
        service = NoteService(store, self.settings, lambda: False)
        window = NotepadWindow(service, self.settings)
        try:
            window.body.setPlainText('Keep this draft')
            with patch.object(store, 'search_ids', side_effect=ValueError('Storage unavailable')):
                window.search.setText('appointment')
            self.assertEqual(window.body.toPlainText(), 'Keep this draft')
            self.assertIn('Search could not finish', window.status.text())
        finally:
            service.stop()
            window.shutdown()
            window.close()
            window.deleteLater()
            service.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
