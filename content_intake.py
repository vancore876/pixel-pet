"""Bounded, asynchronous PDF and public website imports for Jeffery's notebook.

Imported text is source material. It is never executed or treated as instructions.
The service does not save notes or change reminder schedules; callers review/save
its plain-text sections. Requests use normal verified TLS and system proxy rules.
"""
from __future__ import annotations

import atexit
import io
import http.client
import ipaddress
from html.parser import HTMLParser
from pathlib import Path
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from PySide6.QtCore import QObject, QThread, Signal, Slot

MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_PDF_PAGES = 120
MAX_WEB_BYTES = 2 * 1024 * 1024
MAX_TEXT = 120_000
SECTION_SIZE = 5500
NETWORK_TIMEOUT = 12
MAX_REDIRECTS = 4
SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"


class IntakeError(ValueError):
    """An error safe to display to the user."""


class _Canceled(Exception):
    pass


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise _Canceled()


def _is_public_address(value):
    address = ipaddress.ip_address(value.split("%", 1)[0])
    return address.is_global and not (address.is_multicast or address.is_reserved or address.is_unspecified)


def normalize_text(text):
    """Keep line/paragraph boundaries while removing invisible control bytes."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    text = "".join(c for c in text if c.isprintable() or c in "\n\t")
    text = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def note_sections(title, text, limit=SECTION_SIZE):
    """Split at paragraph/line/word boundaries, retaining all nonblank text."""
    if limit < 80:
        raise ValueError("Section size must be at least 80 characters.")
    text = normalize_text(text)
    parts = []
    while text:
        if len(text) <= limit:
            parts.append(text)
            break
        cut = text.rfind("\n\n", limit // 2, limit + 1)
        if cut < 0:
            cut = text.rfind("\n", limit // 2, limit + 1)
        if cut < 0:
            cut = text.rfind(" ", limit // 2, limit + 1)
        if cut < 0:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    count = len(parts)
    return [{"title": (title[:80] + (f" · {i + 1}/{count}" if count > 1 else ""))[:100],
             "body": part} for i, part in enumerate(parts)]


def _result(kind, title, text, url="", sources=None, truncated=False, **extra):
    text = normalize_text(text)
    truncated = truncated or len(text) > MAX_TEXT
    text = text[:MAX_TEXT].rstrip()
    title = normalize_text(str(title)).replace("\n", " ")[:100]
    return {"kind": kind, "title": title, "text": text, "url": url,
            "sources": sources or [], "sections": note_sections(title, text),
            "truncated": truncated, **extra}


def extract_pdf(path, cancel=None):
    """Extract chosen PDF text without OCR, executing attachments, or saving it."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    path = Path(path)
    if path.suffix.casefold() != ".pdf":
        raise IntakeError("Choose a PDF file (.pdf).")
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_PDF_BYTES + 1)
    except OSError:
        raise IntakeError("The PDF could not be opened. Check that the file still exists and is readable.") from None
    if len(raw) > MAX_PDF_BYTES:
        raise IntakeError("Choose a PDF smaller than 12 MB, or split it into smaller documents.")
    if not raw.startswith(b"%PDF-"):
        raise IntakeError("This file does not appear to be a valid PDF.")
    _check_cancel(cancel)
    try:
        reader = PdfReader(io.BytesIO(raw), strict=False)
        if reader.is_encrypted:
            raise IntakeError("This PDF is encrypted. Save an unlocked copy before importing it.")
        pages = len(reader.pages)
        if pages > MAX_PDF_PAGES:
            raise IntakeError("This PDF has more than 120 pages. Import a shorter document or split it first.")
        text, length, truncated = [], 0, False
        for index, page in enumerate(reader.pages):
            _check_cancel(cancel)
            content = normalize_text(page.extract_text() or "")
            if not content:
                continue
            section = f"Page {index + 1}\n{content}"
            remaining = MAX_TEXT - length - (2 if text else 0)
            if len(section) > remaining:
                text.append(section[:max(0, remaining)])
                truncated = True
                break
            text.append(section)
            length += len(section) + (2 if len(text) > 1 else 0)
        if not text:
            raise IntakeError("No selectable text was found. This may be a scanned PDF; run OCR and import the text-enabled copy.")
        metadata = reader.metadata
        title = str(metadata.title) if metadata and metadata.title else path.stem
    except IntakeError:
        raise
    except (PdfReadError, ValueError, TypeError, KeyError, OSError, OverflowError, RecursionError):
        raise IntakeError("The PDF could not be read. Try opening and exporting it as a new PDF.") from None
    return _result("pdf", title, "\n\n".join(text), truncated=truncated,
                   pages=pages, filename=path.name)


