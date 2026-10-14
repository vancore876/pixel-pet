"""Work profiles and notebook databases stay local when the app is shared."""
import copy
import ctypes
import json
import os
from pathlib import Path, PureWindowsPath
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from PySide6.QtWidgets import QApplication

import config
from ai_privacy import note_allowed_for_ai
from notebook_ui_jobs import export_notebook
from notes import NoteService, NoteStore
from settings import AppSettings
from team_service import TeamService
from team_store import TeamStore


class TeamConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.local = self.root / 'local-app-data'
        self.portable = self.root / 'shared-application'
        self.environ = patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)})
        self.environ.start()

    def tearDown(self):
        self.environ.stop()
        self.temp.cleanup()

    def test_windows_unc_paths_are_remote_without_requiring_drive_api(self):
        with patch.object(config, 'os', SimpleNamespace(name='nt')):
            for path in (r'\\server\department\Jeffery\data', '//server/department/Jeffery/data',
                         r'\\?\UNC\server\department\Jeffery\data'):
                with self.subTest(path=path):
                    self.assertTrue(config._network_location(path))

    def test_windows_mapped_drive_is_remote_and_fixed_drive_is_local(self):
        drive_type = Mock(return_value=4)
        windows = SimpleNamespace(kernel32=SimpleNamespace(GetDriveTypeW=drive_type))
        with patch.object(config, 'os', SimpleNamespace(name='nt')), \
                patch.object(config, 'Path', PureWindowsPath), patch.object(ctypes, 'windll', windows, create=True):
            self.assertTrue(config._network_location(PureWindowsPath('Z:/Jeffery/data')))
            drive_type.assert_called_with('Z:\\')
            drive_type.return_value = 3
            self.assertFalse(config._network_location(PureWindowsPath('C:/Jeffery/data')))

    def test_shared_application_uses_local_notebook_even_with_copied_portable_settings(self):
        copied = self.portable / 'data' / 'settings.json'
        copied.parent.mkdir(parents=True)
        copied.write_text(json.dumps({'team_sync_enabled': True, 'team_employee_name': 'Another employee',
                                     'team_shared_folder': r'\\server\department',
                                     'team_profile_path': r'\\server\department\team.sqlite'}), encoding='utf-8')
        with patch.object(config, 'ROOT', self.portable), patch.object(config, '_network_location', return_value=True):
            settings = AppSettings()
            self.assertEqual(settings.path, self.local / 'PixelSystemBuddy' / 'settings.json')
            self.assertFalse(settings['team_sync_enabled'])
            self.assertEqual(settings['team_employee_name'], '')
            settings.save({'team_employee_name': 'Local employee'})
            store = NoteStore(settings.path.parent / 'notes.json')
            try:
                store.add('Local note', 'Keep the local database independent.', repeat=0)
                store.documents.prepare_text('A local searchable document.', title='Local document')
                self.assertEqual(store.documents.path, self.local / 'PixelSystemBuddy' / 'documents.sqlite')
                self.assertTrue(store.documents.path.exists())
                self.assertFalse((copied.parent / 'documents.sqlite').exists())
                self.assertFalse((copied.parent / 'notes.json').exists())
                self.assertEqual(json.loads(copied.read_text())['team_employee_name'], 'Another employee')
            finally:
                store.close()

    def test_read_only_installation_falls_back_without_leaving_a_write_probe(self):
        preferred = self.portable / 'data'
        write_text = Path.write_text

        def deny_probe(path, *args, **kwargs):
            if path == preferred / '.write-check':
                raise PermissionError('Installation folder is read only.')
            return write_text(path, *args, **kwargs)

        with patch.object(config, 'ROOT', self.portable), patch.object(config, '_network_location', return_value=False), \
                patch.object(Path, 'write_text', deny_probe):
            self.assertEqual(config.data_directory(), self.local / 'PixelSystemBuddy')
        self.assertFalse((preferred / '.write-check').exists())

    def test_local_portable_notebook_can_remain_portable_without_sharing_employee_cache(self):
        with patch.object(config, 'ROOT', self.portable), patch.object(config, '_network_location', return_value=False):
            self.assertEqual(config.data_directory(), self.portable / 'data')
            self.assertEqual(config.team_data_directory(), self.local / 'PixelSystemBuddy' / 'team')
            self.assertFalse((self.portable / 'data' / '.write-check').exists())

    def test_loaded_portable_preferences_cannot_redirect_team_sqlite_to_shared_folder(self):
        path = self.portable / 'data' / 'settings.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'team_shared_folder': r'\\office\team', 'team_employee_id': 'a' * 32,
                                   'team_employee_name': 'Local employee',
                                   'team_profile_path': r'\\office\team\team.sqlite',
                                   'local_profile_path': r'\\office\team\team.sqlite'}), encoding='utf-8')
        settings = AppSettings(path)
        store = NoteStore(self.root / 'personal-notebook' / 'notes.json')
        service = NoteService(store, settings)
        team = TeamService(service, settings)
        try:
            self.assertEqual(team.profile_path, self.local / 'PixelSystemBuddy' / 'team' / 'team.sqlite')
            self.assertNotIn('team_profile_path', settings.values)
            self.assertNotIn('local_profile_path', settings.values)
            self.assertNotEqual(team.profile_path.parent, settings.path.parent)
            self.assertFalse((path.parent / 'team.sqlite').exists())
        finally:
            team.stop()
            service.stop()
            store.close()

    def test_copied_preferences_use_independent_local_device_identities(self):
        profiles = []
        shared_preferences = {'team_employee_id': 'a' * 32, 'team_employee_name': 'Same employee',
                              'team_shared_folder': r'\\office\team'}
        for pc in ('pc-one', 'pc-two'):
            with patch.dict(os.environ, {'LOCALAPPDATA': str(self.root / pc)}):
                profile = TeamStore(config.team_data_directory() / 'team.sqlite')
                try:
                    profiles.append(profile.configure('b' * 32, shared_preferences['team_employee_name'],
                                                      shared_preferences['team_employee_id']))
                finally:
                    profile.close()
        self.assertEqual(profiles[0]['employee_id'], profiles[1]['employee_id'])
        self.assertNotEqual(profiles[0]['device_id'], profiles[1]['device_id'])

    def test_remote_team_cache_rejected_before_any_sqlite_file_is_created(self):
        remote = self.root / 'mapped-network-drive' / 'team.sqlite'
        with patch.object(config, '_network_location', return_value=True), self.assertRaises(ValueError):
            TeamStore(remote)
        self.assertFalse(remote.exists())


