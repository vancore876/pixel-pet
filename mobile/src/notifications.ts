import { Platform } from 'react-native';
import * as Notifications from 'expo-notifications';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Book } from './model';
import { planReminders } from './reminder-plan';
const ledgerKey = 'jeffery-reminder-ledger-v1';
Notifications.setNotificationHandler({ handleNotification: async () => ({ shouldShowBanner: true, shouldShowList: true, shouldPlaySound: true, shouldSetBadge: false }) });
export async function enableNotifications(): Promise<boolean> {
  if (Platform.OS === 'web') throw new Error('Phone notifications are available in the Android and iPhone builds.');
  await Notifications.setNotificationChannelAsync('jeffery-orders', { name: 'Jeffery notes and orders', importance: Notifications.AndroidImportance.HIGH });
  const existing = await Notifications.getPermissionsAsync();
  const result = existing.granted ? existing : await Notifications.requestPermissionsAsync();
  if (!result.granted) return false;
  await Notifications.setNotificationCategoryAsync('jeffery-note', [
    { identifier: 'OPEN', buttonTitle: 'Open', options: { opensAppToForeground: true } },
    { identifier: 'SNOOZE_15', buttonTitle: 'Later · 15 min', options: { opensAppToForeground: true } },
    { identifier: 'DONE', buttonTitle: 'Done', options: { opensAppToForeground: true } }
  ]);
  return true;
}
let serial: Promise<void> = Promise.resolve();
export function syncNotifications(book: Book): Promise<void> {
  if (Platform.OS === 'web') return Promise.resolve();
  serial = serial.catch(() => {}).then(async () => {
    const planned = planReminders(book), ledger = JSON.parse(await AsyncStorage.getItem(ledgerKey) || '{}') as Record<string, string>;
    const scheduled = await Notifications.getAllScheduledNotificationsAsync();
    const desired = new Set(planned.map(p => p.id));
    for (const previous of scheduled) if (previous.identifier.startsWith('jeffery:') && !desired.has(previous.identifier)) await Notifications.cancelScheduledNotificationAsync(previous.identifier);
    if (!book.preferences.notifications || book.preferences.quiet) {
      await AsyncStorage.setItem(ledgerKey, '{}');
      return;
    }
    const permission = await Notifications.getPermissionsAsync();
    if (!permission.granted) throw new Error('Phone notifications are disabled in your device settings.');
    for (const p of planned) {
      const tag = `${p.dueTag}:${p.body}`;
      const exists = scheduled.some(s => s.identifier === p.id);
      // A delivered one-time reminder is not repeated on every app launch.
      if (!exists && p.at <= Date.now() && ledger[p.id]?.startsWith(p.dueTag + ':')) continue;
      if (exists && ledger[p.id] === tag) continue;
      if (exists) await Notifications.cancelScheduledNotificationAsync(p.id);
      await Notifications.scheduleNotificationAsync({ identifier: p.id,
        content: { title: `Jeffery · ${p.title}`, body: p.body, sound: 'default', categoryIdentifier: 'jeffery-note', data: { noteId: p.noteId } },
        trigger: { type: Notifications.SchedulableTriggerInputTypes.DATE, date: new Date(Math.max(Date.now() + 5000, p.at)), channelId: 'jeffery-orders' } });
      ledger[p.id] = tag;
    }
    const pruned = Object.fromEntries(Object.entries(ledger).filter(([id]) => book.notes.some(n => id.startsWith(`jeffery:${n.id}:`))));
    await AsyncStorage.setItem(ledgerKey, JSON.stringify(pruned));
  });
  return serial;
}
