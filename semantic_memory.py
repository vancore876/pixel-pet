"""Optional local meaning-based recall with a bounded, persistent derived cache.

The notebook remains the source of truth. Model loading, small indexing batches,
query encoding and bounded approximate vector scoring happen in a worker. The
model must already exist locally; this module never downloads a model or imports
PyTorch during desktop startup. Cached passages are local data, not training.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import math
from pathlib import Path
import random
import sqlite3
import struct
import threading
import time

MAX_SOURCES = 4000
SEGMENT_CHARS = 900
SEGMENT_OVERLAP = 100
INDEX_BATCH = 32
SOURCE_BATCH = 8
MAX_CANDIDATES = 512
MAX_RESULTS = 6
MIN_SIMILARITY = 0.28
_APPLICATION_ID = 0x50534D44
_SCHEMA_VERSION = 2


class SemanticError(ValueError):
    """A local semantic-recall error that is safe to show in the desktop UI."""


class SemanticCancelled(SemanticError):
    pass


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise SemanticCancelled("Local memory search was canceled.")


def _local_encoder(path):
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        raise SemanticError("Meaning-based recall needs the optional semantic requirements. Install them and the local memory model first.") from None
    try:
        # CPU use avoids consuming the user's GPU while the desktop pet runs.
        # Limit inference parallelism so background recall leaves room for work.
        import torch
        torch.set_num_threads(2)
        return SentenceTransformer(str(path), device="cpu", local_files_only=True,
                                   trust_remote_code=False,
                                   model_kwargs={"use_safetensors": True})
    except Exception:
        raise SemanticError("The local memory model could not be loaded. Install or repair the model using the setup helper.") from None


def snapshot_notes(notes):
    """Small immutable-text references; no copying or concatenating full documents."""
    result = []
    seen = set()
    for note in notes:
        if not isinstance(note, dict):
            continue
        identifier = note.get("id")
        if not isinstance(identifier, str) or not 0 < len(identifier) <= 64 or identifier in seen:
            continue
        seen.add(identifier)
        result.append(dict(note))
        if len(result) >= MAX_SOURCES:
            break
    return result


def note_fingerprint(note):
    fields = (note.get("title", ""), note.get("document_id", ""),
              note.get("body_sha256", ""), "" if note.get("document_id") else note.get("body", ""))
    digest = hashlib.sha256()
    for value in fields:
        digest.update(str(value).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _normalized(vector):
    try:
        values = [float(value) for value in vector]
    except (ValueError, TypeError, OverflowError):
        raise SemanticError("The local memory model returned an invalid embedding.") from None
    if not 1 <= len(values) <= 4096 or not all(math.isfinite(value) for value in values):
        raise SemanticError("The local memory model returned an invalid embedding.")
    length = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(length) or length <= 0:
        raise SemanticError("The local memory model returned an empty embedding.")
    return tuple(value / length for value in values)


class SemanticMemory:
    """A connection-per-operation cache; serializes model work without blocking Qt."""

    def __init__(self, path, model_path="", documents=None, encoder_factory=None):
        self.path = Path(path)
        self.documents = documents
        self.encoder_factory = encoder_factory or _local_encoder
        self._requested_path = str(model_path or "")
        self._state_lock = threading.Lock()
        self._work_lock = threading.Lock()
        self._encoder = None
        self._model_identity = None
        self._directions = {}

    def configure(self, model_path=""):
        # Called by Qt: changing the requested path never waits for inference.
        with self._state_lock:
            self._requested_path = str(model_path or "")

    @contextmanager
    def _working(self, cancel):
        while not self._work_lock.acquire(timeout=0.05):
            _check_cancel(cancel)
        try:
            _check_cancel(cancel)
            yield
        finally:
            self._work_lock.release()

    def _load(self, cancel):
        _check_cancel(cancel)
        with self._state_lock:
            selected = self._requested_path
        path = Path(selected).expanduser() if selected else self.path.parent / "models" / "all-MiniLM-L6-v2"
        if not path.is_dir():
            raise SemanticError("Install the local memory model before enabling meaning-based recall. No model is downloaded automatically.")
        # Small model manifests make replaced model files invalidate the cache;
        # avoid hashing the much larger weights on every chat request.
        digest = hashlib.sha256(str(path.resolve()).encode("utf-8"))
        for name in ("config.json", "modules.json", "sentence_bert_config.json", "model.safetensors", "pytorch_model.bin"):
            file = path / name
            if file.is_file():
                stat = file.stat()
                digest.update(f"{name}:{stat.st_size}:{stat.st_mtime_ns}".encode())
        identity = digest.hexdigest()
        if identity != self._model_identity:
            _check_cancel(cancel)
            encoder = self.encoder_factory(path)
            _check_cancel(cancel)
            self._encoder, self._model_identity = encoder, identity
        return identity

    @contextmanager
    def _connection(self, cancel):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=1, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.set_progress_handler(lambda: 1 if cancel is not None and cancel.is_set() else 0, 1000)
            application = connection.execute("PRAGMA application_id").fetchone()[0]
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if application == _APPLICATION_ID and version == _SCHEMA_VERSION:
                pass
            elif application or version or connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1").fetchone():
                raise SemanticError("The local memory cache uses an unsupported format. Keep the notebook and replace only semantic.sqlite to rebuild it.")
            else:
                connection.executescript("""
                    BEGIN IMMEDIATE;
                    CREATE TABLE sources (
                        model TEXT NOT NULL, note_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                        active INTEGER NOT NULL DEFAULT 1, complete INTEGER NOT NULL DEFAULT 0,
                        next_page INTEGER NOT NULL DEFAULT 0, next_offset INTEGER NOT NULL DEFAULT 0,
                        indexed_at REAL NOT NULL DEFAULT 0,
                        PRIMARY KEY(model, note_id)
                    );
                    CREATE TABLE vectors (
                        model TEXT NOT NULL, note_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                        page INTEGER NOT NULL, offset INTEGER NOT NULL, text TEXT NOT NULL,
                        dimensions INTEGER NOT NULL, embedding BLOB NOT NULL,
                        bucket0 INTEGER NOT NULL, bucket1 INTEGER NOT NULL,
                        bucket2 INTEGER NOT NULL, bucket3 INTEGER NOT NULL,
                        UNIQUE(model, note_id, fingerprint, page, offset)
                    );
                    CREATE TABLE garbage (
                        model TEXT NOT NULL, note_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                        PRIMARY KEY(model, note_id, fingerprint)
                    );
                    CREATE INDEX sources_active ON sources(model, active);
                    CREATE INDEX vectors_source ON vectors(model, note_id, fingerprint);
                    CREATE INDEX vectors_bucket0 ON vectors(model, bucket0);
                    CREATE INDEX vectors_bucket1 ON vectors(model, bucket1);
                    CREATE INDEX vectors_bucket2 ON vectors(model, bucket2);
                    CREATE INDEX vectors_bucket3 ON vectors(model, bucket3);
                """)
                connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
                connection.execute("COMMIT")
            connection.execute("PRAGMA journal_mode = WAL")
            yield connection
        except sqlite3.Error:
            _check_cancel(cancel)
            raise SemanticError("The local memory cache could not be updated. Keyword recall remains available; check storage space and permissions.") from None
        finally:
            connection.close()

    def _encode(self, texts, cancel):
        _check_cancel(cancel)
        try:
            rows = self._encoder.encode(texts, batch_size=8, show_progress_bar=False,
                                        normalize_embeddings=True, convert_to_numpy=True)
        except Exception:
            _check_cancel(cancel)
            raise SemanticError("The local memory model could not finish. Keyword recall remains available.") from None
        _check_cancel(cancel)
        vectors = [_normalized(row) for row in rows]
        if len(vectors) != len(texts) or len({len(row) for row in vectors}) != 1:
            raise SemanticError("The local memory model returned inconsistent embeddings.")
        return vectors

    def _buckets(self, vector):
        # Four deterministic sparse random projections permit indexed candidate
        # lookup. Scoring never loads or traverses the entire embedding cache.
        dimensions = len(vector)
        if dimensions not in self._directions:
            generator = random.Random(0x4A454646 + dimensions)
            self._directions[dimensions] = tuple(tuple(
                (generator.randrange(dimensions), generator.choice((-1, 1)))
                for _ in range(32)) for _ in range(48))
        signs = [sum(vector[index] * sign for index, sign in direction) >= 0
                 for direction in self._directions[dimensions]]
        return tuple(sum(int(signs[table * 12 + bit]) << bit for bit in range(12)) for table in range(4))

    @staticmethod
    def _synchronize(connection, model, notes):
        connection.execute("BEGIN IMMEDIATE")
        try:
            current = {note["id"]: note_fingerprint(note) for note in notes}
            # Find changed identities among small source metadata, rather than
            # scanning all cached passage rows looking for garbage on each chat.
            for previous in connection.execute("SELECT model, note_id, fingerprint FROM sources WHERE active = 1"):
                if previous["model"] != model or current.get(previous["note_id"]) != previous["fingerprint"]:
                    connection.execute("INSERT OR IGNORE INTO garbage VALUES (?, ?, ?)",
                        (previous["model"], previous["note_id"], previous["fingerprint"]))
            connection.execute("UPDATE sources SET active = 0")
            for note in notes:
                connection.execute("""INSERT INTO sources(model, note_id, fingerprint) VALUES (?, ?, ?)
                    ON CONFLICT(model, note_id) DO UPDATE SET active = 1,
                    complete = CASE WHEN fingerprint = excluded.fingerprint THEN complete ELSE 0 END,
                    next_page = CASE WHEN fingerprint = excluded.fingerprint THEN next_page ELSE 0 END,
                    next_offset = CASE WHEN fingerprint = excluded.fingerprint THEN next_offset ELSE 0 END,
                    indexed_at = CASE WHEN fingerprint = excluded.fingerprint THEN indexed_at ELSE 0 END,
                    fingerprint = excluded.fingerprint""", (model, note["id"], current[note["id"]]))
            # Deleted and edited content cannot be recalled. Reclaim obsolete
            # derived rows a few at a time, rather than blocking on a huge delete.
            for old in connection.execute("SELECT model, note_id, fingerprint FROM garbage LIMIT 8").fetchall():
                key = (old["model"], old["note_id"], old["fingerprint"])
                connection.execute("""DELETE FROM vectors WHERE rowid IN (
                    SELECT rowid FROM vectors WHERE model = ? AND note_id = ?
                    AND fingerprint = ? LIMIT 64)""", key)
                if connection.execute("SELECT 1 FROM vectors WHERE model = ? AND note_id = ? AND fingerprint = ? LIMIT 1", key).fetchone() is None:
                    connection.execute("DELETE FROM garbage WHERE model = ? AND note_id = ? AND fingerprint = ?", key)
            connection.execute("DELETE FROM sources WHERE active = 0")
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def _work(self, connection, model, notes, cancel):
        current = {note["id"]: note for note in notes}
        work, progress = [], []
        for source in connection.execute("SELECT * FROM sources WHERE model = ? AND active = 1 AND complete = 0 ORDER BY indexed_at, note_id LIMIT 32", (model,)):
            _check_cancel(cancel)
            note = current[source["note_id"]]
            page, offset, done = source["next_page"], source["next_offset"], False
            loaded_page, loaded_text = -1, ""
            produced, checked_pages = 0, 0
            while produced < SOURCE_BATCH and len(work) < INDEX_BATCH and checked_pages < SOURCE_BATCH:
                _check_cancel(cancel)
                if note.get("document_id"):
                    if self.documents is None:
                        from documents import DocumentStore
                        self.documents = DocumentStore(self.path.parent / "documents.sqlite")
                    if loaded_page != page:
                        rows = self.documents.read_pages(note["document_id"], start_page=page, limit=1, cancel=cancel)
                        loaded_page, loaded_text = page, rows[0]["text"] if rows else ""
                    text = loaded_text
                else:
                    text = note.get("body", "") if page == 0 else ""
                    text = text if isinstance(text, str) else ""
                if not text:
                    done = True
                    break
                segment = text[offset:offset + SEGMENT_CHARS]
                if segment.strip():
                    work.append({"note_id": note["id"], "fingerprint": source["fingerprint"],
                                 "page": page, "offset": offset, "text": segment,
                                 "title": str(note.get("title", ""))[:100]})
                    produced += 1
                if offset + SEGMENT_CHARS >= len(text):
                    page, offset = page + 1, 0
                    checked_pages += 1
                    if not note.get("document_id"):
                        done = True
                        break
                else:
                    offset += SEGMENT_CHARS - SEGMENT_OVERLAP
            progress.append((page, offset, int(done), time.time(), model, note["id"], source["fingerprint"]))
            if len(work) >= INDEX_BATCH:
                break
        return work, progress

    def _index(self, connection, model, notes, cancel):
        self._synchronize(connection, model, notes)
        work, progress = self._work(connection, model, notes, cancel)
        for start in range(0, len(work), 8):
            batch = work[start:start + 8]
            vectors = self._encode([row["title"] + "\n" + row["text"] for row in batch], cancel)
            connection.execute("BEGIN IMMEDIATE")
            try:
                for row, vector in zip(batch, vectors):
                    _check_cancel(cancel)
                    connection.execute("""INSERT OR REPLACE INTO vectors VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (model, row["note_id"], row["fingerprint"], row["page"], row["offset"], row["text"],
                         len(vector), struct.pack(f"<{len(vector)}f", *vector), *self._buckets(vector)))
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        _check_cancel(cancel)
        connection.executemany("""UPDATE sources SET next_page = ?, next_offset = ?, complete = ?, indexed_at = ?
            WHERE model = ? AND note_id = ? AND fingerprint = ?""", progress)
        pending = connection.execute("SELECT 1 FROM sources WHERE model = ? AND active = 1 AND complete = 0 LIMIT 1", (model,)).fetchone()
        return "Meaning-based recall ready; background indexing continues." if pending else "Meaning-based recall ready. Your local notebook index is current."

    def _search(self, connection, model, query, notes, cancel):
        vector = self._encode([query[:1000]], cancel)[0]
        candidates = {}
        # Exact buckets first, then one-bit neighbors. Each query is bounded and
        # indexed; four independent tables improve recall without a full scan.
        buckets = self._buckets(vector)
        for radius in (0, 1):
            for table, bucket in enumerate(buckets):
                _check_cancel(cancel)
                keys = [bucket] if radius == 0 else [bucket ^ (1 << bit) for bit in range(12)]
                for key in keys:
                    # Equality preserves index order without a potentially huge
                    # IN-bucket temporary sort. LIMIT inside the subquery also
                    # bounds raw rows before rejecting inactive/stale sources.
                    # 4*32 exact + 4*12*8 neighbor rows = at most 512 reads.
                    rows = connection.execute(self._candidate_sql(table),
                                              (model, key, 32 if radius == 0 else 8))
                    for row in rows:
                        candidates.setdefault(row["vector_id"], row)
                        if len(candidates) >= MAX_CANDIDATES:
                            break
                    if len(candidates) >= MAX_CANDIDATES:
                        break
                if len(candidates) >= MAX_CANDIDATES:
                    break
            if len(candidates) >= MAX_CANDIDATES:
                break
        ranked = []
        for row in candidates.values():
            _check_cancel(cancel)
            if row["dimensions"] != len(vector) or len(row["embedding"]) != len(vector) * 4:
                continue
            embedding = struct.unpack(f"<{len(vector)}f", row["embedding"])
            score = sum(left * right for left, right in zip(vector, embedding))
            if math.isfinite(score) and score >= MIN_SIMILARITY:
                ranked.append((score, row["vector_id"], row))
        current = {note["id"]: note for note in notes}
        selected = {}
        for score, _, row in sorted(ranked, key=lambda item: (-item[0], -item[1])):
            identifier = row["note_id"]
            if identifier not in current:
                continue
            if identifier not in selected:
                if len(selected) >= MAX_RESULTS:
                    continue
                selected[identifier] = {**current[identifier], "body": row["text"],
                                        "semantic_score": round(score, 3),
                                        "semantic_fingerprint": row["fingerprint"]}
            elif len(selected[identifier]["body"]) < SEGMENT_CHARS * 2:
                selected[identifier]["body"] = (selected[identifier]["body"] + "\n\n" + row["text"])[:SEGMENT_CHARS * 2]
        return list(selected.values())

    @staticmethod
    def _candidate_sql(table):
        # table is an internal 0..3 index, never user-controlled query syntax.
        return f"""SELECT candidate.* FROM (
            SELECT rowid AS vector_id, * FROM vectors INDEXED BY vectors_bucket{table}
            WHERE model = ? AND bucket{table} = ? ORDER BY rowid DESC LIMIT ?
            ) AS candidate JOIN sources
            ON sources.model = candidate.model AND sources.note_id = candidate.note_id
            AND sources.fingerprint = candidate.fingerprint AND sources.active = 1"""

    def refresh(self, argument, cancel=None):
        notes = snapshot_notes(argument.get("notes", ()))
        with self._working(cancel):
            model = self._load(cancel)
            with self._connection(cancel) as connection:
                status = self._index(connection, model, notes, cancel)
            return {"operation": "refresh", "status": status}

    def recall(self, argument, cancel=None):
        query = argument.get("query", "")
        if not isinstance(query, str) or not query.strip():
            return {"operation": "recall", "query": "", "notes": [], "status": "Enter a question to search local memory."}
        notes = snapshot_notes(argument.get("notes", ()))
        with self._working(cancel):
            model = self._load(cancel)
            with self._connection(cancel) as connection:
                status = self._index(connection, model, notes, cancel)
                found = self._search(connection, model, query, notes, cancel)
            return {"operation": "recall", "query": query, "notes": found, "status": status}


