"""Employee-recorded, chassis-specific part associations and catalogue candidates.

The supplied catalogue is free text; candidate search is never verified fitment.
The database stays local. Explicit export/import can move records between PCs.
"""
from __future__ import annotations
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from chassis_catalog import normalize_number
from parts_catalog import PUBLIC_COLUMNS

MAX_EXPORT_BYTES = 16 * 1024 * 1024
_UNSET = object()


def _text(value, label, maximum=120, required=False):
    if not isinstance(value, str):
        raise ValueError(f'{label} must be text.')
    value = value.strip()
    if len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError(f'{label} is invalid or too long.')
    if required and not value:
        raise ValueError(f'{label} is required.')
    return value


def _number(value):
    number = normalize_number(value)
    if not number:
        raise ValueError('Enter a chassis number using 8–32 letters or digits.')
    return number


def _vehicle(chassis, make, model, year=None, engine='', note=''):
    chassis = _number(chassis)
    make = _text(make, 'Make', required=True)
    model = _text(model, 'Model', required=True)
    engine = _text(engine, 'Engine')
    note = _text(note, 'Note', maximum=1000)
    if year in (None, ''):
        year = None
    else:
        if isinstance(year, bool) or not str(year).isdigit() or not 1886 <= int(year) <= 2100:
            raise ValueError('Enter a valid four-digit vehicle year or leave it empty.')
        year = int(year)
    return chassis, make, model, year, engine, note


def _part(part_number, note='', source=''):
    part_number = _text(part_number, 'Part number', maximum=100, required=True)
    return part_number, _text(note, 'Note', maximum=1000), _text(source, 'Source', maximum=200)


