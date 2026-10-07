"""Isolated PDFium extraction/rendering and optional local Tesseract OCR.

PDFium is not thread-safe. Every operation runs in a fresh spawned process;
native handles and PDF pages never cross the process boundary. Text messages
are bounded, and cancellation also stops an OCR executable owned by the worker.
"""
from __future__ import annotations

import importlib.util
import codecs
import math
import multiprocessing
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

TEXT_CHUNK = 32_768
MAX_RENDER_PIXELS = 12_000_000
OCR_PAGE_TIMEOUT = 90
POLL_INTERVAL = 0.05


class PDFEngineError(ValueError):
    """An extraction error safe to display without native exception details."""


class PDFEngineCanceled(Exception):
    pass


class PDFEngineCompatibilityError(PDFEngineError):
    """A native text-object limit requires the compatibility reader."""


def pdfium_available():
    # Do not load/initialize the native library inside a GUI worker thread.
    try:
        return importlib.util.find_spec("pypdfium2") is not None
    except (ImportError, ValueError):
        return False


def available_engines():
    return ("pdfium", "pypdf") if pdfium_available() else ("pypdf",)


def find_tesseract(executable=""):
    """Find an explicitly configured executable, PATH, or Windows install."""
    configured = str(executable or os.environ.get("TESSERACT_CMD", "")).strip()
    if configured:
        candidate = shutil.which(configured)
        if candidate:
            return candidate
        path = Path(configured).expanduser()
        return str(path.resolve()) if path.is_file() else ""
    candidate = shutil.which("tesseract")
    if candidate:
        return candidate
    if os.name == "nt":
        for directory in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if directory:
                path = Path(directory) / "Tesseract-OCR" / "tesseract.exe"
                if path.is_file():
                    return str(path)
    return ""


def ocr_available(tesseract_path=""):
    return bool(find_tesseract(tesseract_path))


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise PDFEngineCanceled()


def _normalized_chunks(chunks):
    """Normalize PDF text without allocating an entire page's text at once."""
    buffer = []
    length = 0
    started = False
    pending_space = False
    newlines = 0
    previous_cr = False
    for chunk in chunks:
        for character in chunk:
            if character == "\n" and previous_cr:
                previous_cr = False
                continue
            previous_cr = character == "\r"
            if previous_cr:
                character = "\n"
            if character == "\n":
                if started:
                    newlines = min(2, newlines + 1)
                pending_space = False
                continue
            if character in " \t\u00a0":
                if started and not newlines:
                    pending_space = True
                continue
            if not character.isprintable():
                continue
            prefix = "\n" * newlines if newlines else (" " if pending_space else "")
            buffer.append(prefix + character)
            length += len(prefix) + 1
            newlines, pending_space, started = 0, False, True
            if length >= TEXT_CHUNK:
                yield "".join(buffer)
                buffer, length = [], 0
    if buffer:
        yield "".join(buffer)


def _page_text_chunks(text_page, pdfium, cancel):
    """Preserve full Unicode while bounding text allocations on unusual pages.

    PDFium's range API is UCS-2 and truncates non-BMP characters. The bounded
    API supports Unicode and is efficient for ordinary pages; very large pages
    use the raw Unicode code points in small batches instead.
    """
    count = text_page.count_chars()
    if count <= TEXT_CHUNK:
        _check_cancel(cancel)
        text = text_page.get_text_bounded(left=-1e30, bottom=-1e30, right=1e30, top=1e30)
        for offset in range(0, len(text), TEXT_CHUNK):
            _check_cancel(cancel)
            yield text[offset:offset + TEXT_CHUNK]
        return
    get_unicode = pdfium.raw.FPDFText_GetUnicode
    # Some fonts expose non-BMP characters as two internal surrogate entries;
    # keep decoder state across batches rather than dropping a split pair.
    decoder = codecs.getincrementaldecoder("utf-16-le")(errors="ignore")
    for offset in range(0, count, TEXT_CHUNK):
        _check_cancel(cancel)
        points = (get_unicode(text_page, index) for index in range(offset, min(count, offset + TEXT_CHUNK)))
        encoded = "".join(chr(point) for point in points if 0 < point <= 0x10FFFF).encode("utf-16-le", errors="surrogatepass")
        yield decoder.decode(encoded)
    final = decoder.decode(b"", final=True)
    if final:
        yield final


def _check_native_text_limit(page, text_page, pdfium, cancel):
    # PDFium 5.14 can silently cap one exceptionally long text object at 65534
    # UTF-16 units. Inspect object lengths only on unusually large text pages;
    # ordinary pages retain the fast native path. A saturated object is never
    # accepted as complete text, including when other objects share the page.
    if text_page.count_chars() < 65534:
        return
    for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_TEXT], textpage=text_page):
        _check_cancel(cancel)
        size = pdfium.raw.FPDFTextObj_GetText(obj, text_page, None, 0)
        if size >= 131070:
            raise PDFEngineCompatibilityError("This PDF contains an unusually long text object. Choose the compatibility PDF reader with OCR turned off to preserve all its text.")


