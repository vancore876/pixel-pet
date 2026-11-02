"""Install the shipped parts snapshot and chassis list into a local profile."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from chassis_catalog import ChassisCatalog
from parts_catalog import PartsCatalog

PARTS_FILE = 'Famous_Twins_All_Parts_Locations_2026-10-22.csv'
CHASSIS_FILE = 'chassis_numbers.txt'


def builtin_directory():
    """Resolve resources in both source and PyInstaller one-file builds."""
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return root / 'assets' / 'offline_defaults'


def _digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def ensure_builtin_data(profile_dir, source_dir=None):
    """Populate a profile without replacing user-imported parts or chassis.

    Run in a worker: parsing the bundled catalogue involves disk I/O. Each
    import is transactional. The marker is written only after both complete,
    so an interrupted or failed run can safely retry. Existing parts win even
    when a separate process imports them concurrently.
    """
    profile = Path(profile_dir)
    source = Path(source_dir) if source_dir is not None else builtin_directory()
    parts_path, chassis_path = source / PARTS_FILE, source / CHASSIS_FILE
    version = {'parts': _digest(parts_path), 'chassis': _digest(chassis_path)}
    profile.mkdir(parents=True, exist_ok=True)
    marker = profile / 'offline-builtins.json'
    try:
        saved = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        saved = None
    if (saved == version and (profile / 'parts-catalog.sqlite').exists()
            and (profile / 'chassis.sqlite').exists()):
        return {'already_loaded': True}
    parts = PartsCatalog(profile / 'parts-catalog.sqlite').import_csv(parts_path, only_if_empty=True)
    chassis = ChassisCatalog(profile / 'chassis.sqlite').import_text(chassis_path)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=profile,
                                         prefix='.offline-builtins-', delete=False) as temp:
            temp_name = temp.name
            json.dump(version, temp)
        os.replace(temp_name, marker)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
    return {'already_loaded': False, 'parts': parts.get('row_count', 0),
            'chassis_added': chassis['added'], 'chassis_total': chassis['total']}
