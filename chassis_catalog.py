"""Private local membership list for VINs and other chassis/frame numbers.

This list does not contain vehicle details or link vehicles to parts.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import re
import sqlite3

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_NUMBERS = 250_000
_NUMBER = re.compile(r'[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\Z', re.ASCII)


class ChassisCatalogError(ValueError):
    """A chassis file could not be imported; existing numbers are unchanged."""


def normalize_number(value):
    """Normalize spacing/case and optional hyphens, without assuming VIN rules.

    Many valid chassis and frame numbers are shorter than a modern 17-digit
    VIN. Treat all of them as opaque identifiers: do not decode them.
    """
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not _NUMBER.fullmatch(raw):
        return None
    value = raw.replace('-', '').upper()
    return value if 8 <= len(value) <= 32 else None


class ChassisCatalog:
    """Private per-profile list of known chassis identifiers."""

    def __init__(self, db_path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS chassis_numbers (number TEXT PRIMARY KEY)')

    def _connect(self):
        return sqlite3.connect(str(self.path), timeout=1.5)

    def count(self):
        with closing(self._connect()) as db:
            return db.execute('SELECT COUNT(*) FROM chassis_numbers').fetchone()[0]

    def contains(self, value):
        number = normalize_number(value)
        if number is None:
            return False
        with closing(self._connect()) as db:
            return db.execute('SELECT 1 FROM chassis_numbers WHERE number=?', (number,)).fetchone() is not None

    def import_text(self, path):
        """Add a UTF-8 text list atomically without deleting existing entries.

        Identifiers can be separated by whitespace, commas, or semicolons.
        Invalid input rejects the entire import so no partial list is saved.
        """
        try:
            path = Path(path)
            if path.stat().st_size > MAX_FILE_BYTES:
                raise ChassisCatalogError('The chassis list is larger than the 10 MB limit.')
            text = path.read_text(encoding='utf-8-sig')
            tokens = [token for token in re.split(r'[\s,;]+', text) if token]
            if not tokens:
                raise ChassisCatalogError('The chassis list contains no numbers.')
            if len(tokens) > MAX_NUMBERS:
                raise ChassisCatalogError('The chassis list exceeds the 250,000-number limit.')
            numbers = []
            for index, token in enumerate(tokens, 1):
                number = normalize_number(token)
                if number is None:
                    raise ChassisCatalogError(f'Entry {index:,} is not a valid chassis identifier. '
                                              'Use 8–32 letters or digits; hyphens are optional.')
                numbers.append((number,))
            with closing(self._connect()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                before = db.execute('SELECT COUNT(*) FROM chassis_numbers').fetchone()[0]
                db.executemany('INSERT OR IGNORE INTO chassis_numbers(number) VALUES (?)', numbers)
                total = db.execute('SELECT COUNT(*) FROM chassis_numbers').fetchone()[0]
            return {'added': total - before, 'total': total, 'entries': len(numbers)}
        except ChassisCatalogError:
            raise
        except (OSError, UnicodeError, sqlite3.Error) as exc:
            raise ChassisCatalogError('The chassis list could not be imported; existing numbers remain available.') from exc
