"""Two actual Qt clients against a disposable office server. No real user data."""
from pathlib import Path
import unittest

import check_work_chat_integration as harness
from check_work_chat_integration import APP, wait_for
from notes import NoteStore, NoteService
from notepad_window import NotepadWindow
from settings import AppSettings
from shared_business import SharedBusinessController
from auto_parts import order_totals


class SharedOfficeTests(harness.RealServerTests):
    def setUp(self):
        super().setUp()
        self.profiles = []

    def tearDown(self):
        for controller, service, window in self.profiles:
            window.dirty = False
            controller.shutdown()
            window.shutdown()
            service.stop()
            service.store.close()
            window.deleteLater()
            controller.deleteLater()
        super().tearDown()

    def coworker(self, name):
        chat = self.account(name)
        root = Path(self.temp.name) / name
        root.mkdir()
        settings = AppSettings(root / 'settings.json')
        service = NoteService(NoteStore(root / 'notes.json'), settings)
        window = NotepadWindow(service, settings)
        controller = SharedBusinessController(service)
        window.shared_business = controller
        controller.status_changed.connect(window.set_shared_status)
        chat.session_changed.connect(controller.set_session)
        controller.session_expired.connect(chat._clear_session)
        self.profiles.append((controller, service, window))
        controller.set_session(chat.api.base_url, chat.token, chat.user)
        wait_for(lambda: controller.ready)
        return chat, controller, service, window

    def saved(self, controller, identifier=None, **changes):
        results = []
        details = {'kind': 'order', 'customer': 'Famous Twins customer', 'vehicle': 'Corolla 2015',
            'order_ref': 'QA-FT-101', 'currency': 'JMD', 'payment_received_cents': 500000,
            'checklist': [{'id': 'pads', 'text': 'Brake pads', 'quantity': 2, 'done': False,
                           'unit_price_cents': 425025, 'part_number': 'BP-42', 'supplier': 'Local supplier',
                           'bin_location': 'A-03', 'stock_status': 'to_order'}], **changes}
        self.assertTrue(controller.save(identifier, 'Brake order', 'Confirm fitment before handover',
            0, None, details, lambda note, error: results.append((note, error))))
        wait_for(lambda: bool(results))
        self.assertFalse(results[0][1], results[0][1])
        return results[0][0]

    def sync(self, controller, predicate):
        wait_for(lambda: not controller._polling and not controller._writing)
        controller.refresh()
        wait_for(predicate)

    def test_shared_orders_checklists_conflict_delete_and_personal_writing(self):
        alice, ac, a, aw = self.coworker('parts_alice')
        bob, bc, b, bw = self.coworker('parts_bob')
        private = a.store.add('Personal writing', 'Only on Alice profile', repeat=0)
        order = self.saved(ac)
        identifier = order['id']
        self.sync(bc, lambda: b.store.find(identifier) is not None)
        received = b.store.find(identifier)
        self.assertEqual(received['checklist'][0]['part_number'], 'BP-42')
        self.assertEqual(order_totals(received)['balance_cents'], 350050)
        self.assertIsNone(b.store.find(private['id']))

        aw.load_note(identifier)
        aw.order_notes.setPlainText('Alice unsaved draft must survive Bob editing')
        self.assertTrue(aw.dirty)
        bc.mark_editing(identifier)
        changed = []
        bc.modify(identifier, {'order_status': 'ready'}, lambda note, error: changed.append((note, error)))
        wait_for(lambda: bool(changed))
        self.assertFalse(changed[0][1])
        self.sync(ac, lambda: a.store.find(identifier)['order_status'] == 'ready')
        self.assertTrue(aw.dirty)
        self.assertIn('Alice unsaved', aw.body.toPlainText())
        self.assertTrue(aw.save_note())
        wait_for(lambda: not aw.business_pending)
        self.assertTrue(aw.dirty)
        self.assertIn('Another coworker changed', aw.status.text())
        self.assertEqual(a.store.find(identifier)['body'], 'Confirm fitment before handover')
        aw.dirty = False
        aw.load_note(identifier)
        aw.order_notes.setPlainText('Alice reviewed the ready order')
        self.assertTrue(aw.save_note())
        wait_for(lambda: not aw.business_pending)
        self.assertFalse(aw.dirty, aw.status.text())
        self.sync(bc, lambda: b.store.find(identifier)['body'] == 'Alice reviewed the ready order')

        ac.mark_editing(None)
        checklist = self.saved(ac, kind='list', checklist=[{'id': 'opening', 'text': 'Check counter',
            'quantity': 1, 'done': False}], customer='', vehicle='', payment_received_cents=0)
        self.sync(bc, lambda: b.store.find(checklist['id']) is not None)
        self.assertEqual(b.store.find(checklist['id'])['kind'], 'list')
        bc.mark_editing(checklist['id'])
        completed = []
        bc.complete(checklist['id'], True, lambda n, e: completed.append((n, e)))
        wait_for(lambda: bool(completed))
        self.assertFalse(completed[0][1])
        self.sync(ac, lambda: a.store.find(checklist['id'])['done'])

        bc.mark_editing(identifier)
        deleted = []
        bc.delete(identifier, lambda n, e: deleted.append((n, e)))
        wait_for(lambda: bool(deleted))
        self.assertFalse(deleted[0][1])
        self.sync(ac, lambda: a.store.find(identifier) is None)
        alice.sign_out()
        wait_for(lambda: not ac.token)
        self.assertIsNone(a.store.find(checklist['id']))
        self.assertEqual(a.store.find(private['id'])['body'], 'Only on Alice profile')


if __name__ == '__main__':
    unittest.main(verbosity=2)
