"""Local, dated parts catalogue snapshots for offline lookup.

The imported catalogue is deliberately separate from live office inventory.
Imports are transactional; a malformed replacement cannot erase an older copy.
"""
from __future__ import annotations

import csv
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3


FIELDS = {
    'PartNumber': 'part_number', 'Description': 'description',
    'AlternatePartNumber': 'alternate_part_number', 'SubstitutePartNumber': 'substitute_part_number',
    'IsObsolete': 'is_obsolete', 'RetailPrice': 'retail_price',
    'WholeSalePrice': 'wholesale_price', 'CostPrice': 'cost_price',
    'StoreLocation': 'store_location', 'StoreQuantity': 'store_quantity',
    'StoreBin1': 'store_bin1', 'StoreBin2': 'store_bin2', 'StoreBin3': 'store_bin3',
    'WarehouseLocation': 'warehouse_location', 'WarehouseQuantity': 'warehouse_quantity',
    'WarehouseBin1': 'warehouse_bin1', 'WarehouseBin2': 'warehouse_bin2', 'WarehouseBin3': 'warehouse_bin3',
}
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_ROWS = 200_000
COLUMNS = tuple(FIELDS.values())
PUBLIC_COLUMNS = tuple(name for name in COLUMNS if name not in ('cost_price', 'wholesale_price'))
SEARCH_FIELDS = ('part_number', 'description', 'alternate_part_number', 'substitute_part_number',
                 'store_location', 'store_bin1', 'store_bin2', 'store_bin3',
                 'warehouse_location', 'warehouse_bin1', 'warehouse_bin2', 'warehouse_bin3')


class PartsCatalogError(ValueError):
    """The catalogue was not imported; the previous snapshot remains intact."""


def _tokens(value):
    return re.findall(r'[^\W_]+', str(value).casefold(), flags=re.UNICODE)


