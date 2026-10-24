"""Exact, read-only comparisons between notebook requests and imported stock."""
import re


def requested_stock(catalog, notes, limit=80):
    codes, descriptions = {}, {}
    for row in catalog:
        for code in {str(row.get(key, '')).strip().casefold() for key in
                     ('sku', 'barcode', 'alternate_part_number', 'substitute_part_number')} - {''}:
            codes.setdefault(code, []).append(row)
        description = str(row.get('description', '')).strip().casefold()
        if description:
            descriptions.setdefault(description, []).append(row)
    results, checked, totals, demand = [], 0, {}, {}
    scan_limited = False
    for note in notes:
        if checked >= 5000:
            scan_limited = True
            break
        if note.get('done'):
            continue
        requests = [row for row in note.get('checklist', []) if not row.get('done')]
        if not requests and note.get('kind') == 'note':
            seen = set()
            for line in str(note.get('body', '')).splitlines():
                tokens = re.findall(r'[\w]+(?:[-./][\w]+)*', line)
                candidates = [token for token in tokens if token.casefold() in codes or
                              any(c.isdigit() for c in token) and any(c.isalpha() for c in token)]
                if not candidates and line.strip().casefold() in descriptions:
                    requests.append({'text': line.strip(), 'quantity': 1})
                for token in candidates:
                    if token.casefold() not in seen:
                        seen.add(token.casefold())
                        requests.append({'text': token, 'part_number': token, 'quantity': 1})
        for request in requests:
            if checked >= 5000:
                scan_limited = True
                break
            checked += 1
            code = str(request.get('part_number', '')).strip()
            text = str(request.get('text', '')).strip()
            quantity = request.get('quantity', 1)
            quantity = quantity if type(quantity) is int and quantity > 0 else 1
            matches = codes.get(code.casefold(), []) if code else descriptions.get(text.casefold(), [])
            status = 'not_found'
            extra = {}
            if len(matches) > 1:
                status = 'ambiguous'
            elif matches:
                row = matches[0]
                available = int(row.get('quantity', 0))
                status = 'out_of_stock' if available == 0 else 'insufficient' if available < quantity else 'available'
                extra = {'sku': row['sku'], 'available_quantity': available,
                         'shortfall': max(0, quantity - available), 'location': row.get('location', '')}
                combined = demand.setdefault(row['sku'], {'sku': row['sku'], 'requested_quantity': 0,
                                                          'available_quantity': available, 'location': row.get('location', '')})
                combined['requested_quantity'] += quantity
            totals[status] = totals.get(status, 0) + 1
            results.append({'note_title': note.get('title', ''), 'note_id': note.get('id', ''),
                            'requested_text': text, 'part_number': code,
                            'requested_quantity': quantity, 'status': status, **extra})
    priority = {'out_of_stock': 0, 'insufficient': 1, 'not_found': 2, 'ambiguous': 3, 'available': 4}
    results.sort(key=lambda row: priority[row['status']])
    return {'requests': results[:limit], 'request_count': checked, 'counts': totals,
            'truncated': len(results) > limit or scan_limited, 'scan_limited': scan_limited,
            'combined_shortages': [{**row, 'shortfall': row['requested_quantity'] - row['available_quantity']}
                                   for row in demand.values() if row['requested_quantity'] > row['available_quantity']][:limit],
            'quantity_note': 'Unstructured Writing references assume one unit; checklist quantities are explicit. Combined shortages sum all checked open requests.',
            'not_found_meaning': 'No exact match in this snapshot; this does not prove the business never carries the part.'}