def validate_public_url(value, *, resolve=True):
    """Permit ordinary HTTP(S) public websites only, including each redirect."""
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise IntakeError("Enter a complete public website address beginning with https:// or http://.")
    value = value.strip()
    if any(ord(c) < 32 or c.isspace() for c in value) or "\\" in value:
        raise IntakeError("The website address contains invalid characters.")
    try:
        parsed = urllib.parse.urlsplit(value)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        raise IntakeError("The website address is invalid.") from None
    if parsed.scheme.casefold() not in ("https", "http") or not host:
        raise IntakeError("Only public http:// and https:// websites are supported.")
    if parsed.username is not None or parsed.password is not None:
        raise IntakeError("Use a website address without a username or password.")
    if port is not None and port != (443 if parsed.scheme.casefold() == "https" else 80):
        raise IntakeError("Use a public website on its standard HTTP or HTTPS port.")
    host = host.rstrip(".").casefold()
    if (not host or "%" in host or host == "localhost"
            or host.endswith((".localhost", ".local", ".internal", ".test", ".lan", ".home", ".localdomain"))):
        raise IntakeError("Local and private network addresses cannot be imported.")
    try:
        canonical_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise IntakeError("The website hostname is invalid.") from None
    try:
        address = ipaddress.ip_address(canonical_host)
    except ValueError:
        address = None
        # Some HTTP transports accept abbreviated, octal or hexadecimal IPv4
        # spellings (127.1, 0177.0.0.1). Check those even behind a DNS proxy.
        try:
            address = ipaddress.ip_address(socket.inet_aton(canonical_host))
        except OSError:
            pass
    if address is not None and not _is_public_address(str(address)):
        raise IntakeError("Local and private network addresses cannot be imported.")
    if address is None and "." not in canonical_host:
        raise IntakeError("Use a complete public website hostname.")
    if resolve:
        try:
            records = socket.getaddrinfo(canonical_host, port or (443 if parsed.scheme == "https" else 80),
                                         type=socket.SOCK_STREAM)
        except OSError:
            raise IntakeError("This website could not be found. Check the address and Internet connection.") from None
        if not records or any(not _is_public_address(record[4][0]) for record in records):
            raise IntakeError("Local and private network addresses cannot be imported.")
    authority = f"[{canonical_host}]" if ":" in canonical_host else canonical_host
    # Default ports are deliberately omitted so citations have stable URLs.
    path = urllib.parse.quote(parsed.path or "/", safe="/:@!$&'()*+,;=-._~%")
    query = urllib.parse.quote(parsed.query, safe="/:?@!$&'()*+,;=-._~%")
    return urllib.parse.urlunsplit((parsed.scheme.casefold(), authority, path, query, ""))


def _request_url(value):
    # Managed cloud proxies resolve public names themselves. Local machines
    # resolve and check every address; configured proxies own their egress DNS
    # policy while URL checks still reject local hostnames and literal IPs.
    url = validate_public_url(value, resolve=False)
    parsed = urllib.parse.urlsplit(url)
    proxies = urllib.request.getproxies()
    proxied = bool(proxies.get(parsed.scheme) and not urllib.request.proxy_bypass(parsed.hostname))
    return validate_public_url(url, resolve=not proxied)


def _public_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **kwargs):
    """Connect to checked addresses directly, preventing a second DNS lookup."""
    host, port = address
    records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not records or any(not _is_public_address(row[4][0]) for row in records):
        raise IntakeError("Local and private network addresses cannot be imported.")
    last_error = None
    for family, socktype, protocol, _, sockaddr in records:
        connection = socket.socket(family, socktype, protocol)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                connection.settimeout(timeout)
            if source_address:
                connection.bind(source_address)
            connection.connect(sockaddr)
            return connection
        except OSError as exc:
            last_error = exc
            connection.close()
    raise last_error or OSError("No reachable public address")


