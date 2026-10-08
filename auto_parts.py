"""Exact JMD amounts and manually maintained Famous Twins order summaries.

These helpers describe saved orders. They do not look up VINs, infer stock
quantities, calculate tax, or change stock when a checklist item is ticked.
"""
from __future__ import annotations

from itertools import islice
import math
import re
import time


STOCK_STATUSES = ('check_stock', 'in_stock', 'to_order', 'ordered', 'picked')
MAX_PAYMENT_CENTS = 100_000_000_000_000
MAX_UNIT_PRICE_CENTS = 1_000_000_000
MAX_BUSINESS_RECORDS = 2000
CHECKLIST_TEMPLATES = {
    'Opening': (
        "Review today's pickups and promised dates",
        'Check urgent orders and parts still to source',
        'Confirm supplier opening times and pending deliveries',
        'Check counter supplies and prepare the work area',
    ),
    'Closing': (
        'Update order statuses and parts received today',
        'Contact customers about changed pickup arrangements',
        'Review payments recorded and outstanding balances',
        'Back up the notebook and secure the work area',
    ),
    'Parts handover': (
        'Confirm customer and order reference',
        'Match part numbers and quantities against the order',
        'Confirm vehicle details and explain any fitment uncertainty',
        'Review the recorded payment and remaining balance',
        'Give the customer the receipt and agreed return information',
        'Mark the order delivered after handover',
    ),
}


def validated_cents(value, maximum=MAX_PAYMENT_CENTS):
    """Default malformed/negative money to zero and cap oversized integers."""
    return min(value, maximum) if type(value) is int and value >= 0 else 0


def money_to_cents(text):
    """Parse a nonnegative plain decimal, without binary floating point.

    Currency symbols, grouping commas, signs, scientific notation and more
    than two decimal places are rejected rather than silently rounded.
    """
    if not isinstance(text, str):
        raise ValueError('Enter a nonnegative amount with up to two decimal places.')
    value = text.strip()
    if len(value) > 18 or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', value):
        raise ValueError('Enter a nonnegative amount with up to two decimal places.')
    whole, separator, fraction = value.partition('.')
    cents = int(whole) * 100 + (int(fraction.ljust(2, '0')) if separator else 0)
    if cents > MAX_PAYMENT_CENTS:
        raise ValueError('This amount exceeds the supported JMD limit.')
    return cents


def format_money(cents):
    """Format integer cents exactly, including totals larger than one payment."""
    amount = cents if type(cents) is int and cents >= 0 else 0
    return f'JMD {amount // 100:,}.{amount % 100:02d}'


def _text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) else ''


def _quantity(value):
    return max(1, min(9999, value)) if type(value) is int else 1


def _records(notes):
    try:
        records = islice(notes, MAX_BUSINESS_RECORDS)
    except TypeError:
        return
    for note in records:
        if isinstance(note, dict):
            yield note


def _rows(note):
    rows = note.get('checklist')
    return [row for row in rows[:100] if isinstance(row, dict)] if isinstance(rows, list) else []


def _active_order(note):
    return (note.get('kind') == 'order' and note.get('done') is not True
            and note.get('order_status') not in ('delivered', 'cancelled')
            and note.get('order_type', 'order') != 'quote')


