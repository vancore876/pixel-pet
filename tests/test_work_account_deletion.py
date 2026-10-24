"""Permanent account removal keeps company records and separates new identities."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_work_server as server_tests


@unittest.skipUnless(server_tests.SERVER_AVAILABLE, 'Install requirements-server.txt')
class AccountDeletionHTTPTests(unittest.TestCase):
    setUpClass = classmethod(server_tests.WorkServerHTTPTests.setUpClass.__func__)
    tearDownClass = classmethod(server_tests.WorkServerHTTPTests.tearDownClass.__func__)
    request = server_tests.WorkServerHTTPTests.request
    register = server_tests.WorkServerHTTPTests.register

    def delete(self, account, **changes):
        body = {'password': server_tests.PASSWORD, 'confirm_username': account['user']['username']}
        body.update(changes)
        return self.request('/api/account', method='DELETE', token=account['token'], data=body)

    def test_requires_session_current_password_and_exact_confirmation(self):
        account = self.register()
        body = {'password': server_tests.PASSWORD, 'confirm_username': account['user']['username']}
        self.assertEqual(self.request('/api/account', method='DELETE', data=body)[0], 401)
        self.assertEqual(self.delete(account, password='a different password phrase')[0], 401)
        self.assertEqual(self.delete(account, confirm_username=account['user']['username'].upper())[0], 422)
        self.assertEqual(self.delete(account, confirm_username='someone_else')[0], 422)
        self.assertEqual(self.request('/api/me', token=account['token'])[1], account['user'])
        for bad in ({'password': server_tests.PASSWORD}, {**body, 'user_id': 9999}):
            status, result, _ = self.request('/api/account', method='DELETE', token=account['token'], data=bad)
            self.assertEqual(status, 422)
            self.assertNotIn(server_tests.PASSWORD, str(result))

    def test_deletion_revokes_credentials_anonymizes_company_history_and_clears_stages(self):
        alice, bob = self.register(), self.register()
        store = self.app.state.store
        second_session = store.login(alice['user']['username'], server_tests.PASSWORD)
        team = store.send(alice['user']['id'], 'Keep this company announcement')
        direct = store.send(alice['user']['id'], 'Keep our existing conversation', bob['user']['id'])
        identifier = uuid.uuid4().hex
        status, order, _ = self.request('/api/business/' + identifier, method='PUT', token=alice['token'],
            data={'expected_revision': 0, 'entry': {'kind': 'list', 'title': 'Company checklist', 'body': 'Retain these records'}})
        self.assertEqual(status, 200)
        revision = self.request('/api/inventory/summary', token=alice['token'])[1]['revision']
        stage = self.request('/api/inventory/imports', method='POST', token=alice['token'],
            data={'source': 'parts.xlsx', 'expected_revision': revision})[1]['id']
        self.assertEqual(self.request('/api/inventory/imports/' + stage + '/rows', method='POST', token=alice['token'],
            data={'rows': [{'sku': 'DELETE-KEEP', 'description': 'Air filter', 'quantity': 4, 'location': 'Shelf 8'}]})[0], 200)
        self.assertEqual(self.request('/api/inventory/imports/' + stage + '/commit', method='POST', token=alice['token'], data={})[0], 200)
        revision += 1
        pending = self.request('/api/inventory/imports', method='POST', token=alice['token'],
            data={'source': 'not-committed.xlsx', 'expected_revision': revision})[1]['id']
        self.assertEqual(self.request('/api/inventory/imports/' + pending + '/rows', method='POST', token=alice['token'],
            data={'rows': [{'sku': 'DELETE-DROP', 'quantity': 9}]})[0], 200)

        self.assertEqual(self.delete(alice)[0], 204)
        for token in (alice['token'], second_session['token']):
            self.assertEqual(self.request('/api/me', token=token)[0], 401)
        self.assertEqual(self.request('/api/auth/login', method='POST', data={
            'username': alice['user']['username'], 'password': server_tests.PASSWORD})[0], 401)
        self.assertEqual(self.request('/api/auth/reset-password', method='POST', data={
            'username': alice['user']['username'], 'recovery_code': alice['recovery_code'],
            'new_password': 'a replacement password phrase'})[0], 401)
        self.assertNotIn(alice['user'], self.request('/api/users', token=bob['token'])[1]['users'])

        team_history = self.request('/api/messages?after_id=' + str(team['id'] - 1), token=bob['token'])[1]
        retained = next(message for message in team_history['messages'] if message['id'] == team['id'])
        self.assertEqual(retained['sender_username'], 'deleted-user')
        personal = self.request('/api/messages?peer_id=' + str(alice['user']['id']), token=bob['token'])[1]
        self.assertEqual(personal['messages'], [{**direct, 'sender_username': 'deleted-user'}])
        self.assertEqual(self.request('/api/messages', method='POST', token=bob['token'],
            data={'body': 'Cannot send to deleted account', 'recipient_id': alice['user']['id']})[0], 404)
        updated = self.request('/api/business?after_revision=' + str(order['revision']), token=bob['token'])[1]
        retained_order = next(entry for entry in updated['entries'] if entry['id'] == identifier)
        self.assertEqual(retained_order['updated_by_username'], 'deleted-user')
        self.assertEqual(retained_order['entry'], {**order['entry'], 'added_by': 'deleted-user'})
        self.assertGreater(retained_order['revision'], order['revision'])
        item = self.request('/api/inventory/items/DELETE-KEEP', token=bob['token'])[1]
        self.assertEqual((item['quantity'], item['location']), (4, 'Shelf 8'))
        self.assertEqual(self.request('/api/inventory/items/DELETE-DROP', token=bob['token'])[0], 404)
        self.assertNotIn(alice['user']['username'], json.dumps([retained, personal, retained_order]))
        with closing(store._connect()) as connection:
            removed = dict(connection.execute('SELECT * FROM users WHERE id=?', (alice['user']['id'],)).fetchone())
            self.assertNotEqual(removed['username'], alice['user']['username'])
            self.assertEqual(removed['password_hash'], '!deleted')
            self.assertTrue(removed['deleted_at'])
            for table in ('sessions', 'recovery_codes', 'inventory_imports'):
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM ' + table + ' WHERE user_id=?', (alice['user']['id'],)).fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM inventory_import_rows WHERE import_id IN (?,?)', (stage, pending)).fetchone()[0], 0)

    def test_reusing_username_creates_new_identity_without_old_direct_messages(self):
        from work_server.store import StoreError
        alice, bob = self.register(), self.register()
        store = self.app.state.store
        old_message = store.send(bob['user']['id'], 'Only for the original employee', alice['user']['id'])
        self.assertEqual(self.delete(alice)[0], 204)
        with closing(store._connect()) as connection:
            tombstone_name = connection.execute('SELECT username FROM users WHERE id=?', (alice['user']['id'],)).fetchone()[0]
        with self.assertRaises(StoreError) as error:
            store.reset_password(tombstone_name, 'cannot revive this account')
        self.assertEqual(error.exception.status, 404)
        self.assertEqual(self.request('/api/auth/login', method='POST', data={
            'username': tombstone_name, 'password': server_tests.PASSWORD})[0], 401)
        status, replacement, _ = self.request('/api/auth/register', method='POST', data={
            'username': alice['user']['username'], 'password': server_tests.PASSWORD})
        self.assertEqual(status, 201)
        self.assertNotEqual(replacement['user']['id'], alice['user']['id'])
        history = self.request('/api/messages?peer_id=' + str(bob['user']['id']), token=replacement['token'])[1]
        self.assertEqual(history['messages'], [])
        feed = self.request('/api/notifications?after_id=' + str(old_message['id'] - 1), token=replacement['token'])[1]
        self.assertNotIn(old_message['id'], [message['id'] for message in feed['messages']])

    def test_inflight_authenticated_inventory_request_cannot_create_stage_after_deletion(self):
        alice = self.register()
        store = self.app.state.store
        original = store.authenticate
        deleted = []
        def authenticate_then_delete(token):
            user = original(token)
            if token == alice['token'] and not deleted:
                deleted.append(True)
                store.delete_account(user['id'], server_tests.PASSWORD, user['username'])
            return user
        with patch.object(store, 'authenticate', side_effect=authenticate_then_delete):
            status, _, _ = self.request('/api/inventory/imports', method='POST', token=alice['token'],
                data={'source': 'late.xlsx', 'expected_revision': self.app.state.inventory.summary()['revision']})
        self.assertEqual(status, 401)
        with closing(store._connect()) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM inventory_imports WHERE user_id=?', (alice['user']['id'],)).fetchone()[0], 0)


@unittest.skipUnless(server_tests.SERVER_AVAILABLE, 'Install requirements-server.txt')
class AccountDeletionStoreTests(unittest.TestCase):
    def test_deleted_identity_cannot_write_and_capacity_only_counts_active_accounts(self):
        from work_server.store import ChatStore, StoreError
        with tempfile.TemporaryDirectory() as directory:
            store = ChatStore(Path(directory) / 'chat.sqlite', max_users=1)
            account = store.register('one_employee', server_tests.PASSWORD)
            store.delete_account(account['user']['id'], server_tests.PASSWORD, account['user']['username'])
            for operation in (lambda: store.send(account['user']['id'], 'No longer permitted'),
                lambda: store.save_business_entry(account['user']['id'], uuid.uuid4().hex, 0, {'kind': 'list', 'title': 'No'}),
                lambda: store.generate_recovery_code(account['user']['id'], server_tests.PASSWORD)):
                with self.assertRaises(StoreError) as error:
                    operation()
                self.assertEqual(error.exception.status, 401)
            new = store.register('one_employee', server_tests.PASSWORD)
            self.assertNotEqual(new['user']['id'], account['user']['id'])
            self.assertEqual(store.users(), [new['user']])

    def test_existing_database_without_deletion_column_migrates_in_place(self):
        from work_server.store import ChatStore
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'chat.sqlite'
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute('CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL)')
                connection.execute('INSERT INTO users VALUES (1,?,?,?)', ('legacy_employee', '!unused', '2025-01-01T00:00:00Z'))
            store = ChatStore(path)
            self.assertEqual(store.users(), [{'id': 1, 'username': 'legacy_employee'}])
            with closing(store._connect()) as connection:
                self.assertIsNone(connection.execute('SELECT deleted_at FROM users WHERE id=1').fetchone()[0])


if __name__ == '__main__':
    unittest.main()
