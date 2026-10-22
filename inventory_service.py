"""Background spreadsheet imports and account-scoped office inventory caches."""
from __future__ import annotations

from concurrent.futures import Future
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from queue import Empty, Queue
import re
import threading
import uuid
from urllib.parse import urlencode

from PySide6.QtCore import QObject, QTimer, Signal

from work_chat import _ApiClient, normalize_server_url
from inventory import FIELDS


class _DaemonExecutor:
    """One bounded-lifecycle worker, including when an SMB read blocks in the OS.

    A normal ThreadPoolExecutor installs an interpreter shutdown join, which can
    hold a Windows app open forever on a disconnected network drive. This worker
    uses public Future APIs without registering an interpreter exit handler.
    """

    def __init__(self):
        self._queue = Queue()
        self._lock = threading.Lock()
        self._shutdown = False
        self._thread = threading.Thread(target=self._run, name="inventory", daemon=True)
        self._thread.start()

    def submit(self, task, *args, **kwargs):
        future = Future()
        with self._lock:
            if self._shutdown:
                raise RuntimeError("Inventory worker has stopped.")
            self._queue.put((future, task, args, kwargs))
        return future

    def _run(self):
        while True:
            work = self._queue.get()
            if work is None:
                return
            future, task, args, kwargs = work
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(task(*args, **kwargs))
            except BaseException as exc:
                future.set_exception(exc)

    def shutdown(self, wait=True, *, cancel_futures=False):
        with self._lock:
            if not self._shutdown:
                self._shutdown = True
                if cancel_futures:
                    while True:
                        try:
                            work = self._queue.get_nowait()
                        except Empty:
                            break
                        if work is not None:
                            work[0].cancel()
                self._queue.put(None)
        if wait and threading.current_thread() is not self._thread:
            self._thread.join(timeout=.5)


