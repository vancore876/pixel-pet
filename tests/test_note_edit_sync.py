"""Successive local saves must not become extra copies of a real conflict."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from team_service import TeamService
from team_store import TeamStore
import test_team_revision_recovery as recovery


class NoteEditSyncTests(unittest.TestCase):
    setUpClass = classmethod(recovery.TeamRevisionRecoveryTests.setUpClass.__func__)
    setUp = recovery.TeamRevisionRecoveryTests.setUp
    tearDown = recovery.TeamRevisionRecoveryTests.tearDown
    wait = recovery.TeamRevisionRecoveryTests.wait
    client = recovery.TeamRevisionRecoveryTests.client
    sync = recovery.TeamRevisionRecoveryTests.sync
    create_three = recovery.TeamRevisionRecoveryTests.create_three
    all_sync = recovery.TeamRevisionRecoveryTests.all_sync
    assert_no_errors = recovery.TeamRevisionRecoveryTests.assert_no_errors

    def overlapping_edits(self, restart=False):
        alice, bob, carol, identifier = self.create_three('Original text')
        store, notes, team, errors = alice
        published, release = threading.Event(), threading.Event()
        original_sync = team._sync

        def delayed_completion(state, snapshot, cancel, notify):
            result = original_sync(state, snapshot, cancel, notify)
            if (not published.is_set()
                    and any(note['body'] == 'Alice first edit' for note in snapshot['notes'])):
                published.set()
                if not release.wait(12):
                    raise RuntimeError('The second local save did not complete in time.')
            return result

        with patch.object(team, '_sync', side_effect=delayed_completion):
            try:
                notes.save_note(identifier, 'Customer notebook', 'Alice first edit', 0)
                self.wait(published.is_set)
                # Bob has not consumed Alice's first edit, so this is a genuine
                # simultaneous body edit that must remain available for review.
                bob[1].save_note(identifier, 'Customer notebook', 'Bob independent edit', 0)
                self.sync(bob[2])
                notes.save_note(identifier, 'Customer notebook', 'Alice final edit', 0,
                                details={'highlights': ['Alice']})
                if restart:
                    # Stop before the first published result can update the
                    # notebook's old revision. Its ledger survives this restart.
                    team.stop()
            finally:
                release.set()
            if not restart:
                self.sync(team)
        if restart:
            team._thread.join(timeout=3)
            self.assertFalse(team._thread.is_alive())
            notes.changed.disconnect(team._local_changed)
            replacement = TeamService(notes, team.settings, team.profile_path,
                                      can_notify=lambda: False)
            replacement.error.connect(errors.append)
            alice = (store, notes, replacement, errors)
            self.clients[self.clients.index((store, notes, team, errors))] = alice
            team.deleteLater()
            replacement.configure(str(self.share), 'Alice')
            replacement.timer.stop()
            self.wait(lambda: bool(replacement.identity()) and not replacement._busy)

        self.all_sync((alice, bob, carol), rounds=3)
        for client in (alice, bob, carol):
            self.assertEqual(len(client[0].notes), 2,
                             'The earlier local save must not remain as a third conflict copy.')
            self.assertEqual({note['body'] for note in client[0].notes},
                             {'Alice final edit', 'Bob independent edit'})
            latest = next(note for note in client[0].notes if note['body'] == 'Alice final edit')
            self.assertEqual(latest['highlights'], ['Alice'])
            cache = TeamStore(client[2].profile_path)
            try:
                entity = cache.get_entity('note', identifier)
                self.assertTrue(entity['conflict'], 'The coworker version must not be silently overwritten.')
                self.assertEqual(len(entity['revisions']), 2)
            finally:
                cache.close()
        self.assert_no_errors()

    def test_rapid_local_edits_replace_own_branch_but_preserve_coworker_edit(self):
        self.overlapping_edits()

    def test_restart_after_published_edit_preserves_only_latest_own_branch(self):
        self.overlapping_edits(restart=True)


class LocalSuccessorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = uuid.uuid4().hex
        self.stores = []
        self.alice = self.store('Alice')
        self.note = {'id': uuid.uuid4().hex, 'title': 'Stock note', 'body': 'Original',
                     'created': 100, 'repeat_minutes': 0, 'due_at': None, 'recipients': []}
        self.base = self.alice.put_note(self.note)

    def tearDown(self):
        for store in self.stores:
            store.close()
        self.temp.cleanup()

    def store(self, name, employee_id=None):
        store = TeamStore(self.root / (uuid.uuid4().hex + '.sqlite'))
        store.configure(self.workspace, name, employee_id)
        self.stores.append(store)
        return store

    def test_own_edit_chain_is_available_after_database_reopen(self):
        first = self.alice.put_note({**self.note, 'body': 'First'}, [self.base['event_id']])
        last = self.alice.put_note({**self.note, 'body': 'Last'}, [first['event_id']])
        path = self.alice.path
        self.alice.close()
        reopened = TeamStore(path)
        self.stores.append(reopened)
        self.assertTrue(reopened.is_local_successor('note', self.note['id'],
                        [self.base['event_id']], [last['event_id']]))
        self.assertFalse(reopened.is_local_successor('note', self.note['id'],
                         [first['event_id'], uuid.uuid4().hex], [last['event_id']]))
        self.assertFalse(reopened.is_local_successor('note', uuid.uuid4().hex,
                         [self.base['event_id']], [last['event_id']]))

    def test_another_pc_with_the_same_employee_is_not_a_local_edit(self):
        other_pc = self.store('Alice second PC', self.alice.identity()['employee_id'])
        other_pc.ingest_event(self.base)
        remote = other_pc.put_note({**self.note, 'body': 'Second PC edit'}, [self.base['event_id']])
        self.alice.ingest_event(remote)
        own = self.alice.put_note({**self.note, 'title': 'Later local title'}, [remote['event_id']])
        self.assertEqual(remote['actor_id'], own['actor_id'])
        self.assertNotEqual(remote['device_id'], own['device_id'])
        self.assertFalse(self.alice.is_local_successor('note', self.note['id'],
                         [self.base['event_id']], [remote['event_id']]))
        self.assertFalse(self.alice.is_local_successor('note', self.note['id'],
                         [self.base['event_id']], [own['event_id']]))
        self.assertTrue(self.alice.is_local_successor('note', self.note['id'],
                        [remote['event_id']], [own['event_id']]))

    def test_deletion_and_unrelated_roots_cannot_advance_old_parents(self):
        deleted = self.alice.delete_note(self.note['id'], [self.base['event_id']])
        self.assertFalse(self.alice.is_local_successor('note', self.note['id'],
                         [self.base['event_id']], [deleted['event_id']]))
        self.assertFalse(self.alice.is_local_successor('note', self.note['id'], [],
                         [self.base['event_id']]))
        other = self.alice.put_note({**self.note, 'id': uuid.uuid4().hex})
        self.assertFalse(self.alice.is_local_successor('note', self.note['id'],
                         [self.base['event_id']], [other['event_id']]))


if __name__ == '__main__':
    unittest.main()
