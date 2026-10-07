"""File-backed, immutable notebook documents with bounded pages and full-text recall.

The JSON notebook holds only document IDs and previews. Full document text lives
in SQLite in small UTF-8 chunks; imports, recall and backups never concatenate a
whole document. Reading an absent document store does not create one.
"""
from __future__ import annotations

from contextlib import contextmanager
import atexit
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import threading
import uuid

MAX_NOTEBOOK_CHARACTERS = 1_000_000_000
CHUNK_CHARS = 10_000
PREVIEW_CHARS = 10_000
EXCERPT_CHARS = 1200
_APPLICATION_ID = 0x504A4442
_SCHEMA_VERSION = 2
_GC_CHUNKS = 32
_CLEANUP_LOCK = threading.Lock()
_CLEANUP_WORKERS = {}
_ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")
_HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_QUERY_STOP_WORDS = frozenset("""a an and are as at be been but by can could did do does for
    from had has have how i in is it its me my of on or our please that the their them
    there these they this to was we were what when where which who why will with
    would you your""".split())


class DocumentError(ValueError):
    """A document error that can be displayed without exposing database details."""


class DocumentCancelled(DocumentError):
    """An import or export was canceled without committing a partial document."""


class DocumentBusy(DocumentError):
    """A document write cannot acquire its database lock immediately."""


class _CleanupState:
    def __init__(self):
        self.pending = threading.Event()
        self.stop = threading.Event()
        self.thread = None


def _stop_all_cleanup():
    with _CLEANUP_LOCK:
        states = list(_CLEANUP_WORKERS.values())
        for state in states:
            state.stop.set()
            state.pending.set()
    for state in states:
        if state.thread is not threading.current_thread():
            state.thread.join(timeout=0.5)


atexit.register(_stop_all_cleanup)


def _check_cancel(cancel):
    if cancel is None:
        return
    check = getattr(cancel, "is_set", None)
    if (check() if callable(check) else cancel() if callable(cancel) else bool(cancel)):
        raise DocumentCancelled("The document operation was canceled.")


def _identifier(value):
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise DocumentError("The notebook document ID is invalid.")
    return value


def _other_characters(value):
    if type(value) is not int or value < 0 or value > MAX_NOTEBOOK_CHARACTERS:
        raise DocumentError("The notebook character count is invalid.")
    return value


