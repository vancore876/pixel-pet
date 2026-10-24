import copy
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QLabel
from app_updates import file_digest, managed_path, update_available, install_source, UpdateController

APP = QApplication.instance() or QApplication([])


def manifest(files):
    return {'commit': 'a' * 40, 'committed_at': 200, 'exe_sha256': '0' * 64,
            'files': {name: file_digest(data, name) for name, data in files.items()}}


class UpdateTests(unittest.TestCase):
    def test_detects_updates_and_does_not_offer_older_release(self):
        value = manifest({'main.py': b'new'})
        self.assertFalse(update_available(value, value['files']))
        self.assertTrue(update_available(value, {'main.py': file_digest(b'old', 'main.py')}))
        self.assertFalse(update_available(value, {}, 'b' * 40, 300))
        self.assertFalse(update_available(value, {}, 'a' * 40, 100))

    def test_installs_only_verified_application_files_preserving_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'app'
            root.mkdir()
            (root / 'main.py').write_bytes(b'old')
            (root / 'data').mkdir()
            notes = root / 'data' / 'notes.json'
            notes.write_text('private notes')
            archive = Path(folder) / 'source.zip'
            with zipfile.ZipFile(archive, 'w') as bundle:
                bundle.writestr('pixel-pet/main.py', b'new')
                bundle.writestr('pixel-pet/data/notes.json', b'do not install')
            install_source(archive, root, manifest({'main.py': b'new'}))
            self.assertEqual((root / 'main.py').read_bytes(), b'new')
            self.assertEqual(notes.read_text(), 'private notes')

    def test_bad_checksum_never_replaces_current_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'app'
            root.mkdir()
            (root / 'main.py').write_bytes(b'old')
            archive = Path(folder) / 'source.zip'
            with zipfile.ZipFile(archive, 'w') as bundle:
                bundle.writestr('pixel-pet/main.py', b'tampered')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                install_source(archive, root, manifest({'main.py': b'new'}))
            self.assertEqual((root / 'main.py').read_bytes(), b'old')
        for name in ('../notes.py', '/main.py', 'data/notes.json', 'assets/../../main.py', 'C:/main.py'):
            self.assertFalse(managed_path(name))

    def test_failed_install_rolls_back_files_already_replaced(self):
        import shutil
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'app'
            root.mkdir()
            (root / 'main.py').write_bytes(b'old main')
            (root / 'other.py').write_bytes(b'old other')
            archive = Path(folder) / 'source.zip'
            with zipfile.ZipFile(archive, 'w') as bundle:
                bundle.writestr('repo/main.py', b'new main')
                bundle.writestr('repo/other.py', b'new other')
            original_copy = shutil.copy2
            def copy(source, target, *args, **kwargs):
                if Path(source).name == 'other.py' and 'stage' in Path(source).parts:
                    raise OSError('Synthetic interrupted copy')
                return original_copy(source, target, *args, **kwargs)
            with patch('app_updates.shutil.copy2', side_effect=copy):
                with self.assertRaisesRegex(OSError, 'interrupted'):
                    install_source(archive, root, manifest({'main.py': b'new main', 'other.py': b'new other'}))
            self.assertEqual((root / 'main.py').read_bytes(), b'old main')
            self.assertEqual((root / 'other.py').read_bytes(), b'old other')

    def test_button_is_at_top_right_of_existing_and_new_windows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'main.py').write_bytes(b'old')
            controller = UpdateController(APP, root, root=root, auto_check=False)
            first, second = QDialog(), QDialog()
            try:
                QVBoxLayout(first).addWidget(QLabel('Home'))
                first.resize(600, 300)
                first.show()
                APP.processEvents()
                controller._accept_check(manifest({'main.py': b'new'}))
                QVBoxLayout(second).addWidget(QLabel('Notepad'))
                second.resize(600, 300)
                second.show()
                APP.processEvents()
                APP.processEvents()
                self.assertTrue(first.property('jeffery_update_button'))
                self.assertTrue(second.property('jeffery_update_button'))
                for button in controller.buttons:
                    self.assertGreater(button.x(), button.parentWidget().width() / 2)
                self.assertFalse(controller.busy)
            finally:
                controller.stop()
                APP.removeEventFilter(controller)
                first.close()
                second.close()
