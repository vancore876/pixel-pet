"""Optional setup/profiling tools keep network and output writes explicit."""
from contextlib import redirect_stderr, redirect_stdout
from importlib import metadata
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools import profile_desktop, setup_semantic_model


class OptionalToolTests(unittest.TestCase):
    def test_model_check_never_calls_downloader(self):
        with tempfile.TemporaryDirectory() as directory:
            download = Mock()
            with patch.object(setup_semantic_model.metadata, 'version', return_value='installed'), \
                    patch.object(setup_semantic_model, 'validate_model') as validate, \
                    patch.dict('sys.modules', {'huggingface_hub': SimpleNamespace(snapshot_download=download)}), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(setup_semantic_model.main(['--check', '--output-dir', directory]), 0)
                validate.assert_called_once_with(Path(directory).resolve())
                download.assert_not_called()

    def test_missing_dependency_stops_before_creating_model_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'model'
            with patch.object(setup_semantic_model.metadata, 'version',
                              side_effect=metadata.PackageNotFoundError('torch')), \
                    redirect_stderr(io.StringIO()):
                self.assertEqual(setup_semantic_model.main(['--output-dir', str(destination)]), 1)
            self.assertFalse(destination.exists())

    def test_model_validation_uses_only_local_safe_tensor_cpu_files(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory)
            for name in setup_semantic_model.MODEL_FILES:
                file = model / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('placeholder', encoding='utf-8')
            factory = Mock(return_value=SimpleNamespace(encode=lambda *args, **kwargs: [[1.0] * 384]))
            with patch.dict('sys.modules', {'sentence_transformers': SimpleNamespace(SentenceTransformer=factory)}):
                setup_semantic_model.validate_model(model)
            self.assertTrue(factory.call_args.kwargs['local_files_only'])
            self.assertFalse(factory.call_args.kwargs['trust_remote_code'])
            self.assertEqual(factory.call_args.kwargs['device'], 'cpu')
            self.assertTrue(factory.call_args.kwargs['model_kwargs']['use_safetensors'])

    def test_download_is_explicit_and_omits_pickle_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            download = Mock()
            with patch.object(setup_semantic_model.metadata, 'version', return_value='installed'), \
                    patch.object(setup_semantic_model, 'validate_model'), \
                    patch.dict('sys.modules', {'huggingface_hub': SimpleNamespace(snapshot_download=download)}), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(setup_semantic_model.main(['--output-dir', directory, '--revision', 'abc123']), 0)
            self.assertEqual(download.call_args.kwargs['repo_id'], setup_semantic_model.MODEL_ID)
            self.assertEqual(download.call_args.kwargs['revision'], 'abc123')
            self.assertIn('*.safetensors', download.call_args.kwargs['allow_patterns'])
            self.assertNotIn('*.bin', download.call_args.kwargs['allow_patterns'])

    def test_existing_flamegraph_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'existing.svg'
            output.write_text('previous report', encoding='utf-8')
            with patch.object(profile_desktop.subprocess, 'run') as run, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                profile_desktop.main(['--launch', '--output', str(output)])
            run.assert_not_called()
            self.assertEqual(output.read_text(encoding='utf-8'), 'previous report')

    def test_profile_output_with_spaces_remains_one_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'my reports' / 'cpu profile.svg'
            with patch.object(profile_desktop, 'find_profiler', return_value='py-spy'), \
                    patch.object(profile_desktop.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run, \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(profile_desktop.main(['--pid', '1234', '--output', str(output)]), 0)
            command = run.call_args.args[0]
            self.assertEqual(command[command.index('--output') + 1], str(output.resolve()))
            self.assertEqual(command[command.index('--pid') + 1], '1234')
            self.assertNotIn('shell', run.call_args.kwargs)


if __name__ == '__main__':
    unittest.main()