def _bounded_excerpt(text, terms):
    if len(text) <= EXCERPT_CHARS:
        return text
    folded = text.casefold()
    positions = [folded.find(term.casefold()) for term in terms]
    positions = [position for position in positions if position >= 0]
    start = max(0, min(positions) - EXCERPT_CHARS // 4) if positions else 0
    return text[start:start + EXCERPT_CHARS]


def _search_parameters(query, document_ids):
    terms = re.findall(r"[^\W_]+", query[:512], flags=re.UNICODE)[:24] if isinstance(query, str) else []
    terms = list(dict.fromkeys(term[:80] for term in terms
                               if term and term.casefold() not in _QUERY_STOP_WORDS))
    selected = None
    if document_ids is not None:
        selected = list(dict.fromkeys(_identifier(value) for value in document_ids))
        if len(selected) > 4000:
            raise DocumentError("Select fewer notebook documents for this search.")
    return terms, " OR ".join('"' + term + '"' for term in terms), selected


class DocumentStore:
    """A connection-per-operation store safe to use from background import jobs."""

    def __init__(self, path):
        self.path = Path(path)
        self._cleanup_closed = False
        if self.path.exists():
            self._schedule_cleanup()

    @contextmanager
    def _connection(self, *, write=False, busy_timeout=30_000, create=True):
        connection = None
        try:
            if not write and not self.path.exists():
                yield None
                return
            if write and create:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(self.path, timeout=busy_timeout / 1000, isolation_level=None)
            else:
                connection = sqlite3.connect(self.path.resolve().as_uri() + ("?mode=rw" if write else "?mode=ro"),
                                             uri=True, timeout=busy_timeout / 1000, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {busy_timeout}")
            connection.execute("PRAGMA foreign_keys = ON")
            if write:
                self._initialize(connection)
                connection.execute("PRAGMA journal_mode = WAL")
            else:
                connection.execute("PRAGMA query_only = ON")
                self._validate_schema(connection)
            yield connection
        except sqlite3.OperationalError as error:
            if getattr(error, "sqlite_errorcode", 0) & 255 in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise DocumentBusy("Notebook documents are busy. Try again after the current import finishes.") from None
            raise DocumentError("Notebook document storage could not be read or updated. "
                                "Keep the original files and check storage permissions.") from None
        except sqlite3.Error:
            raise DocumentError("Notebook document storage could not be read or updated. "
                                "Keep the original files and check storage permissions.") from None
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _validate_schema(connection):
        if (connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                or connection.execute("PRAGMA user_version").fetchone()[0] not in (1, _SCHEMA_VERSION)):
            raise DocumentError("This notebook document store uses an unsupported format.")

    @staticmethod
    def _active(connection, alias=""):
        if connection.execute("PRAGMA user_version").fetchone()[0] == 1:
            return "1"
        return (alias + "." if alias else "") + "deleted = 0"

    @classmethod
    def _initialize(cls, connection):
        connection.execute("BEGIN IMMEDIATE")
        try:
            application = connection.execute("PRAGMA application_id").fetchone()[0]
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if application == _APPLICATION_ID and version == _SCHEMA_VERSION:
                connection.execute("COMMIT")
                return
            if application == _APPLICATION_ID and version == 1:
                connection.execute("ALTER TABLE documents ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0, 1))")
                connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
                connection.execute("COMMIT")
                return
            if application or version or connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1").fetchone():
                raise DocumentError("This notebook document store uses an unsupported format.")
            connection.execute("""CREATE TABLE documents (
                document_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                characters INTEGER NOT NULL CHECK(characters >= 0),
                sha256 TEXT NOT NULL,
                pages INTEGER NOT NULL CHECK(pages >= 0),
                deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0, 1))
            )""")
            connection.execute("""CREATE TABLE chunks (
                document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
                page INTEGER NOT NULL CHECK(page >= 0),
                text TEXT NOT NULL,
                characters INTEGER NOT NULL CHECK(characters >= 0 AND characters <= 10000),
                UNIQUE(document_id, page)
            )""")
            connection.execute("""CREATE VIRTUAL TABLE document_text USING fts5(
                text, content='chunks', content_rowid='rowid',
                tokenize='porter unicode61 remove_diacritics 2'
            )""")
            connection.execute("""CREATE TRIGGER chunks_insert AFTER INSERT ON chunks BEGIN
                INSERT INTO document_text(rowid, text) VALUES (new.rowid, new.text);
            END""")
            connection.execute("""CREATE TRIGGER chunks_delete AFTER DELETE ON chunks BEGIN
                INSERT INTO document_text(document_text, rowid, text)
                    VALUES ('delete', old.rowid, old.text);
            END""")
            connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise

    @classmethod
    def _total(cls, connection):
        total = connection.execute("SELECT COALESCE(SUM(characters), 0) FROM documents WHERE "
                                   + cls._active(connection)).fetchone()[0]
        if type(total) is not int or not 0 <= total <= MAX_NOTEBOOK_CHARACTERS:
            raise DocumentError("The notebook document character counts are invalid.")
        return total

    def total_characters(self):
        with self._connection() as connection:
            return self._total(connection) if connection is not None else 0

    def prepare_import(self, argument, cancel=None):
        """Stream an explicitly selected UTF-8 text file into a single transaction."""
        if not isinstance(argument, dict) or not isinstance(argument.get("text_file"), (str, Path)):
            raise DocumentError("Choose an extracted UTF-8 document before saving it.")
        _check_cancel(cancel)
        other = _other_characters(argument.get("other_note_characters", 0))
        try:
            with Path(argument["text_file"]).open("r", encoding="utf-8", newline="") as handle:
                return self._prepare_chunks(iter(lambda: handle.read(CHUNK_CHARS), ""),
                                            argument.get("title", ""), other, cancel)
        except UnicodeError:
            raise DocumentError("The document is not valid UTF-8 text.") from None

    def prepare_text(self, text, title="", other_note_characters=0, cancel=None):
        """Store an existing editor string without making another full-sized copy."""
        if not isinstance(text, str):
            raise DocumentError("The notebook document must contain text.")
        other = _other_characters(other_note_characters)
        return self._prepare_chunks((text[start:start + CHUNK_CHARS]
                                     for start in range(0, len(text), CHUNK_CHARS)),
                                    title, other, cancel)

    def _prepare_chunks(self, chunks, title, other, cancel):
        _check_cancel(cancel)
        title = title.strip()[:100] if isinstance(title, str) else ""
        document_id = uuid.uuid4().hex
        digest, characters, pages, preview, nonblank = hashlib.sha256(), 0, 0, "", False
        with self._connection(write=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                occupied = self._total(connection) + other
                if occupied > MAX_NOTEBOOK_CHARACTERS:
                    raise DocumentError("Your notebook is full. Its capacity is 1,000,000,000 characters.")
                connection.execute("INSERT INTO documents(document_id, title, characters, sha256, pages) VALUES (?, ?, 0, ?, 0)",
                                   (document_id, title, digest.hexdigest()))
                for chunk in chunks:
                    _check_cancel(cancel)
                    if not chunk:
                        continue
                    if len(chunk) > CHUNK_CHARS:
                        raise DocumentError("A notebook document chunk is too large.")
                    characters += len(chunk)
                    if occupied + characters > MAX_NOTEBOOK_CHARACTERS:
                        raise DocumentError("This document exceeds the notebook's 1,000,000,000-character "
                                            "capacity. Remove older documents or save a smaller selection.")
                    try:
                        digest.update(chunk.encode("utf-8"))
                    except UnicodeError:
                        raise DocumentError("The document is not valid UTF-8 text.") from None
                    if not pages:
                        preview = chunk[:PREVIEW_CHARS]
                    nonblank = nonblank or bool(chunk.strip())
                    connection.execute("INSERT INTO chunks(document_id, page, text, characters) VALUES (?, ?, ?, ?)",
                                       (document_id, pages, chunk, len(chunk)))
                    pages += 1
                _check_cancel(cancel)
                if not nonblank:
                    raise DocumentError("The document contains no text to save.")
                connection.execute("UPDATE documents SET characters = ?, sha256 = ?, pages = ? WHERE document_id = ?",
                                   (characters, digest.hexdigest(), pages, document_id))
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        return {"document_id": document_id, "title": title, "body_characters": characters,
                "body_sha256": digest.hexdigest(), "body": preview, "page_count": pages}

    @classmethod
    def _metadata(cls, connection, document_id):
        row = connection.execute("SELECT * FROM documents WHERE document_id = ? AND " + cls._active(connection),
                                 (document_id,)).fetchone()
        if row is None:
            return {}
        if (type(row["characters"]) is not int or not 0 < row["characters"] <= MAX_NOTEBOOK_CHARACTERS
                or type(row["pages"]) is not int or row["pages"] <= 0
                or row["pages"] != (row["characters"] + CHUNK_CHARS - 1) // CHUNK_CHARS
                or not isinstance(row["sha256"], str) or not _HASH_PATTERN.fullmatch(row["sha256"])):
            raise DocumentError("The saved notebook document metadata is damaged.")
        first = connection.execute("SELECT text, characters FROM chunks WHERE document_id = ? AND page = 0",
                                   (document_id,)).fetchone()
        if first is None or len(first["text"]) != first["characters"] or len(first["text"]) > CHUNK_CHARS:
            raise DocumentError("The saved notebook document preview is damaged.")
        return {"document_id": document_id, "title": row["title"], "body_characters": row["characters"],
                "body_sha256": row["sha256"], "body": first["text"][:PREVIEW_CHARS], "page_count": row["pages"]}

    def metadata(self, document_id):
        document_id = _identifier(document_id)
        with self._connection() as connection:
            if connection is None:
                return {}
            connection.execute("BEGIN")
            return self._metadata(connection, document_id)

    def exists(self, document_id):
        return bool(self.metadata(document_id))

    def page_count(self, document_id):
        return self.metadata(document_id).get("page_count", 0)

    def read_page(self, document_id, index0):
        document_id = _identifier(document_id)
        if type(index0) is not int or index0 < 0:
            raise DocumentError("Choose a valid notebook document page.")
        with self._connection() as connection:
            if connection is None:
                return ""
            row = connection.execute("SELECT chunks.text, chunks.characters FROM chunks "
                                     "JOIN documents ON documents.document_id = chunks.document_id "
                                     "WHERE chunks.document_id = ? AND chunks.page = ? AND "
                                     + self._active(connection, "documents"),
                                     (document_id, index0)).fetchone()
            if row is None:
                return ""
            if not isinstance(row["text"], str) or len(row["text"]) != row["characters"] or len(row["text"]) > CHUNK_CHARS:
                raise DocumentError("The saved notebook document page is damaged.")
            return row["text"]

    @staticmethod
    def _iter_checked(connection, metadata):
        count, pages, digest = 0, 0, hashlib.sha256()
        for row in connection.execute("SELECT page, text, characters FROM chunks WHERE document_id = ? ORDER BY page",
                                      (metadata["document_id"],)):
            text = row["text"]
            if (row["page"] != pages or not isinstance(text, str) or len(text) != row["characters"]
                    or not 0 < len(text) <= CHUNK_CHARS):
                raise DocumentError("The saved notebook document pages are damaged.")
            digest.update(text.encode("utf-8"))
            count += len(text)
            pages += 1
            yield text
        if (count != metadata["body_characters"] or pages != metadata["page_count"]
                or digest.hexdigest() != metadata["body_sha256"]):
            raise DocumentError("The saved notebook document failed its integrity check.")

    def iter_text(self, document_id):
        document_id = _identifier(document_id)
        with self._connection() as connection:
            if connection is None:
                return
            connection.execute("BEGIN")
            metadata = self._metadata(connection, document_id)
            if metadata:
                yield from self._iter_checked(connection, metadata)

    def search(self, query, document_ids=None, limit=12):
        """Recall bounded passages using quoted terms, never raw FTS query syntax."""
        if not isinstance(query, str) or type(limit) is not int or limit <= 0:
            return []
        terms, match, selected = _search_parameters(query, document_ids)
        if not terms or selected == []:
            return []
        with self._connection() as connection:
            if connection is None:
                return []
            connection.execute("BEGIN")
            restriction = (" AND chunks.document_id IN (" + ",".join("?" for _ in selected) + ")") if selected else ""
            candidates = connection.execute(
                "SELECT chunks.rowid AS chunk_id, chunks.document_id, chunks.page "
                "FROM document_text JOIN chunks ON chunks.rowid = document_text.rowid "
                "JOIN documents ON documents.document_id = chunks.document_id "
                "WHERE document_text MATCH ? AND " + self._active(connection, "documents")
                + restriction + " ORDER BY bm25(document_text), chunks.rowid",
                [match, *(selected or [])])
            chosen, per_document = [], {}
            try:
                for row in candidates:
                    identifier = row["document_id"]
                    count = per_document.get(identifier, 0)
                    if count >= 2:
                        continue
                    chosen.append(row)
                    per_document[identifier] = count + 1
                    if len(chosen) >= min(limit, 2000):
                        break
            finally:
                candidates.close()
            if not chosen:
                return []
            placeholders = ",".join("?" for _ in chosen)
            snippets = connection.execute(
                "SELECT rowid, snippet(document_text, 0, '', '', ' … ', 64) AS excerpt "
                "FROM document_text WHERE document_text MATCH ? AND rowid IN (" + placeholders + ")",
                [match, *[row["chunk_id"] for row in chosen]])
            excerpts = {row["rowid"]: _bounded_excerpt(row["excerpt"], terms) for row in snippets}
            return [{"document_id": row["document_id"], "page": row["page"],
                     "text": excerpts[row["chunk_id"]]} for row in chosen]

    def matching_ids(self, query, document_ids=None):
        """Find every matching document using only indexed IDs, without excerpts."""
        terms, match, selected = _search_parameters(query, document_ids)
        if not terms or selected == []:
            return set()
        with self._connection() as connection:
            if connection is None:
                return set()
            restriction = (" AND chunks.document_id IN (" + ",".join("?" for _ in selected) + ")") if selected else ""
            rows = connection.execute(
                "SELECT DISTINCT chunks.document_id "
                "FROM document_text JOIN chunks ON chunks.rowid = document_text.rowid "
                "JOIN documents ON documents.document_id = chunks.document_id "
                "WHERE document_text MATCH ? AND " + self._active(connection, "documents") + restriction,
                [match, *(selected or [])])
            return {row["document_id"] for row in rows}

    def delete(self, document_id):
        document_id = _identifier(document_id)
        if not self.path.exists():
            return False
        if not self.exists(document_id):
            self._schedule_cleanup()
            return False
        with self._connection(write=True, busy_timeout=0) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                deleted = connection.execute("UPDATE documents SET deleted = 1 WHERE document_id = ? AND deleted = 0",
                                             (document_id,)).rowcount
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        self._schedule_cleanup()
        return bool(deleted)

    def _schedule_cleanup(self):
        """Coalesce requests into one recoverable daemon worker per database."""
        if self._cleanup_closed:
            return
        key = str(self.path.resolve())
        with _CLEANUP_LOCK:
            if self._cleanup_closed:
                return
            state = _CLEANUP_WORKERS.get(key)
            if state is not None:
                state.pending.set()
                return
            state = _CleanupState()
            _CLEANUP_WORKERS[key] = state
            state.thread = threading.Thread(target=self._cleanup_worker, args=(key, state),
                                            name="notebook-document-cleanup", daemon=True)
            state.thread.start()

    def stop_cleanup(self, timeout=0.5):
        """Stop cleanup before app shutdown or removal of the data directory.

        A current small transaction may finish; the bounded join returns whether
        it has stopped. This instance never schedules more cleanup afterwards.
        Construct a new store to resume cleanup when the notebook is reopened.
        """
        self._cleanup_closed = True
        key = str(self.path.resolve())
        with _CLEANUP_LOCK:
            state = _CLEANUP_WORKERS.get(key)
            if state is None:
                return True
            state.stop.set()
            state.pending.set()
            thread = state.thread
        if thread is threading.current_thread():
            return False
        thread.join(timeout=max(0, timeout))
        return not thread.is_alive()

    def close(self, timeout=0.5):
        return self.stop_cleanup(timeout)

    def _cleanup_worker(self, key, state):
        try:
            while not state.stop.is_set():
                state.pending.clear()
                while self.path.exists() and not state.stop.is_set():
                    try:
                        if not self._cleanup_batch():
                            break
                    except DocumentBusy:
                        state.stop.wait(0.05)
                        continue
                    except (DocumentError, OSError):
                        return
                    state.stop.wait(0.01)
                with _CLEANUP_LOCK:
                    if state.pending.is_set() and self.path.exists() and not state.stop.is_set():
                        continue
                    if _CLEANUP_WORKERS.get(key) is state:
                        del _CLEANUP_WORKERS[key]
                    return
        finally:
            with _CLEANUP_LOCK:
                if _CLEANUP_WORKERS.get(key) is state:
                    del _CLEANUP_WORKERS[key]

    def _cleanup_batch(self):
        """Purge at most a few pages; existing WAL readers retain their snapshots."""
        with self._connection(write=True, busy_timeout=0, create=False) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute("SELECT document_id FROM documents WHERE deleted = 1 LIMIT 1").fetchone()
                if row is None:
                    connection.execute("COMMIT")
                    return False
                identifier = row["document_id"]
                connection.execute("DELETE FROM chunks WHERE rowid IN "
                                   "(SELECT rowid FROM chunks WHERE document_id = ? LIMIT ?)",
                                   (identifier, _GC_CHUNKS))
                if connection.execute("SELECT 1 FROM chunks WHERE document_id = ? LIMIT 1", (identifier,)).fetchone() is None:
                    connection.execute("DELETE FROM documents WHERE document_id = ?", (identifier,))
                connection.execute("COMMIT")
                return True
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def export_document(self, document_id, target_text_path, cancel=None):
        """Write a verified UTF-8 backup atomically, preserving an existing target on failure."""
        document_id = _identifier(document_id)
        target = Path(target_text_path)
        reserved = {self.path.resolve(), Path(str(self.path) + "-wal").resolve(),
                    Path(str(self.path) + "-shm").resolve()}
        if target.resolve() in reserved:
            raise DocumentError("Choose a backup file outside the notebook document database.")
        _check_cancel(cancel)
        temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with self._connection() as connection:
                if connection is None:
                    raise DocumentError("The saved notebook document could not be found.")
                connection.execute("BEGIN")
                metadata = self._metadata(connection, document_id)
                if not metadata:
                    raise DocumentError("The saved notebook document could not be found.")
                target.parent.mkdir(parents=True, exist_ok=True)
                with temporary.open("w", encoding="utf-8", newline="") as handle:
                    for chunk in self._iter_checked(connection, metadata):
                        _check_cancel(cancel)
                        handle.write(chunk)
                    _check_cancel(cancel)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
                return metadata
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