def _timestamp(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def order_totals(note):
    """Return exact line totals and the balance; check marks do not change price."""
    note = note if isinstance(note, dict) else {}
    subtotal = sum(_quantity(row.get('quantity', 1))
                   * validated_cents(row.get('unit_price_cents'), MAX_UNIT_PRICE_CENTS)
                   for row in _rows(note))
    paid = validated_cents(note.get('payment_received_cents'))
    return {'subtotal_cents': subtotal, 'paid_cents': paid,
            'balance_cents': max(0, subtotal - paid), 'credit_cents': max(0, paid - subtotal)}


def parts_to_source(notes):
    """Group unchecked requested parts from active orders, retaining customers.

    Each group includes its requested quantity and an ``orders`` list with
    order/customer/vehicle details. Matching descriptions, part numbers,
    suppliers, bin locations and manual stock statuses are combined. Quotes,
    completed orders, checked rows, in-stock rows and picked rows are excluded.
    """
    groups = {}
    for note in _records(notes):
        if not _active_order(note):
            continue
        for row in _rows(note):
            status = row.get('stock_status', 'check_stock')
            text = _text(row.get('text'), 200)
            if row.get('done') is True or not text or status not in ('check_stock', 'to_order', 'ordered'):
                continue
            details = {'text': text, 'part_number': _text(row.get('part_number'), 80),
                       'supplier': _text(row.get('supplier'), 100),
                       'bin_location': _text(row.get('bin_location'), 60), 'stock_status': status}
            key = tuple(value.casefold() for value in details.values())
            group = groups.setdefault(key, {**details, 'quantity': 0, 'orders': []})
            quantity = _quantity(row.get('quantity', 1))
            group['quantity'] += quantity
            identifier = _text(note.get('id'), 64)
            order = next((saved for saved in group['orders'] if saved['id'] == identifier), None)
            if order is None:
                order = {'id': identifier, 'title': _text(note.get('title'), 100),
                         'order_ref': _text(note.get('order_ref'), 80),
                         'customer': _text(note.get('customer'), 100), 'contact': _text(note.get('contact'), 100),
                         'vehicle': _text(note.get('vehicle'), 160), 'registration': _text(note.get('registration'), 32),
                         'priority': 'urgent' if note.get('priority') == 'urgent' else 'normal', 'quantity': 0}
                group['orders'].append(order)
            order['quantity'] += quantity
    return sorted(groups.values(), key=lambda row: (
        not any(order['priority'] == 'urgent' for order in row['orders']),
        row['supplier'].casefold(), row['part_number'].casefold(), row['text'].casefold(), row['stock_status']))


def dashboard_summary(notes, now=None):
    """Summarize active work; quote amounts never become receivables.

    ``parts_to_source`` counts requested units, rather than distinct grouped
    descriptions. This is a work list, not an inventory quantity.
    """
    records = list(_records(notes))
    current = time.time() if now is None else _timestamp(now)
    current = time.time() if current is None else current
    orders = [note for note in records if _active_order(note)]
    quotes = [note for note in records if note.get('kind') == 'order'
              and note.get('order_type') == 'quote' and note.get('done') is not True
              and note.get('order_status') not in ('delivered', 'cancelled')]
    return {'open_orders': len(orders),
            'ready_orders': sum(note.get('order_status') == 'ready' for note in orders),
            'late_orders': sum(_timestamp(note.get('order_due')) is not None
                               and note['order_due'] < current for note in orders),
            'urgent_orders': sum(note.get('priority') == 'urgent' for note in orders),
            'quotes': len(quotes),
            'balance_cents': sum(order_totals(note)['balance_cents'] for note in orders),
            'parts_to_source': sum(row['quantity'] for row in parts_to_source(records))}


def customer_history(notes, query=''):
    """Return bounded order/quote summaries, never full notes or credentials."""
    needle = _text(query, 1000).casefold()
    result = []
    fields = {'id': 64, 'title': 100, 'customer': 100, 'contact': 100, 'order_ref': 80,
              'vehicle': 160, 'registration': 32, 'vin': 32}
    for note in _records(notes):
        if note.get('kind') != 'order':
            continue
        row = {key: _text(note.get(key), limit) for key, limit in fields.items()}
        if needle and needle not in ' '.join(row.values()).casefold():
            continue
        row.update(order_status=note.get('order_status') if note.get('order_status') in
                   ('new', 'preparing', 'ready', 'delivered', 'cancelled') else 'new',
                   order_type='quote' if note.get('order_type') == 'quote' else 'order',
                   priority='urgent' if note.get('priority') == 'urgent' else 'normal', currency='JMD',
                   done=note.get('done') is True or note.get('order_status') in ('delivered', 'cancelled'),
                   created=_timestamp(note.get('created')) or 0,
                   order_due=_timestamp(note.get('order_due')), **order_totals(note))
        result.append(row)
    return sorted(result, key=lambda row: (row['created'], row['id']), reverse=True)