def _connection_factory(cls, proxied):
    def create(*args, **kwargs):
        connection = cls(*args, **kwargs)
        if not proxied:
            connection._create_connection = _public_connection
        return connection
    return create


class _PublicHTTP(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_connection_factory(http.client.HTTPConnection,
            req.has_proxy() or bool(req._tunnel_host)), req)


class _PublicHTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_connection_factory(http.client.HTTPSConnection,
            req.has_proxy() or bool(req._tunnel_host)), req, context=self._context)


class _PublicRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, cancel):
        self.count, self.cancel = 0, cancel
        self.started = time.monotonic()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_cancel(self.cancel)
        if time.monotonic() - self.started > NETWORK_TIMEOUT * 2:
            raise IntakeError("This website took too long to respond. Try again later.")
        self.count += 1
        if self.count > MAX_REDIRECTS:
            raise IntakeError("This website redirects too many times. Try its final page address.")
        target = _request_url(urllib.parse.urljoin(req.full_url, newurl))
        if req.full_url.startswith("https:") and target.startswith("http:"):
            raise IntakeError("This website redirected to an insecure page. Use its HTTPS page address.")
        return super().redirect_request(req, fp, code, msg, headers, target)


def fetch_public_bytes(url, cancel=None):
    """Read a bounded response with verified TLS, DNS checks and normal proxies."""
    _check_cancel(cancel)
    url = _request_url(url)
    opener = urllib.request.build_opener(_PublicRedirect(cancel), _PublicHTTP(),
                                        _PublicHTTPS(context=ssl.create_default_context()))
    request = urllib.request.Request(url, headers={"User-Agent": "JefferyCompanion/7.0 (personal reading)",
        "Accept": "text/html,text/plain;q=0.9", "Accept-Encoding": "identity"})
    try:
        started = time.monotonic()
        with opener.open(request, timeout=NETWORK_TIMEOUT) as response:
            final_url = _request_url(response.geturl())
            content_type = response.headers.get_content_type()
            if content_type not in ("text/html", "application/xhtml+xml", "text/plain"):
                raise IntakeError("This address is not a readable webpage. Download PDFs and use Import PDF instead.")
            size = response.headers.get("Content-Length", "")
            if size.isdigit() and int(size) > MAX_WEB_BYTES:
                raise IntakeError("This webpage is larger than 2 MB. Use a smaller article page.")
            payload = bytearray()
            while len(payload) <= MAX_WEB_BYTES:
                _check_cancel(cancel)
                if time.monotonic() - started > NETWORK_TIMEOUT * 2:
                    raise IntakeError("This website took too long to respond. Try again later.")
                piece = response.read(min(64 * 1024, MAX_WEB_BYTES + 1 - len(payload)))
                if not piece:
                    break
                payload.extend(piece)
            if len(payload) > MAX_WEB_BYTES:
                raise IntakeError("This webpage is larger than 2 MB. Use a smaller article page.")
            encoding = response.headers.get("Content-Encoding", "identity").casefold().strip()
            if encoding in ("gzip", "deflate"):
                decompressor = zlib.decompressobj(31 if encoding == "gzip" else zlib.MAX_WBITS)
                payload = decompressor.decompress(bytes(payload), MAX_WEB_BYTES + 1)
                if len(payload) > MAX_WEB_BYTES or decompressor.unconsumed_tail or not decompressor.eof:
                    raise IntakeError("This compressed webpage is too large or incomplete.")
            elif encoding not in ("", "identity"):
                raise IntakeError("This webpage uses an unsupported encoding. Try another page.")
            charset = response.headers.get_content_charset() or "utf-8"
            try:
                source = bytes(payload).decode(charset, errors="replace")
            except LookupError:
                source = bytes(payload).decode("utf-8", errors="replace")
            return final_url, content_type, source
    except IntakeError:
        raise
    except urllib.error.HTTPError as exc:
        labels = {401: "This page requires sign-in.", 403: "This website does not allow automated reading.",
                  404: "This webpage was not found.", 429: "This website is busy. Wait a little and try again."}
        raise IntakeError(labels.get(exc.code, f"The website returned HTTP {exc.code}. Try a different public page.")) from None
    except urllib.error.URLError as exc:
        if "Tunnel connection failed: 403" in str(exc.reason):
            raise IntakeError("Your network proxy blocked this website. Allow this site's domain or try another public source.") from None
        raise IntakeError("Could not read this website. Check your Internet connection and trusted HTTPS certificates.") from None
    except (OSError, ssl.SSLError, zlib.error):
        raise IntakeError("Could not read this website. Check your Internet connection and trusted HTTPS certificates.") from None


