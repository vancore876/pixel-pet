"""Background preparation and streaming exports for the desktop notebook."""
from pathlib import Path
import json
import os
import tempfile

from content_intake import _check_cancel
from notebook_backup import export_backup, export_text, prepare_backup, _output_path


def prepare_document(argument, cancel=None):
    store = argument['store']
    prepared = store.documents.prepare_import({
        'text_file': argument['text_file'], 'title': argument['title'],
        'other_note_characters': sum(len(n['body']) for n in store.notes if not n.get('document_id')),
    }, cancel)
    if ((argument.get('character_count') is not None and prepared['body_characters'] != argument['character_count'])
            or (argument.get('full_text_sha256') and prepared['body_sha256'] != argument['full_text_sha256'])):
        store.documents.delete(prepared['document_id'])
        raise ValueError('The extracted document changed before saving. Read it again to preserve the full text.')
    return {'kind': 'document_prepared', 'document': prepared,
            'title': argument['title'], 'source': argument['source'],
            '_cleanup': lambda: store._discard_unreferenced([prepared['document_id']])}


def prepare_notebook_backup(argument, cancel=None):
    result = prepare_backup(argument, cancel)
    result['_cleanup'] = lambda: argument['store']._discard_unreferenced(result.get('prepared_ids', []))
    return result


def export_notebook(argument, cancel=None):
    if argument['format'] == 'text':
        return export_text(argument, cancel)
    if argument['format'] == 'zip':
        return export_backup(argument['store'], argument['path'], cancel,
                             snapshot=argument['snapshot'])
    snapshot = argument['snapshot']
    if any(note.get('document_id') for note in snapshot['notes']):
        raise ValueError('Choose a desktop ZIP backup to include full documents.')
    path = _output_path(argument['store'], argument['path'])
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.jeffery-backup-', suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            for piece in json.JSONEncoder(ensure_ascii=False, indent=2).iterencode(snapshot):
                _check_cancel(cancel)
                output.write(piece)
        _check_cancel(cancel)
        os.replace(temporary, path)
        return {'kind': 'backup_exported', 'path': str(path)}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
