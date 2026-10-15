"""Local, structured parts inventory and explicit spreadsheet import planning.

Workbooks are read for cell values only. Import plans never change stock; callers
must explicitly commit them. This module has no AI, network, or order side effects.
"""
from __future__ import annotations

from contextlib import closing
import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import io
from pathlib import Path
import re
import sqlite3
import time
import zipfile


MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
MAX_ROWS = 100_000
MAX_COLUMNS = 100
FIELDS = ("sku", "description", "quantity", "location", "reorder_point",
          "reorder_quantity", "target_stock", "supplier")
TEXT_LIMITS = {"sku": 128, "description": 2000, "location": 500, "supplier": 500}
COUNT_LIMIT = 2**31 - 1
ALIASES = {
    "sku": ("sku", "part", "partnumber", "partno", "partcode", "itemcode", "itemnumber", "itemno", "productcode", "stockcode"),
    "description": ("description", "item", "itemname", "product", "productname", "partname", "name"),
    "quantity": ("quantity", "qty", "stock", "onhand", "quantityonhand", "stockquantity", "instock", "balance"),
    "location": ("location", "bin", "binlocation", "shelf", "shelflocation", "aisle", "rack"),
    "reorder_point": ("reorderpoint", "reorderlevel", "minimum", "minimumstock", "minstock", "lowstock", "minqty"),
    "reorder_quantity": ("reorderquantity", "reorderqty", "orderquantity", "restockquantity", "restockqty"),
    "target_stock": ("targetstock", "targetquantity", "targetqty", "maximumstock", "maxstock"),
    "supplier": ("supplier", "vendor", "suppliername", "vendorname"),
}


class InventoryError(ValueError):
    def __init__(self, detail, status=422):
        super().__init__(detail)
        self.detail, self.status = detail, status


class InventoryCancelled(InventoryError):
    pass


@dataclass
class InventoryPreview:
    headers: list[str]
    sample_rows: list[list]
    sheet_names: list[str]
    total_rows: int
    mapping: dict = field(default_factory=dict)
    source: str = ""
    sheet: str = ""


@dataclass
class InventoryImportPlan:
    rows: list[dict]
    errors: list[str]
    warnings: list[str]
    source: str
    headers: list[str]
    mapping: dict
    sheet: str = ""

    @property
    def valid(self):
        return bool(self.rows) and not self.errors


def _cancelled(cancel):
    if cancel is not None and (cancel() if callable(cancel) else cancel.is_set()):
        raise InventoryCancelled("Inventory import was cancelled.")


def _header_key(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower().replace("#", "number"))


def suggest_mapping(headers):
    result = {}
    for target, aliases in ALIASES.items():
        matches = [index for index, header in enumerate(headers) if _header_key(header) in aliases]
        if len(matches) == 1:
            result[target] = matches[0]
    return result


def _text(value, name, required=False):
    if value is None:
        value = ""
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise InventoryError(f"{name.replace('_', ' ').title()} must contain text or a simple number.")
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if (required and not value) or len(value) > TEXT_LIMITS[name] or any(ord(c) < 32 and c not in "\t\n\r" for c in value):
        raise InventoryError(f"{name.replace('_', ' ').title()} must contain {'1–' if required else 'at most '}{TEXT_LIMITS[name]} readable characters.")
    return value


def _count(value, name, nullable=False):
    if value is None or value == "":
        return None if nullable else 0
    if isinstance(value, bool):
        raise InventoryError(f"{name.replace('_', ' ').title()} must be a nonnegative whole number.")
    if isinstance(value, str):
        value = value.strip()
        if "," in value:
            if not re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.0+)?", value):
                raise InventoryError(f"{name.replace('_', ' ').title()} has an invalid number format.")
            value = value.replace(",", "")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise InventoryError(f"{name.replace('_', ' ').title()} must be a nonnegative whole number.") from None
    if not number.is_finite() or number < 0 or number > COUNT_LIMIT or number != number.to_integral_value():
        raise InventoryError(f"{name.replace('_', ' ').title()} must be a nonnegative whole number up to {COUNT_LIMIT}.")
    return int(number)


def validate_row(row):
    if not isinstance(row, dict) or any(key not in FIELDS for key in row):
        raise InventoryError("Inventory rows must contain only recognized stock fields.")
    if "sku" not in row or "quantity" not in row or row["quantity"] is None or row["quantity"] == "":
        raise InventoryError("Each stock row needs a part number and quantity.")
    result = {"sku": _text(row["sku"], "sku", True), "quantity": _count(row["quantity"], "quantity")}
    for name in FIELDS:
        if name in result or name not in row:
            continue
        result[name] = _text(row[name], name) if name in TEXT_LIMITS else _count(row[name], name, name == "target_stock")
    return result


