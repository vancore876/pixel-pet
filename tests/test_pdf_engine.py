"""Native process isolation, bounded extraction, preview rendering and OCR."""
import hashlib
import io
import multiprocessing
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import zlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject
import content_intake as intake
import pdf_engine
from test_content_intake import create_pdf


def _slow_worker(connection, cancel, path, options):
    # A genuine spawned process is used so cancellation exercises IPC cleanup.
    connection.send(("metadata", {"title": "Slow", "pages": 1}))
    connection.send(("text", 1, "A first page."))
    while not cancel.is_set():
        time.sleep(0.01)
    connection.close()


def _crash_worker(connection, cancel, path, options):
    # A native crash must produce a safe error rather than a silent partial save.
    connection.send(("metadata", {"title": "Crash", "pages": 1}))
    connection.send(("text", 1, "Partial text"))
    os._exit(13)


def _temp_crash_worker(connection, cancel, path, options):
    (Path(options["work_directory"]) / "private-scan.png").write_bytes(b"private")
    os._exit(13)


def _owned_child_worker(connection, cancel, path, options):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    connection.send(("metadata", {"title": "OCR child", "pages": 1}))
    connection.send(("text", 1, str(child.pid)))
    # Simulate a native call which cannot observe the cancellation event.
    time.sleep(60)