class ReadableHTML(HTMLParser):
    """A small text reader; it does not load images, links or execute JavaScript."""
    BLOCKS = {"article", "section", "main", "div", "p", "h1", "h2", "h3", "h4", "h5", "h6",
              "li", "ul", "ol", "blockquote", "pre", "table", "tr"}
    OMIT = {"script", "style", "nav", "footer", "header", "aside", "form", "noscript", "svg", "math", "template"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.output, self.title_parts = [], [], []

    @property
    def hidden(self):
        return any(row[1] for row in self.stack)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        style = re.sub(r"\s+", "", attrs.get("style", "")).casefold()
        hidden = (tag in self.OMIT or "hidden" in attrs or attrs.get("aria-hidden", "").casefold() == "true"
                  or "display:none" in style or "visibility:hidden" in style
                  or attrs.get("role", "").casefold() in ("navigation", "banner", "contentinfo"))
        parent_hidden = self.hidden
        if tag not in self.VOID:
            self.stack.append((tag, hidden))
        if not parent_hidden and not hidden:
            if tag in self.BLOCKS or tag == "hr":
                self.output.append("\n\n")
            elif tag == "br":
                self.output.append("\n")
            elif tag in ("td", "th"):
                self.output.append(" | ")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not self.hidden and tag in self.BLOCKS:
            self.output.append("\n\n")
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.hidden:
            return
        if any(tag == "title" for tag, _ in self.stack):
            self.title_parts.append(data)
        elif not any(tag == "head" for tag, _ in self.stack):
            self.output.append(data)

    def read(self, source):
        self.feed(source)
        self.close()
        return normalize_text("".join(self.title_parts)), normalize_text("".join(self.output))


def extract_web(url, cancel=None):
    final_url, mime, source = fetch_public_bytes(url, cancel)
    _check_cancel(cancel)
    title, text = ReadableHTML().read(source) if mime != "text/plain" else ("", normalize_text(source))
    title = title or urllib.parse.urlsplit(final_url).hostname
    if not text:
        raise IntakeError("No readable text was found. This page may need sign-in or JavaScript; choose a public article instead.")
    return _result("web", title, text, final_url, [{"title": title, "url": final_url}])


class _SearchHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results, self.active, self.capture, self.parts = [], None, None, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get("class", "").split())
        if tag == "a" and classes.intersection({"result__a", "result-link"}):
            self.finish_capture()
            if self.active:
                self.results.append(self.active)
            self.active = {"title": "", "url": attrs.get("href", ""), "snippet": ""}
            self.capture, self.parts = "title", []
        elif classes.intersection({"result__snippet", "result-snippet"}) and self.active:
            self.finish_capture()
            self.capture, self.parts = "snippet", []

    def handle_endtag(self, tag):
        if (self.capture == "title" and tag == "a") or (self.capture == "snippet" and tag in ("a", "td", "div")):
            self.finish_capture()

    def handle_data(self, data):
        if self.capture:
            self.parts.append(data)

    def finish_capture(self):
        if self.capture and self.active:
            self.active[self.capture] = normalize_text("".join(self.parts))
        self.capture, self.parts = None, []

    def read(self, source):
        self.feed(source)
        self.close()
        self.finish_capture()
        if self.active:
            self.results.append(self.active)
        results, seen = [], set()
        for row in self.results:
            raw = urllib.parse.urljoin(SEARCH_ENDPOINT, row["url"])
            parsed = urllib.parse.urlsplit(raw)
            if parsed.hostname and parsed.hostname.endswith("duckduckgo.com"):
                raw = urllib.parse.parse_qs(parsed.query).get("uddg", [raw])[0]
            try:
                row["url"] = validate_public_url(raw, resolve=False)
            except IntakeError:
                continue
            if not row["title"] or row["url"] in seen or "duckduckgo.com" == urllib.parse.urlsplit(row["url"]).hostname:
                continue
            seen.add(row["url"])
            results.append(row)
            if len(results) == 5:
                break
        return results


