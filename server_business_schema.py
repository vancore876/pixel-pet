"""Portable validation for shared orders and checklists, without desktop imports."""
from __future__ import annotations

import json
import math
import re
import time

from auto_parts import MAX_PAYMENT_CENTS, MAX_UNIT_PRICE_CENTS, STOCK_STATUSES


MAX_BUSINESS_ENTRY_BYTES = 240 * 1024
MIN_TIMESTAMP = 946684800  # 2000-01-01, also safe for Windows local-time conversion.
MAX_TIMESTAMP = 4102444800  # 2100-01-01.
ORDER_STATUSES = ('new', 'preparing', 'ready', 'delivered', 'cancelled')
NOTE_FIELDS = {
    'id', 'title', 'body', 'created', 'updated', 'written', 'next_due', 'repeat_minutes',
    'done', 'pinned', 'pin_position', 'unannounced', 'source', 'document_source',
    'ai_guidance', 'reminder_history', 'kind', 'customer', 'contact', 'order_ref',
    'order_status', 'order_due', 'vehicle', 'registration', 'vin', 'priority',
    'order_type', 'currency', 'payment_received_cents', 'checklist',
}
ROW_FIELDS = {'id', 'text', 'quantity', 'done', 'part_number', 'supplier',
              'bin_location', 'unit_price_cents', 'stock_status'}


class BusinessValidationError(ValueError):
    """A business entry has invalid types or unsupported values."""


def validate_business_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise BusinessValidationError('Choose a valid shared entry identifier.')
    return value


def _text(value, limit, label, *, multiline=False):
    if not isinstance(value, str) or len(value) > limit:
        raise BusinessValidationError(f'{label} must be text with at most {limit:,} characters.')
    if any(ord(char) < 32 and (not multiline or char not in '\n\r\t') for char in value):
        raise BusinessValidationError(f'{label} contains an unsupported control character.')
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise BusinessValidationError(f'{label} contains invalid Unicode.')
    return value.strip()


def _integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise BusinessValidationError(f'{label} must be an integer from {minimum:,} to {maximum:,}.')
    return value


def _boolean(value, label):
    if type(value) is not bool:
        raise BusinessValidationError(f'{label} must be true or false.')
    return value


def _choice(value, choices, label):
    if value not in choices:
        raise BusinessValidationError(f'{label} has an unsupported value.')
    return value


def _timestamp(value, label, *, optional=False):
    if value is None and optional:
        return None
    if (type(value) not in (int, float) or not MIN_TIMESTAMP <= value <= MAX_TIMESTAMP
            or not math.isfinite(value)):
        raise BusinessValidationError(f'{label} must be a valid date from 2000 to 2100.')
    return float(value)


def validate_business_entry(data, identifier, *, now=None):
    """Return a canonical NoteStore-compatible order/list with neutral local data.

    Full documents, arbitrary extension fields, machine paths, local pinning and
    generated AI guidance do not belong in the shared business database.
    """
    validate_business_id(identifier)
    if not isinstance(data, dict) or set(data) - NOTE_FIELDS:
        raise BusinessValidationError('Shared entries contain only supported order or checklist fields.')
    if 'id' in data and data['id'] != identifier:
        raise BusinessValidationError('The shared entry identifier does not match its address.')
    current = _timestamp(time.time() if now is None else now, 'Current date')
    title = _text(data.get('title', ''), 100, 'Title')
    body = _text(data.get('body', ''), 10000, 'Body', multiline=True)
    if not (title or body):
        raise BusinessValidationError('Give the shared order or checklist a title.')
    if not title:
        title = body.splitlines()[0][:80]
    kind = _choice(data.get('kind'), ('order', 'list'), 'Entry type')
    status = _choice(data.get('order_status', 'new'), ORDER_STATUSES, 'Order status')
    done = _boolean(data.get('done', False), 'Done') or kind == 'order' and status in ('delivered', 'cancelled')
    if kind == 'order' and done and status not in ('delivered', 'cancelled'):
        status = 'delivered'
    raw_rows = data.get('checklist', [])
    if not isinstance(raw_rows, list) or len(raw_rows) > 100:
        raise BusinessValidationError('A shared entry supports up to 100 checklist rows.')
    rows, identifiers = [], set()
    for row in raw_rows:
        if not isinstance(row, dict) or set(row) - ROW_FIELDS:
            raise BusinessValidationError('A checklist row contains unsupported fields.')
        row_id = _text(row.get('id', ''), 64, 'Row identifier')
        text = _text(row.get('text', ''), 200, 'Part or checklist description')
        if not row_id or not text or row_id in identifiers:
            raise BusinessValidationError('Checklist rows need unique identifiers and a description.')
        identifiers.add(row_id)
        rows.append({'id': row_id, 'text': text,
                     'quantity': _integer(row.get('quantity', 1), 1, 9999, 'Quantity'),
                     'done': _boolean(row.get('done', False), 'Checklist done'),
                     'part_number': _text(row.get('part_number', ''), 80, 'Part number'),
                     'supplier': _text(row.get('supplier', ''), 100, 'Supplier'),
                     'bin_location': _text(row.get('bin_location', ''), 60, 'Bin location'),
                     'unit_price_cents': _integer(row.get('unit_price_cents', 0), 0, MAX_UNIT_PRICE_CENTS, 'Unit price cents'),
                     'stock_status': _choice(row.get('stock_status', 'check_stock'), STOCK_STATUSES, 'Stock status')})
    created = _timestamp(data.get('created', current), 'Created date')
    updated = _timestamp(data.get('updated', current), 'Updated date')
    result = {'id': identifier, 'title': title, 'body': body, 'created': created, 'updated': updated,
              'written': _timestamp(data.get('written', updated), 'Written date'),
              'next_due': _timestamp(data.get('next_due'), 'Reminder date', optional=True),
              'repeat_minutes': _integer(data.get('repeat_minutes', 0), 0, 1440, 'Reminder interval'),
              'done': done, 'pinned': False, 'pin_position': None, 'unannounced': False,
              'source': '', 'document_source': '', 'ai_guidance': {}, 'reminder_history': [], 'kind': kind,
              'customer': _text(data.get('customer', ''), 100, 'Customer'),
              'contact': _text(data.get('contact', ''), 100, 'Contact'),
              'order_ref': _text(data.get('order_ref', ''), 80, 'Order reference'),
              'order_status': status, 'order_due': _timestamp(data.get('order_due'), 'Order due date', optional=True),
              'vehicle': _text(data.get('vehicle', ''), 160, 'Vehicle'),
              'registration': _text(data.get('registration', ''), 32, 'Registration'),
              'vin': _text(data.get('vin', ''), 32, 'VIN'),
              'priority': _choice(data.get('priority', 'normal'), ('normal', 'urgent'), 'Priority'),
              'order_type': _choice(data.get('order_type', 'order'), ('order', 'quote'), 'Order type'),
              'currency': _choice(data.get('currency', 'JMD'), ('JMD',), 'Currency'),
              'payment_received_cents': _integer(data.get('payment_received_cents', 0), 0, MAX_PAYMENT_CENTS, 'Payment cents'),
              'checklist': rows}
    if len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > MAX_BUSINESS_ENTRY_BYTES:
        raise BusinessValidationError('This shared entry is too large. Split it into smaller orders.')
    return result