class FitmentStore:
    """Local employee-confirmed chassis records. Connections are per operation."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS vehicles (chassis TEXT PRIMARY KEY, make TEXT NOT NULL, '
                       'model TEXT NOT NULL, year INTEGER, engine TEXT NOT NULL, note TEXT NOT NULL, '
                       'revision INTEGER NOT NULL, updated_at TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS fitment_parts (chassis TEXT NOT NULL REFERENCES vehicles(chassis), '
                       'part_number TEXT NOT NULL COLLATE NOCASE, note TEXT NOT NULL, source TEXT NOT NULL, '
                       'vehicle_revision INTEGER NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(chassis,part_number))')
            db.execute('CREATE TABLE IF NOT EXISTS deleted_parts (chassis TEXT NOT NULL REFERENCES vehicles(chassis), '
                       'part_number TEXT NOT NULL COLLATE NOCASE, deleted_at TEXT NOT NULL, PRIMARY KEY(chassis,part_number))')

    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=3)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def save_vehicle(self, chassis, make, model, year=None, engine='', note='', *, expected_updated_at=_UNSET):
        chassis, make, model, year, engine, note = _vehicle(chassis, make, model, year, engine, note)
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM vehicles WHERE chassis=?', (chassis,)).fetchone()
            if expected_updated_at is not _UNSET and ((old is None and expected_updated_at is not None)
                    or (old is not None and old['updated_at'] != expected_updated_at)):
                raise ValueError('This vehicle changed on this PC. Reload it before saving.')
            revision = 1 if old is None else old['revision'] + int(
                (old['make'].casefold(), old['model'].casefold(), old['year'], old['engine'].casefold()) !=
                (make.casefold(), model.casefold(), year, engine.casefold()))
            db.execute('INSERT INTO vehicles VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(chassis) DO UPDATE SET '
                       'make=excluded.make,model=excluded.model,year=excluded.year,engine=excluded.engine,'
                       'note=excluded.note,revision=excluded.revision,updated_at=excluded.updated_at',
                       (chassis, make, model, year, engine, note, revision, self._now()))
        return self.get_vehicle(chassis)

    def get_vehicle(self, chassis):
        chassis = _number(chassis)
        with closing(self._connect()) as db:
            row = db.execute('SELECT * FROM vehicles WHERE chassis=?', (chassis,)).fetchone()
            return dict(row) if row else None

    def list_vehicles(self, limit=100):
        limit = max(1, min(int(limit), 10000))
        with closing(self._connect()) as db:
            return [dict(row) for row in db.execute('SELECT * FROM vehicles ORDER BY chassis LIMIT ?', (limit,))]

    def add_part(self, chassis, part_number, *, note='', source='', expected_vehicle_updated_at=_UNSET,
                 expected_part_updated_at=_UNSET):
        chassis = _number(chassis)
        part_number, note, source = _part(part_number, note, source)
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision,updated_at FROM vehicles WHERE chassis=?', (chassis,)).fetchone()
            if not row:
                raise ValueError('Save the chassis and vehicle details first.')
            if expected_vehicle_updated_at is not _UNSET and row['updated_at'] != expected_vehicle_updated_at:
                raise ValueError('This vehicle changed on this PC. Reload it before confirming a part.')
            old = db.execute('SELECT updated_at FROM fitment_parts WHERE chassis=? AND part_number=?',
                             (chassis, part_number)).fetchone()
            if expected_part_updated_at is not _UNSET and ((old is None and expected_part_updated_at is not None)
                    or (old is not None and old['updated_at'] != expected_part_updated_at)):
                raise ValueError('This part mapping changed on this PC. Reload it before saving.')
            db.execute('DELETE FROM deleted_parts WHERE chassis=? AND part_number=?', (chassis, part_number))
            db.execute('INSERT INTO fitment_parts VALUES (?,?,?,?,?,?) ON CONFLICT(chassis,part_number) DO UPDATE SET '
                       'note=excluded.note,source=excluded.source,vehicle_revision=excluded.vehicle_revision,'
                       'updated_at=excluded.updated_at', (chassis, part_number, note, source, row['revision'], self._now()))
        return self.parts_for(chassis)

    def parts_for(self, chassis):
        return self.fitment_view(chassis)[1]

    def fitment_view(self, chassis):
        """Read the vehicle and its associations in one consistent snapshot."""
        chassis = _number(chassis)
        with closing(self._connect()) as db:
            db.execute('BEGIN')
            vehicle = db.execute('SELECT * FROM vehicles WHERE chassis=?', (chassis,)).fetchone()
            rows = db.execute('SELECT p.chassis,p.part_number,p.note,p.source,p.updated_at, '
                              '(p.vehicle_revision=v.revision) AS confirmed_for_current_vehicle '
                              'FROM fitment_parts p JOIN vehicles v ON v.chassis=p.chassis '
                              'WHERE p.chassis=? ORDER BY p.part_number COLLATE NOCASE', (chassis,)).fetchall()
            return (dict(vehicle) if vehicle else None,
                    [{**dict(row), 'confirmed_for_current_vehicle': bool(row['confirmed_for_current_vehicle'])} for row in rows])

    def remove_part(self, chassis, part_number, *, expected_updated_at=_UNSET):
        chassis = _number(chassis)
        part_number, _, _ = _part(part_number)
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT updated_at FROM fitment_parts WHERE chassis=? AND part_number=?',
                             (chassis, part_number)).fetchone()
            if expected_updated_at is not _UNSET and ((old is None and expected_updated_at is not None)
                    or (old is not None and old['updated_at'] != expected_updated_at)):
                raise ValueError('This part mapping changed on this PC. Reload it before removing.')
            removed = bool(db.execute('DELETE FROM fitment_parts WHERE chassis=? AND part_number=?',
                                      (chassis, part_number)).rowcount)
            if removed:
                db.execute('INSERT INTO deleted_parts VALUES (?,?,?) ON CONFLICT(chassis,part_number) '
                           'DO UPDATE SET deleted_at=excluded.deleted_at', (chassis, part_number, self._now()))
            return removed

    def export_to(self, path):
        """Write a complete snapshot atomically; user chooses its destination."""
        path = Path(path)
        with closing(self._connect()) as db:
            db.execute('BEGIN')
            vehicles = [dict(row) for row in db.execute('SELECT * FROM vehicles ORDER BY chassis')]
            parts = [dict(row) for row in db.execute('SELECT * FROM fitment_parts ORDER BY chassis,part_number')]
            deleted = [dict(row) for row in db.execute('SELECT * FROM deleted_parts ORDER BY chassis,part_number')]
        data = json.dumps({'version': 1, 'vehicles': vehicles, 'parts': parts, 'deleted_parts': deleted}, ensure_ascii=False, indent=2)
        if len(data.encode('utf-8')) > MAX_EXPORT_BYTES:
            raise ValueError('The mapping export exceeds the 16 MB limit.')
        temp = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False, prefix='.fitment-') as stream:
                temp = stream.name
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)
        return {'vehicles': len(vehicles), 'parts': len(parts)}

    def import_from(self, path):
        """Merge a snapshot atomically; conflicting existing records require review."""
        path = Path(path)
        if path.stat().st_size > MAX_EXPORT_BYTES:
            raise ValueError('The mapping file exceeds the 16 MB limit.')
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
        except (ValueError, UnicodeError) as exc:
            raise ValueError('The mapping file is not valid JSON.') from exc
        if (not isinstance(data, dict) or data.get('version') != 1 or
                not isinstance(data.get('vehicles'), list) or not isinstance(data.get('parts'), list) or
                not isinstance(data.get('deleted_parts', []), list) or
                len(data['vehicles']) > 100000 or len(data['parts']) > 200000 or len(data.get('deleted_parts', [])) > 200000):
            raise ValueError('The mapping file has an unsupported format.')
        vehicles, parts, deleted = {}, {}, {}
        for item in data['vehicles']:
            if not isinstance(item, dict):
                raise ValueError('A vehicle record is invalid.')
            values = _vehicle(item.get('chassis'), item.get('make'), item.get('model'), item.get('year'), item.get('engine', ''), item.get('note', ''))
            revision = item.get('revision')
            if type(revision) is not int or not 1 <= revision <= 1000000 or values[0] in vehicles:
                raise ValueError('A vehicle record has an invalid revision or duplicate chassis.')
            vehicles[values[0]] = (values, revision)
        for item in data['parts']:
            if not isinstance(item, dict):
                raise ValueError('A part record is invalid.')
            chassis = _number(item.get('chassis'))
            part, note, source = _part(item.get('part_number'), item.get('note', ''), item.get('source', ''))
            revision = item.get('vehicle_revision')
            key = (chassis, part.casefold())
            if chassis not in vehicles or type(revision) is not int or not 0 <= revision <= vehicles[chassis][1] or key in parts:
                raise ValueError('A part record has an invalid vehicle, revision, or duplicate part number.')
            parts[key] = (chassis, part, note, source, revision)
        for item in data.get('deleted_parts', []):
            if not isinstance(item, dict):
                raise ValueError('A deletion record is invalid.')
            chassis = _number(item.get('chassis'))
            part, _, _ = _part(item.get('part_number'))
            key = (chassis, part.casefold())
            if chassis not in vehicles or key in parts or key in deleted:
                raise ValueError('A deletion record has an invalid vehicle or duplicate part.')
            deleted[key] = (chassis, part)
        added_vehicles = added_parts = 0
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            for chassis, (values, revision) in vehicles.items():
                old = db.execute('SELECT * FROM vehicles WHERE chassis=?', (chassis,)).fetchone()
                if old:
                    if tuple(old[k] for k in ('chassis','make','model','year','engine','note')) != values:
                        raise ValueError(f'Chassis {chassis} has different vehicle details on this PC. Review the conflict before importing.')
                else:
                    db.execute('INSERT INTO vehicles VALUES (?,?,?,?,?,?,?,?)', (*values, revision, self._now()))
                    added_vehicles += 1
            for chassis, part in deleted.values():
                if db.execute('SELECT 1 FROM fitment_parts WHERE chassis=? AND part_number=?', (chassis, part)).fetchone():
                    raise ValueError(f'Part {part} for chassis {chassis} was deleted in the imported file but exists here. Review the conflict before importing.')
                db.execute('INSERT OR IGNORE INTO deleted_parts VALUES (?,?,?)', (chassis, part, self._now()))
            for chassis, part, note, source, revision in parts.values():
                if db.execute('SELECT 1 FROM deleted_parts WHERE chassis=? AND part_number=?', (chassis, part)).fetchone():
                    raise ValueError(f'Part {part} for chassis {chassis} was deleted on this PC. Review the conflict before importing.')
                old = db.execute('SELECT * FROM fitment_parts WHERE chassis=? AND part_number=?', (chassis, part)).fetchone()
                incoming_current = revision == vehicles[chassis][1]
                local_revision = db.execute('SELECT revision FROM vehicles WHERE chassis=?', (chassis,)).fetchone()[0]
                if old:
                    if old['note'] != note or old['source'] != source or (old['vehicle_revision'] == local_revision) != incoming_current:
                        raise ValueError(f'Part {part} for chassis {chassis} differs on this PC. Review the conflict before importing.')
                else:
                    # A stale association stays stale even when local revision numbers differ.
                    target_revision = local_revision if incoming_current else 0
                    db.execute('INSERT INTO fitment_parts VALUES (?,?,?,?,?,?)',
                               (chassis, part, note, source, target_revision, self._now()))
                    added_parts += 1
        return {'vehicles': added_vehicles, 'parts': added_parts}


def candidate_parts(parts_catalog, make, model, query='', limit=20):
    """Return exact-token description matches; these are not confirmed fitment."""
    make = _text(make, 'Make', required=True)
    model = _text(model, 'Model', required=True)
    query = _text(query, 'Part search', maximum=200)
    tokens = lambda value: re.findall(r'[^\W_]+', value.casefold(), flags=re.UNICODE)
    make_tokens, model_tokens, query_tokens = tokens(make), tokens(model), tokens(query)
    if not make_tokens or not model_tokens:
        raise ValueError('Enter a make and model to search descriptions.')
    aliases = {'toyota': ('toyota', 'toy'), 'nissan': ('nissan', 'nis'),
               'mitsubishi': ('mitsubishi', 'mitsu'), 'honda': ('honda', 'hon')}
    groups = [aliases.get(token, (token,)) for token in make_tokens]
    groups.extend((token,) for token in model_tokens)
    singular = [token[:-1] if len(token) > 3 and token.endswith('s') and not token.endswith('ss') else token
                for token in query_tokens]
    part_aliases = {'front': ('front', 'frt'), 'rear': ('rear', 'rr')}
    for token in singular:
        if token == 'brake' and 'pad' in singular:
            groups.append(('brake', 'disc'))
        else:
            groups.append(part_aliases.get(token, (token,)))
    if len(groups) > 12:
        raise ValueError('Use a shorter vehicle or part search.')
    expression = ' AND '.join('(' + ' OR '.join('"' + token + '"' for token in group) + ')' for group in groups)
    columns = ','.join('p."' + name + '"' for name in PUBLIC_COLUMNS)
    limit = max(1, min(int(limit), 100))
    with closing(parts_catalog._connect()) as db:
        db.execute('BEGIN')
        metadata = parts_catalog._read_metadata(db)
        rows = db.execute(f'SELECT {columns} FROM catalog_search s JOIN catalog_parts p ON p.id=s.rowid '
                          'WHERE catalog_search MATCH ? ORDER BY bm25(catalog_search),p.part_number COLLATE NOCASE LIMIT ?',
                          (expression, limit)).fetchall()
    return metadata, [dict(row) for row in rows]
