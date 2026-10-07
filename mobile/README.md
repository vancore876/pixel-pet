# Jeffery Business for Android and iPhone

An Expo React Native app with Today, Orders, Notes, Chat, and Settings screens.
It includes Robot, Cat and Knight, 49 animation states, drag/parachute landing,
an in-app folder hideout, and replies that slide in line by line.

The app stores notes and orders on the device. Groq can write fresh greetings,
answer business questions using saved entries, and suggest a reminder and next
step based on unchecked items and order status. Dates and progress stay under
your control. No API key is bundled.

## Start

1. Install Node.js 24 LTS (tested with 24.19.0).
2. From this folder, run:

   ```sh
   npm ci
   npm start
   ```

3. Use a compatible Expo Go app for a quick preview, or create the development
   build below. The project uses Expo SDK 57, React Native 0.86.3 and React 19.2.3.
   The phone and computer must be able to reach each other on the same network.
4. Press **w** in the terminal for a browser preview. This checks screens and
   notebook workflows; phone notifications and protected key storage need a
   native phone build.

On Windows, **Start_Mobile.bat** in the parent directory installs dependencies
on the first run and starts Expo. On macOS/Linux, use **start-mobile.sh**.
If the installed Expo Go version does not support SDK 57, use a development build.

## Orders and reminders

- **+ Order:** add a customer, contact, order number, items, quantities and details.
- Check prepared items and **Save**. New, Preparing, and Ready remain open.
  Delivered, Cancelled, or Done stops reminders. Restore returns an order to New.
- **+ Checklist:** keep a preparation list or general to-do list.
- Choose a reminder date/time, a quick time, or a repeat interval. The pickup
  deadline is separate; **Remind at deadline** uses it for the next reminder.
- **Settings → Phone reminder notifications:** explicitly enable permission.
  Notifications offer Open, Later (15 minutes), and Done.
- **Quiet mode:** cancels pending Jeffery notifications and pauses AI speech.
- **Settings → Groq:** save a working key and test the connection. Native builds
  use protected device storage. The browser preview keeps the key only in the
  current session. **Forget key** removes the saved device key.
- The AI toggle controls sharing saved notes/orders. Manual chat can still send
  the message you type, with notebook details excluded when the toggle is off.
- Tap Jeffery, Wave, or Fresh greeting for a new greeting. Refresh Groq advice
  on a saved entry for new wording. Advice suggestions never change a chosen
  date, customer detail, item check, or order status automatically.

The app calls Groq while open. When closed, local notifications use the saved
AI reminder for the next occurrence and varied local reminders after that.
It queues up to four occurrences per repeating entry and 48 notifications total,
ordered by time. Reopen the app regularly to refill the queue. Continuous fresh
Groq generation while the phone app is closed needs a backend, which this version
does not include. Phone notification delivery also depends on device settings.

## Move notes between desktop and phone

1. Desktop: Notepad → **Mobile backup** saves a JSON file.
2. Phone: Settings → **Import JSON / .txt** selects that file.
3. To return changes, phone → **Export backup**, then desktop → **Import backup**.

Orders, notes, checklists, quantities, customer details, dates and completion
state transfer. Matching IDs keep the newer version; an import does not remove
entries absent from the file. AI advice is regenerated as needed. The export
does not contain API keys or phone preferences. A .txt import creates a checklist
from up to 100 nonblank lines. Transfers are manual; there is no cloud sync.

## Build installable apps

The ZIP contains source, not an APK, IPA, or store release. Expo's Android and iOS
JavaScript/Hermes bundle exports have passed; compilation, signing, installation
and notification delivery need native builds and real-device testing.

Set a unique `android.package` and `ios.bundleIdentifier` in app.json before
publishing your own app. Create or sign in to your Expo account:

```sh
npx eas-cli@latest login
npx eas-cli@latest build:configure
```

Android installation APK:

```sh
npx eas-cli@latest build --platform android --profile preview
```

Install the resulting APK on an Android phone. This preview profile contains
the complete app, including local notifications and device key storage.
**Build_Android.bat** in the parent folder runs that build command.

Development builds with Expo's developer tools:

```sh
npx eas-cli@latest build --platform android --profile development
# Or, for an iPhone registered for internal distribution:
npx eas-cli@latest build --platform ios --profile development
npx expo start --dev-client
```

The development client dependency is included. Physical iPhone builds require
Apple signing credentials and device registration for internal distribution.
A TestFlight/App Store build uses:

```sh
npx eas-cli@latest build --platform ios --profile production
```

Apple Developer membership, App Store Connect configuration, and submission are
separate steps. An Android Play Store build uses the production profile instead
of preview. EAS may apply account/build limits; it does not use your Groq key.

Local native builds are also supported with `npx expo run:android` (Android SDK
and JDK required) or `npx expo run:ios` (macOS and Xcode required).

## Verification

```sh
npm run typecheck
npm test
npx expo export --platform web
npx expo export --platform android --platform ios --output-dir native-bundles
```

TypeScript checks, 14 meaningful business/AI/reminder tests, and web/Android/iOS
bundle exports passed in the development workspace. Groq transport responses
were scripted; test your own working key in Settings. The browser UI workflow
checks customer orders, quantities, checked items, saving, reload persistence,
status changes, and desktop/phone backup transfer.

Windows folder/tab access, selected-letter editing, the system HUD, and floating
desktop play remain desktop features. Mobile Jeffery lives inside his app.

References: [Expo build setup](https://docs.expo.dev/build/setup/),
[Android APKs](https://docs.expo.dev/build-reference/apk/),
[development builds](https://docs.expo.dev/develop/development-builds/introduction/),
[local notifications](https://docs.expo.dev/versions/latest/sdk/notifications/),
[Groq API](https://console.groq.com/docs/api-reference).
