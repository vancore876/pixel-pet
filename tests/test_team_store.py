"""Work-team state uses fake employees and temporary local databases only."""
import copy
import tempfile
import unittest
import uuid
from pathlib import Path

from team_store import TeamStore


class TeamStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = uuid.uuid4().hex
        self.stores = []
        self.alice = self.store("Alice")
        self.bob = self.store("Bob")
        self.carol = self.store("Carol")

    def tearDown(self):
        for store in self.stores:
            store.close()
        self.temp.cleanup()

    def store(self, name, employee_id=None):
        result = TeamStore(self.root / f"{name}-{uuid.uuid4().hex}.sqlite")
        result.configure(self.workspace, name, employee_id)
        self.stores.append(result)
        return result

    def employee(self, store):
        return store.identity()["employee_id"]

    def sync(self, source, *destinations):
        for event in source.pending_events():
            for destination in destinations:
                destination.ingest_event(event)
            source.mark_published(event["event_id"])

    def note(self, title="A task", **fields):
        return {"id": uuid.uuid4().hex, "title": title, "body": "Details", "repeat_minutes": 30,
                "created": 10, "due_at": 100, "recipients": [], **fields}

    def message(self, recipients=None, **fields):
        event = self.alice.send_message("Please check", "The delivery is due", recipients or [self.employee(self.bob)], first_due=100, **fields)
        self.sync(self.alice, self.bob, self.carol)
        return event["entity_id"]

    def test_legacy_client_omission_preserves_highlights_and_explicit_empty_removes(self):
        note = self.note(highlights=['PART-1'])
        first = self.alice.put_note(note)
        self.sync(self.alice, self.bob)
        legacy = {key: value for key, value in note.items() if key != 'highlights'}
        second = self.bob.put_note(legacy, base_revisions=[first['event_id']])
        self.sync(self.bob, self.alice)
        self.assertEqual(self.alice.get_entity('note', note['id'])['payload']['highlights'], ['PART-1'])
        self.assertNotIn('highlights', second['payload'])
        self.alice.put_note({**note, 'highlights': []}, base_revisions=[second['event_id']])
        self.assertEqual(self.alice.get_entity('note', note['id'])['payload']['highlights'], [])

    def test_equivalent_legacy_and_current_heads_do_not_create_conflict_copies(self):
        note = self.note(highlights=['PART-1'])
        first = self.alice.put_note(note)
        self.sync(self.alice, self.bob)
        self.alice.put_note(note, base_revisions=[first['event_id']])
        self.bob.put_note({k: v for k, v in note.items() if k != 'highlights'}, base_revisions=[first['event_id']])
        self.sync(self.bob, self.alice)
        entity = self.alice.get_entity('note', note['id'])
        self.assertFalse(entity['conflict'])
        self.assertEqual(entity['payload']['highlights'], ['PART-1'])

    def test_durable_identity_and_outbox_survive_restart(self):
        original = self.alice.identity()
        event = self.alice.put_note(self.note())
        path = self.alice.path
        self.alice.close()
        restarted = TeamStore(path)
        self.stores.append(restarted)
        self.assertEqual(original, restarted.identity())
        self.assertEqual([event], restarted.pending_events())
        restarted.mark_published(event["event_id"])
        self.assertEqual([], restarted.pending_events())

    def test_workspace_switch_keeps_old_outbox_separate(self):
        event = self.alice.put_note(self.note())
        other = uuid.uuid4().hex
        self.alice.configure(other, "Alice")
        self.assertEqual([], self.alice.pending_events())
        self.assertEqual([], self.alice.entities("note"))
        self.alice.configure(self.workspace, "Alice")
        self.assertEqual([event], self.alice.pending_events())

    def test_network_cache_is_rejected(self):
        with self.assertRaises(ValueError):
            TeamStore("\\\\server\\share\\cache.sqlite")

    def test_profiles_and_existing_employee_identity(self):
        self.alice.publish_profile()
        self.sync(self.alice, self.bob)
        self.assertEqual([{"id": self.employee(self.alice), "name": "Alice"}], self.bob.roster())
        second_pc = self.store("Alice laptop", self.employee(self.alice))
        self.assertEqual(self.employee(self.alice), self.employee(second_pc))
        self.assertNotEqual(self.alice.identity()["device_id"], second_pc.identity()["device_id"])

    def test_wrong_recipient_gets_no_inbox_or_popup(self):
        self.message()
        self.assertEqual([], self.carol.inbox())
        self.assertEqual([], self.carol.due_notifications(self.employee(self.carol), 100))
        self.assertEqual(1, len(self.bob.inbox()))

    def test_everyone_message_and_notes_are_routed(self):
        event = self.alice.send_message("Everyone", "Team meeting", [], first_due=100)
        self.sync(self.alice, self.bob, self.carol)
        self.assertEqual(event["entity_id"], self.carol.inbox()[0]["id"])

    def test_event_replay_is_idempotent_and_tampering_rejected(self):
        event = self.alice.put_note(self.note())
        self.assertTrue(self.bob.ingest_event(event))
        self.assertFalse(self.bob.ingest_event(copy.deepcopy(event)))
        changed = copy.deepcopy(event)
        changed["payload"]["title"] = "Changed"
        with self.assertRaises(ValueError):
            self.bob.ingest_event(changed)
        self.assertEqual("A task", self.bob.get_entity("note", event["entity_id"])["payload"]["title"])

    def test_foreign_workspace_event_is_rejected(self):
        event = self.alice.put_note(self.note())
        event["workspace_id"] = uuid.uuid4().hex
        with self.assertRaises(ValueError):
            self.bob.ingest_event(event)

    def test_two_offline_edits_preserve_both_versions(self):
        note = self.note()
        base = self.alice.put_note(note)
        self.sync(self.alice, self.bob)
        a = self.alice.put_note({**note, "title": "Alice edit"}, [base["event_id"]])
        b = self.bob.put_note({**note, "title": "Bob edit"}, [base["event_id"]])
        self.alice.ingest_event(b)
        self.bob.ingest_event(a)
        av = self.alice.get_entity("note", note["id"])
        self.assertTrue(av["conflict"])
        self.assertEqual({"Alice edit", "Bob edit"}, {version["payload"]["title"] for version in av["versions"]})
        self.assertEqual(av, self.bob.get_entity("note", note["id"]))

    def test_deletion_prevents_offline_resurrection_and_keeps_edit(self):
        note = self.note()
        base = self.alice.put_note(note)
        self.sync(self.alice, self.bob)
        deleted = self.alice.delete_note(note["id"])
        edited = self.bob.put_note({**note, "title": "Offline edit"}, [base["event_id"]])
        self.bob.ingest_event(deleted)
        self.alice.ingest_event(edited)
        entity = self.bob.get_entity("note", note["id"])
        self.assertTrue(entity["deleted"])
        self.assertTrue(entity["conflict"])
        self.assertEqual(2, len(entity["versions"]))
        with self.assertRaises(ValueError):
            self.bob.put_note(note)
        self.assertEqual([], self.bob.due_notifications(self.employee(self.bob), 100))

    def test_explicit_conflict_resolution_can_restore_reviewed_edit(self):
        note = self.note()
        base = self.alice.put_note(note)
        self.sync(self.alice, self.bob)
        deleted = self.alice.delete_note(note["id"])
        edited = self.bob.put_note({**note, "title": "Reviewed edit"}, [base["event_id"]])
        self.bob.ingest_event(deleted)
        resolved = self.bob.resolve_entity("note", note["id"], edited["event_id"])
        self.alice.ingest_event(edited)
        self.alice.ingest_event(resolved)
        entity = self.alice.get_entity("note", note["id"])
        self.assertFalse(entity["deleted"])
        self.assertFalse(entity["conflict"])
        self.assertEqual("Reviewed edit", entity["payload"]["title"])

    def test_out_of_order_parent_arrival_converges(self):
        note = self.note()
        first = self.alice.put_note(note)
        second = self.alice.put_note({**note, "title": "Latest"})
        self.bob.ingest_event(second)
        self.bob.ingest_event(first)
        self.assertEqual([second["event_id"]], self.bob.get_entity("note", note["id"])["revisions"])

    def test_causal_edit_wins_even_when_clock_goes_backwards(self):
        note = self.note()
        first = self.alice.put_note(note)
        second = self.alice.put_note({**note, "title": "Later causal edit"})
        second["created"] = first["created"] - 100
        self.bob.ingest_event(second)
        self.bob.ingest_event(first)
        self.assertEqual("Later causal edit", self.bob.get_entity("note", note["id"])["payload"]["title"])

    def test_presented_popup_is_not_read_or_acknowledged(self):
        entity_id = self.message()
        employee = self.employee(self.bob)
        alert = self.bob.due_notifications(employee, 50)[0]
        self.assertTrue(self.bob.mark_presented(employee, "message", entity_id, alert["occurrence_id"], 50))
        receipt = self.bob.receipt(employee, "message", entity_id)
        self.assertFalse(receipt["read"])
        self.assertFalse(receipt["acknowledged"])
        self.assertEqual(100, receipt["next_due"])
        self.assertEqual([], self.bob.due_notifications(employee, 99))
        self.assertEqual("reminder", self.bob.due_notifications(employee, 100)[0]["notification_kind"])

    def test_duplicate_presented_occurrence_is_not_saved_again(self):
        entity_id = self.message()
        employee = self.employee(self.bob)
        alert = self.bob.due_notifications(employee, 50)[0]
        self.bob.mark_presented(employee, "message", entity_id, alert["occurrence_id"], 50)
        pending = self.bob.pending_events()
        self.assertFalse(self.bob.mark_presented(employee, "message", entity_id, alert["occurrence_id"], 50))
        self.assertEqual(pending, self.bob.pending_events())

    def test_restart_and_reconnect_do_not_replay_initial_popup(self):
        entity_id = self.message()
        employee = self.employee(self.bob)
        alert = self.bob.due_notifications(employee, 50)[0]
        self.bob.mark_presented(employee, "message", entity_id, alert["occurrence_id"], 50)
        path = self.bob.path
        self.bob.close()
        restarted = TeamStore(path)
        self.stores.append(restarted)
        self.assertEqual([], restarted.due_notifications(employee, 99))
        self.assertEqual("reminder", restarted.due_notifications(employee, 100)[0]["notification_kind"])

    def test_reminder_interval_advances_after_presentation(self):
        entity_id = self.message(repeat_minutes=5)
        employee = self.employee(self.bob)
        first = self.bob.due_notifications(employee, 50)[0]
        self.bob.mark_presented(employee, "message", entity_id, first["occurrence_id"], 50)
        reminder = self.bob.due_notifications(employee, 100)[0]
        self.bob.mark_presented(employee, "message", entity_id, reminder["occurrence_id"], 100)
        self.assertEqual([], self.bob.due_notifications(employee, 399))
        next_alert = self.bob.due_notifications(employee, 400)[0]
        self.assertNotEqual(reminder["occurrence_id"], next_alert["occurrence_id"])

    def test_one_shot_reminder_stops_after_due_popup(self):
        entity_id = self.message(repeat_minutes=0)
        employee = self.employee(self.bob)
        initial = self.bob.due_notifications(employee, 50)[0]
        self.bob.mark_presented(employee, "message", entity_id, initial["occurrence_id"], 50)
        reminder = self.bob.due_notifications(employee, 100)[0]
        self.bob.mark_presented(employee, "message", entity_id, reminder["occurrence_id"], 100)
        self.assertEqual([], self.bob.due_notifications(employee, 1000))

    def test_snooze_before_initial_popup_defers_it(self):
        entity_id = self.message()
        employee = self.employee(self.bob)
        self.bob.snooze(employee, "message", entity_id, 5, 10)
        self.assertEqual([], self.bob.due_notifications(employee, 309))
        self.assertEqual("added", self.bob.due_notifications(employee, 310)[0]["notification_kind"])

    def test_reading_does_not_cancel_reminders(self):
        entity_id = self.message()
        employee = self.employee(self.bob)
        self.bob.mark_read(employee, "message", entity_id, 50)
        self.assertTrue(self.bob.inbox()[0]["read"])
        self.assertFalse(self.bob.inbox()[0]["acknowledged"])
        self.assertEqual(1, len(self.bob.due_notifications(employee, 100)))

    def test_employee_acknowledgements_and_snoozes_are_independent(self):
        entity_id = self.message([self.employee(self.bob), self.employee(self.carol)])
        self.bob.acknowledge(self.employee(self.bob), "message", entity_id, 50)
        self.sync(self.bob, self.alice, self.carol)
        self.assertEqual([], self.bob.due_notifications(self.employee(self.bob), 100))
        self.assertEqual(1, len(self.carol.due_notifications(self.employee(self.carol), 100)))
        self.carol.snooze(self.employee(self.carol), "message", entity_id, 5, 100)
        self.sync(self.carol, self.bob)
        self.assertTrue(self.bob.receipt(self.employee(self.bob), "message", entity_id)["acknowledged"])

    def test_receipt_replay_syncs_between_two_pcs_for_one_employee(self):
        entity_id = self.message()
        second = self.store("Bob laptop", self.employee(self.bob))
        for entity_event in self.alice._events("message", entity_id):
            second.ingest_event(entity_event)
        alert = self.bob.due_notifications(self.employee(self.bob), 50)[0]
        self.bob.mark_presented(self.employee(self.bob), "message", entity_id, alert["occurrence_id"], 50)
        self.sync(self.bob, second)
        self.assertEqual([], second.due_notifications(self.employee(second), 99))

    def test_receipts_for_another_employee_are_rejected(self):
        entity_id = self.message()
        with self.assertRaises(ValueError):
            self.carol.acknowledge(self.employee(self.bob), "message", entity_id, 100)
        with self.assertRaises(ValueError):
            self.carol.acknowledge(self.employee(self.carol), "message", entity_id, 100)

    def test_shared_completion_cancels_every_employee(self):
        note = self.note(kind="order", customer="Example Customer", order_ref="TEST-1", order_status="ready")
        self.alice.put_note(note)
        self.sync(self.alice, self.bob, self.carol)
        self.assertEqual(1, len(self.bob.due_notifications(self.employee(self.bob), 100)))
        self.alice.put_note({**note, "order_status": "delivered", "done": True})
        self.sync(self.alice, self.bob, self.carol)
        self.assertEqual([], self.bob.due_notifications(self.employee(self.bob), 100))
        self.assertEqual([], self.carol.due_notifications(self.employee(self.carol), 100))

    def test_cancelled_order_does_not_need_done_flag(self):
        self.alice.put_note(self.note(kind="order", order_status="cancelled"))
        self.assertEqual([], self.alice.due_notifications(self.employee(self.alice), 100))

    def test_order_due_is_used_without_due_at(self):
        note = self.note(kind="order", due_at=None, order_due=150)
        self.alice.put_note(note)
        alert = self.alice.due_notifications(self.employee(self.alice), 50)[0]
        self.assertEqual(150, alert["due"])

    def test_document_import_without_schedule_has_no_popup(self):
        note = self.note(repeat_minutes=0, due_at=None, document={"sha256": "a" * 64, "characters": 1_000_000_000})
        self.alice.put_note(note)
        self.assertEqual([], self.alice.due_notifications(self.employee(self.alice), 100))

    def test_invalid_large_document_and_oversized_preview_rejected(self):
        with self.assertRaises(ValueError):
            self.alice.put_note(self.note(document={"sha256": "a" * 64, "characters": 1_000_000_001}))
        with self.assertRaises(ValueError):
            self.alice.put_note(self.note(body="x" * 10_001))
        self.assertEqual([], self.alice.pending_events())

    def test_cross_record_revision_rejected_even_parent_arrives_later(self):
        parent = self.alice.put_note(self.note())
        child = self.alice.put_note(self.note())
        child["parents"] = [parent["event_id"]]
        self.bob.ingest_event(child)
        with self.assertRaises(ValueError):
            self.bob.ingest_event(parent)

    def test_cyclic_events_are_rejected_transactionally(self):
        base = self.alice.put_note(self.note())
        a, b, c = [copy.deepcopy(base) for _ in range(3)]
        for event in (a, b, c):
            event["event_id"] = uuid.uuid4().hex
        a["parents"], b["parents"], c["parents"] = [b["event_id"]], [c["event_id"]], [a["event_id"]]
        self.bob.ingest_event(a)
        self.bob.ingest_event(b)
        with self.assertRaises(ValueError):
            self.bob.ingest_event(c)
        self.assertEqual(2, len(self.bob._events("note", base["entity_id"])))

    def test_causal_snooze_ignores_clock_skew(self):
        entity_id = self.message()
        employee = self.employee(self.bob)
        self.bob.snooze(employee, "message", entity_id, 5, 100)
        self.bob.mark_read(employee, "message", entity_id, 90)
        self.bob.snooze(employee, "message", entity_id, 1, 80)
        self.assertEqual(140, self.bob.receipt(employee, "message", entity_id)["next_due"])

    def test_malformed_payload_cannot_block_valid_notification(self):
        valid = self.alice.put_note(self.note())
        invalid = copy.deepcopy(valid)
        invalid["event_id"] = uuid.uuid4().hex
        invalid["entity_id"] = uuid.uuid4().hex
        invalid["payload"]["id"] = invalid["entity_id"]
        invalid["payload"]["created"] = "bad-date"
        with self.assertRaises(ValueError):
            self.bob.ingest_event(invalid)
        self.bob.ingest_event(valid)
        self.assertEqual(valid["entity_id"], self.bob.due_notifications(self.employee(self.bob), 100)[0]["entity_id"])

    def test_consumed_payload_types_are_validated(self):
        for changes in ({"done": "false"}, {"kind": []}, {"customer": []}, {"contact": None},
                        {"order_status": {}}, {"written": float("inf")}, {"notify": 0},
                        {"checklist": [{"id": uuid.uuid4().hex, "text": "Item", "quantity": True}]},
                        {"created": True}, {"recipients": "Everyone"}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, TypeError)):
                self.alice.put_note(self.note(**changes))
        self.assertEqual([], self.alice.pending_events())

    def test_deadline_edit_starts_new_receipt_scope(self):
        note = self.note()
        self.alice.put_note(note)
        employee = self.employee(self.alice)
        alert = self.alice.due_notifications(employee, 50)[0]
        self.alice.mark_presented(employee, "note", note["id"], alert["occurrence_id"], 50)
        self.alice.acknowledge(employee, "note", note["id"], 60)
        self.assertEqual([], self.alice.due_notifications(employee, 100))
        self.alice.put_note({**note, "due_at": 10000})
        changed = self.alice.due_notifications(employee, 100)[0]
        self.assertEqual(10000, changed["due"])
        self.assertNotEqual(alert["occurrence_id"], changed["occurrence_id"])
        self.assertFalse(self.alice.receipt(employee, "note", note["id"])["acknowledged"])

    def test_body_edit_keeps_acknowledgement_and_presentation(self):
        note = self.note()
        self.alice.put_note(note)
        employee = self.employee(self.alice)
        alert = self.alice.due_notifications(employee, 50)[0]
        self.alice.mark_presented(employee, "note", note["id"], alert["occurrence_id"], 50)
        self.alice.acknowledge(employee, "note", note["id"], 60)
        self.alice.put_note({**note, "title": "Spelling correction", "body": "Extra details"})
        self.assertEqual([], self.alice.due_notifications(employee, 100))
        self.assertTrue(self.alice.receipt(employee, "note", note["id"])["acknowledged"])

    def test_deadline_change_back_does_not_reuse_old_acknowledgement(self):
        note = self.note()
        self.alice.put_note(note)
        employee = self.employee(self.alice)
        self.alice.acknowledge(employee, "note", note["id"], 60)
        self.alice.put_note({**note, "due_at": 200})
        self.alice.put_note(note)
        self.assertFalse(self.alice.receipt(employee, "note", note["id"])["acknowledged"])
        self.assertEqual(1, len(self.alice.due_notifications(employee, 100)))

    def test_reassignment_starts_new_receipt_scope(self):
        employee = self.employee(self.alice)
        note = self.note(recipients=[employee])
        self.alice.put_note(note)
        self.alice.acknowledge(employee, "note", note["id"], 60)
        self.alice.put_note({**note, "recipients": [employee, self.employee(self.bob)]})
        self.assertFalse(self.alice.receipt(employee, "note", note["id"])["acknowledged"])

    def test_disabling_schedule_after_presentation_stops_popups(self):
        note = self.note()
        self.alice.put_note(note)
        employee = self.employee(self.alice)
        alert = self.alice.due_notifications(employee, 50)[0]
        self.alice.mark_presented(employee, "note", note["id"], alert["occurrence_id"], 50)
        self.alice.put_note({**note, "due_at": None, "repeat_minutes": 0})
        self.assertEqual([], self.alice.due_notifications(employee, 100))

    def test_reopened_customer_order_is_a_new_task_generation(self):
        note = self.note(kind="order", order_status="ready")
        self.alice.put_note(note)
        employee = self.employee(self.alice)
        self.alice.acknowledge(employee, "note", note["id"], 60)
        self.alice.put_note({**note, "order_status": "delivered", "done": True})
        self.alice.put_note(note)
        self.assertFalse(self.alice.receipt(employee, "note", note["id"])["acknowledged"])

    def test_explicit_notify_false_suppresses_popups(self):
        self.alice.put_note(self.note(notify=False))
        self.assertEqual([], self.alice.due_notifications(self.employee(self.alice), 100))

    def test_editing_conflict_without_review_preserves_other_branch(self):
        note = self.note()
        base = self.alice.put_note(note)
        self.sync(self.alice, self.bob)
        a = self.alice.put_note({**note, "title": "Alice branch"}, [base["event_id"]])
        b = self.bob.put_note({**note, "title": "Bob branch"}, [base["event_id"]])
        self.alice.ingest_event(b)
        self.alice.put_note({**note, "title": "Continued edit"})
        entity = self.alice.get_entity("note", note["id"])
        self.assertTrue(entity["conflict"])
        self.assertEqual(2, len(entity["versions"]))
        self.assertIn("Continued edit", {v["payload"]["title"] for v in entity["versions"]})

    def test_local_cross_record_revision_is_rejected_before_outbox(self):
        parent = self.alice.put_note(self.note())
        with self.assertRaises(ValueError):
            self.alice.put_note(self.note(), [parent["event_id"]])
        self.assertEqual([parent], self.alice.pending_events())

    def test_unrepresentable_date_is_rejected(self):
        with self.assertRaises(ValueError):
            self.alice.put_note(self.note(due_at=1e300))
        self.assertEqual([], self.alice.pending_events())


if __name__ == "__main__":
    unittest.main()