class InventoryService(QObject):
    changed = Signal()
    status_changed = Signal(str)
    error = Signal(str)
    progress = Signal(int, str)
    busy_changed = Signal(bool)
    preview_ready = Signal(object)
    import_ready = Signal(object)
    import_finished = Signal(object)
    auth_expired = Signal()
    scan_ready = Signal(object)
    history_ready = Signal(object)
    movement_finished = Signal(object)
    scope_reset = Signal()
    _completed = Signal(object)

    def __init__(self, settings, local_path, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.local_path = Path(local_path)
        self.api = _ApiClient(self)
        self.rows, self.summary, self.catalog = [], {}, []
        self.preview = self.plan = None
        self.status = ""
        self.busy = False
        self.shared = bool(settings.get("inventory_shared", True)) if hasattr(settings, "get") else bool(
            getattr(settings, "values", {}).get("inventory_shared", True))
        self.origin, self.token, self.user = "", "", None
        self.online = not self.shared
        self._generation = 0
        self._operation = 0
        self._stopped = False
        self._executor = _DaemonExecutor()
        self._cancel = threading.Event()
        self._completed.connect(self._receive)
        self._path = self.local_path
        self._file = ""
        self._sheet = None
        self._query, self._low_stock = "", False
        self._stage = None
        self._network_rows = []
        self._syncing = False
        self._refresh_sequence = 0
        self._pending_import = None
        self._context_index, self._low_indices = {}, []
        self._scan_sequence = self._history_sequence = 0
        self._scan_reply = None
        self.pending_movement = None
        self.movement_uncertain = False
        self._movement_inflight = False
        self._timer = QTimer(self)
        self._timer.setInterval(60_000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        QTimer.singleShot(0, self.refresh)

    @property
    def scope_label(self):
        if not self.shared:
            return "This PC only · manual stock"
        if not self.token or not self.user:
            return "Office inventory · sign in to Work Chat"
        return "Office inventory · " + str(self.user.get("username", "employee"))

    @property
    def can_write(self):
        return (not self._stopped and not self.pending_movement and
                (not self.shared or bool(self.token and self.user and self.online)))

    def _set_status(self, message):
        self.status = message
        self.status_changed.emit(message)

    def _busy(self, value):
        value = bool(value)
        if value != self.busy:
            self.busy = value
            self.busy_changed.emit(value)

    def _submit(self, task, callback, *, busy=False):
        if self._stopped:
            return
        generation = self._generation
        cancel = self._cancel
        if busy:
            self._busy(True)
        future = self._executor.submit(task, cancel)

        def completed(result):
            try:
                value, failure = result.result(), None
            except Exception as exc:
                value, failure = None, exc
            if not self._stopped:
                self._completed.emit((generation, cancel, callback, value, failure, busy))
        future.add_done_callback(completed)

    def _receive(self, result):
        generation, cancel, callback, value, failure, busy = result
        if self._stopped or generation != self._generation or cancel.is_set():
            return
        if busy:
            self._busy(False)
        if failure is not None:
            self._syncing = False
            self._set_status("Inventory operation failed. Your existing stock has not been replaced.")
            self.error.emit(str(failure)[:500])
            return
        callback(value)

    def _reset_scope(self):
        self.cancel()
        self._generation += 1
        self._cancel = threading.Event()
        self.rows, self.summary, self.catalog = [], {}, []
        self.online = not self.shared
        self._context_index, self._low_indices = {}, []
        self.preview = self.plan = None
        self._file = ""
        self._syncing = False
        self._network_rows = []
        self.invalidate_scan()
        self._history_sequence += 1
        self.pending_movement = None
        self.movement_uncertain = self._movement_inflight = False
        self.scope_reset.emit()
        self.changed.emit()

    def set_shared(self, shared):
        if self.shared == bool(shared):
            return
        self.shared = bool(shared)
        self._reset_scope()
        self._select_path()
        self.refresh()

    def set_session(self, origin, token, user):
        valid = isinstance(user, dict) and isinstance(user.get("id"), (int, str))
        origin = normalize_server_url(origin) if origin and token and valid else ""
        token = token if origin else ""
        user = dict(user) if token else None
        if (origin, token, user) == (self.origin, self.token, self.user):
            return
        self._reset_scope()
        self.origin, self.token, self.user = origin, token, user
        self.api.base_url = origin
        self._select_path()
        self.refresh()

    def _select_path(self):
        if self.shared and self.token and self.user:
            identity = self.origin + "\n" + str(self.user["id"])
            scope = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
            self._path = self.local_path.parent / ("inventory-office-" + scope + ".sqlite")
        else:
            self._path = self.local_path

    def configure(self):
        values = getattr(self.settings, "values", self.settings)
        self.set_shared(bool(values.get("inventory_shared", True)))

    @staticmethod
    def _index(rows):
        index, low = {}, []
        for number, row in enumerate(rows):
            text = " ".join(str(row.get(key, "")) for key in ("sku", "barcode", "description", "location", "supplier", "alternate_part_number", "substitute_part_number"))
            for token in set(re.findall(r"[\w-]+", text.casefold())):
                matches = index.setdefault(token, [])
                if len(matches) < 256:
                    matches.append(number)
            if row.get("low_stock", int(row.get("quantity", 0)) <= int(row.get("reorder_point", 0))):
                low.append(number)
        return index, low

    @staticmethod
    def _read_store(path, cancel):
        from inventory import InventoryStore, InventoryCancelled
        store = InventoryStore(path)
        try:
            rows, offset = [], 0
            while True:
                if cancel.is_set():
                    raise InventoryCancelled("Inventory operation cancelled.")
                batch = store.search(limit=500, offset=offset)
                rows.extend(batch)
                if len(batch) < 500:
                    break
                offset += len(batch)
            return rows, store.summary(), InventoryService._index(rows)
        finally:
            store.close()

    def _load_cache(self, offline=False, refresh_sequence=None):
        path = self._path
        def received(result):
            if refresh_sequence is not None and refresh_sequence != self._refresh_sequence:
                return
            self.catalog, self.summary, indexes = result
            self._context_index, self._low_indices = indexes
            self._filter_rows()
            if offline:
                self.online = False
                self._set_status("Offline · showing this account's cached stock. Reconnect before importing or editing office stock.")
            elif not self.shared:
                self._set_status("Local inventory · import stock, save edits, or review a stock action to change quantities.")
            self.changed.emit()
        self._submit(lambda cancel: self._read_store(path, cancel), received)

    def _filter_rows(self):
        query = self._query.casefold().strip()
        matches = []
        for row in self.catalog:
            if self._low_stock and float(row.get("quantity", 0)) > float(row.get("reorder_point", 0)):
                continue
            if query and query not in " ".join(str(row.get(key, "")) for key in
                    ("sku", "barcode", "description", "location", "supplier", "alternate_part_number", "substitute_part_number")).casefold():
                continue
            matches.append(row)
        self.rows = matches[:1000]
        self.match_count = len(matches)

    def refresh(self, query=None, low_stock=None):
        if self._stopped:
            return
        if query is not None:
            self._query = str(query)[:500]
        if low_stock is not None:
            self._low_stock = bool(low_stock)
        self._filter_rows()
        self.changed.emit()
        if self.busy or self._syncing:
            return
        if not self.shared:
            self._load_cache()
            return
        if not self.token or not self.user:
            self._set_status("Sign in to Work Chat to view and import your office's stock.")
            return
        self._syncing = True
        self._refresh_sequence += 1
        self._network_rows = []
        self._set_status("Refreshing office inventory…")
        self._fetch_page(0, None, self._refresh_sequence)

    def search(self, query="", low_stock=False):
        self._query, self._low_stock = str(query)[:500], bool(low_stock)
        self._filter_rows()
        self.changed.emit()

    def _fetch_page(self, offset, revision, sequence):
        generation = self._generation
        def received(data, error, status):
            if self._stopped or generation != self._generation or sequence != self._refresh_sequence:
                return
            if error:
                self.online = False
                self._syncing = False
                self._network_rows = []
                if status in (401, 403):
                    self._reset_scope()
                    self.token, self.user = "", None
                    self._set_status("Office access has ended. Sign in to Work Chat again.")
                    self.auth_expired.emit()
                elif status == 404:
                    self._set_status("Update the office server to enable shared inventory.")
                else:
                    self._load_cache(offline=True, refresh_sequence=sequence)
                return
            rows = data.get("items") if isinstance(data, dict) else None
            summary = data.get("summary") if isinstance(data, dict) else None
            if not isinstance(rows, list) or not isinstance(summary, dict) or any(not isinstance(row, dict) for row in rows):
                self._syncing = False
                self.error.emit("The office server returned invalid inventory data.")
                return
            current_revision = summary.get("revision")
            if revision is not None and current_revision != revision:
                self._syncing = False
                self._network_rows = []
                self._set_status("Office stock changed during refresh. Refresh again to get a consistent list.")
                return
            self._network_rows.extend(rows)
            if len(self._network_rows) > 100_000:
                self._syncing = False
                self._network_rows = []
                self.error.emit("The office inventory exceeds the 100,000-item limit.")
                return
            if len(rows) == 50:
                self._fetch_page(offset + len(rows), current_revision, sequence)
                return
            snapshot = self._network_rows
            self._network_rows = []
            path = self._path
            def save(cancel):
                from inventory import InventoryStore, InventoryCancelled
                if sequence != self._refresh_sequence:
                    return None
                if cancel.is_set():
                    raise InventoryCancelled("Inventory refresh cancelled.")
                store = InventoryStore(path)
                try:
                    store.replace_snapshot(snapshot, revision=summary.get("revision", 0),
                                           last_import_at=summary.get("last_import_at", ""),
                                           last_import_source=summary.get("last_import_source", ""))
                    return snapshot, summary, self._index(snapshot)
                finally:
                    store.close()
            def saved(result):
                if sequence != self._refresh_sequence:
                    return
                self._syncing = False
                self.online = True
                self.catalog, self.summary, indexes = result
                self._context_index, self._low_indices = indexes
                self._filter_rows()
                self._set_status("Office stock up to date · imports and edits are shared with coworkers.")
                self.changed.emit()
            self._submit(save, saved)
        self.api.request("GET", "/api/inventory?" + urlencode({"limit": 50, "offset": offset}),
                         None, received, self.token)

    def inspect_file(self, path, sheet=None):
        if self.busy or not self.can_write:
            return
        self._file, self._sheet = str(path), sheet
        self.plan = self.preview = None
        self._set_status("Reading spreadsheet headers…")
        def inspect(cancel):
            from inventory import inspect_spreadsheet
            return inspect_spreadsheet(path, sheet=sheet, cancel=cancel)
        def received(preview):
            self.preview = preview
            self.preview_ready.emit(preview)
            self._set_status("Confirm Part Number, Quantity and Location. The file is checked automatically.")
        self._submit(inspect, received, busy=True)

    def prepare_import(self, mapping=None, sheet=None, include_zero=True):
        if not self._file or self.busy or not self.can_write:
            return
        path, store_path = self._file, self._path
        revision = self.summary.get("revision", 0)
        self._set_status("Checking spreadsheet rows…")
        def parse(cancel):
            from inventory import parse_excel, InventoryStore
            plan = parse_excel(path, mapping=mapping, sheet=sheet or self._sheet, cancel=cancel, include_zero=include_zero,
                               progress=lambda value, total: self.progress.emit(int(value * 100 / max(1, total)), "Checking stock rows…"))
            if plan.valid:
                store = InventoryStore(store_path)
                try:
                    result = store.preview_import(plan)
                finally:
                    store.close()
            else:
                result = {"created": 0, "updated": 0, "unchanged": 0, "revision": revision}
            return plan, result
        def received(result):
            self.plan, self.import_preview = result
            self._pending_import = None
            self.import_ready.emit({"plan": self.plan, "preview": self.import_preview})
            self._set_status("Ready to confirm import. Existing stock stays unchanged until you press Confirm import."
                             if self.plan.valid else "Fix the row errors shown below before confirming import.")
        self._submit(parse, received, busy=True)

    def commit_import(self):
        if not self.plan or self.busy or not self.can_write or not self.plan.valid:
            return
        self._commit_rows(list(self.plan.rows), self.plan.source, self.import_preview.get("revision"))

    def update_item(self, item, expected_revision=None):
        if self.busy or not self.can_write:
            return
        self._commit_rows([dict(item)], "Manual stock edit", self.summary.get("revision", 0)
                          if expected_revision is None else expected_revision)

    def _commit_rows(self, rows, source, revision):
        if self.shared:
            self._commit_remote(rows, source, revision)
            return
        path = self._path
        def commit(cancel):
            from inventory import InventoryStore, InventoryCancelled
            if cancel.is_set():
                raise InventoryCancelled("Inventory import cancelled.")
            store = InventoryStore(path)
            try:
                return store.import_rows(rows, source=source, expected_revision=revision)
            finally:
                store.close()
        def received(result):
            self.plan = None
            self.import_finished.emit(result)
            self.refresh()
        self._submit(commit, received, busy=True)

    def _commit_remote(self, rows, source, revision):
        self._busy(True)
        self._set_status("Uploading stock import for review by the office server…")
        generation = self._generation
        operation = self._operation
        self._pending_import = rows
        def current():
            return not self._stopped and generation == self._generation and operation == self._operation
        def failed(error, status=0):
            if status in (401, 403):
                self._reset_scope()
                self.token, self.user = "", None
                self.auth_expired.emit()
            self._busy(False)
            self._set_status("Office import was not confirmed. Refresh stock before retrying.")
            self.error.emit(error)
        def committed(data, error, status):
            if not current():
                return
            self._stage = None
            self._busy(False)
            self._pending_import = None
            if error:
                failed(error, status)
                self.refresh()
                return
            self.plan = None
            self._refresh_sequence += 1
            self._syncing = False
            self.import_finished.emit(data)
            self.refresh()
        def chunk(offset):
            if not current():
                return
            if offset >= len(rows):
                self.api.request("POST", f"/api/inventory/imports/{self._stage}/commit", {}, committed, self.token)
                return
            batch, size = [], 0
            for row in rows[offset:offset + 50]:
                row_size = len(json.dumps(row, ensure_ascii=False).encode("utf-8")) + 2
                if batch and size + row_size > 180 * 1024:
                    break
                batch.append(row)
                size += row_size
            next_offset = offset + len(batch)
            def uploaded(data, error, status):
                if not current():
                    return
                if error:
                    self._abort_stage()
                    failed(error, status)
                    return
                self.progress.emit(int(next_offset * 100 / max(1, len(rows))), "Uploading stock rows…")
                chunk(next_offset)
            self.api.request("POST", f"/api/inventory/imports/{self._stage}/rows",
                             {"rows": batch, "offset": offset}, uploaded, self.token)
        def created(data, error, status):
            if not current():
                return
            stage = data.get("id") if isinstance(data, dict) else None
            if error or not isinstance(stage, str) or not stage.isalnum() or len(stage) > 100:
                failed(error or "The server could not start this stock import.", status)
                return
            self._stage = stage
            chunk(0)
        self.api.request("POST", "/api/inventory/imports", {"source": str(source)[:200],
                         "expected_revision": revision}, created, self.token)

    def _abort_stage(self):
        if self._stage and self.token:
            stage, self._stage = self._stage, None
            self.api.request("DELETE", "/api/inventory/imports/" + stage, {}, lambda *args: None, self.token)

    def invalidate_scan(self):
        """Stop publishing an earlier scan without cancelling unrelated requests."""
        self._scan_sequence += 1
        reply, self._scan_reply = self._scan_reply, None
        if reply is not None and hasattr(reply, "abort"):
            try:
                reply.abort()
            except RuntimeError:  # Qt may already have deleted the reply.
                pass

    def _expire_access(self):
        self._reset_scope()
        self.token, self.user = "", None
        self._set_status("Office access has ended. Sign in to Work Chat again.")
        self.auth_expired.emit()

    def _cached_scan(self, code):
        matches = [dict(row) for row in self.catalog if row.get("barcode") == code or
                   str(row.get("sku", "")).casefold() == code.casefold()]
        try:
            saved = datetime.fromtimestamp(self._path.stat().st_mtime, timezone.utc).astimezone().isoformat(timespec="minutes")
        except OSError:
            saved = "unknown time"
        return {"code": code, "item": matches[0] if len(matches) == 1 else None,
                "cached": True, "saved_at": saved,
                "error": "This code matches more than one part. Correct the stock list." if len(matches) > 1 else
                         ("No exact match in this account's cached stock." if not matches else "")}

    def scan_code(self, code):
        """Resolve one exact scanner code. A scan can never change stock."""
        self.invalidate_scan()
        sequence, generation = self._scan_sequence, self._generation
        if self._stopped:
            return
        if not isinstance(code, str) or not code.strip() or len(code) > 128 or not all(c.isprintable() for c in code):
            self.scan_ready.emit({"code": code, "item": None, "error": "Use a readable barcode or part number of 1–128 characters."})
            return
        def current():
            return not self._stopped and sequence == self._scan_sequence and generation == self._generation
        def received(data, error="", status=200):
            if not current():
                return
            if error:
                if status in (401, 403):
                    self._expire_access()
                    return
                if self.shared and (status == 0 or status >= 500):
                    self.online = False
                    self.changed.emit()
                    self.scan_ready.emit(self._cached_scan(code))
                    return
                self.scan_ready.emit({"code": code, "item": None, "error": str(error)})
                return
            item = data.get("item") if isinstance(data, dict) else None
            if not isinstance(item, dict) or not item.get("sku"):
                self.scan_ready.emit({"code": code, "item": None, "error": "No exact part match was returned."})
                return
            if self.shared:
                self.online = True
                self.changed.emit()
            self.scan_ready.emit({"code": code, "item": item, "cached": False})
        if self.shared:
            if not self.token or not self.user:
                received(None, "Sign in to Work Chat before scanning office stock.", 400)
                return
            self._scan_reply = self.api.request("GET", "/api/inventory/scan?" + urlencode({"code": code}),
                                                None, received, self.token)
        else:
            path = self._path
            def lookup(cancel):
                from inventory import InventoryStore
                store = None
                try:
                    store = InventoryStore(path)
                    return ({"item": store.resolve_code(code)}, "", 200)
                except Exception as exc:
                    return (None, str(exc), getattr(exc, "status", 422))
                finally:
                    if store is not None:
                        store.close()
            self._submit(lookup, lambda result: received(*result))

    def load_history(self, sku="", before_id=None):
        self._history_sequence += 1
        sequence, generation = self._history_sequence, self._generation
        def received(data, error="", status=200):
            if self._stopped or sequence != self._history_sequence or generation != self._generation:
                return
            if status in (401, 403):
                self._expire_access()
                return
            if error:
                self.history_ready.emit({"error": str(error), "movements": []})
            elif isinstance(data, dict) and isinstance(data.get("movements"), list):
                self.history_ready.emit({**data, "append": before_id is not None, "sku": sku})
            else:
                self.history_ready.emit({"error": "The server returned invalid stock history.", "movements": []})
        if self.shared:
            if not self.token or not self.user:
                received(None, "Sign in to Work Chat to view shared stock history.", 400)
                return
            params = {"sku": sku, "limit": 50}
            if before_id is not None:
                params["before_id"] = before_id
            self.api.request("GET", "/api/inventory/movements?" + urlencode(params), None, received, self.token)
        else:
            path = self._path
            def history(cancel):
                from inventory import InventoryStore
                store = InventoryStore(path)
                try:
                    return (store.movements(sku=sku, limit=50, before_id=before_id), "", 200)
                except Exception as exc:
                    return (None, str(exc), getattr(exc, "status", 422))
                finally:
                    store.close()
            self._submit(history, lambda result: received(*result))

    def record_movement(self, item, kind, quantity, reason="", reference=""):
        if self.busy or not self.can_write or self.pending_movement:
            return False
        self.pending_movement = {"sku": item["sku"], "kind": kind, "quantity": quantity,
                                 "reason": reason, "reference": reference,
                                 "expected_revision": item["revision"], "request_id": uuid.uuid4().hex}
        self._send_movement()
        return True

    def retry_movement(self):
        # Retain the ORIGINAL payload and ID even if another scan has since shown
        # a newer item revision. This cannot accidentally apply a timed-out sale twice.
        if self.pending_movement and self.movement_uncertain and not self.busy and not self._stopped:
            if not self.shared or (self.token and self.user):
                self._send_movement()

    def _send_movement(self):
        payload = dict(self.pending_movement)
        generation, operation = self._generation, self._operation
        self._movement_inflight = True
        self.movement_uncertain = False
        self._busy(True)
        def received(data, error="", status=200):
            if self._stopped or generation != self._generation or operation != self._operation:
                return
            self._movement_inflight = False
            self._busy(False)
            if status in (401, 403):
                self._expire_access()
                return
            valid = isinstance(data, dict) and isinstance(data.get("item"), dict) and isinstance(data.get("movement"), dict)
            if error or not valid:
                uncertain = status == 0 or status >= 500 or not error
                self.movement_uncertain = uncertain
                if not uncertain:
                    self.pending_movement = None
                self.movement_finished.emit({"error": str(error or "Invalid server response."),
                                             "uncertain": uncertain, "status": status, "request": payload})
                self.changed.emit()
                return
            self.pending_movement = None
            self.movement_uncertain = False
            self._refresh_sequence += 1
            self._syncing = False
            item = data["item"]
            self.catalog = [item if row.get("sku") == item.get("sku") else row for row in self.catalog]
            if isinstance(data.get("summary"), dict):
                self.summary = data["summary"]
            self._context_index, self._low_indices = self._index(self.catalog)
            self._filter_rows()
            self.movement_finished.emit(data)
            self.refresh()
        if self.shared:
            self.api.request("POST", "/api/inventory/movements", payload, received, self.token)
        else:
            path = self._path
            def write(cancel):
                from inventory import InventoryStore
                if cancel.is_set():
                    return (None, "Stock change cancelled before submission.", 400)
                store = None
                try:
                    store = InventoryStore(path)
                    return (store.record_movement(**payload, actor="Local user"), "", 200)
                except Exception as exc:
                    return (None, str(exc), getattr(exc, "status", 500))
                finally:
                    if store is not None:
                        store.close()
            self._submit(write, lambda result: received(*result))

    def cancel(self):
        self._operation += 1
        self._refresh_sequence += 1
        self._cancel.set()
        self._cancel = threading.Event()
        self.api.cancel_all()
        self.invalidate_scan()
        self._history_sequence += 1
        if self._movement_inflight:
            self._movement_inflight = False
            self.movement_uncertain = True
            self.movement_finished.emit({"error": "Submission interrupted. Check the original change before retrying.",
                                         "uncertain": True, "request": self.pending_movement})
        self._abort_stage()
        self._syncing = False
        self._busy(False)
        self._set_status("Inventory operation cancelled. Refresh to check current office stock.")

    def context(self, query="", limit=8):
        """Return only memory-cached stock; callers must apply work AI consent."""
        if self.shared and (not self.token or not self.user):
            return []
        terms = set(re.findall(r"[\w-]+", str(query).casefold()))
        general = not terms or bool(terms & {"restock", "restocking", "reorder", "reordering"}) or {"low", "stock"} <= terms
        scores = {}
        for term in terms:
            for number in self._context_index.get(term, ()):
                scores[number] = scores.get(number, 0) + 1
        if general:
            for number in self._low_indices[:256]:
                scores[number] = scores.get(number, 0) + 1
        ranked = sorted(scores, key=lambda number: scores[number], reverse=True)
        fields = (*FIELDS, "suggested_restock", "low_stock", "updated_at", "source")
        return [{key: self.catalog[number].get(key, "") for key in fields}
                for number in ranked[:max(1, min(int(limit), 20))]]

    def stop(self):
        if self._stopped:
            return
        self._timer.stop()
        self.cancel()
        self._stopped = True
        self._executor.shutdown(wait=True, cancel_futures=True)
