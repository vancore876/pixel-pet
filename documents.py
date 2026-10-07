"""File-backed, immutable notebook documents with bounded pages and full-text recall.

The JSON notebook holds only document IDs and previews. Full document text lives
in SQLite in small UTF-8 chunks; imports, recall and backups never concatenate a
whole document. Reading an absent document store does not create one.
"""
from __future__ import annotations

from contextlib import contextmanager
import atexit
import hashlib
import json
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
_SCHEMA_VERSION = 3
_GC_CHUNKS = 32
_CLEANUP_LOCK = threading.Lock()
_CLEANUP_WORKERS = {}
_ID_PATTERN = re.compile(r"[0-9a-f]{32}\Z")
_HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_RESTORE_LOCK = threading.Lock()
_LIVE_RESTORES = {}
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
        self._restore_id = None
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
                or connection.execute("PRAGMA user_version").fetchone()[0] not in (1, 2, _SCHEMA_VERSION)):
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
            if application == _APPLICATION_ID and version in (1, 2):
                columns = {row["name"] for row in connection.execute("PRAGMA table_info(documents)")}
                if "deleted" not in columns:
                    connection.execute("ALTER TABLE documents ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0, 1))")
                if "staged_restore" not in columns:
                    connection.execute("ALTER TABLE documents ADD COLUMN staged_restore INTEGER NOT NULL DEFAULT 0 CHECK(staged_restore IN (0, 1))")
                if "restore_batch" not in columns:
                    connection.execute("ALTER TABLE documents ADD COLUMN restore_batch TEXT NOT NULL DEFAULT ''")
                connection.execute("CREATE TABLE IF NOT EXISTS restore_batches (restore_id TEXT PRIMARY KEY, retired_ids TEXT NOT NULL)")
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
                deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0, 1)),
                staged_restore INTEGER NOT NULL DEFAULT 0 CHECK(staged_restore IN (0, 1)),
                restore_batch TEXT NOT NULL DEFAULT ''
            )""")
            connection.execute("CREATE TABLE restore_batches (restore_id TEXT PRIMARY KEY, retired_ids TEXT NOT NULL)")
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
        staging = " AND staged_restore = 0" if connection.execute("PRAGMA user_version").fetchone()[0] >= 3 else ""
        total = connection.execute("SELECT COALESCE(SUM(characters), 0) FROM documents WHERE "
                                   + cls._active(connection) + staging).fetchone()[0]
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
                                            argument.get("title", ""), other, cancel,
                                            restore_id=argument.get("restore_id"))
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

    def _prepare_chunks(self, chunks, title, other, cancel, *, restore_id=None):
        _check_cancel(cancel)
        title = title.strip()[:100] if isinstance(title, str) else ""
        document_id = uuid.uuid4().hex
        digest, characters, pages, preview, nonblank = hashlib.sha256(), 0, 0, "", False
        with self._connection(write=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                occupied = self._total(connection) + other
                if restore_id is not None:
                    retired = self._restore_retirements(connection, restore_id)
                    occupied -= sum(self._metadata(connection, identifier).get("body_characters", 0)
                                    for identifier in retired)
                    occupied += connection.execute("SELECT COALESCE(SUM(characters), 0) FROM documents "
                        "WHERE restore_batch = ? AND staged_restore = 1 AND deleted = 0", (restore_id,)).fetchone()[0]
                if occupied > MAX_NOTEBOOK_CHARACTERS:
                    raise DocumentError("Your notebook is full. Its capacity is 1,000,000,000 characters.")
                connection.execute("INSERT INTO documents(document_id, title, characters, sha256, pages, staged_restore, restore_batch) "
                    "VALUES (?, ?, 0, ?, 0, ?, ?)",
                    (document_id, title, digest.hexdigest(), int(restore_id is not None), restore_id or ""))
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

    @staticmethod
    def _restore_retirements(connection, restore_id):
        restore_id = _identifier(restore_id)
        row = connection.execute("SELECT retired_ids FROM restore_batches WHERE restore_id = ?", (restore_id,)).fetchone()
        if row is None:
            raise DocumentError("This notebook restore is no longer prepared. Choose the backup again.")
        try:
            identifiers = json.loads(row["retired_ids"])
            if not isinstance(identifiers, list) or len(identifiers) > 4000:
                raise ValueError()
            return {_identifier(value) for value in identifiers}
        except (ValueError, TypeError):
            raise DocumentError("The prepared notebook restore metadata is invalid.") from None

    def begin_restore(self, retired_ids, other_note_characters=0):
        """Reserve a bounded restore batch without changing original documents.

        Replacement bodies remain separately staged until a valid notebook JSON
        commit chooses their IDs. A durable retirement manifest supports recovery
        when the process stops between the JSON write and SQLite activation.
        """
        retired = {_identifier(value) for value in retired_ids}
        if len(retired) > 4000:
            raise DocumentError("Choose fewer notebook documents for this restore.")
        other = _other_characters(other_note_characters)
        restore_id = uuid.uuid4().hex
        with self._connection(write=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if connection.execute("SELECT 1 FROM restore_batches LIMIT 1").fetchone():
                    raise DocumentBusy("Another notebook restore is prepared. Finish or cancel it before restoring again.")
                credit = sum(self._metadata(connection, value).get("body_characters", 0) for value in retired)
                if self._total(connection) - credit + other > MAX_NOTEBOOK_CHARACTERS:
                    raise DocumentError("This restore exceeds the notebook's 1,000,000,000-character capacity.")
                connection.execute("INSERT INTO restore_batches(restore_id, retired_ids) VALUES (?, ?)",
                                   (restore_id, json.dumps(sorted(retired))))
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        with _RESTORE_LOCK:
            _LIVE_RESTORES[str(self.path.resolve())] = restore_id
        self._restore_id = restore_id
        return restore_id

    def abort_restore(self, restore_id):
        """Discard only a batch's staged bodies, preserving all original IDs."""
        restore_id = _identifier(restore_id)
        # The caller has abandoned this batch even if a temporary writer lock
        # delays cleanup. A valid-manifest recovery can then retry it later.
        with _RESTORE_LOCK:
            if _LIVE_RESTORES.get(str(self.path.resolve())) == restore_id:
                _LIVE_RESTORES.pop(str(self.path.resolve()), None)
        if not self.path.exists():
            return
        with self._connection(write=True, busy_timeout=50, create=False) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute("UPDATE documents SET deleted = 1, staged_restore = 0, restore_batch = '' "
                    "WHERE restore_batch = ? AND staged_restore = 1", (restore_id,))
                connection.execute("DELETE FROM restore_batches WHERE restore_id = ?", (restore_id,))
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        self._schedule_cleanup()

    def finish_restore(self, referenced, other_note_characters=0, *, validate_only=False, recover=False):
        """Validate projected capacity, then activate a committed restore batch.

        ``referenced`` maps IDs to their verified (character count, SHA-256)
        descriptors from the current notebook manifest. Only staged bodies and
        the batch's explicitly retired originals can be discarded here.
        A busy writer returns promptly; committed staged IDs remain readable so
        the next save or a valid-manifest startup can retry activation safely.
        """
        other = _other_characters(other_note_characters)
        if not isinstance(referenced, dict) or len(referenced) > 4000:
            raise DocumentError("The notebook restore document references are invalid.")
        selected = {_identifier(value): descriptor for value, descriptor in referenced.items()}
        if not self.path.exists():
            return False
        with self._connection() as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] < 3:
                return False
            batch = connection.execute("SELECT restore_id FROM restore_batches LIMIT 1").fetchone()
            if batch is None:
                return False
            restore_id = batch["restore_id"]
            if recover:
                with _RESTORE_LOCK:
                    live = _LIVE_RESTORES.get(str(self.path.resolve())) == restore_id
                staged_ids = {row[0] for row in connection.execute("SELECT document_id FROM documents "
                    "WHERE restore_batch = ? AND staged_restore = 1 AND deleted = 0", (restore_id,))}
                if live and not staged_ids.intersection(selected):
                    return False  # Another live window is still preparing this batch.
        with self._connection(write=True, busy_timeout=50, create=False) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                retired = self._restore_retirements(connection, restore_id)
                for identifier, descriptor in selected.items():
                    metadata = self._metadata(connection, identifier)
                    if (not metadata or not isinstance(descriptor, (tuple, list)) or len(descriptor) != 2
                            or (metadata["body_characters"], metadata["body_sha256"]) != tuple(descriptor)):
                        raise DocumentError("A notebook restore document is missing or changed. Original text was preserved.")
                retiring = retired - selected.keys()
                credit = sum(self._metadata(connection, value).get("body_characters", 0) for value in retiring)
                staged = connection.execute("SELECT document_id, characters FROM documents "
                    "WHERE restore_batch = ? AND staged_restore = 1 AND deleted = 0", (restore_id,)).fetchall()
                activating = sum(row["characters"] for row in staged if row["document_id"] in selected)
                if self._total(connection) - credit + activating + other > MAX_NOTEBOOK_CHARACTERS:
                    raise DocumentError("This restore exceeds the notebook's 1,000,000,000-character capacity. "
                                        "Existing notes were preserved.")
                if validate_only:
                    connection.execute("ROLLBACK")
                    return True
                # No chosen staged ID means the batch never became the durable
                # restore. Discard staging only; a verified note transaction's
                # normal cleanup handles any independently replaced originals.
                if not activating:
                    retiring = set()
                for identifier in retiring:
                    connection.execute("UPDATE documents SET deleted = 1 WHERE document_id = ?", (identifier,))
                for row in staged:
                    connection.execute("UPDATE documents SET deleted = ?, staged_restore = 0, restore_batch = '' "
                        "WHERE document_id = ?", (int(row["document_id"] not in selected), row["document_id"]))
                connection.execute("DELETE FROM restore_batches WHERE restore_id = ?", (restore_id,))
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        with _RESTORE_LOCK:
            if _LIVE_RESTORES.get(str(self.path.resolve())) == restore_id:
                _LIVE_RESTORES.pop(str(self.path.resolve()), None)
        self._schedule_cleanup()
        return True

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

    def read_pages(self, document_id, start_page=0, limit=32, cancel=None):
        """Read a small validated batch in one snapshot for background indexing."""
        document_id = _identifier(document_id)
        if type(start_page) is not int or start_page < 0 or type(limit) is not int or not 1 <= limit <= 32:
            raise DocumentError("Choose a valid notebook document page range (up to 32 pages).")
        _check_cancel(cancel)
        with self._connection() as connection:
            if connection is None:
                return []
            connection.execute("BEGIN")
            metadata = self._metadata(connection, document_id)
            if not metadata:
                return []
            expected = min(limit, max(0, metadata["page_count"] - start_page))
            rows = connection.execute("SELECT page, text, characters FROM chunks "
                                      "WHERE document_id = ? AND page >= ? AND page < ? ORDER BY page LIMIT ?",
                                      (document_id, start_page, start_page + limit, limit))
            result = []
            for row in rows:
                _check_cancel(cancel)
                text = row["text"]
                if (row["page"] != start_page + len(result) or not isinstance(text, str)
                        or len(text) != row["characters"] or not 0 < len(text) <= CHUNK_CHARS):
                    raise DocumentError("The saved notebook document pages are damaged.")
                result.append({"page": row["page"], "text": text})
            if len(result) != expected:
                raise DocumentError("The saved notebook document pages are damaged.")
            _check_cancel(cancel)
            return result

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
        with _RESTORE_LOCK:
            if _LIVE_RESTORES.get(str(self.path.resolve())) == self._restore_id:
                _LIVE_RESTORES.pop(str(self.path.resolve()), None)
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
