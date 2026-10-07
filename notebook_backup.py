"""Stream complete notebook backups without loading document bodies into RAM.

The JSON file in an archive contains ordinary note metadata and short document
previews. Each referenced document has one UTF-8 entry whose size and digest are
checked on both export and import. Import reuses verified identical documents or
prepares independent document IDs; only the caller's later notebook transaction
changes the visible notes.
"""
from __future__ import annotations

import codecs
import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile


MAX_NOTEBOOK_CHARACTERS = 1_000_000_000
MAX_METADATA_BYTES = 128 * 1024 * 1024
MAX_NOTES = 2000
MAX_PREVIEW_CHARACTERS = 10000
CHUNK_BYTES = 65536
_WINDOWS_CREATE_ONLY_RENAME = os.name == "nt"
_DOCUMENT_ID = re.compile(r"[0-9a-f]{32}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DOCUMENT_ENTRY = re.compile(r"documents/([0-9a-f]{32})\.txt\Z")


class BackupCancelled(ValueError):
    """A requested backup operation was cancelled before it committed."""


def _check_cancel(cancel):
    if cancel is None:
        return
    check = getattr(cancel, "is_set", None)
    if (check() if callable(check) else cancel() if callable(cancel) else bool(cancel)):
        raise BackupCancelled("Notebook backup cancelled. Nothing was changed.")


def notebook_snapshot(store):
    """Capture on the GUI thread before starting a background export."""
    return copy.deepcopy(store.state)


def _payload(store, snapshot=None):
    raw = notebook_snapshot(store) if snapshot is None else copy.deepcopy(snapshot)
    if not isinstance(raw, dict) or not isinstance(raw.get("notes"), list):
        raise ValueError("The notebook metadata is invalid.")
    if len(raw["notes"]) > MAX_NOTES:
        raise ValueError("A notebook backup supports up to 2,000 notes.")
    raw["version"] = 2
    return raw


def _references(payload):
    """Check references and collect each immutable document exactly once."""
    references = {}
    inline_characters = 0
    for note in payload["notes"]:
        if not isinstance(note, dict) or not isinstance(note.get("body", ""), str):
            raise ValueError("The backup contains an invalid note.")
        body = note.get("body", "")
        if len(body) > MAX_PREVIEW_CHARACTERS:
            raise ValueError("A note preview exceeds 10,000 characters.")
        identifier = note.get("document_id", "")
        if not identifier:
            if (identifier not in (None, "") or note.get("body_characters")
                    or note.get("body_sha256")):
                raise ValueError("The backup contains an incomplete document reference.")
            inline_characters += len(body)
            continue
        count, digest = note.get("body_characters"), note.get("body_sha256")
        if (not isinstance(identifier, str) or not _DOCUMENT_ID.fullmatch(identifier)
                or type(count) is not int or not 0 < count <= MAX_NOTEBOOK_CHARACTERS
                or not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
            raise ValueError("The backup contains an invalid document reference.")
        descriptor = (count, digest)
        if identifier in references and references[identifier] != descriptor:
            raise ValueError("The backup contains conflicting document references.")
        references[identifier] = descriptor
    if inline_characters + sum(count for count, _ in references.values()) > MAX_NOTEBOOK_CHARACTERS:
        raise ValueError("The backup exceeds the 1,000,000,000-character notebook limit.")
    return references, inline_characters


def _document_metadata(store, identifier, descriptor):
    metadata = store.documents.metadata(identifier)
    if (not metadata or metadata.get("body_characters") != descriptor[0]
            or metadata.get("body_sha256") != descriptor[1]):
        raise ValueError("A notebook document is missing or its metadata has changed.")
    return metadata


def _stream_document(store, identifier, descriptor, write, cancel):
    _document_metadata(store, identifier, descriptor)
    digest, characters = hashlib.sha256(), 0
    for text in store.documents.iter_text(identifier):
        _check_cancel(cancel)
        if not isinstance(text, str):
            raise ValueError("A notebook document contains invalid text.")
        characters += len(text)
        if characters > descriptor[0]:
            raise ValueError("A notebook document failed its character-count check.")
        encoded = text.encode("utf-8")
        digest.update(encoded)
        write(encoded)
    _check_cancel(cancel)
    if characters != descriptor[0] or digest.hexdigest() != descriptor[1]:
        raise ValueError("A notebook document failed its integrity check.")


def _output_path(store, path):
    destination = Path(path).expanduser()
    notebook_path = Path(store.path)
    protected = [notebook_path.resolve(), notebook_path.with_suffix(".tmp").resolve()]
    document_path = getattr(store.documents, "path", None)
    if document_path is not None:
        document_path = Path(document_path)
        protected.extend(Path(str(document_path) + suffix).resolve()
                         for suffix in ("", "-wal", "-shm", "-journal"))
    if destination.resolve() in protected:
        raise ValueError("Choose a backup filename outside the notebook's storage files.")
    if not destination.parent.is_dir():
        raise ValueError("Choose an existing folder for the backup.")
    return destination


def _temporary_output(destination):
    handle = tempfile.NamedTemporaryFile(prefix=".jeffery-backup-", suffix=".tmp",
                                         dir=destination.parent, delete=False)
    temporary = Path(handle.name)
    handle.close()
    return temporary


def _write_json(write, payload, cancel):
    count = 0
    encoder = json.JSONEncoder(ensure_ascii=False, indent=2)
    for piece in encoder.iterencode(payload):
        _check_cancel(cancel)
        encoded = piece.encode("utf-8")
        count += len(encoded)
        if count > MAX_METADATA_BYTES:
            raise ValueError("Notebook metadata exceeds the 128 MB backup limit.")
        write(encoded)
    write(b"\n")


def export_backup(store, path, cancel=None, snapshot=None):
    """Atomically write metadata and full document bodies to a ZIP archive."""
    _check_cancel(cancel)
    payload = _payload(store, snapshot)
    references, _ = _references(payload)
    destination = _output_path(store, path)
    temporary = _temporary_output(destination)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6, allowZip64=True) as archive:
            with archive.open("notebook.json", "w", force_zip64=True) as handle:
                _write_json(handle.write, payload, cancel)
            for identifier, descriptor in references.items():
                _check_cancel(cancel)
                with archive.open(f"documents/{identifier}.txt", "w", force_zip64=True) as handle:
                    _stream_document(store, identifier, descriptor, handle.write, cancel)
        _check_cancel(cancel)
        os.replace(temporary, destination)
        return {"kind": "backup_exported", "path": str(destination)}
    finally:
        temporary.unlink(missing_ok=True)


def export_text(argument, cancel=None):
    """Atomically export readable notes, including complete document bodies."""
    store, path = argument["store"], argument["path"]
    _check_cancel(cancel)
    payload = _payload(store, argument.get("snapshot"))
    references, _ = _references(payload)
    destination = _output_path(store, path)
    temporary = _temporary_output(destination)
    try:
        with temporary.open("wb") as handle:
            for note in payload["notes"]:
                _check_cancel(cancel)
                status = "Done" if note.get("done") else "Open"
                title = note.get("title") or "Untitled note"
                header = f"[{status}] {title}\n"
                if note.get("document_source"):
                    header += f"Source: {note['document_source']}\n"
                if note.get("kind") == "order":
                    header += f"Order status: {note.get('order_status', 'new')}\n"
                    for label, key in (("Customer", "customer"), ("Contact", "contact"),
                                       ("Order reference", "order_ref")):
                        if note.get(key):
                            header += f"{label}: {note[key]}\n"
                handle.write(header.encode("utf-8"))
                identifier = note.get("document_id")
                if identifier:
                    _stream_document(store, identifier, references[identifier], handle.write, cancel)
                else:
                    handle.write(note.get("body", "").encode("utf-8"))
                handle.write(b"\n")
                for item in note.get("checklist", []):
                    _check_cancel(cancel)
                    row = f"[{'x' if item.get('done') else ' '}] {item.get('quantity', 1)} x {item.get('text', '')}\n"
                    handle.write(row.encode("utf-8"))
                handle.write(b"\n")
        _check_cancel(cancel)
        os.replace(temporary, destination)
        return {"kind": "backup_exported", "path": str(destination)}
    finally:
        temporary.unlink(missing_ok=True)


def _read_metadata(handle, size, cancel):
    if size > MAX_METADATA_BYTES:
        raise ValueError("Notebook metadata exceeds the 128 MB backup limit.")
    chunks, count = [], 0
    while True:
        _check_cancel(cancel)
        chunk = handle.read(CHUNK_BYTES)
        if not chunk:
            break
        count += len(chunk)
        if count > MAX_METADATA_BYTES:
            raise ValueError("Notebook metadata exceeds the 128 MB backup limit.")
        chunks.append(chunk)
    _check_cancel(cancel)
    try:
        return json.loads(b"".join(chunks).decode("utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("The notebook backup metadata is invalid.") from exc


def _validate_payload(store, raw, *, allow_documents):
    if (not isinstance(raw, dict) or not isinstance(raw.get("notes"), list)
            or len(raw["notes"]) > MAX_NOTES or raw.get("version", 2) not in (1, 2)):
        raise ValueError("Choose a Jeffery notebook backup with up to 2,000 notes.")
    # Validate every incoming row before writing any document to the local store.
    _references(raw)
    notes, identifiers = [], set()
    for data in raw["notes"]:
        if not allow_documents and data.get("document_id"):
            raise ValueError("This JSON backup has document references but no full text. Choose its ZIP backup.")
        note = store.validate_note(data)
        if not note or note["id"] in identifiers:
            raise ValueError("The backup contains an invalid or duplicate note.")
        identifiers.add(note["id"])
        if data.get("document_id"):
            note.update({key: data[key] for key in ("document_id", "body_characters", "body_sha256")})
        notes.append(note)
    existing_ids = {note["id"] for note in store.notes}
    if len(existing_ids | identifiers) > MAX_NOTES:
        raise ValueError("Import would exceed the 2,000-note notebook limit.")
    return {"version": 2, "notes": notes}


def _spool_document(archive, entry, destination, descriptor, cancel):
    expected_characters, expected_digest = descriptor
    if entry.file_size > expected_characters * 4:
        raise ValueError("A backup document exceeds its declared size.")
    digest, characters, byte_count = hashlib.sha256(), 0, 0
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        with archive.open(entry, "r") as source, destination.open("wb") as target:
            while True:
                _check_cancel(cancel)
                chunk = source.read(CHUNK_BYTES)
                if not chunk:
                    break
                byte_count += len(chunk)
                if byte_count > expected_characters * 4:
                    raise ValueError("A backup document exceeds its declared size.")
                characters += len(decoder.decode(chunk))
                if characters > expected_characters:
                    raise ValueError("A backup document failed its character-count check.")
                digest.update(chunk)
                target.write(chunk)
            characters += len(decoder.decode(b"", final=True))
    except UnicodeError as exc:
        raise ValueError("A backup document is not valid UTF-8 text.") from exc
    _check_cancel(cancel)
    if characters != expected_characters or digest.hexdigest() != expected_digest:
        raise ValueError("A backup document failed its integrity check. Nothing was imported.")


def _cleanup_prepared(store, identifiers):
    for identifier in identifiers:
        try:
            store.documents.delete(identifier)
        except (OSError, ValueError):
            pass


def _reusable_documents(store, cancel):
    """Index only documents already attached to the current notebook."""
    matches = {}
    for note in notebook_snapshot(store)["notes"]:
        _check_cancel(cancel)
        identifier = note.get("document_id")
        if identifier:
            descriptor = (note.get("body_characters"), note.get("body_sha256"))
            matches.setdefault(descriptor, identifier)
    return matches


def _merge_plan(store, payload):
    """Mirror new-or-newer note merging without changing the live notebook."""
    existing = {note["id"]: note for note in notebook_snapshot(store)["notes"]}
    incoming = {note["id"]: note for note in payload["notes"]
                if note["id"] not in existing or note["updated"] > existing[note["id"]]["updated"]}
    retained = [note for identifier, note in existing.items() if identifier not in incoming]
    return incoming, retained


def _restore_cleanup(store, restore_id, prepared_ids):
    """A late cleanup must preserve text already referenced by saved notes."""
    manifest = store._document_manifest()
    if set(prepared_ids).intersection(manifest):
        store.documents.finish_restore(manifest, store.inline_character_count())
    else:
        store.documents.abort_restore(restore_id)


def _ensure_restore_baseline(store):
    """Give a first restore an authoritative empty manifest for crash recovery."""
    if store.path.exists():
        return
    if (store.notes or store.blocked_write or not store._restore_recovery_allowed):
        raise ValueError("The original notebook metadata could not be verified. Its document text was preserved.")
    baseline = notebook_snapshot(store)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_output(store.path)
    try:
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(baseline, output, ensure_ascii=False, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        try:
            # Create only: a GUI save may have installed the first valid notes
            # after our snapshot. Linking an already complete file is atomic on
            # Windows NTFS and never overwrites that newer manifest.
            os.link(temporary, store.path)
        except FileExistsError:
            pass
        except OSError as exc:
            unsupported = (exc.errno in (errno.EPERM, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP)
                           or getattr(exc, "winerror", None) in (1, 50))
            if not _WINDOWS_CREATE_ONLY_RENAME or not unsupported:
                raise
            try:
                # FAT/exFAT cannot hard-link. Windows rename also refuses an
                # existing destination, so it preserves a concurrent GUI save.
                # POSIX rename replaces destinations and must never be used.
                os.rename(temporary, store.path)
            except FileExistsError:
                pass
    finally:
        temporary.unlink(missing_ok=True)


def prepare_backup(argument, cancel=None):
    """Validate and stage an import; the caller applies its payload transactionally.

    The caller must delete ``prepared_ids`` after a failed note transaction, and
    remove IDs which the merge leaves unused. Existing notes and document IDs are
    never changed by this function.
    """
    store, path = argument["store"], Path(argument["path"]).expanduser()
    _check_cancel(cancel)
    if not path.is_file():
        raise ValueError("Choose an existing notebook backup.")
    if not zipfile.is_zipfile(path):
        with path.open("rb") as handle:
            raw = _read_metadata(handle, path.stat().st_size, cancel)
        payload = _validate_payload(store, raw, allow_documents=False)
        _check_cancel(cancel)
        return {"kind": "backup_prepared", "payload": payload, "prepared_ids": []}

    prepared_ids = []
    restore_id = None
    try:
        with zipfile.ZipFile(path, "r") as archive, tempfile.TemporaryDirectory(prefix="jeffery-restore-") as directory:
            entries, document_entries = {}, {}
            archive_entries = archive.infolist()
            if len(archive_entries) > MAX_NOTES + 1:
                raise ValueError("A notebook backup contains too many files.")
            for entry in archive_entries:
                _check_cancel(cancel)
                name = entry.filename
                match = _DOCUMENT_ENTRY.fullmatch(name)
                if name in entries or name != "notebook.json" and not match:
                    raise ValueError("The notebook backup contains an unexpected or duplicate file.")
                if entry.is_dir() or entry.flag_bits & 1:
                    raise ValueError("The notebook backup contains an unsupported file.")
                entries[name] = entry
                if match:
                    document_entries[match[1]] = entry
            if "notebook.json" not in entries:
                raise ValueError("The notebook backup has no notebook.json metadata.")
            with archive.open(entries["notebook.json"], "r") as handle:
                raw = _read_metadata(handle, entries["notebook.json"].file_size, cancel)
            payload = _validate_payload(store, raw, allow_documents=True)
            references, _ = _references(payload)
            if references.keys() != document_entries.keys():
                raise ValueError("The backup is missing document text or contains unreferenced documents.")
            # Verify all archived bodies before preparing any local document.
            staged = {}
            for identifier, descriptor in references.items():
                target = Path(directory) / f"{identifier}.txt"
                _spool_document(archive, document_entries[identifier], target, descriptor, cancel)
                staged[identifier] = target

            incoming, retained = _merge_plan(store, payload)
            ignored_ids = {note["id"] for note in payload["notes"]} - incoming.keys()
            ignored_notes = [copy.deepcopy(note) for note in retained if note["id"] in ignored_ids]
            payload["notes"] = list(incoming.values())
            needed, _ = _references(payload)
            remapped = {}
            reusable = _reusable_documents(store, cancel)
            verified_existing = {}
            for identifier, expected in references.items():
                _check_cancel(cancel)
                existing = reusable.get(expected)
                if existing:
                    if existing not in verified_existing:
                        # Matching metadata alone does not establish integrity.
                        # Verify every existing page before trusting its ID, with
                        # no additional copy or whole-document allocation.
                        _stream_document(store, existing, expected, lambda chunk: None, cancel)
                        verified_existing[existing] = store.documents.metadata(existing)
                    if identifier in needed:
                        remapped[identifier] = verified_existing[existing]

            unneeded = {note.get("document_id") for note in store.notes if note.get("document_id")}
            retained_ids = {note.get("document_id") for note in retained if note.get("document_id")}
            retained_ids.update(document["document_id"] for document in remapped.values())
            retired_ids = unneeded - retained_ids
            inline = sum(len(note["body"]) for note in [*retained, *payload["notes"]] if not note.get("document_id"))
            if needed.keys() - remapped.keys():
                _ensure_restore_baseline(store)
                restore_id = store.documents.begin_restore(retired_ids, inline)
            for identifier, expected in needed.items():
                _check_cancel(cancel)
                if identifier in remapped:
                    continue
                source_note = next(n for n in payload["notes"] if n.get("document_id") == identifier)
                prepared = store.documents.prepare_import({
                    "text_file": str(staged[identifier]), "title": source_note["title"],
                    "other_note_characters": inline, "restore_id": restore_id,
                }, cancel)
                new_identifier = prepared["document_id"]
                prepared_ids.append(new_identifier)
                if (prepared.get("body_characters") != expected[0]
                        or prepared.get("body_sha256") != expected[1]):
                    raise ValueError("A restored document failed its integrity check.")
                remapped[identifier] = prepared
            for note in payload["notes"]:
                if note.get("document_id"):
                    prepared = remapped[note["document_id"]]
                    note.update({key: prepared[key] for key in
                                 ("document_id", "body_characters", "body_sha256", "body")})
            # Keep ignored IDs in the returned payload for compatibility, using
            # their current local document references rather than staging text
            # which the new-or-newer merge would immediately discard.
            payload["notes"].extend(ignored_notes)
            _check_cancel(cancel)
            result = {"kind": "backup_prepared", "payload": payload, "prepared_ids": prepared_ids}
            if restore_id:
                result["restore_id"] = restore_id
                result["_cleanup"] = lambda: _restore_cleanup(store, restore_id, prepared_ids)
            return result
    except (zipfile.BadZipFile, RuntimeError, EOFError) as exc:
        if restore_id:
            store.documents.abort_restore(restore_id)
        else:
            _cleanup_prepared(store, prepared_ids)
        raise ValueError("The notebook backup is damaged or unsupported. Nothing was imported.") from exc
    except BaseException:
        if restore_id:
            store.documents.abort_restore(restore_id)
        else:
            _cleanup_prepared(store, prepared_ids)
        raise
