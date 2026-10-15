"""Cross-conversation notification privacy and one-use account recovery."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_work_server as server_tests


@unittest.skipUnless(server_tests.SERVER_AVAILABLE, "Install requirements-server.txt")
class RecoveryAndNotificationHTTPTests(unittest.TestCase):
    setUpClass = classmethod(server_tests.WorkServerHTTPTests.setUpClass.__func__)
    tearDownClass = classmethod(server_tests.WorkServerHTTPTests.tearDownClass.__func__)
    request = server_tests.WorkServerHTTPTests.request
    register = server_tests.WorkServerHTTPTests.register

    def test_notification_feed_is_authenticated_and_has_no_initial_history(self):
        alice, bob = self.register(), self.register()
        message = self.app.state.store.send(alice['user']['id'], 'Existing team announcement')
        self.assertEqual(self.request('/api/notifications')[0], 401)
        status, payload, _ = self.request('/api/notifications', token=bob['token'])
        self.assertEqual(status, 200)
        self.assertEqual(payload, {'messages': [], 'cursor': message['id'],
                                   'latest_id': message['id'], 'has_more': False})
        for query in ('after_id=-1', 'after_id=9223372036854775808', 'limit=0', 'limit=101'):
            self.assertEqual(self.request('/api/notifications?' + query, token=bob['token'])[0], 422)

    def test_auth_cursor_captures_sign_in_and_preserves_messages_before_first_poll(self):
        alice = self.register()
        store = self.app.state.store
        earlier = store.send(alice['user']['id'], 'Already here before Bob joins')
        bob = self.register()
        self.assertEqual(bob['notification_cursor'], earlier['id'])
        just_after_signup = store.send(alice['user']['id'], 'Sent immediately after signup', bob['user']['id'])
        page = self.request('/api/notifications?after_id=' + str(bob['notification_cursor']), token=bob['token'])[1]
        self.assertEqual([message['id'] for message in page['messages']], [just_after_signup['id']])
        logged_in = self.request('/api/auth/login', method='POST', data={
            'username': bob['user']['username'], 'password': server_tests.PASSWORD})[1]
        self.assertEqual(logged_in['notification_cursor'], just_after_signup['id'])
        after_login = store.send(alice['user']['id'], 'New team message before first feed poll')
        page = self.request('/api/notifications?after_id=' + str(logged_in['notification_cursor']), token=logged_in['token'])[1]
        self.assertEqual([message['id'] for message in page['messages']], [after_login['id']])
        self.assertEqual(logged_in['user'], bob['user'])

    def test_feed_drains_all_conversations_without_other_direct_messages_or_self(self):
        alice, bob, charlie = self.register(), self.register(), self.register()
        store = self.app.state.store
        cursor = self.request('/api/notifications', token=bob['token'])[1]['cursor']
        expected = []
        expected.append(store.send(alice['user']['id'], 'New team')['id'])
        store.send(bob['user']['id'], 'My team message')
        expected.append(store.send(alice['user']['id'], 'Personal for Bob', bob['user']['id'])['id'])
        store.send(alice['user']['id'], 'Only Charlie', charlie['user']['id'])
        expected.append(store.send(charlie['user']['id'], 'Another personal for Bob', bob['user']['id'])['id'])
        store.send(bob['user']['id'], 'My personal to Alice', alice['user']['id'])
        received = []
        for _ in range(3):
            status, page, _ = self.request(f'/api/notifications?after_id={cursor}&limit=1', token=bob['token'])
            self.assertEqual(status, 200)
            self.assertEqual(len(page['messages']), 1)
            received += [item['id'] for item in page['messages']]
            self.assertEqual(page['cursor'], received[-1])
            self.assertEqual(page['has_more'], len(received) < 3)
            cursor = page['cursor']
        self.assertEqual(received, expected)
        empty = self.request(f'/api/notifications?after_id={cursor}', token=bob['token'])[1]
        self.assertEqual(empty['messages'], [])
        self.assertEqual(empty['cursor'], cursor)
        future = self.request(f'/api/notifications?after_id={cursor+1000}', token=bob['token'])[1]
        self.assertEqual(future['cursor'], cursor + 1000)

    def test_registration_recovery_code_is_hashed_and_not_returned_on_login(self):
        account = self.register()
        code = account['recovery_code']
        self.assertRegex(code, r'^[A-Za-z0-9_-]{43}$')
        with closing(sqlite3.connect(self.database)) as connection:
            digest = connection.execute('SELECT code_digest FROM recovery_codes WHERE user_id=?',
                                        (account['user']['id'],)).fetchone()[0]
        self.assertEqual(digest, hashlib.sha256(code.encode('ascii')).hexdigest())
        self.assertNotEqual(digest, code)
        logged_in = self.request('/api/auth/login', method='POST',
            data={'username': account['user']['username'], 'password': server_tests.PASSWORD})[1]
        self.assertNotIn('recovery_code', logged_in)

    def test_password_recovery_rotates_code_revokes_all_sessions_and_preserves_messages(self):
        account = self.register()
        username = account['user']['username']
        second = self.app.state.store.login(username, server_tests.PASSWORD)
        message = self.app.state.store.send(account['user']['id'], 'Keep my account and messages')
        payload = {'username': username.upper(), 'recovery_code': account['recovery_code'],
                   'new_password': 'replacement strong passphrase'}
        status, recovered, _ = self.request('/api/auth/reset-password', method='POST', data=payload)
        self.assertEqual(status, 200)
        self.assertEqual(set(recovered), {'recovery_code'})
        self.assertNotEqual(recovered['recovery_code'], account['recovery_code'])
        for token in (account['token'], second['token']):
            self.assertEqual(self.request('/api/me', token=token)[0], 401)
        self.assertEqual(self.request('/api/auth/reset-password', method='POST', data=payload)[0], 401)
        self.assertEqual(self.request('/api/auth/login', method='POST',
            data={'username': username, 'password': server_tests.PASSWORD})[0], 401)
        status, logged_in, _ = self.request('/api/auth/login', method='POST',
            data={'username': username, 'password': payload['new_password']})
        self.assertEqual(status, 200)
        self.assertEqual(logged_in['user'], account['user'])
        history = self.request('/api/messages?after_id=' + str(message['id'] - 1), token=logged_in['token'])[1]
        self.assertIn(message, history['messages'])
        payload['recovery_code'] = recovered['recovery_code']
        payload['new_password'] = 'another replacement passphrase'
        self.assertEqual(self.request('/api/auth/reset-password', method='POST', data=payload)[0], 200)

    def test_invalid_recovery_is_generic_and_validation_does_not_echo_secrets(self):
        account = self.register()
        payload = {'username': account['user']['username'], 'recovery_code': 'x' * 43,
                   'new_password': 'private new password phrase'}
        wrong = self.request('/api/auth/reset-password', method='POST', data=payload)
        payload['username'] = 'no_such_recovery_user'
        unknown = self.request('/api/auth/reset-password', method='POST', data=payload)
        self.assertEqual(wrong[:2], unknown[:2])
        self.assertEqual(wrong[0], 401)
        for key, value in (('recovery_code', 'short'), ('new_password', 'short'), ('username', 'not valid')):
            invalid = dict(payload)
            invalid[key] = value
            status, result, _ = self.request('/api/auth/reset-password', method='POST', data=invalid)
            self.assertEqual(status, 422)
            self.assertNotIn(payload['new_password'], str(result))
        self.assertEqual(self.request('/api/auth/reset-password', method='POST',
            data={'username': account['user']['username'], 'new_password': payload['new_password']})[0], 422)

    def test_recovery_generation_requires_current_password_and_invalidates_previous_code(self):
        account = self.register()
        path = '/api/auth/recovery-code'
        self.assertEqual(self.request(path, method='POST', data={'password': server_tests.PASSWORD})[0], 401)
        self.assertEqual(self.request(path, method='POST', token=account['token'],
            data={'password': 'incorrect password phrase'})[0], 401)
        status, generated, _ = self.request(path, method='POST', token=account['token'],
            data={'password': server_tests.PASSWORD})
        self.assertEqual(status, 200)
        self.assertNotEqual(generated['recovery_code'], account['recovery_code'])
        payload = {'username': account['user']['username'], 'recovery_code': account['recovery_code'],
                   'new_password': 'replacement password phrase'}
        self.assertEqual(self.request('/api/auth/reset-password', method='POST', data=payload)[0], 401)
        payload['recovery_code'] = generated['recovery_code']
        self.assertEqual(self.request('/api/auth/reset-password', method='POST', data=payload)[0], 200)

    def test_recovery_attempts_are_rate_limited_before_password_hashing(self):
        account = self.register()
        limiter = self.app.state.account_limiter
        original_maximum = limiter.maximum
        try:
            limiter.maximum = 1
            limiter.attempts.clear()
            payload = {'username': account['user']['username'], 'recovery_code': 'x' * 43,
                       'new_password': 'private replacement phrase'}
            self.assertEqual(self.request('/api/auth/reset-password', method='POST', data=payload)[0], 401)
            payload['recovery_code'] = account['recovery_code']
            with patch.object(self.app.state.store, '_hash', side_effect=AssertionError('must not hash')):
                status, _, headers = self.request('/api/auth/reset-password', method='POST', data=payload)
            self.assertEqual(status, 429)
            self.assertEqual(headers['Retry-After'], '60')
        finally:
            limiter.maximum = original_maximum
            limiter.attempts.clear()


@unittest.skipUnless(server_tests.SERVER_AVAILABLE, "Install requirements-server.txt")
class RecoveryConcurrencyTests(unittest.TestCase):
    def test_concurrent_code_use_only_one_reset_succeeds(self):
        from work_server.store import ChatStore, StoreError
        with tempfile.TemporaryDirectory() as directory:
            store = ChatStore(Path(directory) / 'chat.sqlite')
            account = store.register('staff_one', server_tests.PASSWORD)
            def reset():
                try:
                    return store.recover_password('staff_one', account['recovery_code'], 'a replacement passphrase')
                except StoreError as error:
                    return error.status
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: reset(), range(2)))
            self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
            self.assertEqual(results.count(401), 1)

    def test_legacy_database_can_generate_code_and_admin_reset_revokes_code(self):
        from work_server.store import ChatStore, StoreError
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'chat.sqlite'
            store = ChatStore(path)
            account = store.register('legacy_staff', server_tests.PASSWORD)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute('DROP TABLE recovery_codes')
            restored = ChatStore(path)
            generated = restored.generate_recovery_code(account['user']['id'], server_tests.PASSWORD)
            restored.reset_password('legacy_staff', 'administrator replacement phrase')
            with self.assertRaises(StoreError) as raised:
                restored.recover_password('legacy_staff', generated['recovery_code'], 'another replacement phrase')
            self.assertEqual(raised.exception.status, 401)
            self.assertEqual(restored.login('legacy_staff', 'administrator replacement phrase')['user'], account['user'])


if __name__ == '__main__':
    unittest.main()
