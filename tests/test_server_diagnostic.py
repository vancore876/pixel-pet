import contextlib
import io
import socket
import unittest
from unittest.mock import patch

from tools import check_work_server as diagnostic


class ServerDiagnosticTests(unittest.TestCase):
    def test_preflight_refuses_occupied_port_without_stopping_process(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            with patch.object(diagnostic, 'source_check', return_value=True), \
                 patch.object(diagnostic, 'health_check', return_value=False), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(diagnostic.main(['--preflight', '--port', str(port)]), 1)
            self.assertIn('already in use', output.getvalue())
            self.assertTrue(diagnostic.listening(port))

    def test_current_source_accepts_writing_notes_and_highlights(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(diagnostic.source_check())

    def test_preflight_does_not_launch_with_unsupported_source(self):
        with patch.object(diagnostic, 'source_check', return_value=False), \
             patch.object(diagnostic, 'listening', return_value=False):
            self.assertEqual(diagnostic.main(['--preflight']), 1)