class TeamPreferenceValidationTests(unittest.TestCase):
    def test_old_settings_default_to_disconnected_local_only_team_data(self):
        settings = AppSettings.validate({'pet_name': 'Jeffery'})
        self.assertFalse(settings['team_sync_enabled'])
        self.assertFalse(settings['team_share_notes_with_ai'])
        self.assertEqual(settings['team_shared_folder'], '')
        self.assertEqual(settings['team_employee_id'], '')
        self.assertEqual(settings['team_reminder_minutes'], 30)

    def test_team_booleans_require_real_booleans(self):
        for value in (1, 0, 'true', 'false', [], None):
            with self.subTest(value=value):
                settings = AppSettings.validate({'team_sync_enabled': value, 'team_share_notes_with_ai': value})
                self.assertFalse(settings['team_sync_enabled'])
                self.assertFalse(settings['team_share_notes_with_ai'])

    def test_team_reminder_interval_bounds_and_wrong_types(self):
        for value, expected in ((-1, 0), (0, 0), (45, 45), (1500, 1440), (True, 30), ('45', 30), (1.5, 30)):
            with self.subTest(value=value):
                self.assertEqual(AppSettings.validate({'team_reminder_minutes': value})['team_reminder_minutes'], expected)

    def test_valid_network_folder_roundtrips_and_control_characters_are_rejected(self):
        folder = r'\\office-server\Sales Team\Jeffery'
        self.assertEqual(AppSettings.validate({'team_shared_folder': '  ' + folder + '  '})['team_shared_folder'], folder)
        for value in (folder + '\x00', folder + '\nsecret', 'x' * 4097, 123, None):
            with self.subTest(value=value):
                self.assertEqual(AppSettings.validate({'team_shared_folder': value})['team_shared_folder'], '')

    def test_employee_identity_validation_and_printable_name(self):
        self.assertEqual(AppSettings.validate({'team_employee_id': 'a1' * 16})['team_employee_id'], 'a1' * 16)
        for value in ('a', 'A' * 32, '../employee', '0' * 31, '0' * 33, None, 123):
            with self.subTest(value=value):
                self.assertEqual(AppSettings.validate({'team_employee_id': value})['team_employee_id'], '')
        self.assertEqual(AppSettings.validate({'team_employee_name': '  Al\nice\x00  '})['team_employee_name'], 'Alice')
        self.assertEqual(len(AppSettings.validate({'team_employee_name': 'x' * 200})['team_employee_name']), 80)

    def test_team_preferences_persist_with_privacy_flag_and_without_unsupported_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'settings.json'
            settings = AppSettings(path)
            settings.save({'team_sync_enabled': True, 'team_shared_folder': r'\\office\work',
                           'team_employee_name': 'Alice', 'team_employee_id': 'a' * 32,
                           'team_reminder_minutes': 45, 'team_share_notes_with_ai': False,
                           'team_profile_path': r'\\office\work\team.sqlite'})
            restored = AppSettings(path)
            self.assertEqual(restored['team_employee_name'], 'Alice')
            self.assertEqual(restored['team_employee_id'], 'a' * 32)
            self.assertEqual(restored['team_shared_folder'], r'\\office\work')
            self.assertEqual(restored['team_reminder_minutes'], 45)
            self.assertTrue(restored['team_sync_enabled'])
            self.assertFalse(restored['team_share_notes_with_ai'])
            self.assertNotIn('team_profile_path', json.loads(path.read_text()))


