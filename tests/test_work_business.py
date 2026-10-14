"""Authenticated shared business records, synchronization and write conflicts."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import copy
import importlib.util
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server_business_schema import BusinessValidationError, validate_business_entry

SERVER_AVAILABLE = all(importlib.util.find_spec(module) for module in ('fastapi', 'uvicorn', 'argon2'))
if SERVER_AVAILABLE:
    import uvicorn
    from work_server.app import RequestGuard, create_app
    from work_server.store import ChatStore


def entry(identifier=None, **changes):
    return {'id': identifier or uuid.uuid4().hex, 'kind': 'order', 'title': 'Famous Twins order',
            'body': 'Confirm the customer before handover.', 'customer': 'Customer A', 'contact': '876-555-0100',
            'order_ref': 'FT-001', 'order_status': 'preparing', 'order_due': 1_800_003_600,
            'vehicle': '2015 Toyota Corolla 1.8', 'registration': '1234 AB', 'vin': 'Manual VIN',
            'priority': 'urgent', 'order_type': 'order', 'currency': 'JMD', 'payment_received_cents': 10000,
            'checklist': [{'id': 'pads', 'text': 'Brake pads', 'quantity': 2, 'done': False,
                           'part_number': 'PAD-123', 'supplier': 'Parts Supplier', 'bin_location': 'A-12',
                           'unit_price_cents': 12345, 'stock_status': 'to_order'}], **changes}


class PortableBusinessSchemaTests(unittest.TestCase):
    def test_portable_schema_preserves_business_and_neutralizes_local_state(self):
        data = entry(pinned=True, pin_position=[30, 40], source='C:\\private\\file.txt',
                     document_source='Private customer file', ai_guidance={'private': 'AI context'},
                     reminder_history=['Personal phrase'], unannounced=True)
        result = validate_business_entry(data, data['id'], now=1_800_000_000)
        self.assertEqual(result['checklist'], data['checklist'])
        self.assertEqual(result['payment_received_cents'], 10000)
        self.assertEqual(result['currency'], 'JMD')
        self.assertEqual((result['pinned'], result['pin_position'], result['source'], result['document_source']),
                         (False, None, '', ''))
        self.assertEqual((result['ai_guidance'], result['reminder_history'], result['unannounced']), ({}, [], False))

    def test_schema_rejects_documents_unknown_fields_and_invalid_dates(self):
        for changes in ({'document_id': ''}, {'admin': True}, {'kind': 'note'}, {'created': float('nan')},
                        {'created': 100}, {'created': 10**1000}, {'next_due': True}, {'order_due': -1},
                        {'currency': 'USD'}, {'payment_received_cents': True}, {'payment_received_cents': -1},
                        {'body': '\x00'}, {'title': '\ud800'}, {'priority': 'express'}):
            data = entry(**changes)
            with self.subTest(changes=str(changes)[:80]), self.assertRaises(BusinessValidationError):
                validate_business_entry(data, data['id'], now=1_800_000_000)

    def test_schema_validates_rows_and_does_not_truncate_silently(self):
        for changes in ({'unit_price_cents': True}, {'unit_price_cents': -1}, {'unit_price_cents': 1_000_000_001},
                        {'quantity': True}, {'stock_status': 'automatic'}, {'supplier': 'a' * 101},
                        {'text': ''}, {'id': ''}, {'extra': 1}):
            data = entry()
            data['checklist'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(BusinessValidationError):
                validate_business_entry(data, data['id'], now=1_800_000_000)
        data = entry()
        data['checklist'] *= 2
        with self.assertRaises(BusinessValidationError):
            validate_business_entry(data, data['id'], now=1_800_000_000)

    def test_terminal_orders_and_checklists_are_canonical(self):
        data = entry(order_status='cancelled')
        self.assertTrue(validate_business_entry(data, data['id'], now=1_800_000_000)['done'])
        data = entry(done=True)
        self.assertEqual(validate_business_entry(data, data['id'], now=1_800_000_000)['order_status'], 'delivered')
        data = entry(kind='list', done=True)
        self.assertEqual(validate_business_entry(data, data['id'], now=1_800_000_000)['kind'], 'list')


@unittest.skipUnless(SERVER_AVAILABLE, 'Optional backend dependencies: install requirements-server.txt')
class SharedBusinessHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.database = Path(cls.directory.name) / 'chat.sqlite'
        cls.now = [1_800_000_000.0]
        cls.app = create_app(cls.database, clock=lambda: cls.now[0], auth_limit=1000,
                             account_auth_limit=1000, business_limit=1000)
        cls.socket = socket.socket()
        cls.socket.bind(('127.0.0.1', 0))
        cls.address = 'http://127.0.0.1:' + str(cls.socket.getsockname()[1])
        cls.server = uvicorn.Server(uvicorn.Config(cls.app, host='127.0.0.1', log_level='error',
                                                  proxy_headers=False, access_log=False))
        cls.thread = threading.Thread(target=cls.server.run, kwargs={'sockets': [cls.socket]}, daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 10
        while not cls.server.started and cls.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not cls.server.started:
            raise RuntimeError('Loopback shared-business test server did not start')
        cls.alice = cls.app.state.store.register('business_alice', 'correct horse battery staple')
        cls.bob = cls.app.state.store.register('business_bob', 'correct horse battery staple')

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=10)
        cls.socket.close()
        cls.directory.cleanup()

    def setUp(self):
        self.now[0] = 1_800_000_000.0
        self.app.state.store.max_business_entries = 2000
        self.app.state.business_limiter.maximum = 1000
        self.app.state.business_limiter.attempts.clear()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('DELETE FROM business_entries')
            connection.execute('UPDATE business_state SET revision = 0')

    def request(self, path, *, method='GET', data=None, token='alice', headers=None, raw=None):
        merged = dict(headers or {})
        if token in ('alice', 'bob'):
            token = getattr(self, token)['token']
        if token:
            merged['Authorization'] = 'Bearer ' + token
        if data is not None:
            raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
            merged['Content-Type'] = 'application/json'
        request = Request(self.address + path, data=raw, headers=merged, method=method)
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            payload = response.read()
            result = json.loads(payload) if payload and 'application/json' in response.headers.get('Content-Type', '') else payload
            return response.status, result, response.headers

    def save(self, data=None, *, expected=0, token='alice'):
        data = entry() if data is None else data
        return self.request('/api/business/' + data['id'], method='PUT', token=token,
                            data={'expected_revision': expected, 'entry': data})

    def test_all_operations_require_an_active_individual_login(self):
        data = entry()
        self.assertEqual(self.request('/api/business', token=None)[0], 401)
        self.assertEqual(self.save(data, token=None)[0], 401)
        self.assertEqual(self.request('/api/business/' + data['id'], method='DELETE',
                                      data={'expected_revision': 0}, token=None)[0], 401)
        account = self.app.state.store.register('business_expired', 'correct horse battery staple')
        self.now[0] += 28801
        self.assertEqual(self.request('/api/business', token=account['token'])[0], 401)

    def test_coworkers_see_and_update_the_same_order(self):
        data = entry()
        status, saved, _ = self.save(data)
        self.assertEqual(status, 200, saved)
        self.assertEqual((saved['revision'], saved['deleted'], saved['updated_by_username']),
                         (1, False, 'business_alice'))
        other = self.request('/api/business', token='bob')[1]
        self.assertEqual(other['entries'], [saved])
        updated = copy.deepcopy(saved['entry'])
        updated['order_status'] = 'ready'
        updated['checklist'][0]['stock_status'] = 'picked'
        updated['payment_received_cents'] = 24690
        status, saved_by_bob, _ = self.save(updated, expected=1, token='bob')
        self.assertEqual(status, 200, saved_by_bob)
        self.assertEqual(saved_by_bob['revision'], 2)
        self.assertEqual(saved_by_bob['updated_by_username'], 'business_bob')
        delta = self.request('/api/business?after_revision=1')[1]
        self.assertEqual(delta['entries'], [saved_by_bob])
        self.assertEqual(saved_by_bob['entry']['payment_received_cents'], 24690)

    def test_stale_saves_and_deletes_cannot_overwrite_another_coworker(self):
        data = entry()
        saved = self.save(data)[1]
        updated = {**saved['entry'], 'customer': 'Updated by Bob'}
        self.assertEqual(self.save(updated, expected=1, token='bob')[0], 200)
        self.assertEqual(self.save({**saved['entry'], 'customer': 'Stale Alice'}, expected=1)[0], 409)
        self.assertEqual(self.request('/api/business/' + data['id'], method='DELETE',
                                      data={'expected_revision': 1})[0], 409)
        current = self.request('/api/business')[1]['entries'][0]
        self.assertEqual(current['entry']['customer'], 'Updated by Bob')
        self.assertEqual(current['revision'], 2)

    def test_server_keeps_original_creation_and_only_moves_written_date_for_text(self):
        saved = self.save(entry(created=1_700_000_000, updated=1_700_000_000, written=1_700_000_000))[1]
        self.assertEqual(saved['entry']['updated'], 1_800_000_000)
        self.now[0] += 10
        updated = {**saved['entry'], 'created': 1_600_000_000, 'written': 1_600_000_000,
                   'payment_received_cents': 24690}
        current = self.save(updated, expected=saved['revision'], token='bob')[1]
        self.assertEqual(current['entry']['created'], 1_700_000_000)
        self.assertEqual(current['entry']['written'], 1_700_000_000)
        self.assertEqual(current['entry']['updated'], 1_800_000_010)
        self.now[0] += 10
        edited = self.save({**current['entry'], 'body': 'Changed handover instructions.'},
                           expected=current['revision'])[1]
        self.assertEqual(edited['entry']['written'], 1_800_000_020)
        self.assertEqual(edited['entry']['created'], 1_700_000_000)

    def test_concurrent_writers_get_one_success_and_one_conflict(self):
        data = entry()
        saved = self.save(data)[1]
        alice = {**saved['entry'], 'customer': 'Alice changed this'}
        bob = {**saved['entry'], 'customer': 'Bob changed this'}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.save, alice, expected=1, token='alice'),
                       pool.submit(self.save, bob, expected=1, token='bob')]
            results = [future.result() for future in futures]
        self.assertEqual(sorted(result[0] for result in results), [200, 409])
        self.assertEqual(self.request('/api/business')[1]['entries'][0]['revision'], 2)

    def test_delete_tombstones_reach_other_clients_and_cannot_resurrect(self):
        data = entry()
        self.save(data)
        status, deleted, _ = self.request('/api/business/' + data['id'], method='DELETE',
                                          data={'expected_revision': 1}, token='bob')
        self.assertEqual(status, 200, deleted)
        self.assertTrue(deleted['deleted'])
        self.assertIsNone(deleted['entry'])
        self.assertEqual(deleted['revision'], 2)
        self.assertEqual(self.request('/api/business?after_revision=1')[1]['entries'], [deleted])
        self.assertEqual(self.save(data, expected=2)[0], 409)
        self.assertEqual(self.save(data)[0], 409)
        self.assertEqual(self.request('/api/business/' + data['id'], method='DELETE',
                                      data={'expected_revision': 2})[0], 409)

    def test_delta_pages_are_monotonic_and_keep_latest_edits(self):
        saved = [self.save()[1] for _ in range(5)]
        first = self.request('/api/business?limit=3')[1]
        self.assertEqual([row['revision'] for row in first['entries']], [1, 2, 3])
        self.assertEqual(first['cursor'], 3)
        self.assertTrue(first['has_more'])
        second = self.request('/api/business?after_revision=3&limit=3')[1]
        self.assertEqual([row['revision'] for row in second['entries']], [4, 5])
        self.assertFalse(second['has_more'])
        self.assertEqual(self.request('/api/business?after_revision=5')[1],
                         {'entries': [], 'cursor': 5, 'has_more': False})
        changed = self.save({**saved[0]['entry'], 'order_status': 'ready'}, expected=1)[1]
        self.assertEqual(changed['revision'], 6)
        self.assertEqual(self.request('/api/business?after_revision=5')[1]['entries'], [changed])

    def test_shared_checklists_are_distinct_from_orders_and_personal_writing(self):
        data = entry(kind='list', title='Opening', checklist=[{'id': 'one', 'text': 'Check pending deliveries'}])
        saved = self.save(data)[1]
        self.assertEqual(saved['entry']['kind'], 'list')
        self.assertEqual(self.request('/api/business', token='bob')[1]['entries'][0]['entry']['title'], 'Opening')
        self.assertEqual(self.save(entry(kind='note'))[0], 422)

    def test_capacity_can_be_released_by_deleting_older_shared_entries(self):
        self.app.state.store.max_business_entries = 2
        first = self.save()[1]
        self.assertEqual(self.save()[0], 200)
        self.assertEqual(self.save()[0], 409)
        self.assertEqual(self.request('/api/business/' + first['id'], method='DELETE',
                                      data={'expected_revision': first['revision']})[0], 200)
        self.assertEqual(self.save()[0], 200)

    def test_records_persist_across_a_store_restart_including_tombstones(self):
        first, second = self.save()[1], self.save()[1]
        self.request('/api/business/' + second['id'], method='DELETE', data={'expected_revision': 2})
        reopened = ChatStore(self.database, clock=lambda: self.now[0])
        records = reopened.business_entries()['entries']
        self.assertEqual(records[0], first)
        self.assertTrue(records[1]['deleted'])
        third = reopened.save_business_entry(self.bob['user']['id'], (data := entry())['id'], 0, data)
        self.assertEqual(third['revision'], 4)

    def test_owning_fields_dates_and_price_validation_are_strict(self):
        invalid = [entry(document_id=''), entry(extra=True), entry(payment_received_cents=True),
                   entry(payment_received_cents=-1), entry(currency='USD'), entry(body='x' * 10001),
                   entry(created=100), entry(created=float('inf'))]
        for data in invalid:
            self.assertEqual(self.save(data)[0], 422, str(data)[:100])
        data = entry()
        for revision in (True, -1, '0', 1.5):
            self.assertEqual(self.request('/api/business/' + data['id'], method='PUT',
                data={'expected_revision': revision, 'entry': data})[0], 422)
        self.assertEqual(self.request('/api/business/not-a-uuid', method='PUT',
            data={'expected_revision': 0, 'entry': data})[0], 422)
        for query in ('limit=4', 'limit=0', 'after_revision=-1', 'after_revision=9223372036854775807'):
            self.assertEqual(self.request('/api/business?' + query)[0], 422)

    def test_large_unicode_entries_fit_three_record_response_and_chat_keeps_small_cap(self):
        rows = [{'id': str(index), 'text': '🚗' * 200, 'part_number': '🚗' * 80,
                 'supplier': '🚗' * 100, 'bin_location': '🚗' * 60,
                 'quantity': 9999, 'unit_price_cents': 1_000_000_000, 'stock_status': 'ordered'}
                for index in range(100)]
        for _ in range(3):
            status, saved, _ = self.save(entry(body='🚗' * 10000, checklist=rows))
            self.assertEqual(status, 200, str(saved)[:100])
        response = self.request('/api/business')[1]
        self.assertEqual(len(response['entries']), 3)
        self.assertLess(len(json.dumps(response, ensure_ascii=False).encode('utf-8')), 1024 * 1024)
        self.assertEqual(self.request('/api/messages', method='POST', raw=b'x' * 16385)[0], 413)
        # Early rejection deliberately leaves oversized bodies unread. Windows
        # may reset a socket whose client is still writing that body, so test the
        # declared-length path without a large in-flight write; the streaming
        # guard test below independently checks actual oversized body bytes.
        self.assertEqual(self.request('/api/business/' + uuid.uuid4().hex, method='PUT', raw=b'x',
                                      headers={'Content-Length': '262145'})[0], 413)
        self.assertEqual(self.request('/api/business-elsewhere', method='PUT', raw=b'x' * 16385)[0], 413)

    def test_shared_mutations_are_rate_limited_and_cross_origin_writes_rejected(self):
        self.app.state.business_limiter.maximum = 1
        self.assertEqual(self.save()[0], 200)
        self.assertEqual(self.save()[0], 429)
        self.assertEqual(self.request('/api/business')[0], 200)
        data = entry()
        self.assertEqual(self.request('/api/business/' + data['id'], method='PUT', token='bob',
            data={'expected_revision': 0, 'entry': data}, headers={'Origin': 'https://another-site.example'})[0], 403)

    def test_server_imports_without_desktop_qt_dependency(self):
        result = subprocess.run([sys.executable, '-c',
            "import sys; import work_server.app; assert not any(n.startswith('PySide6') for n in sys.modules)"],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(SERVER_AVAILABLE, 'Optional backend dependencies: install requirements-server.txt')
class BusinessStreamingGuardTests(unittest.TestCase):
    def test_business_stream_limit_is_enforced_without_content_length(self):
        async def exercise():
            forwarded, output = [], []

            async def downstream(scope, receive, send):
                forwarded.append(scope)

            guard = RequestGuard(downstream)
            chunks = iter([{'type': 'http.request', 'body': b'x' * 200000, 'more_body': True},
                           {'type': 'http.request', 'body': b'x' * 70000, 'more_body': False}])

            async def receive():
                return next(chunks)

            async def send(message):
                output.append(message)

            await guard({'type': 'http', 'method': 'PUT', 'scheme': 'http', 'path': '/api/business/' + uuid.uuid4().hex,
                         'client': ('127.0.0.1', 1234), 'headers': []}, receive, send)
            self.assertFalse(forwarded)
            self.assertEqual(output[0]['status'], 413)
        asyncio.run(exercise())


if __name__ == '__main__':
    unittest.main()