def _validate_source(path, max_bytes):
    try:
        with open(path, "rb") as source:
            if os.fstat(source.fileno()).st_size > max_bytes:
                raise PDFEngineError("Choose a PDF up to 500 MB, or split it into smaller documents.")
            if source.read(5) != b"%PDF-":
                raise PDFEngineError("This file does not appear to be a valid PDF.")
    except OSError:
        raise PDFEngineError("The PDF could not be opened. Check that the file still exists and is readable.") from None


def _render_page(page, target, dpi=200, max_pixels=MAX_RENDER_PIXELS):
    width, height = page.get_size()
    if width <= 0 or height <= 0 or not math.isfinite(width * height):
        raise PDFEngineError("This PDF page has invalid dimensions and could not be rendered.")
    scale = min(float(dpi) / 72, math.sqrt(max_pixels / (width * height)),
                4096 / max(width, height))
    bitmap = page.render(scale=scale)
    try:
        image = bitmap.to_pil()
        try:
            image.save(target, format="PNG")
        finally:
            image.close()
    finally:
        bitmap.close()


def _ocr_page(page, cancel, executable, language, work_directory=None):
    with tempfile.TemporaryDirectory(prefix="pixel-pet-ocr-", dir=work_directory) as directory:
        image_path = Path(directory) / "page.png"
        output = Path(directory) / "recognized"
        _check_cancel(cancel)
        _render_page(page, image_path)
        _check_cancel(cancel)
        # No shell, no stdout pipe to fill, and no console window on Windows.
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        with (Path(directory) / "errors.txt").open("wb") as errors:
            process = subprocess.Popen([executable, str(image_path), str(output), "-l", language,
                                        "--psm", "3"], stdout=subprocess.DEVNULL,
                                       stderr=errors, **options)
            deadline = time.monotonic() + OCR_PAGE_TIMEOUT
            try:
                while process.poll() is None:
                    _check_cancel(cancel)
                    if time.monotonic() >= deadline:
                        raise PDFEngineError("OCR took too long on a page. Try a clearer scan or import fewer pages.")
                    time.sleep(POLL_INTERVAL)
                _check_cancel(cancel)
                if process.returncode:
                    raise PDFEngineError("Tesseract could not read this page. Check the installed OCR language files and the scan quality.")
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)
        try:
            with output.with_suffix(".txt").open("r", encoding="utf-8", errors="replace") as handle:
                while True:
                    _check_cancel(cancel)
                    text = handle.read(TEXT_CHUNK)
                    if not text:
                        break
                    yield text
        except OSError:
            raise PDFEngineError("Tesseract did not produce readable text for this page.") from None


def _pdf_worker(connection, cancel, path, options):
    """Spawn entry point: PDFium exists only inside this process."""
    document = None
    try:
        import pypdfium2 as pdfium
        _check_cancel(cancel)
        _validate_source(path, options["max_bytes"])
        try:
            document = pdfium.PdfDocument(path)
        except pdfium.PdfiumError as exc:
            if getattr(exc, "err_code", None) == 4:
                raise PDFEngineError("This PDF is encrypted. Save an unlocked copy before importing it.") from None
            raise PDFEngineError("The PDF could not be read. Try opening and exporting it as a new PDF.") from None
        pages = len(document)
        # A malicious metadata field can be as large as the source PDF. Check
        # its native UTF-16 byte count before allocating or sending it over IPC.
        title_size = pdfium.raw.FPDF_GetMetaText(document, b"Title", None, 0)
        title = (document.get_metadata_value("Title")[:100] if 0 < title_size <= 4096 else "") or Path(path).stem
        connection.send(("metadata", {"title": title, "pages": pages, "engine": "pdfium"}))
        if options.get("preview"):
            index = options["page"]
            if index < 0 or index >= pages:
                raise PDFEngineError("Choose a page within this PDF's page count.")
            page = document[index]
            try:
                _render_page(page, options["preview"], dpi=100, max_pixels=2_000_000)
            finally:
                page.close()
            _check_cancel(cancel)
            connection.send(("done", {"pages": pages}))
            return
        ocr_pages = 0
        for index in range(pages):
            _check_cancel(cancel)
            page = document[index]
            try:
                text_page = page.get_textpage()
                written = False
                try:
                    _check_native_text_limit(page, text_page, pdfium, cancel)
                    for text in _normalized_chunks(_page_text_chunks(text_page, pdfium, cancel)):
                        _check_cancel(cancel)
                        connection.send(("text", index + 1, text))
                        written = True
                finally:
                    text_page.close()
                if not written and options["ocr"]:
                    for text in _normalized_chunks(_ocr_page(page, cancel, options["tesseract"], options["language"],
                                                            options["work_directory"])):
                        _check_cancel(cancel)
                        connection.send(("text", index + 1, text))
                        written = True
                    if written:
                        ocr_pages += 1
            finally:
                page.close()
        _check_cancel(cancel)
        connection.send(("done", {"ocr_pages": ocr_pages}))
    except PDFEngineCanceled:
        pass
    except PDFEngineCompatibilityError as exc:
        connection.send(("compatibility", str(exc)))
    except PDFEngineError as exc:
        connection.send(("error", str(exc)))
    except MemoryError:
        connection.send(("error", "There is not enough memory to read this PDF. Close other apps or split the document and try again."))
    except ImportError:
        connection.send(("error", "The native PDF reader or image support is missing. Reinstall the desktop dependencies."))
    except Exception:
        # Native messages can include private paths and arbitrary document data.
        connection.send(("error", "The PDF could not be read. Try opening and exporting it as a new PDF."))
    finally:
        if document is not None:
            document.close()
        connection.close()


