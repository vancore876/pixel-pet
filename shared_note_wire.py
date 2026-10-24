"""Lossless Writing/highlight transport for earlier office notebook servers."""
import copy
import json

from note_highlights import validated_highlights

PREFIX = '[Jeffery notebook compatibility v1]\n'


def encode_entry(entry):
    result = copy.deepcopy(entry)
    metadata = {'format': 'jeffery-notebook-1', 'kind': entry.get('kind'),
                'body': entry.get('body', ''), 'highlights': entry.get('highlights', [])}
    body = PREFIX + json.dumps(metadata, ensure_ascii=False, separators=(',', ':'))
    if len(body) > 10000:
        raise ValueError('This note is too large for the older server compatibility format. '
                         'Its text and highlights are unchanged. The server needs native Writing-note support to save this entry.')
    result['body'] = body
    result['kind'] = 'list' if entry.get('kind') == 'note' else entry.get('kind')
    result.pop('highlights', None)
    return result


def decode_entry(entry):
    if not isinstance(entry, dict) or not isinstance(entry.get('body'), str) or not entry['body'].startswith(PREFIX):
        return entry
    try:
        data = json.loads(entry['body'][len(PREFIX):])
    except (ValueError, TypeError):
        return entry
    if (not isinstance(data, dict) or data.get('format') != 'jeffery-notebook-1'
            or data.get('kind') not in ('note', 'order', 'list')
            or not isinstance(data.get('body'), str) or len(data['body']) > 10000
            or not isinstance(data.get('highlights'), list)
            or validated_highlights(data['highlights']) != data['highlights']):
        return entry
    return {**entry, 'kind': data['kind'], 'body': data['body'], 'highlights': data['highlights']}