class PortableTeamBackupTests(unittest.TestCase):
    def test_json_exports_keep_shared_privacy_without_employee_or_workspace_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = NoteStore(root / 'notes.json')
            note = store.add('Shared order', 'Customer pickup instructions.', repeat=30,
                details={'team_id': 'a' * 32, 'team_workspace_id': 'b' * 32,
                         'team_revisions': ['c' * 32], 'team_recipients': ['d' * 32]})
            store.modify(note['id'], source=r'\\office\private-notes.txt')
            before = copy.deepcopy(store.state)
            target = root / 'mobile.json'
            try:
                export_notebook({'format': 'json', 'store': store, 'path': target, 'snapshot': copy.deepcopy(store.state)})
                exported = json.loads(target.read_text())
                portable = exported['notes'][0]
                self.assertTrue(portable['team_shared'])
                self.assertEqual(portable['body'], note['body'])
                self.assertEqual([key for key in portable if key.startswith('team_')], ['team_shared'])
                self.assertNotIn('office', json.dumps(exported))
                self.assertFalse(note_allowed_for_ai(portable, config.DEFAULTS))
                self.assertEqual(store.state, before)
            finally:
                store.close()

    def test_zip_backups_keep_team_links_for_desktop_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = NoteStore(root / 'notes.json')
            note = store.add('Shared order', 'Keep the original team links.', repeat=0,
                             details={'team_id': 'a' * 32, 'team_workspace_id': 'b' * 32,
                                      'team_revisions': ['c' * 32], 'team_recipients': ['d' * 32]})
            target = root / 'desktop.zip'
            try:
                export_notebook({'format': 'zip', 'store': store, 'path': target, 'snapshot': copy.deepcopy(store.state)})
                with zipfile.ZipFile(target) as archive:
                    payload = json.loads(archive.read('notebook.json'))
                self.assertEqual(payload['notes'][0]['team_recipients'], ['d' * 32])
                self.assertEqual(payload['notes'][0]['team_workspace_id'], 'b' * 32)
                self.assertEqual(payload['notes'][0]['team_id'], note['team_id'])
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
