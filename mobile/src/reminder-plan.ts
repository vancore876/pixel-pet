import { Book, localReminder } from './model';
export type Planned = { id: string; noteId: string; dueTag: string; at: number; title: string; body: string };
export function planReminders(book: Book, now = Date.now()): Planned[] {
  if (!book.preferences.notifications || book.preferences.quiet) return [];
  const planned: Planned[] = [];
  for (const n of book.notes) {
    if (n.done || !n.next_due) continue;
    const interval = n.repeat_minutes * 60000, first = n.next_due * 1000;
    const offset = interval ? Math.max(0, Math.floor((now - first) / interval) + 1) : 0;
    for (let i = 0; i < (interval ? 4 : 1); i++) {
      const at = interval ? first + (offset + i) * interval : first;
      const text = i === 0 && n.advice && book.preferences.ai ? n.advice.reminder : localReminder(n, i);
      planned.push({ id: `jeffery:${n.id}:${interval ? Math.round(at) : 'once'}`, noteId: n.id,
        dueTag: `${n.next_due}:${n.repeat_minutes}`, at, title: n.order_ref || n.title, body: text });
    }
  }
  return planned.sort((a, b) => a.at - b.at).slice(0, 48);
}