def search_public_web(query, cancel=None):
    query = normalize_text(query).replace("\n", " ")[:500]
    if not query:
        raise IntakeError("Type a question or search phrase first.")
    url = SEARCH_ENDPOINT + "?" + urllib.parse.urlencode({"q": query})
    _, _, source = fetch_public_bytes(url, cancel)
    _check_cancel(cancel)
    results = _SearchHTML().read(source)
    if not results:
        raise IntakeError("No readable search results were available. Try a website address, another search, or ordinary chat.")
    text = "\n\n".join(f"[{i + 1}] {row['title']}\n{row['url']}\n{row['snippet']}" for i, row in enumerate(results))
    return _result("search", "Web search: " + query[:75], text, url,
                   [{"title": row["title"], "url": row["url"]} for row in results],
                   results=results, query=query, snippets_only=True)


# Detached QThreads remain alive until their work ends even if a window closes.
# Cancel is cooperative; network reads still have their normal bounded timeout.
_ACTIVE_THREADS = set()


def _finish_at_exit():
    # Cancel returns promptly while the GUI is active. Before Python/Qt tear
    # down, finish bounded I/O instead of destroying a running QThread.
    workers = list(_ACTIVE_THREADS)
    for worker in workers:
        worker.cancel_event.set()
    for worker in workers:
        try:
            while not worker.wait(100):
                pass
        except RuntimeError:
            pass  # Qt already released this finished worker.


atexit.register(_finish_at_exit)


class _IntakeThread(QThread):
    ready = Signal(object)
    error = Signal(object)

    def __init__(self, generation, operation, argument):
        super().__init__()
        self.generation, self.operation, self.argument = generation, operation, argument
        self.cancel_event = threading.Event()

    def run(self):
        try:
            result = self.operation(self.argument, self.cancel_event)
            _check_cancel(self.cancel_event)
            self.ready.emit((self.generation, result))
        except _Canceled:
            pass
        except IntakeError as exc:
            if not self.cancel_event.is_set():
                self.error.emit((self.generation, str(exc)))
        except Exception:
            if not self.cancel_event.is_set():
                self.error.emit((self.generation, "This import could not be completed. Try a smaller document or a different public page."))

    @Slot()
    def release(self):
        _ACTIVE_THREADS.discard(self)
        self.deleteLater()


class IntakeService(QObject):
    """One asynchronous read at a time; cancel discards pending/stale results."""
    completed = Signal(object)
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._generation = 0
        self._current = None
        self._closed = False

    @property
    def busy(self):
        return self._current is not None

    def import_pdf(self, path):
        return self._start(extract_pdf, path)

    def fetch_url(self, url):
        return self._start(extract_web, url)

    def search(self, query):
        return self._start(search_public_web, query)

    def _start(self, operation, argument):
        if self.busy or self._closed:
            return False
        self._generation += 1
        worker = _IntakeThread(self._generation, operation, argument)
        worker.ready.connect(self._received)
        worker.error.connect(self._failed)
        worker.finished.connect(self._cleanup)
        worker.finished.connect(worker.release)
        _ACTIVE_THREADS.add(worker)
        self._current = worker
        self.busy_changed.emit(True)
        worker.start()
        return True

    @Slot(object)
    def _received(self, payload):
        generation, result = payload
        if not self._closed and self._current is not None and generation == self._generation:
            self._current = None
            self.busy_changed.emit(False)
            self.completed.emit(result)

    @Slot(object)
    def _failed(self, payload):
        generation, message = payload
        if not self._closed and self._current is not None and generation == self._generation:
            self._current = None
            self.busy_changed.emit(False)
            self.failed.emit(message)

    @Slot()
    def _cleanup(self):
        worker = self.sender()
        if worker is self._current:
            self._current = None
            self.busy_changed.emit(False)

    def cancel(self):
        self._generation += 1
        worker, self._current = self._current, None
        if worker is not None:
            worker.cancel_event.set()
            self.busy_changed.emit(False)

    def shutdown(self):
        self._closed = True
        self.cancel()
        # Calls return immediately; a closed window receives no subsequent result.
        # The thread is intentionally detached so an in-flight read is not killed.

    stop = shutdown