def _stop_worker(process, stop):
    stop.set()
    process.join(timeout=0.2)
    if process.is_alive():
        # Stop a child OCR executable even if a native call prevented cooperative
        # cancellation. psutil is already a desktop dependency.
        try:
            import psutil
            children = psutil.Process(process.pid).children(recursive=True)
            for child in children:
                try:
                    child.terminate()
                except psutil.Error:
                    pass
            _, alive = psutil.wait_procs(children, timeout=0.2)
            for child in alive:
                try:
                    child.kill()
                except psutil.Error:
                    pass
        except Exception:
            pass
        process.terminate()
        process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join(timeout=1)
    process.close()


def stream_pdf(path, cancel=None, *, ocr=False, ocr_language="eng", tesseract_path="",
               max_bytes=500 * 1024 * 1024, preview_path="", page=0):
    """Yield metadata, bounded page text, and completion messages from a worker."""
    _check_cancel(cancel)
    language = str(ocr_language)
    if not re.fullmatch(r"[A-Za-z0-9_+-]{1,64}", language):
        raise PDFEngineError("Use an installed OCR language code such as eng or eng+fra.")
    executable = find_tesseract(tesseract_path) if ocr else ""
    if ocr and not executable:
        raise PDFEngineError("Install Tesseract OCR and its language files, then add it to PATH or choose its executable in settings.")
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    stop = context.Event()
    # The parent owns the directory so cancellation/native crashes cannot leave
    # rendered private pages or recognized text behind in the system temp folder.
    work_directory = tempfile.TemporaryDirectory(prefix="pixel-pet-pdf-")
    options = {"ocr": bool(ocr), "language": language, "tesseract": executable,
               "max_bytes": int(max_bytes), "preview": str(preview_path), "page": int(page),
               "work_directory": work_directory.name}
    process = context.Process(target=_pdf_worker, args=(send, stop, str(Path(path).resolve()), options),
                              name="Jeffery PDF reader", daemon=True)
    started = False
    finished = False
    try:
        process.start()
        started = True
        send.close()
        while True:
            _check_cancel(cancel)
            if receive.poll(POLL_INTERVAL):
                try:
                    message = receive.recv()
                except EOFError:
                    break
                _check_cancel(cancel)
                if message[0] == "error":
                    raise PDFEngineError(message[1])
                if message[0] == "compatibility":
                    raise PDFEngineCompatibilityError(message[1])
                yield message
                if message[0] == "done":
                    finished = True
                    break
            elif not process.is_alive():
                # A final message may be queued just as the process exits.
                if not receive.poll():
                    break
        if not finished:
            _check_cancel(cancel)
            raise PDFEngineError("The PDF reader stopped unexpectedly. Try exporting the PDF again or choose the compatibility reader.")
    finally:
        receive.close()
        send.close()
        if started:
            _stop_worker(process, stop)
        else:
            process.close()
        work_directory.cleanup()


def render_pdf_preview(path, page=0, cancel=None):
    """Return bounded PNG bytes for one page; callers run this off the GUI thread."""
    descriptor, target = tempfile.mkstemp(prefix="pixel-pet-preview-", suffix=".png")
    os.close(descriptor)
    try:
        for _ in stream_pdf(path, cancel, preview_path=target, page=page):
            pass
        return Path(target).read_bytes()
    finally:
        try:
            os.unlink(target)
        except FileNotFoundError:
            pass
