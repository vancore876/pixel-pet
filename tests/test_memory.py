"""User memory provenance, learning, retrieval, and durable-write regression checks."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory import MemoryStore, extraction_prompt, parse_extracted


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "memory.json"
        self.store = MemoryStore(self.path)

    def test_learns_user_preferences_facts_and_daily_routines_durably(self):
        learned = self.store.learn("I like tea. I don't like loud music. Remember my appointment is on Friday. I walk every morning. My name is Alex.")
        self.assertEqual([entry["kind"] for entry in learned], ["like", "dislike", "fact", "routine", "fact"])
        self.assertTrue(all(entry["source"] == "chat" and entry["id"] and entry["created"] > 0 for entry in learned))
        self.assertEqual(learned[0]["evidence"], "I like tea")
        self.assertEqual(MemoryStore(self.path).entries(), self.store.entries())
        self.assertEqual(json.loads(self.path.read_text())["version"], 1)

    def test_preference_change_replaces_conflict_and_preserves_identity(self):
        first = self.store.learn("I like coffee.")[0]
        changed = self.store.learn("I no longer like coffee.")[0]
        self.assertEqual(len(self.store.entries()), 1)
        self.assertEqual(changed["id"], first["id"])
        self.assertEqual(changed["kind"], "dislike")
        self.assertEqual(changed["created"], first["created"])

    def test_imported_documents_and_assistant_text_are_not_preferences(self):
        for source in ("pdf", "web", "notepad", "assistant", "https://example.com"):
            self.assertEqual(self.store.learn("I like coffee.", source=source), [])
        self.assertEqual(self.store.entries(), [])
        self.assertFalse(self.path.exists())

    def test_questions_hypotheticals_negation_and_quotes_are_not_learned(self):
        for text in ("I like tea?", "Do I like tea?", "If I like tea, what should I drink?", "Imagine I like tea.",
                     'Someone said "I like tea".', "I don't dislike tea.", "I like not drinking tea.", "Remember if I like tea."):
            self.assertEqual(self.store.learn(text), [], text)
        self.assertEqual(self.store.entries(), [])

    def test_sensitive_user_input_is_never_persisted(self):
        for text in ("Remember my password is hunter2", "I like coffee; my API key is gsk_secret12345678",
                     "Remember sk-proj-secret12345678", "Remember https://alice:secret@example.com",
                     "Remember my token is 1234", "Remember my Groq key is abc", "Remember my PIN is 1234"):
            self.assertEqual(self.store.learn(text), [])
        with self.assertRaises(ValueError):
            self.store.remember("fact", "password is secret")
        self.assertFalse(self.path.exists())

    def test_manual_edit_delete_and_clear_survive_reload(self):
        entry = self.store.remember("fact", "School starts at 08:00")
        edited = self.store.edit(entry["id"], "routine", "I go to school every weekday")
        self.assertEqual(edited["id"], entry["id"])
        self.assertEqual(MemoryStore(self.path).entries()[0]["kind"], "routine")
        returned = self.store.entries()
        returned[0]["text"] = "accidental mutation"
        self.assertNotEqual(self.store.entries()[0]["text"], "accidental mutation")
        self.assertTrue(self.store.forget(entry["id"]))
        self.assertFalse(self.store.forget(entry["id"]))
        self.store.remember("like", "tea")
        self.store.clear()
        self.assertEqual(MemoryStore(self.path).entries(), [])

    def test_corrupt_or_future_file_is_preserved_and_writes_blocked(self):
        for original in ("{broken", '{"version": 99, "entries": []}', '{"version": 1, "entries": [null]}'):
            self.path.write_text(original, encoding="utf-8")
            store = MemoryStore(self.path)
            self.assertTrue(store.warning)
            with self.assertRaises(OSError):
                store.remember("like", "tea")
            self.assertEqual(self.path.read_text(), original)
            self.assertEqual(store.entries(), [])

    def test_failed_atomic_write_keeps_original_and_in_memory_state(self):
        self.store.remember("like", "tea")
        original = self.path.read_bytes()
        before = self.store.entries()
        with patch("memory.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.learn("I dislike tea. Remember I am Alex.")
            with self.assertRaises(OSError):
                self.store.clear()
        self.assertEqual(self.store.entries(), before)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob(".memory-*.tmp")), [])

    def test_context_ranks_relevant_memories_and_current_completed_tasks(self):
        self.store.remember("like", "tea")
        self.store.remember("fact", "Dentist appointment Tuesday")
        notes = [{"id": "1", "title": "Dentist booking", "body": "Booked for Tuesday", "done": True,
                  "source": "manual", "updated": 1, "checklist": [{"text": "Call dentist", "done": True}]},
                 {"id": "2", "title": "Buy coffee", "body": "Coffee beans", "done": False, "updated": 2}]
        context = self.store.context("dentist", notes=notes, limit=2)
        self.assertEqual(context["memories"][0]["text"], "Dentist appointment Tuesday")
        self.assertEqual(context["tasks"][0]["id"], "1")
        self.assertTrue(context["tasks"][0]["done"])
        self.assertTrue(context["tasks"][0]["checklist"][0]["done"])
        self.assertEqual(len(self.store.entries()), 2)
        self.assertEqual(self.store.context("dentist", notes=[])["tasks"], [])

    def test_context_is_bounded_and_omits_sensitive_tasks(self):
        for i in range(20):
            self.store.remember("fact", f"Fact {i} " + "x" * 350)
        notes = [{"title": "API key", "body": "gsk_secret12345678", "done": False}]
        context = self.store.context("", notes=notes, limit=40, max_chars=900)
        self.assertLessEqual(len(json.dumps(context, ensure_ascii=False)), 900)
        self.assertEqual(context["tasks"], [])
        self.assertEqual(self.store.context(limit=0), {"memories": [], "tasks": []})

    def test_query_retrieves_information_near_end_of_imported_document(self):
        notes = [{"id": "pdf-1", "title": "Handbook PDF", "body": "Introduction text. " * 350 +
                  "The warranty renewal deadline is December 18.", "done": False, "source": "pdf:handbook.pdf"},
                 {"id": "irrelevant", "title": "Recent grocery list", "body": "Buy apples", "updated": 9999999999}]
        context = self.store.context("warranty renewal deadline", notes=notes, limit=1)
        self.assertEqual(context["tasks"][0]["id"], "pdf-1")
        self.assertIn("December 18", context["tasks"][0]["body"])
        self.assertLessEqual(len(context["tasks"][0]["body"]), 1200)
        self.assertEqual(self.store.entries(), [])

    def test_validated_groq_extraction_requires_genuine_literal_evidence(self):
        response = json.dumps({"memories": [{"kind": "like", "text": "tea", "evidence": "I like tea"},
                                               {"kind": "fact", "text": "I am rich", "evidence": "I am rich"}]})
        self.assertEqual(parse_extracted(response, "I like tea."), [("like", "tea")])
        self.store.learn_extracted(response, "I like tea.")
        self.assertEqual(self.store.entries()[0]["text"], "tea")
        self.assertEqual(self.store.learn_extracted(response, "I like tea.", source="pdf"), [])
        negated = json.dumps({"memories": [{"kind": "like", "text": "tea", "evidence": "I do not like tea"}]})
        self.assertEqual(parse_extracted(negated, "I do not like tea"), [])
        hypothetical = json.dumps({"memories": [{"kind": "like", "text": "tea", "evidence": "If I like tea"}]})
        self.assertEqual(parse_extracted(hypothetical, "If I like tea, suggest a shop."), [])
        quoted = json.dumps({"memories": [{"kind": "like", "text": "tea", "evidence": "I like tea"}]})
        self.assertEqual(parse_extracted(quoted, 'The article says "I like tea".'), [])
        with self.assertRaises(ValueError):
            parse_extracted('{"memories": [], "tool": "delete"}', "I like tea.")
        self.assertEqual(extraction_prompt("My password is secret")[-1]["content"], "")


if __name__ == "__main__":
    unittest.main()