class EngineHelpersTests(unittest.TestCase):
    def test_normalization_retains_boundaries_across_chunks(self):
        text = "  First\t\tline.\r\n\r\n\nSecond\u00a0line.\n" + "é🙂 x " * 15000 + "last  "
        chunks = (text[index:index + 113] for index in range(0, len(text), 113))
        parts = list(pdf_engine._normalized_chunks(chunks))
        self.assertEqual("".join(parts), intake.normalize_text(text))
        self.assertTrue(all(len(part) <= pdf_engine.TEXT_CHUNK + 2 for part in parts))

    def test_unusual_pages_use_full_unicode_instead_of_ucs2_ranges(self):
        text = "漢🙂é hello"
        page = Mock(count_chars=Mock(return_value=len(text)))
        library = Mock()
        library.raw.FPDFText_GetUnicode.side_effect = lambda _, index: ord(text[index])
        with patch.object(pdf_engine, "TEXT_CHUNK", 4):
            chunks = list(pdf_engine._page_text_chunks(page, library, None))
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(chunk) <= 4 for chunk in chunks))
        page.get_text_range.assert_not_called()

    def test_unicode_surrogate_pairs_survive_batch_boundaries(self):
        points = [0xD83D, 0xDE00, 0xD83D, 0xDE00, ord("x")]
        page = Mock(count_chars=Mock(return_value=len(points)))
        library = Mock()
        library.raw.FPDFText_GetUnicode.side_effect = lambda _, index: points[index]
        with patch.object(pdf_engine, "TEXT_CHUNK", 3):
            chunks = list(pdf_engine._page_text_chunks(page, library, None))
        self.assertEqual("".join(chunks), "😀😀x")

    def test_explicit_ocr_path_does_not_silently_use_another_install(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "missing-tesseract")
            self.assertEqual(pdf_engine.find_tesseract(missing), "")
            self.assertFalse(pdf_engine.ocr_available(missing))

    def test_missing_optional_engine_falls_back_to_pypdf(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.pdf"
            create_pdf(path)
            with patch.object(pdf_engine, "pdfium_available", return_value=False):
                result = intake.extract_pdf(path)
            self.assertIn("Bring the order", result["text"])
            with patch.object(pdf_engine, "pdfium_available", return_value=False), \
                    self.assertRaisesRegex(intake.IntakeError, "native PDF reader"):
                intake.extract_pdf(path, ocr=True)

    def test_ocr_language_and_executable_validation_happen_before_spawn(self):
        with patch.object(pdf_engine.multiprocessing, "get_context") as spawn:
            with self.assertRaisesRegex(pdf_engine.PDFEngineError, "language code"):
                list(pdf_engine.stream_pdf("unused.pdf", ocr=True, ocr_language="eng; commands"))
            with patch.object(pdf_engine, "find_tesseract", return_value=""), \
                    self.assertRaisesRegex(pdf_engine.PDFEngineError, "Install Tesseract"):
                list(pdf_engine.stream_pdf("unused.pdf", ocr=True))
            spawn.assert_not_called()


@unittest.skipUnless(pdf_engine.pdfium_available(), "Optional PDFium is not installed")
class NativePDFTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "tasks.pdf"
        self.results = []

    def tearDown(self):
        for result in self.results:
            intake.cleanup_result(result)
        self.temp.cleanup()

    def test_native_engine_preserves_all_pages_beyond_preview(self):
        create_pdf(self.path, tuple(f"Task {index}: appointment on Friday." for index in range(50)))
        with patch.object(intake, "MAX_TEXT", 80):
            result = intake.extract_pdf(self.path, engine="pdfium")
        self.results.append(result)
        self.assertEqual(result["engine"], "pdfium")
        self.assertEqual(result["pages"], 50)
        full = Path(result["text_file"]).read_text(encoding="utf-8")
        self.assertIn("Page 50\nTask 49: appointment on Friday.", full)
        self.assertLessEqual(len(result["text"]), 80)
        self.assertEqual(result["character_count"], len(full))
        self.assertEqual(result["full_text_sha256"], hashlib.sha256(full.encode()).hexdigest())
        self.assertFalse(result["truncated"])

    def test_oversized_metadata_uses_filename_without_large_ipc_messages(self):
        create_pdf(self.path)
        writer = PdfWriter(clone_from=str(self.path))
        writer.add_metadata({"/Title": "x" * 10000})
        writer.write(self.path)
        result = intake.extract_pdf(self.path, engine="pdfium")
        self.results.append(result)
        self.assertEqual(result["title"], "tasks")
        self.assertIn("Bring the order", result["text"])

    def test_native_text_keeps_non_bmp_characters(self):
        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=300)
        cmap = DecodedStreamObject()
        cmap.set_data(b"/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
                      b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
                      b"/CMapName /TestUnicode def\n/CMapType 2 def\n1 begincodespacerange\n"
                      b"<00> <FF>\nendcodespacerange\n2 beginbfchar\n"
                      b"<41> <D83DDE00>\n<42> <6F22>\nendbfchar\nendcmap\n"
                      b"CMapName currentdict /CMap defineresource pop\nend\nend")
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/ToUnicode"): writer._add_object(cmap)})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
            DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        content = DecodedStreamObject()
        content.set_data(b"BT /F1 12 Tf 20 240 Td (AB) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(content)
        writer.write(self.path)
        result = intake.extract_pdf(self.path, engine="pdfium")
        self.results.append(result)
        self.assertIn("Page 1\n😀漢", result["text"])
        # Exercise the bounded Unicode code-point path on an unusual long page.
        count = pdf_engine.TEXT_CHUNK // 2 + 9
        content.set_data(b"BT /F1 12 Tf 20 240 Td (" + b"A" * count +
                         b") Tj 0 -20 Td (" + b"A" * count + b") Tj ET")
        writer.write(self.path)
        large = intake.extract_pdf(self.path, engine="pdfium")
        self.results.append(large)
        self.assertEqual(large["text"], "Page 1\n" + "😀" * count + "\n" + "😀" * count)
        # A single huge text object hits a PDFium engine limit. Automatic mode
        # must preserve all original text, while explicit native mode explains
        # the compatibility reader instead of returning a silent partial save.
        count = pdf_engine.TEXT_CHUNK + 9
        content.set_data(b"BT /F1 12 Tf 20 240 Td (" + b"A" * count + b") Tj ET")
        writer.write(self.path)
        before = set(intake._TEMP_TEXT_FILES)
        with self.assertRaisesRegex(intake.IntakeError, "compatibility PDF reader"):
            intake.extract_pdf(self.path, engine="pdfium")
        with patch.object(pdf_engine, "find_tesseract", return_value="unused-tesseract"), \
                self.assertRaisesRegex(intake.IntakeError, "OCR turned off"):
            intake.extract_pdf(self.path, engine="auto", ocr=True)
        self.assertEqual(set(intake._TEMP_TEXT_FILES), before)
        fallback = intake.extract_pdf(self.path, engine="auto")
        self.results.append(fallback)
        self.assertTrue(fallback["compatibility_fallback"])
        self.assertEqual(fallback["engine"], "pypdf")
        self.assertEqual(fallback["text"], "Page 1\n" + "😀" * count)

    def test_native_character_cap_stops_and_cleans_the_worker(self):
        create_pdf(self.path, ("A long paragraph of appointment information.",) * 100)
        with patch.object(intake, "MAX_DOCUMENT_CHARACTERS", 83), patch.object(intake, "MAX_TEXT", 30):
            result = intake.extract_pdf(self.path, engine="pdfium")
        self.results.append(result)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["character_count"], 83)
        self.assertFalse([child for child in multiprocessing.active_children()
                          if child.name == "Jeffery PDF reader"])

    def test_process_cancel_removes_partial_spool_and_exits_promptly(self):
        create_pdf(self.path)
        cancel = threading.Event()
        original_append = intake._TextSpool.append

        def append_then_cancel(spool, text):
            original_append(spool, text)
            if "A first page." in text:
                cancel.set()

        before = set(intake._TEMP_TEXT_FILES)
        started = time.monotonic()
        with patch.object(pdf_engine, "_pdf_worker", _slow_worker), \
                patch.object(intake._TextSpool, "append", append_then_cancel), \
                self.assertRaises(intake._Canceled):
            intake.extract_pdf(self.path, cancel, engine="pdfium")
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(set(intake._TEMP_TEXT_FILES), before)
        self.assertFalse([child for child in multiprocessing.active_children()
                          if child.name == "Jeffery PDF reader"])

    def test_cancel_stops_the_workers_owned_subprocess(self):
        import psutil
        cancel = threading.Event()
        with patch.object(pdf_engine, "_pdf_worker", _owned_child_worker):
            stream = pdf_engine.stream_pdf(self.path, cancel)
            self.assertEqual(next(stream)[0], "metadata")
            child_pid = int(next(stream)[2])
            cancel.set()
            with self.assertRaises(pdf_engine.PDFEngineCanceled):
                next(stream)
        try:
            self.assertEqual(psutil.Process(child_pid).status(), psutil.STATUS_ZOMBIE)
        except psutil.NoSuchProcess:
            pass

    def test_native_crash_does_not_return_partial_text(self):
        create_pdf(self.path)
        before = set(intake._TEMP_TEXT_FILES)
        with patch.object(pdf_engine, "_pdf_worker", _crash_worker), \
                self.assertRaisesRegex(intake.IntakeError, "stopped unexpectedly"):
            intake.extract_pdf(self.path, engine="pdfium")
        self.assertEqual(set(intake._TEMP_TEXT_FILES), before)

    def test_native_crash_removes_worker_owned_private_files(self):
        create_pdf(self.path)
        original = pdf_engine.tempfile.TemporaryDirectory
        with patch.object(pdf_engine.tempfile, "TemporaryDirectory",
                          side_effect=lambda **kwargs: original(dir=self.temp.name, **kwargs)), \
                patch.object(pdf_engine, "_pdf_worker", _temp_crash_worker), \
                self.assertRaisesRegex(pdf_engine.PDFEngineError, "stopped unexpectedly"):
            list(pdf_engine.stream_pdf(self.path))
        self.assertFalse(list(Path(self.temp.name).glob("pixel-pet-pdf-*")))

    def test_preview_is_a_bounded_png_and_rejects_missing_pages(self):
        from PIL import Image
        create_pdf(self.path)
        data = pdf_engine.render_pdf_preview(self.path)
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertLessEqual(image.width * image.height, 2_010_000)
        with self.assertRaisesRegex(pdf_engine.PDFEngineError, "page count"):
            pdf_engine.render_pdf_preview(self.path, page=10)

    @unittest.skipUnless(pdf_engine.ocr_available(), "Optional Tesseract executable is not installed")
    def test_real_scanned_pdf_ocr_keeps_page_source_and_skips_text_pages(self):
        from PIL import Image, ImageDraw, ImageFont
        writer = PdfWriter()
        # A plain bitmap-only PDF page, with no hidden selectable text.
        image = Image.new("RGB", (1200, 320), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=48)
        draw.text((40, 50), "Remember the appointment", fill="black", font=font)
        draw.text((40, 140), "Bring your ID on Friday", fill="black", font=font)
        page = writer.add_blank_page(width=600, height=160)
        bitmap = DecodedStreamObject()
        bitmap.set_data(zlib.compress(image.tobytes()))
        bitmap.update({NameObject("/Type"): NameObject("/XObject"),
                       NameObject("/Subtype"): NameObject("/Image"),
                       NameObject("/Width"): NumberObject(image.width),
                       NameObject("/Height"): NumberObject(image.height),
                       NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                       NameObject("/BitsPerComponent"): NumberObject(8),
                       NameObject("/Filter"): NameObject("/FlateDecode")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"):
            DictionaryObject({NameObject("/Im0"): writer._add_object(bitmap)})})
        content = DecodedStreamObject()
        content.set_data(b"q 600 0 0 160 0 0 cm /Im0 Do Q")
        page[NameObject("/Contents")] = writer._add_object(content)
        text_path = Path(self.temp.name) / "selectable.pdf"
        create_pdf(text_path, ("A selectable second page.",))
        writer.append(str(text_path))
        writer.write(self.path)
        with self.assertRaisesRegex(intake.IntakeError, "OCR"):
            # Restrict a copy to its first scanned page for the no-OCR error.
            scan = PdfWriter()
            scan.add_page(writer.pages[0])
            scanned_path = Path(self.temp.name) / "scan-only.pdf"
            scan.write(scanned_path)
            intake.extract_pdf(scanned_path, engine="pdfium")
        result = intake.extract_pdf(self.path, engine="pdfium", ocr=True)
        self.results.append(result)
        self.assertEqual(result["ocr_pages"], 1)
        self.assertIn("Page 1\nRemember the appointment", result["text"])
        self.assertIn("Bring your ID on Friday", result["text"])
        self.assertIn("Page 2\nA selectable second page.", result["text"])


if __name__ == "__main__":
    unittest.main()
