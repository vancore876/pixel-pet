"""Keep shared workplace data out of automatic cloud prompts by default."""
from __future__ import annotations

from collections.abc import Mapping


def _enabled(settings, key):
    try:
        return settings[key] is True
    except (KeyError, TypeError):
        return False


def is_shared_note(note):
    return isinstance(note, Mapping) and bool(
        note.get("team_shared") is True or note.get("team_workspace_id") or note.get("team_id") or note.get("team_revisions")
        or note.get("team_recipients") or note.get("team_due_at") is not None)


def note_allowed_for_ai(note, settings, share_notes=None):
    """The team opt-in supplements the ordinary notebook-sharing control."""
    sharing = _enabled(settings, "ai_share_notes") and (share_notes is None or bool(share_notes))
    return (isinstance(note, Mapping) and sharing and
            (not is_shared_note(note) or _enabled(settings, "team_share_notes_with_ai")))


def notes_allowed_for_ai(notes, settings, share_notes=None):
    """Select current records, hiding shared-folder paths from cloud recall."""
    return [{**note, "source": ""} if is_shared_note(note) else note
            for note in notes if note_allowed_for_ai(note, settings, share_notes)]


_EMPLOYEE_FIELDS = frozenset({
    "employee", "employees", "employee_id", "employee_name", "employee_roster",
    "roster", "inbox", "messages", "employee_messages", "private_messages",
    "sender", "sender_id", "sender_name", "recipient", "recipient_id", "recipients",
    "delivery_receipts", "receipts", "workspace_path", "shared_folder", "share_path", "added_by",
})
_NOTE_CONTEXT_FIELDS = frozenset({
    "incomplete_notes", "notebook_references", "semantic_notebook_references",
    "business_summary", "notes", "saved_notes",
})
_OMIT = object()


def filtered_ai_context(context, settings, share_notes=None):
    """Remove workplace transport metadata and employee inboxes recursively.

    Shared-note content is allowed only after its separate opt-in. Employee
    messages and identities are never automatic prompt context, even then.
    Text deliberately typed or quoted by the user is handled by chat itself.
    """
    sharing = _enabled(settings, "ai_share_notes") and (share_notes is None or bool(share_notes))

    def clean(value):
        if isinstance(value, Mapping):
            shared = is_shared_note(value)
            if shared and not note_allowed_for_ai(value, settings, sharing):
                return _OMIT
            result = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    continue
                lowered = key.casefold()
                if lowered.startswith("team_") or lowered in _EMPLOYEE_FIELDS:
                    continue
                if not sharing and lowered in _NOTE_CONTEXT_FIELDS:
                    continue
                if lowered == "user_memory" and not _enabled(settings, "ai_share_memory"):
                    continue
                if lowered in {"inventory", "inventory_references", "inventory_summary"} and not _enabled(settings, "inventory_share_with_ai"):
                    continue
                if shared and lowered == "source":
                    continue
                sanitized = clean(item)
                if sanitized is not _OMIT:
                    result[key] = sanitized
            return result
        if isinstance(value, (list, tuple)):
            return [sanitized for item in value if (sanitized := clean(item)) is not _OMIT]
        return value

    result = clean(context)
    return result if isinstance(result, dict) else {}


def sharing_permissions(settings, share_notes=None):
    """Small snapshot used to reject replies after a sharing permission changes."""
    return (_enabled(settings, "ai_share_notes") and (share_notes is None or bool(share_notes)),
            _enabled(settings, "team_share_notes_with_ai"), _enabled(settings, "ai_share_memory"),
            _enabled(settings, "inventory_share_with_ai"))


def permissions_withdrawn(previous, current):
    return previous is not None and any(before and not after for before, after in zip(previous, current))
