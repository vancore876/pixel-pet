"""Persistent part-number markers, rendered without changing plain note text."""
from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QColor, QTextCursor
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
    """Find all terms in one pass and paint their merged ranges.

    Qt offsets are UTF-16, so use Qt's regex offsets directly rather than
    Python string indexes. Merging overlaps prevents competing selections
    from flickering when many part numbers share a prefix.
    """
    selections = []
    terms = validated_highlights(terms)
    ranges = []
    if terms:
        alternatives = '|'.join(QRegularExpression.escape(term) for term in sorted(terms, key=len, reverse=True))
        pattern = QRegularExpression('(?=(' + alternatives + '))', QRegularExpression.CaseInsensitiveOption)
        matches = pattern.globalMatch(editor.toPlainText())
        while matches.hasNext():
            match = matches.next()
            start, end = match.capturedStart(1), match.capturedEnd(1)
            if ranges and start <= ranges[-1][1]:
                ranges[-1] = (ranges[-1][0], max(end, ranges[-1][1]))
            else:
                ranges.append((start, end))
    for start, end in ranges:
        cursor = QTextCursor(editor.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format.setBackground(QColor('#ffe071'))
        selection.format.setForeground(QColor('#302400'))
        selections.append(selection)
    editor.setExtraSelections(selections)