class PartsCatalog:
    """One locally stored, read-only-to-the-app catalogue snapshot.

    Each operation has its own connection, so import_csv may run on a worker
    thread while the GUI continues to use its own connection.
    """
    def __init__(self, db_path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS catalog_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS catalog_parts (id INTEGER PRIMARY KEY, '
                       + ', '.join('"' + name + '" TEXT NOT NULL' for name in COLUMNS) + ')')
            db.execute('CREATE INDEX IF NOT EXISTS catalog_sku ON catalog_parts(part_number COLLATE NOCASE)')
            db.execute('CREATE INDEX IF NOT EXISTS catalog_alternate ON catalog_parts(alternate_part_number COLLATE NOCASE)')
            db.execute('CREATE INDEX IF NOT EXISTS catalog_substitute ON catalog_parts(substitute_part_number COLLATE NOCASE)')
            db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS catalog_search USING fts5('
                       + ', '.join(SEARCH_FIELDS) + ", content='catalog_parts', content_rowid='id')")

    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=1.5)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _read_metadata(db):
        data = dict(db.execute('SELECT key, value FROM catalog_meta').fetchall())
        if data:
            data['row_count'] = int(data.get('row_count', '0'))
        return data

    def metadata(self):
        with closing(self._connect()) as db:
            return self._read_metadata(db)

    def import_csv(self, path, *, only_if_empty=False):
        source = Path(path)
        try:
            if source.stat().st_size > MAX_FILE_BYTES:
                raise PartsCatalogError('The catalogue is larger than the 100 MB import limit.')
            with source.open('r', encoding='utf-8-sig', newline='') as stream, closing(self._connect()) as db, db:
                reader = csv.DictReader(stream, strict=True)
                if not reader.fieldnames or any(name not in reader.fieldnames for name in FIELDS):
                    raise PartsCatalogError('The file is missing one or more Famous Twins parts columns.')
                names = ','.join('"' + field + '"' for field in COLUMNS)
                sql = f"INSERT INTO catalog_parts ({names}) VALUES ({','.join('?' for _ in COLUMNS)})"
                db.execute('BEGIN IMMEDIATE')
                # The built-in catalogue must never overwrite a user's import,
                # including when another process imports while startup runs.
                if only_if_empty and db.execute('SELECT 1 FROM catalog_parts LIMIT 1').fetchone():
                    return self._read_metadata(db)
                db.execute('DELETE FROM catalog_parts')
                count = 0
                for row in reader:
                    if None in row or any(row.get(name) is None for name in FIELDS):
                        raise PartsCatalogError('The catalogue contains an incomplete or malformed row.')
                    values = tuple(row[name].strip() for name in FIELDS)
                    if any(len(value) > 10000 for value in values):
                        raise PartsCatalogError('A catalogue field is too large.')
                    if not any(values):
                        continue
                    count += 1
                    if count > MAX_ROWS:
                        raise PartsCatalogError('The catalogue exceeds the 200,000-row limit.')
                    db.execute(sql, values)
                if not count:
                    raise PartsCatalogError('The catalogue contains no parts.')
                db.execute("INSERT INTO catalog_search(catalog_search) VALUES('rebuild')")
                found = re.search(r'(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)', source.name)
                source_date = found.group(1) if found else ''
                if source_date:
                    try:
                        datetime.strptime(source_date, '%Y-%m-%d')
                    except ValueError:
                        source_date = ''
                metadata = {'source': source.name, 'source_date': source_date, 'row_count': str(count),
                            'imported_at': datetime.now(timezone.utc).isoformat()}
                db.execute('DELETE FROM catalog_meta')
                db.executemany('INSERT INTO catalog_meta(key,value) VALUES (?,?)', metadata.items())
            return self.metadata()
        except PartsCatalogError:
            raise
        except (OSError, UnicodeError, csv.Error, sqlite3.Error) as exc:
            raise PartsCatalogError(f'The catalogue could not be imported: {exc}') from exc

    def lookup(self, query, limit=5):
        """Read metadata and FTS results from the same immutable SQLite snapshot."""
        query = str(query or '').strip()[:500]
        words = _tokens(query)[:12]
        limit = max(1, min(int(limit), 50))
        expression = ' AND '.join('"' + word + '"*' for word in words)
        columns = ','.join('p."' + name + '"' for name in PUBLIC_COLUMNS)
        with closing(self._connect()) as db:
            db.execute('BEGIN')
            metadata = self._read_metadata(db)
            matches = db.execute(f'''SELECT {columns} FROM catalog_search s
                JOIN catalog_parts p ON p.id=s.rowid WHERE catalog_search MATCH ?
                ORDER BY CASE WHEN p.part_number = ? COLLATE NOCASE THEN 0 ELSE 1 END,
                         bm25(catalog_search), p.part_number COLLATE NOCASE LIMIT ?''',
                (expression, query, limit)).fetchall() if words else []
        return metadata, [dict(row) for row in matches]

    def search(self, query, limit=5):
        """Return matching rows; only the imported snapshot, never current stock."""
        return self.lookup(query, limit)[1]

    def lookup_exact(self, code, limit=2):
        """Read metadata and exact scanner matches from the same SQLite snapshot."""
        code = str(code or '').strip()
        limit = max(1, min(int(limit), 50))
        columns = ','.join('"' + name + '"' for name in PUBLIC_COLUMNS)
        with closing(self._connect()) as db:
            db.execute('BEGIN')
            metadata = self._read_metadata(db)
            rows = db.execute(f'''SELECT {columns} FROM catalog_parts
                WHERE part_number = ? COLLATE NOCASE OR alternate_part_number = ? COLLATE NOCASE
                   OR substitute_part_number = ? COLLATE NOCASE
                ORDER BY part_number COLLATE NOCASE, id LIMIT ?''',
                (code, code, code, limit)).fetchall() if code and len(code) <= 500 else []
        return metadata, [dict(row) for row in rows]

    def exact_matches(self, code, limit=2):
        """Find scanner codes exactly, keeping ambiguous identifiers visible."""
        return self.lookup_exact(code, limit)[1]
