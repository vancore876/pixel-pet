"""Persistent part-number markers, rendered without changing plain note text."""
from PySide6.QtGui import QColor, QTextCursor, QTextDocument
from PySide6.QtWidgets import QTextEdit

MAX_HIGHLIGHTS = 200
MAX_HIGHLIGHT_LENGTH = 200


def validated_highlights(value):
    if not isinstance(value, list):
        return []
    result, seen = [], set()
    for term in value[:MAX_HIGHLIGHTS]:
        if not isinstance(term, str):
            continue
        term = term.strip()
        if (not term or len(term) > MAX_HIGHLIGHT_LENGTH
                or any(char in term for char in '\n\r\u2029\u2028')
                or term.casefold() in seen):
            continue
        result.append(term)
        seen.add(term.casefold())
    return result


def render_highlights(editor, terms):
    selections = []
    for term in terms:
        cursor = QTextCursor(editor.document())
        while True:
            cursor = editor.document().find(term, cursor, QTextDocument.FindFlags())
            if cursor.isNull():
                break
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format.setBackground(QColor('#ffe071'))
            selection.format.setForeground(QColor('#302400'))
            selections.append(selection)
    editor.setExtraSelections(selections)
