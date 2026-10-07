"""An existing Windows environment refreshes when a runtime dependency changes."""
from importlib import metadata
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runtime_setup import missing_requirements


class RuntimeSetupTests(unittest.TestCase):
    def test_current_pins_need_no_install_and_new_pdf_dependency_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'requirements.txt'
            path.write_text('PySide6==6.11.2\npypdf==6.19.0\n')
            def old_versions(name):
                if name == 'pypdf':
                    raise metadata.PackageNotFoundError(name)
                return '6.11.2'
            with patch('runtime_setup.metadata.version', side_effect=old_versions):
                self.assertEqual(missing_requirements(path), ['pypdf==6.19.0'])
            with patch('runtime_setup.metadata.version', side_effect=lambda name: '6.19.0' if name == 'pypdf' else '6.11.2'):
                self.assertEqual(missing_requirements(path), [])

    def test_installed_wrong_version_triggers_refresh(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'requirements.txt'
            path.write_text('# runtime\npypdf==6.19.0\n')
            with patch('runtime_setup.metadata.version', return_value='5.0.0'):
                self.assertEqual(missing_requirements(path), ['pypdf==6.19.0'])
