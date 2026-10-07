export type Kind = 'note' | 'list' | 'order';
export const orderStatuses = ['new', 'preparing', 'ready', 'delivered', 'cancelled'] as const;
export type OrderStatus = typeof orderStatuses[number];
export type Item = { id: string; text: string; quantity: number; done: boolean };
export type Advice = { reminder: string; next_step: string; suggested_due: number | null; reason: string; signature: string };
export type Note = {
  id: string; title: string; body: string; kind: Kind; created: number; updated: number; written: number;
  next_due: number | null; repeat_minutes: number; done: boolean; pinned: boolean; pin_position: null;
  unannounced: boolean; source: string; customer: string; contact: string; order_ref: string;
  order_status: OrderStatus; order_due: number | null; checklist: Item[]; reminder_history: string[]; advice?: Advice;
};
export type Preferences = { businessName: string; model: string; ai: boolean; notifications: boolean; character: 'robot' | 'cat' | 'knight'; quiet: boolean };
export type Book = { version: 2; notes: Note[]; preferences: Preferences; phrases: string[] };
export const defaults: Preferences = { businessName: '', model: 'openai/gpt-oss-20b', ai: true, notifications: false, character: 'robot', quiet: false };
export const freshBook = (): Book => ({ version: 2, notes: [], preferences: { ...defaults }, phrases: [] });
export const identifier = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
const clean = (v: unknown, n: number) => typeof v === 'string' ? v.trim().slice(0, n) : '';
const stamp = (v: unknown): number | null => typeof v === 'number' && Number.isFinite(v) && v > 0 && v < 253402300000 ? v : null;
const integer = (v: unknown, low: number, high: number, fallback: number) => typeof v === 'number' && Number.isInteger(v) ? Math.max(low, Math.min(high, v)) : fallback;
export function normalizeNote(raw: unknown): Note | null {
  if (!raw || typeof raw !== 'object') return null;
  const n = raw as Record<string, unknown>;
  const id = clean(n.id, 64), body = clean(n.body, 10000), title = clean(n.title, 100) || body.split('\n')[0].slice(0, 80);
  if (!id || (!title && !body)) return null;
  const kind: Kind = n.kind === 'order' || n.kind === 'list' ? n.kind : 'note';
  let status: OrderStatus = orderStatuses.includes(n.order_status as OrderStatus) ? n.order_status as OrderStatus : 'new';
  const done = n.done === true || (kind === 'order' && ['delivered', 'cancelled'].includes(status));
  if (done && kind === 'order' && !['delivered', 'cancelled'].includes(status)) status = 'delivered';
  const created = stamp(n.created) || Date.now() / 1000, updated = stamp(n.updated) || created;
  const seen = new Set<string>();
  const checklist = (Array.isArray(n.checklist) ? n.checklist : []).slice(0, 100).flatMap(rawItem => {
    if (!rawItem || typeof rawItem !== 'object') return [];
    const text = clean(rawItem.text, 200), itemId = clean(rawItem.id, 64) || identifier();
    if (!text || seen.has(itemId)) return [];
    seen.add(itemId);
    return [{ id: itemId, text, quantity: integer(rawItem.quantity, 1, 9999, 1), done: rawItem.done === true }];
  });
  const note: Note = { id, title, body, kind, created, updated, written: stamp(n.written) || updated,
    next_due: done ? null : stamp(n.next_due), repeat_minutes: integer(n.repeat_minutes, 0, 1440, 30),
    done, pinned: false, pin_position: null, unannounced: false, source: '', customer: clean(n.customer, 100),
    contact: clean(n.contact, 100), order_ref: clean(n.order_ref, 80), order_status: status, order_due: stamp(n.order_due), checklist,
    reminder_history: (Array.isArray(n.reminder_history) ? n.reminder_history : []).slice(-5).map(v => clean(v, 280)).filter(Boolean) };
  const advice = n.advice as Advice | undefined;
  if (advice && typeof advice.reminder === 'string' && advice.signature === signature(note)) {
    note.advice = { reminder: clean(advice.reminder, 280), next_step: clean(advice.next_step, 180),
      suggested_due: stamp(advice.suggested_due), reason: clean(advice.reason, 180), signature: advice.signature };
  }
  return note;
}
export function newNote(kind: Kind = 'note'): Note {
  const now = Date.now() / 1000;
  return normalizeNote({ id: identifier(), title: kind === 'order' ? 'New order' : kind === 'list' ? 'Checklist' : 'New note',
    kind, created: now, updated: now, written: now, repeat_minutes: 0 })!;
}
export function signature(n: Note): string {
  return JSON.stringify([n.title, n.body, n.kind, n.customer, n.contact, n.order_ref, n.order_status, n.order_due, n.checklist]);
}
export function saveNote(book: Book, draft: Note, now = Date.now() / 1000): Book {
  if (!draft.title.trim() && !draft.body.trim()) throw new Error('Give your note or order a title.');
  if (draft.kind !== 'note' && !draft.checklist.length && !draft.body.trim()) throw new Error('Add an item or write the order details.');
  const old = book.notes.find(n => n.id === draft.id);
  const next = normalizeNote({ ...draft, updated: now,
    written: old && old.title === draft.title.trim() && old.body === draft.body.trim() ? old.written : now })!;
  if (!old && book.notes.length >= 2000) throw new Error('The notebook is full. Export and remove older entries first.');
  return { ...book, notes: old ? book.notes.map(n => n.id === next.id ? next : n) : [next, ...book.notes] };
}
export function changeNote(book: Book, id: string, changes: Partial<Note>, now = Date.now() / 1000): Book {
  return { ...book, notes: book.notes.map(n => n.id === id ? normalizeNote({ ...n, ...changes, updated: now })! : n) };
}
export function complete(book: Book, id: string, done = true): Book {
  const note = book.notes.find(n => n.id === id);
  if (!note) return book;
  return changeNote(book, id, { done, next_due: done ? null : note.repeat_minutes ? Date.now() / 1000 + note.repeat_minutes * 60 : null,
    order_status: note.kind === 'order' ? done ? 'delivered' : 'new' : note.order_status });
}
export function summary(notes: Note[], now = Date.now() / 1000) {
  const active = notes.filter(n => !n.done), orders = active.filter(n => n.kind === 'order');
  return { tasks: active.length, orders: orders.length, ready: orders.filter(n => n.order_status === 'ready').length,
    late: orders.filter(n => n.order_due && n.order_due < now).length, unchecked: active.reduce((a, n) => a + n.checklist.filter(i => !i.done).length, 0) };
}
export function matchNote(n: Note, query: string): boolean {
  return [n.title, n.body, n.customer, n.contact, n.order_ref, ...n.checklist.map(i => i.text)].join(' ').toLowerCase().includes(query.trim().toLowerCase());
}
export function localReminder(n: Note, index = 0): string {
  const pending = n.checklist.filter(i => !i.done), label = n.order_ref || n.title;
  const task = pending[0] ? `${pending[0].quantity} × ${pending[0].text}` : n.body.slice(0, 100) || label;
  const customer = n.customer ? ` for ${n.customer}` : '';
  const choices = n.kind === 'order' && n.order_status === 'ready'
    ? [`${label}${customer} is marked ready. Check the handover details.`, `Your ready list includes ${label}${customer}. Review the pickup time.`, `Ready for handover: ${label}${customer}.`]
    : [`A quick check on ${label}${customer}: ${task}.`, `Next on your list: ${task}.`, `Keep ${label}${customer} moving. Check what remains.`, `A little nudge for ${label}${customer}. Is ${task} sorted?`];
  const fresh = choices.filter(s => !n.reminder_history.includes(s));
  const available = fresh.length ? fresh : choices;
  return available[index % available.length].slice(0, 280);
}
export function mergeBackup(book: Book, payload: unknown): Book {
  if (!payload || typeof payload !== 'object' || !Array.isArray((payload as { notes?: unknown }).notes)) throw new Error('Choose a Jeffery notebook JSON backup.');
  const rows = (payload as { notes: unknown[] }).notes;
  if (rows.length > 2000) throw new Error('A backup can contain at most 2,000 entries.');
  const result = new Map(book.notes.map(n => [n.id, n]));
  for (const row of rows) {
    const n = normalizeNote(row);
    if (!n) throw new Error('The backup has an invalid entry. Nothing was imported.');
    const old = result.get(n.id);
    if (!old || n.updated > old.updated) result.set(n.id, n);
  }
  if (result.size > 2000) throw new Error('The merged notebook exceeds 2,000 entries.');
  return { ...book, notes: [...result.values()] };
}
export const timeLabel = (stamp: number | null) => stamp ? new Date(stamp * 1000).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : 'No reminder time';