# Import Qt/service infrastructure only after the lightweight cache API. The
# heavyweight optional model library is still imported exclusively in workers.
from PySide6.QtCore import QObject, Signal
from content_intake import IntakeService


class SemanticService(QObject):
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    status_changed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, index, parent=None):
        super().__init__(parent)
        self.index = index
        self.worker = IntakeService(self)
        self.worker.completed.connect(self._completed)
        self.worker.failed.connect(self._failed)
        self.worker.busy_changed.connect(self.busy_changed.emit)

    @property
    def busy(self):
        return self.worker.busy

    def configure(self, model_path=""):
        selected = str(model_path or "")
        if selected != self.index._requested_path:
            self.cancel()
            self.index.configure(selected)

    def refresh(self, notes):
        return self.worker.run(self.index.refresh, {"notes": snapshot_notes(notes)})

    def recall(self, query, notes):
        # Foreground recall replaces an idle indexing job; serial model work
        # checks cancellation while waiting for the previous small batch.
        if self.busy:
            self.worker.cancel()
        return self.worker.run(self.index.recall, {"query": query, "notes": snapshot_notes(notes)})

    def _completed(self, result):
        self.status_changed.emit(result.get("status", "Meaning-based recall ready."))
        if result.get("operation") == "recall":
            self.completed.emit(result)

    def _failed(self, message):
        self.status_changed.emit(message)
        self.failed.emit(message)

    def cancel(self):
        was_busy = self.busy
        self.worker.cancel()
        if was_busy:
            self.cancelled.emit()

    def shutdown(self):
        self.worker.shutdown()

    stop = shutdown
