"""Small, bounded typo matching; exact retrieval works without RapidFuzz.

Only short labels and saved preferences belong here. Indexed document bodies
stay in SQLite and never become a list of fuzzy-search candidates.
"""
from __future__ import annotations

import re
import unicodedata

try:
    from rapidfuzz import fuzz as _fuzz, process as _process
except ImportError:
    _fuzz = _process = None

_WORDS = re.compile(r"[\w']+", re.UNICODE)
_STOP_WORDS = frozenset(("a", "an", "and", "are", "as", "at", "be", "can", "do", "does", "for",
    "from", "how", "i", "in", "is", "it", "me", "my", "of", "on", "or", "the", "to", "what",
    "when", "where", "which", "with", "you"))
MAX_QUERY_WORDS = 12
MAX_LABEL_CHARACTERS = 600


def search_words(query):
    text = unicodedata.normalize("NFKC", query[:1000]).casefold() if isinstance(query, str) else ""
    return tuple(dict.fromkeys(word for word in _WORDS.findall(text) if word not in _STOP_WORDS))[:MAX_QUERY_WORDS]


def typo_score(words, label):
    """Score approximate words only; exact words keep their own higher rank.

    Short words are exact-only to avoid confusing preferences such as cat/car.
    Candidate work is capped even when called by an imported notebook.
    """
    if _process is None or not words or not isinstance(label, str):
        return 0.0
    normalized = unicodedata.normalize('NFKC', label[:MAX_LABEL_CHARACTERS]).casefold()
    candidates = set(_WORDS.findall(normalized)[:100]) - _STOP_WORDS
    if not candidates:
        return 0.0
    score = 0.0
    for word in words[:MAX_QUERY_WORDS]:
        if word in candidates or not 4 <= len(word) <= 40:
            continue
        match = _process.extractOne(word, candidates, scorer=_fuzz.ratio, score_cutoff=80)
        if match is not None:
            score += match[1] / 100
    return score


def command_suggestions(command, aliases, limit=3):
    """Suggest known command names; callers must never execute these guesses."""
    if not isinstance(command, str) or not 2 <= len(command) <= 40:
        return []
    choices = [name for name in aliases if isinstance(name, str) and len(name) <= 40][:100]
    folded = command.casefold()
    if folded in choices:
        return [folded]
    if _process is None:
        return []
    count = max(0, min(3, limit)) if type(limit) is int else 3
    return [match[0] for match in _process.extract(folded, choices, scorer=_fuzz.ratio,
                                                score_cutoff=75, limit=count)]