def validate_rows(rows):
    if not isinstance(rows, (list, tuple)) or not rows or len(rows) > MAX_ROWS:
        raise InventoryError(f"Import must contain 1–{MAX_ROWS:,} rows.")
    normalized, seen = [], {}
    for index, row in enumerate(rows, 1):
        try:
            item = validate_row(row)
        except InventoryError as error:
            raise InventoryError(f"Row {index}: {error}") from None
        key = item["sku"].casefold()
        if key in seen:
            raise InventoryError(f"Row {index}: duplicate part number {item['sku']!r} (also row {seen[key]}).")
        seen[key] = index
        normalized.append(item)
    return normalized


def _cell_value(cell):
    value = cell.value
    # Excel stores numeric part codes without their displayed leading zeroes.
    # Honor only simple zero-padding formats; do not evaluate format expressions.
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 and value == int(value):
        pattern = str(getattr(cell, "number_format", ""))
        if re.fullmatch(r"0{2,128}", pattern):
            return str(int(value)).zfill(len(pattern))
        if isinstance(value, float):
            return int(value)
    return value


def _read_spreadsheet(path, sheet=None, cancel=None):
    path = Path(path)
    if not path.is_file():
        raise InventoryError("Choose an existing spreadsheet file.")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise InventoryError("Inventory files must be 50 MiB or smaller.")
    suffix = path.suffix.lower()
    _cancelled(cancel)
    if suffix in (".xlsx", ".xlsm"):
        try:
            import openpyxl
        except ImportError:
            raise InventoryError("Excel import needs openpyxl. Install the desktop requirements.") from None
        try:
            with zipfile.ZipFile(path) as archive:
                if sum(item.file_size for item in archive.infolist()) > MAX_UNCOMPRESSED_BYTES:
                    raise InventoryError("The expanded Excel workbook is too large.")
            book = openpyxl.load_workbook(path, read_only=True, data_only=True, keep_links=False)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
            raise InventoryError("Cannot read this Excel workbook. Save a fresh .xlsx copy and try again.") from error
        try:
            sheets = list(book.sheetnames)
            if sheet is not None and sheet not in sheets:
                raise InventoryError("The selected worksheet does not exist.")
            selected = sheet or sheets[0]
            worksheet = book[selected]
            rows = []
            # Ignore untrusted sheet dimensions; cap actual iteration ourselves.
            worksheet.reset_dimensions()
            for index, cells in enumerate(worksheet.iter_rows(max_col=MAX_COLUMNS + 1)):
                if index > MAX_ROWS:
                    raise InventoryError(f"Import at most {MAX_ROWS:,} data rows at a time.")
                _cancelled(cancel)
                values = [_cell_value(cell) for cell in cells]
                if len(values) > MAX_COLUMNS and values[-1] not in (None, ""):
                    raise InventoryError(f"Import at most {MAX_COLUMNS} columns.")
                rows.append(values[:MAX_COLUMNS])
            return rows, sheets, selected
        finally:
            book.close()
    if suffix == ".xls":
        try:
            import xlrd
        except ImportError:
            raise InventoryError("Legacy Excel import needs xlrd. Install the desktop requirements.") from None
        try:
            book = xlrd.open_workbook(str(path), on_demand=True, formatting_info=True)
        except Exception as error:
            raise InventoryError("Cannot read this .xls workbook. Save a fresh Excel copy and try again.") from error
        try:
            sheets = book.sheet_names()
            if sheet is not None and sheet not in sheets:
                raise InventoryError("The selected worksheet does not exist.")
            selected = sheet or sheets[0]
            worksheet = book.sheet_by_name(selected)
            if worksheet.nrows > MAX_ROWS + 1 or worksheet.ncols > MAX_COLUMNS:
                raise InventoryError(f"Import at most {MAX_ROWS:,} rows and {MAX_COLUMNS} columns.")
            rows = []
            for index in range(worksheet.nrows):
                _cancelled(cancel)
                values = []
                for column in range(worksheet.ncols):
                    cell = worksheet.cell(index, column)
                    value = cell.value
                    if cell.ctype == xlrd.XL_CELL_NUMBER and value >= 0 and value == int(value):
                        fmt = book.format_map.get(book.xf_list[worksheet.cell_xf_index(index, column)].format_key)
                        pattern = fmt.format_str if fmt else ""
                        value = str(int(value)).zfill(len(pattern)) if re.fullmatch(r"0{2,128}", pattern) else int(value)
                    values.append(value)
                rows.append(values)
            return rows, sheets, selected
        finally:
            book.release_resources()
    if suffix == ".csv":
        raw = path.read_bytes()
        _cancelled(cancel)
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                content = raw.decode("utf-16") if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else raw.decode("cp1252")
            except UnicodeDecodeError:
                raise InventoryError("Save this CSV as UTF-8 and try again.") from None
        try:
            dialect = csv.Sniffer().sniff(content[:8192], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = []
        try:
            for index, row in enumerate(csv.reader(io.StringIO(content), dialect)):
                _cancelled(cancel)
                if index > MAX_ROWS or len(row) > MAX_COLUMNS:
                    raise InventoryError(f"Import at most {MAX_ROWS:,} rows and {MAX_COLUMNS} columns.")
                rows.append(row)
        except csv.Error:
            raise InventoryError("Cannot read this CSV. Check its quoting and delimiters.") from None
        return rows, [], ""
    raise InventoryError("Choose an .xlsx, .xlsm, .xls, or .csv file.")


def _headers(rows):
    if not rows:
        raise InventoryError("The spreadsheet is empty.")
    while rows and all(value is None or str(value).strip() == "" for value in rows[-1]):
        rows.pop()
    if not rows:
        raise InventoryError("The spreadsheet is empty.")
    headers = [str(value).strip() if value is not None else "" for value in rows[0]]
    while headers and not headers[-1]:
        headers.pop()
    if not headers:
        raise InventoryError("The first row must contain column headings.")
    return headers


def inspect_spreadsheet(path, sheet=None, cancel=None):
    rows, sheets, selected = _read_spreadsheet(path, sheet, cancel)
    headers = _headers(rows)
    return InventoryPreview(headers, [row[:len(headers)] for row in rows[1:11]], sheets,
                            max(0, len(rows) - 1), suggest_mapping(headers), Path(path).name, selected)


def parse_excel(path, mapping=None, sheet=None, cancel=None, progress=None):
    rows, _sheets, selected = _read_spreadsheet(path, sheet, cancel)
    headers = _headers(rows)
    mapping = suggest_mapping(headers) if mapping is None else dict(mapping)
    resolved = {}
    errors, warnings, result, seen = [], [], [], {}
    for name, column in mapping.items():
        if name not in FIELDS or column is None or column == "":
            continue
        if isinstance(column, str):
            matches = [index for index, heading in enumerate(headers) if heading == column]
            if len(matches) != 1:
                errors.append(f"Choose one unambiguous column for {name.replace('_', ' ')}.")
                continue
            column = matches[0]
        if isinstance(column, bool) or not isinstance(column, int) or not 0 <= column < len(headers):
            errors.append(f"Invalid column for {name.replace('_', ' ')}.")
        else:
            resolved[name] = column
    for name in ("sku", "quantity"):
        if name not in resolved:
            errors.append(f"Map a column to {name.replace('_', ' ')} before importing.")
    if errors:
        return InventoryImportPlan([], errors, warnings, Path(path).name, headers, resolved, selected)
    missing = [name.replace("_", " ") for name in ("description", "location", "reorder_point", "supplier") if name not in resolved]
    if missing:
        warnings.append("Columns not mapped: " + ", ".join(missing) + ". Existing values stay unchanged; new parts use blank text and a restock point of zero.")
    for index, values in enumerate(rows[1:], 2):
        _cancelled(cancel)
        if all(value is None or str(value).strip() == "" for value in values):
            continue
        source = {name: values[column] if column < len(values) else None for name, column in resolved.items()}
        try:
            item = validate_row(source)
            key = item["sku"].casefold()
            if key in seen:
                raise InventoryError(f"Duplicate part number {item['sku']!r}; also appears on row {seen[key]}.")
            seen[key] = index
            result.append(item)
        except InventoryError as error:
            if len(errors) < 100:
                errors.append(f"Row {index}: {error}")
        if progress is not None and index % 250 == 0:
            progress(index - 1, len(rows) - 1)
    if not result and not errors:
        errors.append("The spreadsheet has no stock rows to import.")
    return InventoryImportPlan(result, errors, warnings, Path(path).name, headers, resolved, selected)


class InventoryStore:
    def __init__(self, path, *, clock=time.time):
        if str(path).startswith(("\\\\", "//")):
            raise InventoryError("Keep the inventory database on this PC's local disk.")
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        with closing(self._connect()) as connection, connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS inventory_state (
                    id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL DEFAULT 0,
                    last_import_at TEXT NOT NULL DEFAULT '', last_import_source TEXT NOT NULL DEFAULT ''
                );
                INSERT OR IGNORE INTO inventory_state(id) VALUES(1);
                CREATE TABLE IF NOT EXISTS inventory_items (
                    key TEXT PRIMARY KEY, sku TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                    quantity INTEGER NOT NULL, location TEXT NOT NULL DEFAULT '',
                    reorder_point INTEGER NOT NULL DEFAULT 0, reorder_quantity INTEGER NOT NULL DEFAULT 0,
                    target_stock INTEGER, supplier TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL, source TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS inventory_description ON inventory_items(description COLLATE NOCASE);
            """)

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _public(row):
        if row is None:
            return None
        item = dict(row)
        item.pop("key", None)
        item["low_stock"] = item["quantity"] <= item["reorder_point"]
        target = item["target_stock"] if item["target_stock"] is not None else item["reorder_point"] + 1
        item["suggested_restock"] = max(item["reorder_quantity"], target - item["quantity"], 0) if item["low_stock"] else 0
        return item

    def get(self, sku):
        with closing(self._connect()) as connection:
            return self._public(connection.execute("SELECT * FROM inventory_items WHERE key=?", (str(sku).strip().casefold(),)).fetchone())

    def search(self, query="", limit=100, offset=0, low_stock=False):
        if not isinstance(query, str) or len(query) > 500:
            raise InventoryError("Search must contain at most 500 characters.")
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        where, parameters = [], []
        for word in query.split():
            pattern = "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("(sku LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' OR location LIKE ? ESCAPE '\\' OR supplier LIKE ? ESCAPE '\\')")
            parameters.extend([pattern] * 4)
        if low_stock:
            where.append("quantity <= reorder_point")
        sql = "SELECT * FROM inventory_items" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY sku COLLATE NOCASE LIMIT ? OFFSET ?"
        with closing(self._connect()) as connection:
            return [self._public(row) for row in connection.execute(sql, (*parameters, limit, offset))]

    def low_stock(self, limit=100, offset=0):
        return self.search(limit=limit, offset=offset, low_stock=True)

    def summary(self):
        with closing(self._connect()) as connection:
            state = dict(connection.execute("SELECT revision,last_import_at,last_import_source FROM inventory_state WHERE id=1").fetchone())
            totals = connection.execute("SELECT COUNT(*), COALESCE(SUM(quantity),0), COALESCE(SUM(quantity<=reorder_point),0) FROM inventory_items").fetchone()
        return {**state, "item_count": totals[0], "total_quantity": totals[1], "low_stock_count": totals[2]}

    def preview_import(self, plan):
        if isinstance(plan, InventoryImportPlan):
            if not plan.valid:
                raise InventoryError("Resolve spreadsheet errors before importing.")
            rows = plan.rows
        else:
            rows = plan
        normalized = validate_rows(rows)
        created = updated = unchanged = 0
        # Report changed fields, including replacements of existing quantities.
        changes = []
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            summary = self._summary(connection)
            for item in normalized:
                old = connection.execute("SELECT * FROM inventory_items WHERE key=?", (item["sku"].casefold(),)).fetchone()
                if old is None:
                    created += 1
                    if len(changes) < 100:
                        changes.append({"sku": item["sku"], "kind": "new", "before": None, "after": item})
                else:
                    fields = [name for name, value in item.items() if old[name] != value]
                    if fields:
                        updated += 1
                        if len(changes) < 100:
                            changes.append({"sku": item["sku"], "kind": "update", "fields": fields,
                                            "before": {name: old[name] for name in fields}, "after": {name: item[name] for name in fields}})
                    else:
                        unchanged += 1
        return {"revision": summary["revision"], "created": created, "updated": updated,
                "unchanged": unchanged, "rows": len(normalized), "changes": changes}

    def close(self):
        """Connections are short lived; provided for service lifecycle symmetry."""

    @staticmethod
    def _summary(connection):
        state = dict(connection.execute("SELECT revision,last_import_at,last_import_source FROM inventory_state WHERE id=1").fetchone())
        totals = connection.execute("SELECT COUNT(*),COALESCE(SUM(quantity),0),COALESCE(SUM(quantity<=reorder_point),0) FROM inventory_items").fetchone()
        return {**state, "item_count": totals[0], "total_quantity": totals[1], "low_stock_count": totals[2]}

    def _apply_rows(self, connection, normalized, source, expected_revision):
        """Apply already validated rows in the caller's write transaction."""
        timestamp = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        revision = connection.execute("SELECT revision FROM inventory_state WHERE id=1").fetchone()[0]
        if expected_revision is not None and expected_revision != revision:
            raise InventoryError("Stock changed since the preview. Refresh and review this import again.", 409)
        existing_count = connection.execute("SELECT COUNT(*) FROM inventory_items").fetchone()[0]
        incoming_new = sum(not connection.execute("SELECT 1 FROM inventory_items WHERE key=?", (row["sku"].casefold(),)).fetchone()
                           for row in normalized)
        if existing_count + incoming_new > MAX_ROWS:
            raise InventoryError(f"The inventory can contain at most {MAX_ROWS:,} different parts.", 413)
        revision += 1
        created = updated = unchanged = 0
        for item in normalized:
            key = item["sku"].casefold()
            old = connection.execute("SELECT * FROM inventory_items WHERE key=?", (key,)).fetchone()
            if old is None:
                created += 1
                values = {"description": "", "location": "", "reorder_point": 0,
                          "reorder_quantity": 0, "target_stock": None, "supplier": "", **item}
                connection.execute("""INSERT INTO inventory_items(key,sku,description,quantity,location,reorder_point,reorder_quantity,target_stock,supplier,revision,updated_at,source)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (key, *(values[name] for name in FIELDS), revision, timestamp, source))
            elif any(old[name] != value for name, value in item.items()):
                updated += 1
                assignments = ",".join(f"{name}=?" for name in item)
                connection.execute(f"UPDATE inventory_items SET {assignments},revision=?,updated_at=?,source=? WHERE key=?",
                                   (*item.values(), revision, timestamp, source, key))
            else:
                unchanged += 1
        connection.execute("UPDATE inventory_state SET revision=?,last_import_at=?,last_import_source=? WHERE id=1", (revision, timestamp, source))
        return {**self._summary(connection), "created": created, "updated": updated, "unchanged": unchanged, "rows": len(normalized)}

    def import_rows(self, rows, source="", expected_revision=None):
        normalized = validate_rows(rows)
        source = str(source).strip()
        if len(source) > 255 or any(ord(c) < 32 for c in source):
            raise InventoryError("Import source must be a readable name of at most 255 characters.")
        if expected_revision is not None and (isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0):
            raise InventoryError("Expected revision must be a nonnegative whole number.")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._apply_rows(connection, normalized, source, expected_revision)

    def replace_snapshot(self, rows, revision, last_import_at="", last_import_source=""):
        """Replace an account-scoped local cache with an authoritative server snapshot."""
        if not isinstance(rows, (list, tuple)) or len(rows) > MAX_ROWS:
            raise InventoryError("The stock snapshot is too large.")
        normalized = validate_rows([{name: row[name] for name in FIELDS if name in row} for row in rows]) if rows else []
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise InventoryError("Invalid inventory snapshot revision.")
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM inventory_items")
            if normalized:
                self._apply_rows(connection, normalized, str(last_import_source)[:255], None)
            connection.execute("UPDATE inventory_items SET revision=?", (revision,))
            for row in rows:
                row_revision = row.get("revision", revision)
                updated_at = row.get("updated_at", last_import_at)
                source = row.get("source", last_import_source)
                if isinstance(row_revision, bool) or not isinstance(row_revision, int) or not 0 <= row_revision <= revision:
                    raise InventoryError("Invalid cached item revision.")
                if not isinstance(updated_at, str) or len(updated_at) > 80 or not isinstance(source, str) or len(source) > 255:
                    raise InventoryError("Invalid cached item source metadata.")
                connection.execute("UPDATE inventory_items SET revision=?,updated_at=?,source=? WHERE key=?",
                                   (row_revision, updated_at, source, str(row["sku"]).strip().casefold()))
            connection.execute("UPDATE inventory_state SET revision=?,last_import_at=?,last_import_source=? WHERE id=1",
                               (revision, str(last_import_at)[:80], str(last_import_source)[:255]))
        return self.summary()
