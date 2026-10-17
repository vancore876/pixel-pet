"""Authenticated stock lookup and bounded, atomic staged spreadsheet imports."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import re
import uuid
from typing import Annotated, Any

from fastapi import Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from inventory import InventoryError, InventoryStore, MAX_ROWS, validate_rows
from .store import StoreError


class ImportStart(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source: Annotated[StrictStr, Field(max_length=255)] = ""
    expected_revision: Annotated[StrictInt, Field(ge=0, lt=2**63 - 1)]


class ImportRows(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    rows: Annotated[list[dict[str, Any]], Field(min_length=1, max_length=500)]
    offset: Annotated[StrictInt, Field(ge=0, le=MAX_ROWS)] | None = None


class StockMovement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sku: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    kind: Annotated[StrictStr, Field(pattern=r"^(receive|sale|return|count)$")]
    quantity: Annotated[StrictInt, Field(ge=0, le=2**31 - 1)]
    expected_revision: Annotated[StrictInt, Field(ge=0, lt=2**63 - 1)]
    request_id: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{32}$")]
    reason: Annotated[StrictStr, Field(max_length=500)] = ""
    reference: Annotated[StrictStr, Field(max_length=200)] = ""


def register_inventory_routes(app, store, current_user):
    """Use the chat server's local database and existing bearer authentication."""
    inventory = InventoryStore(store.path, clock=store.clock)
    app.state.inventory = inventory
    with closing(inventory._connect()) as connection, connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS inventory_imports (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                source TEXT NOT NULL, expected_revision INTEGER NOT NULL,
                expires_at REAL NOT NULL, result_json TEXT
            );
            CREATE TABLE IF NOT EXISTS inventory_import_rows (
                import_id TEXT NOT NULL, position INTEGER NOT NULL, sku_key TEXT NOT NULL,
                row_json TEXT NOT NULL, PRIMARY KEY(import_id,position), UNIQUE(import_id,sku_key)
            );
            CREATE INDEX IF NOT EXISTS inventory_import_owner ON inventory_imports(user_id,expires_at);
        """)

    @app.exception_handler(InventoryError)
    async def inventory_error(_request, error):
        return JSONResponse({"detail": error.detail}, status_code=error.status)

    def purge(connection):
        connection.execute("DELETE FROM inventory_import_rows WHERE import_id IN (SELECT id FROM inventory_imports WHERE expires_at<=?)", (store.clock(),))
        connection.execute("DELETE FROM inventory_imports WHERE expires_at<=?", (store.clock(),))

    def stage(connection, identifier, user):
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise InventoryError("Inventory import not found. Preview the spreadsheet again.", 404)
        value = connection.execute("SELECT * FROM inventory_imports WHERE id=? AND user_id=? AND expires_at>?",
                                   (identifier, user["id"], store.clock())).fetchone()
        if value is None:
            raise InventoryError("Inventory import expired or was not found. Preview the spreadsheet again.", 404)
        return value

    def require_current_session(connection, request, user):
        # Dependency authentication happens before acquiring the write lock.
        # Recheck the exact session under that lock so an IT sign-out, password
        # reset, or deletion that won the race prevents the queued stock write.
        parts = request.headers.get("authorization", "").split(" ")
        if len(parts) != 2 or not re.fullmatch(r"[A-Za-z0-9_-]{43}", parts[1]):
            raise StoreError(401, "Your session has ended. Sign in again.")
        row = connection.execute("""SELECT u.id,u.username FROM sessions s
            JOIN users u ON u.id=s.user_id
            WHERE s.token_digest=? AND s.expires_at>? AND u.deleted_at IS NULL AND u.id=?""",
            (hashlib.sha256(parts[1].encode("ascii")).hexdigest(), store.clock(), user["id"])).fetchone()
        if row is None:
            raise StoreError(401, "Your session has ended. Sign in again.")
        return dict(row)

    @app.get("/api/inventory/scan")
    def scan_inventory(code: Annotated[str, Query(min_length=1, max_length=128)], user=Depends(current_user)):
        return {"item": inventory.resolve_code(code)}

    @app.get("/api/inventory/movements")
    def inventory_movements(sku: Annotated[str, Query(max_length=128)] = "",
                            limit: Annotated[int, Query(ge=1, le=200)] = 50,
                            before_id: Annotated[int, Query(ge=1, lt=2**63)] | None = None,
                            user=Depends(current_user)):
        return inventory.movements(sku, limit, before_id)

    @app.post("/api/inventory/movements")
    def record_stock_movement(body: StockMovement, request: Request, user=Depends(current_user)):
        with closing(inventory._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            actor = require_current_session(connection, request, user)
            return inventory._record_movement(connection, **body.model_dump(),
                                               actor=actor["username"], actor_id=actor["id"])

    @app.get("/api/inventory/summary")
    def inventory_summary(user=Depends(current_user)):
        return inventory.summary()

    @app.get("/api/inventory")
    def inventory_search(query: Annotated[str, Query(max_length=500)] = "",
                         limit: Annotated[int, Query(ge=1, le=500)] = 100,
                         offset: Annotated[int, Query(ge=0, le=MAX_ROWS)] = 0,
                         low_stock: bool = False, user=Depends(current_user)):
        # Never pair stock from one revision with metadata from another revision.
        for _attempt in range(3):
            before = inventory.summary()
            items = inventory.search(query, limit, offset, low_stock)
            after = inventory.summary()
            if before["revision"] == after["revision"]:
                return {"items": items, "summary": after}
        raise InventoryError("Stock is being updated. Refresh inventory in a moment.", 409)

    @app.get("/api/inventory/items/{sku:path}")
    def inventory_item(sku: str, user=Depends(current_user)):
        item = inventory.get(sku)
        if item is None:
            raise InventoryError("Part number was not found.", 404)
        return item

    @app.post("/api/inventory/imports", status_code=201)
    def start_import(body: ImportStart, request: Request, user=Depends(current_user)):
        source = body.source.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if any(ord(character) < 32 for character in source):
            raise InventoryError("Import source must contain readable characters.")
        identifier = uuid.uuid4().hex
        with closing(inventory._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            require_current_session(connection, request, user)
            purge(connection)
            revision = inventory._summary(connection)["revision"]
            if revision != body.expected_revision:
                raise InventoryError("Stock changed since the preview. Refresh and review the import again.", 409)
            active = connection.execute("SELECT COUNT(*),SUM(user_id=?) FROM inventory_imports WHERE result_json IS NULL", (user["id"],)).fetchone()
            if active[0] >= 32 or (active[1] or 0) >= 4:
                raise InventoryError("Too many stock imports are open. Cancel an import or wait ten minutes.", 429)
            connection.execute("INSERT INTO inventory_imports VALUES(?,?,?,?,?,NULL)",
                               (identifier, user["id"], source, revision, store.clock() + 600))
        return {"id": identifier, "expected_revision": revision, "expires_in": 600}

    @app.post("/api/inventory/imports/{identifier}/rows")
    def add_rows(identifier: str, body: ImportRows, request: Request, user=Depends(current_user)):
        normalized = validate_rows(body.rows)
        serialized = [json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in normalized]
        with closing(inventory._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            require_current_session(connection, request, user)
            purge(connection)
            value = stage(connection, identifier, user)
            if value["result_json"] is not None:
                raise InventoryError("This inventory import is already complete.", 409)
            size = connection.execute("SELECT COUNT(*) FROM inventory_import_rows WHERE import_id=?", (identifier,)).fetchone()[0]
            offset = size if body.offset is None else body.offset
            if offset != size:
                # A timed-out chunk may be resent safely with its original offset.
                previous = [row[0] for row in connection.execute("SELECT row_json FROM inventory_import_rows WHERE import_id=? AND position>=? AND position<? ORDER BY position", (identifier, offset, offset + len(serialized)))]
                if previous == serialized:
                    return {"rows": size, "next_offset": size}
                raise InventoryError("Import rows arrived out of order. Preview the file again.", 409)
            totals = connection.execute("SELECT COUNT(*),COALESCE(SUM(length(CAST(row_json AS BLOB))),0) FROM inventory_import_rows").fetchone()
            if size + len(serialized) > MAX_ROWS or totals[0] + len(serialized) > 200_000 or totals[1] + sum(len(row.encode('utf-8')) for row in serialized) > 64 * 1024 * 1024:
                raise InventoryError("The staged inventory is too large. Import fewer rows at a time.", 413)
            for index, (row, encoded) in enumerate(zip(normalized, serialized), size):
                key = row["sku"].casefold()
                if connection.execute("SELECT 1 FROM inventory_import_rows WHERE import_id=? AND sku_key=?", (identifier, key)).fetchone():
                    raise InventoryError(f"Duplicate part number {row['sku']!r} across import batches.")
                connection.execute("INSERT INTO inventory_import_rows VALUES(?,?,?,?)", (identifier, index, key, encoded))
            connection.execute("UPDATE inventory_imports SET expires_at=? WHERE id=?", (store.clock() + 600, identifier))
        return {"rows": size + len(serialized), "next_offset": size + len(serialized)}

    @app.post("/api/inventory/imports/{identifier}/commit")
    def commit_import(identifier: str, request: Request, user=Depends(current_user)):
        with closing(inventory._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            actor = require_current_session(connection, request, user)
            purge(connection)
            value = stage(connection, identifier, user)
            if value["result_json"] is not None:
                return json.loads(value["result_json"])
            rows = [json.loads(row[0]) for row in connection.execute("SELECT row_json FROM inventory_import_rows WHERE import_id=? ORDER BY position", (identifier,))]
            normalized = validate_rows(rows)
            result = inventory._apply_rows(connection, normalized, value["source"], value["expected_revision"],
                                           actor=actor["username"], actor_id=actor["id"])
            result["imported_by"] = actor["username"]
            # Commit stock and its retry receipt together, including after process restart.
            connection.execute("UPDATE inventory_imports SET result_json=?,expires_at=? WHERE id=?", (json.dumps(result), store.clock() + 86400, identifier))
            connection.execute("DELETE FROM inventory_import_rows WHERE import_id=?", (identifier,))
        return result

    @app.delete("/api/inventory/imports/{identifier}", status_code=204)
    def cancel_import(identifier: str, request: Request, user=Depends(current_user)):
        with closing(inventory._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            require_current_session(connection, request, user)
            stage(connection, identifier, user)
            connection.execute("DELETE FROM inventory_import_rows WHERE import_id=?", (identifier,))
            connection.execute("DELETE FROM inventory_imports WHERE id=?", (identifier,))
        return Response(status_code=204)

    return inventory
