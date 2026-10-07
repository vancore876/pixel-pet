import assert from 'node:assert/strict';
import { test } from 'node:test';
import { changeNote, complete, freshBook, localReminder, matchNote, mergeBackup, newNote, normalizeNote, saveNote, signature, summary } from '../src/model';
import { context, parseAdvice, reminderMessages, requestGroq } from '../src/groq';
import { planReminders } from '../src/reminder-plan';

const order = () => ({ ...newNote('order'), title: 'Pack customer order', customer: 'Customer A', order_ref: 'ORD-001', body: 'Pickup on October 9 at 9 AM',
  checklist: [{ id: 'item1', text: 'Brake pads', quantity: 2, done: false }, { id: 'item2', text: 'Air filter', quantity: 1, done: true }] });
test('order fields and checked items survive a desktop-format backup', () => {
  const n = order(), imported = mergeBackup(freshBook(), { version: 2, notes: [n], linked_file: 'C:/Private/Notes.txt' });
  assert.equal(imported.notes[0].customer, n.customer);
  assert.equal(imported.notes[0].checklist[1].done, true);
  assert.equal(imported.notes[0].source, '');
  assert.equal(imported.notes[0].checklist[0].quantity, 2);
});
test('terminal order statuses stop reminders, restore reopens them', () => {
  const n = { ...order(), next_due: 2000 }, book = saveNote(freshBook(), n);
  const delivered = changeNote(book, n.id, { order_status: 'delivered' });
  assert.equal(delivered.notes[0].done, true);
  assert.equal(delivered.notes[0].next_due, null);
  assert.equal(complete(delivered, n.id, false).notes[0].order_status, 'new');
  assert.equal(summary(delivered.notes).orders, 0);
});
test('quantities, duplicates, invalid fields and checklist size are bounded', () => {
  const n = normalizeNote({ ...order(), checklist: [{ id: 'same', text: 'Item', quantity: 50000 }, { id: 'same', text: 'Duplicate' }], order_status: 'unknown' })!;
  assert.equal(n.checklist.length, 1); assert.equal(n.checklist[0].quantity, 9999); assert.equal(n.order_status, 'new');
  assert.equal(normalizeNote({ id: 'x', title: '' }), null);
});
test('save and snooze preserve relative-date anchoring', () => {
  let book = saveNote(freshBook(), order(), 100);
  const n = book.notes[0];
  book = changeNote(book, n.id, { next_due: 1000 }, 200);
  assert.equal(book.notes[0].written, 100);
  book = saveNote(book, { ...book.notes[0], checklist: book.notes[0].checklist.map(i => ({ ...i, done: true })) }, 300);
  assert.equal(book.notes[0].written, 100);
  assert.equal(book.notes[0].updated, 300);
});
test('order deadline stays separate from the next notification', () => {
  const n = { ...order(), order_due: 500, next_due: 1500 };
  const book = changeNote(saveNote(freshBook(), n), n.id, { next_due: 3000 });
  assert.equal(book.notes[0].order_due, 500);
  assert.equal(summary(book.notes, 1000).late, 1);
});
test('search finds customer, order number and checklist text', () => {
  const n = order(); assert.ok(matchNote(n, 'Customer A')); assert.ok(matchNote(n, 'ord-001')); assert.ok(matchNote(n, 'brake'));
});
test('merge keeps newer entries and rejects bad backups atomically', () => {
  const n = { ...order(), updated: 500 }, original = mergeBackup(freshBook(), { notes: [n] });
  assert.equal(mergeBackup(original, { notes: [{ ...n, customer: 'Older', updated: 400 }] }).notes[0].customer, n.customer);
  assert.throws(() => mergeBackup(original, { notes: [{ id: 'invalid' }] }));
  assert.equal(original.notes[0].customer, n.customer);
});
test('Groq reminder prompt knows remaining work and original written date', () => {
  const n = order(), prompt = JSON.parse(reminderMessages(n)[1].content);
  assert.equal(prompt.checklist[0].done, false); assert.equal(prompt.checklist[1].done, true);
  assert.ok(prompt.note_written); assert.ok(Array.isArray(prompt.recent_reminders));
});
test('advice rejects invalid schemas and ambiguous or past time guesses', () => {
  const n = order();
  assert.throws(() => parseAdvice('{"reminder":"Do this","action":"delete"}', n));
  const fields = { reminder: 'Pack the remaining pads.', next_step: 'Check the pads.', reason: '', suggested_due: 'tomorrow' };
  assert.equal(parseAdvice(JSON.stringify(fields), n).suggested_due, null);
  assert.equal(parseAdvice(JSON.stringify({ ...fields, suggested_due: '2020-01-01T10:00:00Z' }), n).suggested_due, null);
});
test('checked-item edits invalidate advice and repeated reminders vary', () => {
  const n = order();
  n.reminder_history = [localReminder(n)]; assert.notEqual(localReminder(n), n.reminder_history[0]);
  const result = parseAdvice(JSON.stringify({ reminder: n.reminder_history[0], next_step: 'Check remaining pads', suggested_due: null, reason: '' }), n);
  assert.notEqual(result.reminder, n.reminder_history[0]);
  const changed = { ...n, checklist: n.checklist.map(i => ({ ...i, done: true })) };
  assert.notEqual(signature(n), signature(changed));
});
test('scheduled mobile reminders vary and respect capacity and quiet mode', () => {
  const book = freshBook(); book.preferences.notifications = true;
  book.notes = [{ ...order(), next_due: 100, repeat_minutes: 15 }];
  const planned = planReminders(book, 200000);
  assert.equal(planned.length, 4); assert.ok(planned.every(p => p.at > 200000));
  assert.ok(new Set(planned.map(p => p.body)).size > 1);
  assert.equal(new Set(planned.map(p => p.id)).size, 4);
  book.notes = Array.from({ length: 100 }, (_, i) => ({ ...order(), id: 'order' + i, next_due: 1000, repeat_minutes: 15 }));
  assert.equal(planReminders(book, 0).length, 48);
  book.preferences.quiet = true; assert.deepEqual(planReminders(book), []);
});
test('completed orders never enter the notification plan', () => {
  const book = freshBook(); book.preferences.notifications = true;
  book.notes = [{ ...order(), done: true, next_due: 100 }]; assert.deepEqual(planReminders(book), []);
});
test('AI sharing off excludes saved note details', () => {
  const book = freshBook(); book.notes = [order()]; book.preferences.ai = false;
  assert.deepEqual(context(book).incomplete_notes, []);
});
test('Groq transport sends JSON-mode requests and handles rejected keys', async () => {
  const original = globalThis.fetch;
  try {
    let sent: Record<string, unknown> = {};
    globalThis.fetch = async (_url, options) => { sent = JSON.parse(options!.body as string); return new Response(JSON.stringify({ choices: [{ message: { content: 'Fresh greeting' } }] }), { status: 200 }); };
    const key = 'gsk_' + 'testplaceholder'.repeat(3);
    assert.equal(await requestGroq(key, 'openai/gpt-oss-20b', [{ role: 'user', content: 'hello' }], true), 'Fresh greeting');
    assert.deepEqual(sent.response_format, { type: 'json_object' }); assert.equal(sent.tools, undefined);
    globalThis.fetch = async () => new Response('{}', { status: 401 });
    await assert.rejects(requestGroq(key, 'openai/gpt-oss-20b', [{ role: 'user', content: 'hello' }]), /rejected the key/);
  } finally { globalThis.fetch = original; }
});
