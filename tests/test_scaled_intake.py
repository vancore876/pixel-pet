"""Large document retention, bounded previews, and temporary-file ownership."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
import content_intake as intake


class ScaledExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "archive.pdf"
        self.path.write_bytes(b"%PDF-1.7\nfixture")
        self.results = []
        original = tempfile.mkstemp
        self.spool_patch = patch.object(intake.tempfile, "mkstemp",
            side_effect=lambda **kwargs: original(dir=self.temp.name, **kwargs))
        self.spool_patch.start()

    def tearDown(self):
        for result in self.results:
            intake.cleanup_result(result)
        self.spool_patch.stop()
        self.temp.cleanup()

    def reader(self, texts):
        pages = [Mock(extract_text=Mock(return_value=text)) for text in texts]
        return Mock(is_encrypted=False, pages=pages, metadata=Mock(title="Archive"))

    def pdf(self, texts, preview=96, limit=4096):
        with patch("pypdf.PdfReader", return_value=self.reader(texts)), \
                patch.object(intake, "MAX_TEXT", preview), \
                patch.object(intake, "MAX_DOCUMENT_CHARACTERS", limit):
            result = intake.extract_pdf(self.path, engine="pypdf")
        self.results.append(result)
        return result

    def assert_no_spools(self):
        self.assertFalse(list(Path(self.temp.name).glob("pixel-pet-intake-*.txt")))

    def test_one_billion_character_limit_is_distinct_from_500_mb_file_limit(self):
        self.assertEqual(intake.MAX_DOCUMENT_CHARACTERS, 1_000_000_000)
        self.assertEqual(intake.MAX_PDF_BYTES, 500 * 1024 * 1024)
        self.assertEqual(intake.MAX_TEXT, 120_000)

    def test_full_pdf_text_and_last_page_survive_a_short_preview(self):
        texts = ["A first page. " * 30, "Remember the final appointment on Friday."]
        result = self.pdf(texts)
        expected = "\n\n".join(f"Page {i + 1}\n{intake.normalize_text(text)}"
                                 for i, text in enumerate(texts))
        self.assertLessEqual(len(result["text"]), 96)
        self.assertNotIn("final appointment", result["text"])
        self.assertEqual(Path(result["text_file"]).read_text(encoding="utf-8"), expected)
        self.assertEqual(result["character_count"], len(expected))
        self.assertEqual(result["full_text_sha256"], hashlib.sha256(expected.encode()).hexdigest())
        self.assertTrue(result["preview_truncated"])
        self.assertFalse(result["truncated"])
        self.assertTrue(all(len(section["body"]) <= 96 for section in result["sections"]))

    def test_character_limit_counts_unicode_characters_instead_of_utf8_bytes(self):
        result = self.pdf(["漢字🙂é" * 80], preview=80, limit=200)
        saved = Path(result["text_file"]).read_text(encoding="utf-8")
        self.assertEqual(len(saved), 200)
        self.assertEqual(result["character_count"], 200)
        self.assertGreater(Path(result["text_file"]).stat().st_size, 200)
        self.assertTrue(result["truncated"])

    def test_full_limit_stops_extraction_after_the_overflow_page(self):
        reader = self.reader(["a" * 50, "b" * 50, "never extracted"])
        with patch("pypdf.PdfReader", return_value=reader), \
                patch.object(intake, "MAX_TEXT", 40), \
                patch.object(intake, "MAX_DOCUMENT_CHARACTERS", 80):
            result = intake.extract_pdf(self.path, engine="pypdf")
        self.results.append(result)
        self.assertEqual(result["character_count"], 80)
        self.assertTrue(result["truncated"])
        reader.pages[2].extract_text.assert_not_called()

    def test_small_pdf_keeps_original_inline_result_and_removes_spool(self):
        result = self.pdf(["Buy groceries."], preview=120)
        self.assertNotIn("text_file", result)
        self.assertFalse(result["preview_truncated"])
        self.assertFalse(result["truncated"])
        self.assertEqual(result["character_count"], len(result["text"]))
        self.assert_no_spools()

    def test_cancel_between_pages_removes_partial_full_text(self):
        canceled = threading.Event()
        reader = self.reader(["x" * 200, "unreachable"])
        reader.pages[1].extract_text.side_effect = lambda: canceled.set() or "Second page."
        with patch("pypdf.PdfReader", return_value=reader), patch.object(intake, "MAX_TEXT", 32):
            with self.assertRaises(intake._Canceled):
                intake.extract_pdf(self.path, canceled, engine="pypdf")
        self.assert_no_spools()

    def test_parser_failure_after_spooling_removes_partial_full_text(self):
        reader = self.reader(["x" * 200, "unreachable"])
        reader.pages[1].extract_text.side_effect = ValueError("bad page")
        with patch("pypdf.PdfReader", return_value=reader), patch.object(intake, "MAX_TEXT", 32):
            with self.assertRaises(intake.IntakeError):
                intake.extract_pdf(self.path, engine="pypdf")
        self.assert_no_spools()

    def test_long_public_article_retains_text_beyond_its_preview(self):
        content = "First paragraph.\n\n" + "article " * 80 + "Last appointment."
        with patch.object(intake, "fetch_public_bytes",
                return_value=("https://example.org/article", "text/plain", content)), \
                patch.object(intake, "MAX_TEXT", 96):
            result = intake.extract_web("https://example.org/article")
        self.results.append(result)
        self.assertTrue(Path(result["text_file"]).read_text().endswith("Last appointment."))
        self.assertFalse(result["truncated"])
        self.assertTrue(result["preview_truncated"])

    def test_cleanup_is_idempotent_and_only_deletes_intake_owned_files(self):
        result = self.pdf(["x" * 200])
        path = Path(result["text_file"])
        callback = Mock()
        result["_cleanup"] = callback
        intake.cleanup_result(result)
        intake.cleanup_result(result)
        callback.assert_called_once_with()
        self.assertFalse(path.exists())
        intake.cleanup_result({"text_file": str(self.path)})
        self.assertTrue(self.path.exists())

    def test_callback_failure_still_cleans_owned_text(self):
        result = self.pdf(["x" * 200])
        path = Path(result["text_file"])
        result["_cleanup"] = Mock(side_effect=RuntimeError("closed database"))
        intake.cleanup_result(result)
        self.assertFalse(path.exists())


class ScaledIntakeServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.002)
        self.app.processEvents()
        self.assertTrue(predicate())

    def test_generic_run_transfers_accepted_result_ownership_to_caller(self):
        service = intake.IntakeService()
        received = []
        service.completed.connect(received.append)
        with patch.object(intake, "MAX_TEXT", 32):
            self.assertTrue(service.run(lambda argument, cancel: intake._result("web", "A", argument, cancel=cancel),
                                        "x" * 100))
            self.wait_for(lambda: bool(received))
            self.wait_for(lambda: not intake._ACTIVE_THREADS)
        path = Path(received[0]["text_file"])
        self.assertTrue(path.exists())
        self.assertFalse(service.busy)
        intake.cleanup_result(received[0])
        self.assertFalse(path.exists())
        service.shutdown()

    def test_canceled_finished_operation_discards_result_and_callback(self):
        service = intake.IntakeService()
        received, paths = [], []
        service.completed.connect(received.append)
        started, release = threading.Event(), threading.Event()
        callback = Mock()

        def operation(argument, cancel):
            result = intake._result("pdf", "A", "x" * 100)
            paths.append(Path(result["text_file"]))
            result["_cleanup"] = callback
            started.set()
            release.wait(2)
            return result

        with patch.object(intake, "MAX_TEXT", 32):
            self.assertTrue(service.run(operation, None))
            self.wait_for(started.is_set)
            service.cancel()
            release.set()
            self.wait_for(lambda: not intake._ACTIVE_THREADS)
        self.assertFalse(received)
        self.assertFalse(paths[0].exists())
        callback.assert_called_once_with()
        service.shutdown()

    def test_result_without_a_connected_consumer_is_cleaned(self):
        service = intake.IntakeService()
        paths = []

        def operation(argument, cancel):
            result = intake._result("pdf", "A", "x" * 100)
            paths.append(Path(result["text_file"]))
            return result

        with patch.object(intake, "MAX_TEXT", 32):
            self.assertTrue(service.run(operation, None))
            self.wait_for(lambda: bool(paths) and not intake._ACTIVE_THREADS)
        self.assertFalse(paths[0].exists())
        service.shutdown()

    def test_exit_before_gui_delivery_cleans_prepared_result(self):
        callback = Mock()
        worker = intake._IntakeThread(1, lambda argument, cancel: {'_cleanup': callback}, None)
        intake._ACTIVE_THREADS.add(worker)
        worker.start()
        self.assertTrue(worker.wait(1000))
        self.assertIsNotNone(worker.pending_result)
        intake._finish_at_exit()
        callback.assert_called_once_with()
        self.assertIsNone(worker.pending_result)
        worker.release()


if __name__ == "__main__":
    unittest.main()
