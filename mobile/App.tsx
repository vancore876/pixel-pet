import React, { useEffect, useRef, useState } from 'react';
import { Alert, AppState, Modal, Platform, Pressable, ScrollView, StyleSheet, Switch, Text, TextInput, View } from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import * as SecureStore from 'expo-secure-store';
import * as Notifications from 'expo-notifications';
import * as DocumentPicker from 'expo-document-picker';
import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';
import DateTimePicker from '@react-native-community/datetimepicker';
import { Advice, Book, Kind, Note, changeNote, complete, identifier, localReminder, matchNote, mergeBackup, newNote, orderStatuses, saveNote, signature, summary, timeLabel } from './src/model';
import { localGreeting, Message, parseAdvice, plain, reminderMessages, requestGroq, systemMessage } from './src/groq';
import { useBook } from './src/store';
import { enableNotifications, syncNotifications } from './src/notifications';
import { Pet } from './src/Pet';
import { SlidingReply } from './src/SlidingReply';

type Tab = 'Today' | 'Orders' | 'Notes' | 'Chat' | 'Settings';
const keyName = 'jeffery-groq-key';
const errorText = (error: unknown) => plain(error instanceof Error ? error.message : 'That action could not finish. Try again.');
const dateInput = (stamp: number | null) => {
  if (!stamp) return '';
  const d = new Date(stamp * 1000), pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
function Button({ title, onPress, primary = false, disabled = false }: { title: string; onPress: () => void; primary?: boolean; disabled?: boolean }) {
  return <Pressable accessibilityRole="button" disabled={disabled} onPress={onPress} style={[s.button, primary && s.primary, disabled && { opacity: .45 }]}><Text style={[s.buttonText, primary && { color: '#10251e' }]}>{title}</Text></Pressable>;
}
function Field({ label, value, onChange, multiline = false, placeholder = '', secret = false }: { label: string; value: string; onChange: (text: string) => void; multiline?: boolean; placeholder?: string; secret?: boolean }) {
  return <View style={s.field}><Text style={s.label}>{label}</Text><TextInput accessibilityLabel={label} style={[s.input, multiline && { minHeight: 90, textAlignVertical: 'top' }]} value={value} onChangeText={onChange} multiline={multiline} placeholder={placeholder} placeholderTextColor="#74889d" secureTextEntry={secret} autoCapitalize={secret ? 'none' : 'sentences'} autoCorrect={!secret} maxLength={multiline ? 10000 : 256} /></View>;
}
function DateField({ label, value, onChange }: { label: string; value: number | null; onChange: (stamp: number | null) => void }) {
  const [draft, setDraft] = useState(dateInput(value)), [invalid, setInvalid] = useState(false);
  useEffect(() => { setDraft(dateInput(value)); setInvalid(false); }, [value]);
  function apply() {
    if (!draft.trim()) { onChange(null); setInvalid(false); return; }
    const stamp = Date.parse(draft.trim().replace(' ', 'T'));
    if (!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(draft.trim()) || !Number.isFinite(stamp) || dateInput(stamp / 1000) !== draft.trim()) { setInvalid(true); return; }
    onChange(stamp / 1000); setInvalid(false);
  }
  return <View style={s.field}><Text style={s.label}>{label}</Text><TextInput accessibilityLabel={label} style={s.input} value={draft} onChangeText={setDraft} onBlur={apply} onSubmitEditing={apply} placeholder="YYYY-MM-DD HH:MM" placeholderTextColor="#74889d" autoCapitalize="none" /><Text style={s.subtitle}>{invalid ? 'Use a valid local date and time: YYYY-MM-DD HH:MM.' : 'Local time. Applied when you leave this field.'}</Text></View>;
}
export default function App() { return <SafeAreaProvider><Main /></SafeAreaProvider>; }
function Main() {
  const { book, current, ready, error, transact } = useBook();
  const [tab, setTab] = useState<Tab>('Today'), [query, setQuery] = useState(''), [filter, setFilter] = useState('Active');
  const [editor, setEditor] = useState<Note | null>(null), [itemText, setItemText] = useState(''), [quantity, setQuantity] = useState('1');
  const originalDraft = useRef('');
  const [picker, setPicker] = useState<{ field: 'next_due' | 'order_due'; mode: 'date' | 'time' } | null>(null);
  const [key, setKey] = useState(''), [keyDraft, setKeyDraft] = useState(''), [keyReady, setKeyReady] = useState(false);
  const [status, setStatus] = useState('Orders and notes are ready offline.'), [greeting, setGreeting] = useState('Hello! Let’s keep the day moving.');
  const [busy, setBusy] = useState(false), [chatInput, setChatInput] = useState('');
  const [chat, setChat] = useState<{ who: 'You' | 'Jeffery'; text: string }[]>([]);
  const activeRequest = useRef<AbortController | null>(null), blocked = useRef(false), nextAI = useRef(0);
  const greetingDone = useRef(false), handledResponses = useRef(new Set<string>());
  const prefs = book.preferences, counts = summary(book.notes);
  const notify = (title: string, text: string) => Platform.OS === 'web' ? setStatus(text) : Alert.alert(title, text);
  function commit(change: (b: Book) => Book) { try { return transact(change); } catch (e) { notify('Notebook', errorText(e)); return null; } }
  function openNote(note: Note) { const copy = JSON.parse(JSON.stringify(note)) as Note; originalDraft.current = JSON.stringify(copy); setEditor(copy); setItemText(''); setQuantity('1'); setPicker(null); }
  function add(kind: Kind) { openNote(newNote(kind)); }
  function closeEditor() {
    if (editor && JSON.stringify(editor) !== originalDraft.current) {
      if (Platform.OS === 'web') { if (!window.confirm('Discard unsaved changes?')) return; }
      else { Alert.alert('Unsaved changes', 'Save before closing, or discard this draft.', [{ text: 'Keep editing', style: 'cancel' }, { text: 'Discard', style: 'destructive', onPress: () => setEditor(null) }]); return; }
    }
    setEditor(null);
  }
  function saveEditor() {
    if (!editor) return;
    const next = commit(b => saveNote(b, editor));
    if (next) { setEditor(null); setStatus('Saved. Jeffery will use these details for reminders.'); }
  }
  function patchDraft(changes: Partial<Note>) { setEditor(n => n ? { ...n, ...changes } : n); }
  function addItem() {
    if (!editor || !itemText.trim()) return;
    if (editor.checklist.length >= 100) { notify('Checklist', 'A checklist supports up to 100 items.'); return; }
    const qty = Number(quantity);
    if (!Number.isInteger(qty) || qty < 1 || qty > 9999) { notify('Quantity', 'Use a quantity from 1 to 9,999.'); return; }
    patchDraft({ checklist: [...editor.checklist, { id: identifier(), text: itemText.trim().slice(0, 200), quantity: qty, done: false }] });
    setItemText(''); setQuantity('1');
  }
  function snooze(id: string, minutes: number) { commit(b => changeNote(b, id, { next_due: Date.now() / 1000 + minutes * 60 })); setStatus(`Reminder moved to ${minutes} minutes from now.`); }
  function remove(note: Note) {
    const run = () => { commit(b => ({ ...b, notes: b.notes.filter(n => n.id !== note.id) })); setEditor(null); };
    if (Platform.OS === 'web') { if (window.confirm(`Delete ${note.title}?`)) run(); }
    else Alert.alert('Delete entry', `Delete ${note.title}?`, [{ text: 'Cancel', style: 'cancel' }, { text: 'Delete', style: 'destructive', onPress: run }]);
  }
  useEffect(() => {
    if (Platform.OS === 'web') setKeyReady(true);
    else void SecureStore.getItemAsync(keyName).then(value => setKey(value || '')).catch(() => setStatus('Save your Groq key again in Settings.')).finally(() => setKeyReady(true));
    return () => { activeRequest.current?.abort(); };
  }, []);
  async function runAI(messages: Message[], json = false) {
    if (activeRequest.current) throw new Error('Jeffery is finishing another thought. Try in a moment.');
    const controller = new AbortController(); activeRequest.current = controller; setBusy(true);
    try { return await requestGroq(key, current.current.preferences.model, messages, json, controller.signal); }
    catch (e) { if (errorText(e).includes('rejected the key')) blocked.current = true; nextAI.current = Date.now() + 120000; throw e; }
    finally { if (activeRequest.current === controller) activeRequest.current = null; setBusy(false); }
  }
  function rememberPhrase(text: string) { commit(b => ({ ...b, phrases: [...b.phrases.slice(-11), text.slice(0, 500)] })); }
  async function greet() {
    if (!ready || current.current.preferences.quiet) return;
    const local = localGreeting(current.current);
    if (!key || !current.current.preferences.ai || activeRequest.current || blocked.current) { setGreeting(local); rememberPhrase(local); return; }
    try {
      setStatus('Jeffery is thinking of a fresh greeting…');
      const text = (await runAI([systemMessage(current.current), { role: 'user', content: 'Greet me in one or two short sentences, and mention the most useful next business step if my notes show one.' }])).slice(0, 280);
      const fresh = current.current.phrases.slice(-6).includes(text) ? local : text;
      setGreeting(fresh); rememberPhrase(fresh); setStatus('Groq connected. Your actual orders guide Jeffery.');
    } catch (e) { setGreeting(local); rememberPhrase(local); setStatus(errorText(e)); }
  }
  async function advice(note: Note, announce = false) {
    if (!key || !current.current.preferences.ai || activeRequest.current || blocked.current || current.current.preferences.quiet) return;
    const before = signature(note);
    try {
      setStatus(`Jeffery is reviewing ${note.order_ref || note.title}…`);
      const result = parseAdvice(await runAI(reminderMessages(note), true), note);
      const latest = current.current.notes.find(n => n.id === note.id);
      if (!latest || latest.done || signature(latest) !== before || !current.current.preferences.ai) return;
      commit(b => ({ ...b, notes: b.notes.map(n => n.id === note.id ? { ...n, advice: result, reminder_history: [...n.reminder_history.slice(-4), result.reminder] } : n) }));
      if (announce && !current.current.preferences.quiet) setGreeting(result.reminder);
      nextAI.current = Date.now() + 5000;
      setStatus('Fresh reminder ready. Suggested times are yours to choose.');
    } catch (e) { setStatus(errorText(e)); }
  }
  useEffect(() => {
    if (ready && keyReady && !greetingDone.current) { greetingDone.current = true; void greet(); }
  }, [ready, keyReady]);
  useEffect(() => {
    if (!ready || !key || busy || !prefs.ai || prefs.quiet || blocked.current) return;
    const note = book.notes.find(n => !n.done && (!n.advice || n.advice.signature !== signature(n)));
    if (note) { const timer = setTimeout(() => { const latest = current.current.notes.find(n => n.id === note.id); if (latest && !latest.done) void advice(latest); }, Math.max(700, nextAI.current - Date.now())); return () => clearTimeout(timer); }
  }, [book, busy, key, ready]);
  useEffect(() => {
    if (ready) void syncNotifications(book).catch(e => setStatus(errorText(e)));
  }, [book, ready]);
  useEffect(() => {
    if (!ready || Platform.OS === 'web') return;
    const handle = (response: Notifications.NotificationResponse) => {
      const token = response.notification.request.identifier + ':' + response.actionIdentifier;
      if (handledResponses.current.has(token)) return;
      handledResponses.current.add(token);
      const id = response.notification.request.content.data?.noteId;
      if (typeof id !== 'string') return;
      const note = current.current.notes.find(n => n.id === id);
      if (!note) return;
      if (response.actionIdentifier === 'DONE') commit(b => complete(b, id));
      else if (response.actionIdentifier === 'SNOOZE_15') snooze(id, 15);
      else { setTab(note.kind === 'order' ? 'Orders' : 'Notes'); openNote(note); }
      void Notifications.clearLastNotificationResponseAsync();
    };
    const response = Notifications.addNotificationResponseReceivedListener(handle);
    const received = Notifications.addNotificationReceivedListener(notification => {
      const note = current.current.notes.find(n => n.id === notification.request.content.data?.noteId);
      if (note && !current.current.preferences.quiet) { setGreeting(notification.request.content.body || localReminder(note)); void advice(note, true); }
    });
    void Notifications.getLastNotificationResponseAsync().then(value => { if (value) handle(value); });
    return () => { response.remove(); received.remove(); };
  }, [ready, key]);
  useEffect(() => {
    if (!ready) return;
    const sub = AppState.addEventListener('change', state => { if (state === 'active') { void syncNotifications(current.current).catch(e => setStatus(errorText(e))); } });
    return () => sub.remove();
  }, [ready]);
  async function sendChat() {
    const text = chatInput.trim(); if (!text || busy) return;
    setChatInput(''); setChat(rows => [...rows, { who: 'You', text }]);
    try {
      const history: Message[] = chat.slice(-12).map(row => ({ role: row.who === 'You' ? 'user' : 'assistant', content: row.text }));
      const reply = await runAI([systemMessage(current.current), ...history, { role: 'user', content: text }]);
      setChat(rows => [...rows, { who: 'Jeffery', text: reply }]); rememberPhrase(reply); setStatus('Ready for your next question.');
    } catch (e) { setStatus(errorText(e)); }
  }
  async function saveKey() {
    const value = keyDraft.trim();
    if (!/^gsk_[A-Za-z0-9_-]{20,}$/.test(value)) { notify('Groq key', 'Paste a valid-format Groq key.'); return; }
    try {
      if (Platform.OS !== 'web') await SecureStore.setItemAsync(keyName, value);
      setKey(value); setKeyDraft(''); blocked.current = false; nextAI.current = 0; setStatus('Key saved. Tap Test connection.');
    } catch (e) { setStatus(errorText(e)); }
  }
  async function testKey() {
    try { await runAI([{ role: 'user', content: 'Reply with only: Connected.' }]); blocked.current = false; nextAI.current = 0; setStatus('Connected to Groq. Jeffery is ready.'); }
    catch (e) { setStatus(errorText(e)); }
  }
  async function forgetKey() {
    activeRequest.current?.abort();
    try { if (Platform.OS !== 'web') await SecureStore.deleteItemAsync(keyName); setKey(''); blocked.current = false; setStatus('Key removed from this device.'); }
    catch (e) { setStatus(errorText(e)); }
  }
  async function toggleNotifications() {
    try {
      if (prefs.notifications) { commit(b => ({ ...b, preferences: { ...b.preferences, notifications: false } })); return; }
      const allowed = await enableNotifications();
      if (allowed) { commit(b => ({ ...b, preferences: { ...b.preferences, notifications: true } })); setStatus('Phone reminders enabled.'); }
      else setStatus('Enable Jeffery notifications in your phone settings.');
    } catch (e) { setStatus(errorText(e)); }
  }
  async function exportBackup() {
    try {
      const text = JSON.stringify({ version: 2, notes: current.current.notes.map(({ advice, ...n }) => n) }, null, 2);
      if (Platform.OS === 'web') {
        const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
        const a = document.createElement('a'); a.href = url; a.download = 'JefferyNotebook.json'; a.click(); URL.revokeObjectURL(url); return;
      }
      if (!FileSystem.cacheDirectory) throw new Error('The phone cache is not available.');
      const path = FileSystem.cacheDirectory + 'JefferyNotebook.json';
      await FileSystem.writeAsStringAsync(path, text);
      if (!await Sharing.isAvailableAsync()) throw new Error('File sharing is not available on this device.');
      await Sharing.shareAsync(path, { mimeType: 'application/json', dialogTitle: 'Save your Jeffery backup', UTI: 'public.json' });
      setStatus('Backup ready to save or import on the desktop.');
    } catch (e) { setStatus(errorText(e)); }
  }
  async function importBackup() {
    try {
      const picked = await DocumentPicker.getDocumentAsync({ type: ['application/json', 'text/plain'], copyToCacheDirectory: true });
      if (picked.canceled) return;
      const file = picked.assets[0];
      if ((file.size || 0) > 8 * 1024 * 1024) throw new Error('Choose a file smaller than 8 MB.');
      const text = Platform.OS === 'web' ? await (await fetch(file.uri)).text() : await FileSystem.readAsStringAsync(file.uri);
      if (text.length > 8 * 1024 * 1024) throw new Error('Choose a file smaller than 8 MB.');
      if (file.name.toLowerCase().endsWith('.txt')) {
        const lines = text.split(/\r?\n/).map(t => t.trim()).filter(Boolean);
        if (!lines.length || lines.length > 100) throw new Error('Import between 1 and 100 nonblank checklist lines.');
        const note = newNote('list'); note.title = file.name.replace(/\.txt$/i, '').slice(0, 100);
        note.checklist = lines.map(line => ({ id: identifier(), text: line.slice(0, 200), quantity: 1, done: false }));
        commit(b => saveNote(b, note));
      } else { const payload = JSON.parse(text); commit(b => mergeBackup(b, payload)); }
      setStatus('Imported. Newer versions are kept; existing entries are preserved.');
    } catch (e) { setStatus(errorText(e)); }
  }
  const visible = book.notes.filter(n => (tab === 'Orders' ? n.kind === 'order' : tab === 'Notes' ? n.kind !== 'order' : !n.done) && matchNote(n, query))
    .filter(n => filter === 'Done' ? n.done : filter === 'Ready' ? !n.done && n.order_status === 'ready' : filter === 'All' ? true : !n.done)
    .sort((a, b) => (a.order_due || a.next_due || Infinity) - (b.order_due || b.next_due || Infinity));
  function noteCard(n: Note) { return <View key={n.id} style={s.card}>
    <Pressable onPress={() => openNote(n)}><View style={s.row}><Text style={[s.cardTitle, { flex: 1 }]}>{n.order_ref || n.title}</Text><Text style={[s.badge, n.done && { color: '#94a5b6' }]}>{n.done ? 'Done' : n.kind === 'order' ? n.order_status : n.kind}</Text></View>
      {n.customer ? <Text style={s.subtitle}>{n.customer}</Text> : null}
      {n.order_due ? <Text style={[s.subtitle, n.order_due * 1000 < Date.now() && !n.done && { color: '#ffbc91' }]}>Pickup · {timeLabel(n.order_due)}</Text> : null}
      <Text style={s.subtitle}>{timeLabel(n.next_due)}</Text>
      {n.checklist.length ? <Text style={s.subtitle}>{n.checklist.filter(i => i.done).length}/{n.checklist.length} items checked</Text> : <Text style={s.body}>{n.body.slice(0, 120)}</Text>}
    </Pressable>
    {n.advice && prefs.ai ? <View style={s.advice}><SlidingReply text={n.advice.reminder} /><Text style={s.subtitle}>{n.advice.next_step}</Text></View> : null}
    <View style={s.row}><Button title="Open" onPress={() => openNote(n)} /><Button title={n.done ? 'Restore' : 'Done'} onPress={() => commit(b => complete(b, n.id, !n.done))} />{!n.done && <Button title="Later · 15m" onPress={() => snooze(n.id, 15)} />}</View>
  </View>; }
  return <SafeAreaView style={s.safe}><StatusBar style="light" /><View style={s.shell}>
    <View style={s.header}><View><Text style={s.brand}>Jeffery</Text><Text style={s.subtitle}>{prefs.businessName || 'Your little business companion'}</Text></View><Text style={s.connection}>{key && prefs.ai ? 'AI on' : 'Offline ready'}</Text></View>
    <ScrollView style={{ flex: 1 }} contentContainerStyle={s.content} keyboardShouldPersistTaps="handled">
      {!ready ? <Text style={s.body}>Opening your notebook…</Text> : <>
      {tab === 'Today' && <>
        <View style={s.heroCard}><Pet character={prefs.character} onGreeting={() => { void greet(); }} /><SlidingReply text={greeting} /></View>
        <View style={s.stats}>{[['Open orders', counts.orders], ['Ready', counts.ready], ['Past deadline', counts.late]].map(([label, value]) => <View key={label} style={s.stat}><Text style={s.statValue}>{value}</Text><Text style={s.statLabel}>{label}</Text></View>)}</View>
        <View style={s.row}><Button title="+ Order" primary onPress={() => add('order')} /><Button title="+ Checklist" onPress={() => add('list')} /><Button title="Fresh greeting" disabled={busy} onPress={() => { void greet(); }} /></View>
        <Text style={s.section}>Next to handle</Text>
        {visible.slice(0, 6).map(noteCard)}
        {!visible.length && <View style={s.empty}><Text style={s.cardTitle}>A clearer day starts with one entry.</Text><Text style={s.subtitle}>Add a customer order or a checklist. Jeffery will help you keep the remaining work in view.</Text></View>}
      </>}
      {(tab === 'Orders' || tab === 'Notes') && <>
        <View style={s.row}><Text style={[s.section, { flex: 1 }]}>{tab === 'Orders' ? 'Customer orders' : 'Notes & checklists'}</Text><Button title={tab === 'Orders' ? '+ Order' : '+ Note'} primary onPress={() => add(tab === 'Orders' ? 'order' : 'note')} />{tab === 'Notes' && <Button title="+ List" onPress={() => add('list')} />}</View>
        <TextInput accessibilityLabel="Search entries" style={s.input} placeholder="Search customer, order or item…" placeholderTextColor="#74889d" value={query} onChangeText={setQuery} />
        <View style={s.row}>{['Active', ...(tab === 'Orders' ? ['Ready'] : []), 'Done', 'All'].map(label => <Pressable key={label} style={[s.chip, filter === label && s.selected]} onPress={() => setFilter(label)}><Text style={s.buttonText}>{label}</Text></Pressable>)}</View>
        {visible.map(noteCard)}
        {!visible.length && <Text style={s.subtitle}>No matching entries. Add one or change the filter.</Text>}
      </>}
      {tab === 'Chat' && <>
        <Text style={s.section}>Talk business with Jeffery</Text><Text style={s.subtitle}>Ask what is overdue, what to prepare next, or how to organize an order.</Text>
        {!chat.length && <Button title="What should I handle first?" onPress={() => setChatInput('What should I handle first from my open orders and notes?')} />}
        {chat.map((row, i) => <View key={i} style={[s.card, row.who === 'You' && { backgroundColor: '#243b3a' }]}><Text style={s.label}>{row.who}</Text><SlidingReply text={row.text} /></View>)}
        <Field label="Your message" value={chatInput} onChange={setChatInput} multiline placeholder="Ask Jeffery…" />
        <View style={s.row}><Button title={busy ? 'Thinking…' : 'Send'} primary disabled={busy || !chatInput.trim()} onPress={() => { void sendChat(); }} /><Button title="Clear chat" disabled={busy} onPress={() => setChat([])} /></View>
      </>}
      {tab === 'Settings' && <>
        <Text style={s.section}>Make Jeffery yours</Text>
        <Field label="Business name" value={prefs.businessName} onChange={value => commit(b => ({ ...b, preferences: { ...b.preferences, businessName: value.slice(0, 80) } }))} placeholder="Your business (optional)" />
        <Text style={s.label}>Character</Text><View style={s.row}>{(['robot', 'cat', 'knight'] as const).map(character => <Pressable key={character} style={[s.chip, prefs.character === character && s.selected]} onPress={() => commit(b => ({ ...b, preferences: { ...b.preferences, character } }))}><Text style={s.buttonText}>{character}</Text></Pressable>)}</View>
        <View style={s.row}><Text style={[s.body, { flex: 1 }]}>Use Groq with saved notes & orders</Text><Switch value={prefs.ai} onValueChange={ai => { if (!ai) activeRequest.current?.abort(); commit(b => ({ ...b, preferences: { ...b.preferences, ai } })); }} trackColor={{ true: '#72cbb0' }} /></View>
        <Field label="Groq key" value={keyDraft} onChange={setKeyDraft} secret placeholder={key ? 'Key saved · paste to replace' : 'Paste a working Groq key'} />
        <Field label="Groq model" value={prefs.model} onChange={model => commit(b => ({ ...b, preferences: { ...b.preferences, model } }))} />
        <View style={s.row}><Button title="Save key" primary onPress={() => { void saveKey(); }} /><Button title="Test connection" disabled={busy} onPress={() => { void testKey(); }} /><Button title="Forget key" onPress={() => { void forgetKey(); }} /></View>
        <Text style={s.subtitle}>{Platform.OS === 'web' ? 'Web preview keeps the key in this session.' : 'Your key stays in this device’s protected storage.'} Enabled AI reads saved order details. No key is included in this app.</Text>
        <View style={s.row}><Text style={[s.body, { flex: 1 }]}>Phone reminder notifications</Text><Switch value={prefs.notifications} onValueChange={() => { void toggleNotifications(); }} trackColor={{ true: '#72cbb0' }} /></View>
        <View style={s.row}><Text style={[s.body, { flex: 1 }]}>Quiet mode</Text><Switch value={prefs.quiet} onValueChange={quiet => { if (quiet) activeRequest.current?.abort(); commit(b => ({ ...b, preferences: { ...b.preferences, quiet } })); }} trackColor={{ true: '#72cbb0' }} /></View>
        <Text style={s.subtitle}>Phone notifications use saved reminders while the app is closed. Open Jeffery regularly to refresh the next scheduled reminders and fresh Groq wording.</Text>
        <Text style={s.section}>Move your notebook</Text><View style={s.row}><Button title="Export backup" onPress={() => { void exportBackup(); }} /><Button title="Import JSON / .txt" onPress={() => { void importBackup(); }} /></View>
        <Text style={s.subtitle}>Use a Jeffery JSON backup to move notes, checklists and orders between desktop and phone. Transfers are manual; this version has no automatic cloud sync.</Text>
      </>}
      {busy && <Button title="Cancel AI request" onPress={() => activeRequest.current?.abort()} />}
      <Text style={[s.status, !!error && { color: '#ffbc91' }]}>{error || status}</Text>
      </>}
    </ScrollView>
    <View style={s.tabs}>{(['Today', 'Orders', 'Notes', 'Chat', 'Settings'] as Tab[]).map(item => <Pressable accessibilityRole="tab" accessibilityState={{ selected: item === tab }} key={item} onPress={() => { setTab(item); setQuery(''); setFilter('Active'); }} style={s.tab}><Text style={[s.tabText, item === tab && { color: '#8ee0bf' }]}>{item}</Text></Pressable>)}</View>
    <Modal visible={!!editor} animationType="slide" presentationStyle="pageSheet" onRequestClose={closeEditor}>
      <SafeAreaView style={s.safe}><View style={s.modalHeader}><Button title="Close" onPress={closeEditor} /><Text style={s.cardTitle}>Your {editor?.kind || 'note'}</Text><Button title="Save" primary onPress={saveEditor} /></View>
      {editor && <ScrollView contentContainerStyle={s.content} keyboardShouldPersistTaps="handled">
        <View style={s.row}>{(['note', 'list', 'order'] as Kind[]).map(kind => <Pressable key={kind} style={[s.chip, editor.kind === kind && s.selected]} onPress={() => patchDraft({ kind })}><Text style={s.buttonText}>{kind === 'list' ? 'Checklist' : kind}</Text></Pressable>)}</View>
        <Field label="Title" value={editor.title} onChange={title => patchDraft({ title })} />
        {editor.kind === 'order' && <>
          <Field label="Customer" value={editor.customer} onChange={customer => patchDraft({ customer })} placeholder="Name or Walk-in" />
          <Field label="Phone or email" value={editor.contact} onChange={contact => patchDraft({ contact })} placeholder="Optional" />
          <Field label="Order number" value={editor.order_ref} onChange={order_ref => patchDraft({ order_ref })} placeholder="e.g. ORD-001" />
          <Text style={s.label}>Status</Text><View style={s.row}>{orderStatuses.map(order_status => <Pressable key={order_status} style={[s.chip, editor.order_status === order_status && s.selected]} onPress={() => patchDraft({ order_status, done: ['delivered', 'cancelled'].includes(order_status) })}><Text style={s.buttonText}>{order_status}</Text></Pressable>)}</View>
        </>}
        <Field label="Details" value={editor.body} onChange={body => patchDraft({ body })} multiline placeholder="Useful details, dates, or special instructions…" />
        <Text style={s.section}>Items & tasks</Text>
        {editor.checklist.map(item => <View key={item.id} style={s.item}><Pressable accessibilityRole="checkbox" accessibilityState={{ checked: item.done }} onPress={() => patchDraft({ checklist: editor.checklist.map(i => i.id === item.id ? { ...i, done: !i.done } : i) })} style={s.check}><Text style={s.checkText}>{item.done ? '✓' : ''}</Text></Pressable><Text style={[s.body, { flex: 1 }, item.done && { textDecorationLine: 'line-through', color: '#8091a4' }]}>{item.quantity} × {item.text}</Text><Pressable accessibilityLabel={`Remove ${item.text}`} onPress={() => patchDraft({ checklist: editor.checklist.filter(i => i.id !== item.id) })}><Text style={s.subtitle}>Remove</Text></Pressable></View>)}
        <View style={s.row}><TextInput accessibilityLabel="Item or task" style={[s.input, { flex: 1 }]} value={itemText} onChangeText={setItemText} placeholder="Item or task…" placeholderTextColor="#74889d" maxLength={200} /><TextInput accessibilityLabel="Quantity" style={[s.input, { width: 52 }]} value={quantity} onChangeText={setQuantity} keyboardType="number-pad" /><Button title="Add" onPress={addItem} /></View>
        <Text style={s.section}>Reminder</Text><Text style={s.subtitle}>{timeLabel(editor.next_due)}</Text>
        <View style={s.row}>{[15, 60, 1440].map(minutes => <Button key={minutes} title={minutes === 1440 ? 'Tomorrow' : minutes === 60 ? 'In 1 hour' : 'In 15 min'} onPress={() => patchDraft({ next_due: Date.now() / 1000 + minutes * 60 })} />)}<Button title="Off" onPress={() => patchDraft({ next_due: null, repeat_minutes: 0 })} /></View>
        {Platform.OS === 'web' ? <DateField label="Reminder date · YYYY-MM-DD HH:MM" value={editor.next_due} onChange={next_due => patchDraft({ next_due })} /> : <View style={s.row}><Button title="Choose date" onPress={() => setPicker({ field: 'next_due', mode: 'date' })} /><Button title="Choose time" onPress={() => setPicker({ field: 'next_due', mode: 'time' })} /></View>}
        <Text style={s.label}>Repeat</Text><View style={s.row}>{[0, 15, 30, 60, 1440].map(minutes => <Pressable key={minutes} style={[s.chip, editor.repeat_minutes === minutes && s.selected]} onPress={() => patchDraft({ repeat_minutes: minutes, next_due: minutes ? editor.next_due || Date.now() / 1000 + minutes * 60 : editor.next_due })}><Text style={s.buttonText}>{minutes === 0 ? 'Once' : minutes === 1440 ? 'Daily' : `${minutes} min`}</Text></Pressable>)}</View>
        {editor.kind === 'order' && <><Text style={s.section}>Pickup / delivery deadline</Text><Text style={s.subtitle}>{timeLabel(editor.order_due)}</Text>{Platform.OS === 'web' ? <DateField label="Deadline · YYYY-MM-DD HH:MM" value={editor.order_due} onChange={order_due => patchDraft({ order_due })} /> : <View style={s.row}><Button title="Deadline date" onPress={() => setPicker({ field: 'order_due', mode: 'date' })} /><Button title="Deadline time" onPress={() => setPicker({ field: 'order_due', mode: 'time' })} /></View>}<Button title="Remind at deadline" disabled={!editor.order_due} onPress={() => patchDraft({ next_due: editor.order_due })} /></>}
        {picker && Platform.OS !== 'web' && <View><DateTimePicker value={new Date((editor[picker.field] || Date.now() / 1000 + 3600) * 1000)} mode={picker.mode} themeVariant="dark" onChange={(event, value) => { if (Platform.OS === 'android' || event.type === 'dismissed') setPicker(null); if (value && event.type !== 'dismissed') patchDraft({ [picker.field]: value.getTime() / 1000 }); }} /><Button title="Done choosing" onPress={() => setPicker(null)} /></View>}
        {editor.advice && editor.advice.signature === signature(editor) && prefs.ai && <View style={s.card}><Text style={s.label}>Jeffery’s advice</Text><SlidingReply text={editor.advice.reminder} /><Text style={s.subtitle}>Next step: {editor.advice.next_step}</Text>{editor.advice.suggested_due && editor.advice.suggested_due > Date.now() / 1000 && <Button title={`Use ${timeLabel(editor.advice.suggested_due)}`} onPress={() => patchDraft({ next_due: editor.advice!.suggested_due })} />}</View>}
        <View style={s.row}><Button title="Save entry" primary onPress={saveEditor} /><Button title="Refresh Groq advice" disabled={busy || !book.notes.some(n => n.id === editor.id)} onPress={() => { const saved = current.current.notes.find(n => n.id === editor.id); if (saved && signature(saved) === signature(editor)) { setEditor(null); void advice(saved); } else notify('Save first', 'Save your changes so Jeffery can read the latest entry.'); }} /><Button title="Delete" onPress={() => remove(editor)} /></View>
        <Text style={s.subtitle}>Save your checks and changes. Delivered or Cancelled stops order reminders.</Text>
      </ScrollView>}</SafeAreaView>
    </Modal>
  </View></SafeAreaView>;
}
const s = StyleSheet.create({ safe: { flex: 1, backgroundColor: '#101924' }, shell: { flex: 1, width: '100%', maxWidth: 600, alignSelf: 'center' },
  header: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: 20, paddingVertical: 16 }, brand: { fontSize: 29, fontWeight: '800', color: '#e7f0f7', letterSpacing: -.8 }, connection: { fontSize: 12, color: '#8ee0bf', backgroundColor: '#203d37', padding: 9, borderRadius: 16 },
  content: { padding: 18, gap: 14, paddingBottom: 32 }, row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, alignItems: 'center' }, heroCard: { backgroundColor: '#182738', padding: 18, borderRadius: 22 },
  card: { backgroundColor: '#192737', padding: 16, borderRadius: 18, gap: 10 }, cardTitle: { fontSize: 17, fontWeight: '700', color: '#e5eef7' }, subtitle: { color: '#9aaec2', fontSize: 13, lineHeight: 20 }, body: { color: '#d8e6f1', fontSize: 15, lineHeight: 23 }, label: { color: '#a8c1d0', fontSize: 12, fontWeight: '600', marginBottom: 5 },
  button: { backgroundColor: '#293b4c', paddingVertical: 11, paddingHorizontal: 15, borderRadius: 12 }, buttonText: { fontSize: 13, color: '#d9ebe6', fontWeight: '600' }, primary: { backgroundColor: '#88d8b7' },
  input: { backgroundColor: '#1a2a3b', borderColor: '#33495a', borderWidth: 1, color: '#e5edf6', borderRadius: 12, padding: 13, fontSize: 15 }, field: { gap: 4 },
  section: { fontSize: 20, fontWeight: '700', color: '#e4eef7', marginTop: 8 }, chip: { backgroundColor: '#213144', paddingVertical: 9, paddingHorizontal: 13, borderRadius: 16 }, selected: { backgroundColor: '#32564d', borderColor: '#8ed6bc', borderWidth: 1 }, badge: { fontSize: 11, fontWeight: '700', color: '#9de1c6', textTransform: 'capitalize' },
  stats: { flexDirection: 'row', gap: 9 }, stat: { flex: 1, backgroundColor: '#182b37', padding: 14, borderRadius: 16 }, statValue: { fontSize: 27, color: '#a2e8cb', fontWeight: '700' }, statLabel: { color: '#9eb3c1', fontSize: 11, marginTop: 4 },
  tabs: { flexDirection: 'row', borderTopColor: '#2b3d4b', borderTopWidth: 1, paddingVertical: 13, backgroundColor: '#15222f' }, tab: { flex: 1, alignItems: 'center', paddingVertical: 5 }, tabText: { color: '#8197ab', fontWeight: '600', fontSize: 12 },
  status: { fontSize: 12, color: '#91adbd', lineHeight: 19, paddingTop: 8 }, empty: { padding: 22, backgroundColor: '#172535', borderRadius: 16, gap: 10 }, advice: { borderLeftColor: '#71cda8', borderLeftWidth: 2, paddingLeft: 10, gap: 6 },
  modalHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: 16, borderBottomColor: '#2b3d4b', borderBottomWidth: 1 }, item: { flexDirection: 'row', alignItems: 'center', gap: 10, backgroundColor: '#182a3b', padding: 12, borderRadius: 12 }, check: { width: 25, height: 25, borderColor: '#83cbb0', borderWidth: 1, borderRadius: 6, alignItems: 'center', justifyContent: 'center' }, checkText: { color: '#90dbb9', fontWeight: '700' }
});
