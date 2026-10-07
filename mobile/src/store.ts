import { useEffect, useRef, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Book, defaults, freshBook, mergeBackup } from './model';
const storageKey = 'jeffery-business-book-v2';
export function useBook() {
  const [book, setBook] = useState<Book>(freshBook);
  const current = useRef(book);
  const [ready, setReady] = useState(false), [error, setError] = useState('');
  const closed = useRef(false), blocked = useRef(false);
  const writes = useRef<Promise<void>>(Promise.resolve());
  useEffect(() => {
    closed.current = false;
    void AsyncStorage.getItem(storageKey).then(text => {
      let next = freshBook();
      if (text) {
        if (text.length > 8 * 1024 * 1024) throw new Error('The saved notebook is too large. Its original data was preserved.');
        const raw = JSON.parse(text);
        next = mergeBackup(next, raw);
        const p = raw.preferences || {};
        next.preferences = { ...defaults, businessName: typeof p.businessName === 'string' ? p.businessName.slice(0, 80) : '',
          model: typeof p.model === 'string' && /^[\w/.-]{1,100}$/.test(p.model) ? p.model : defaults.model,
          ai: typeof p.ai === 'boolean' ? p.ai : true, notifications: p.notifications === true, quiet: p.quiet === true,
          character: ['robot', 'cat', 'knight'].includes(p.character) ? p.character : 'robot' };
        next.phrases = Array.isArray(raw.phrases) ? raw.phrases.filter((s: unknown) => typeof s === 'string').slice(-12).map((s: string) => s.slice(0, 500)) : [];
      }
      if (!closed.current) { current.current = next; setBook(next); }
    }).catch(() => {
      blocked.current = true;
      setError('Your saved notebook could not be read. It has been preserved; restart before editing.');
    }).finally(() => { if (!closed.current) setReady(true); });
    return () => { closed.current = true; };
  }, []);
  function transact(change: (previous: Book) => Book): Book {
    if (!ready || blocked.current) throw new Error('The notebook is not ready. Your existing data has been preserved.');
    const next = change(current.current);
    current.current = next;
    setBook(next);
    writes.current = writes.current.then(() => AsyncStorage.setItem(storageKey, JSON.stringify(next)))
      .catch(() => { setError('Could not save your notebook. Keep the app open and export a backup.'); });
    return next;
  }
  return { book, current, ready, error, transact };
}
