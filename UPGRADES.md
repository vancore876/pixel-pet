# Jeffery's upgrades

The **interface renewal** keeps version 6.0 and brings the Windows workspace,
office chat, and optional AI assistant together through Jeffery Home.

See the [Famous Twins workflow](FAMOUS_TWINS.md) for everyday use and
[Work Chat setup](WORK_CHAT.md) for the Windows office server and shared launcher.

| Included | Behavior |
| --- | --- |
| Central Home | New order, New checklist, Write a note, business counts, attention queues, recent work, and search |
| Easier navigation | Orders & quotes, Checklists, Writing, Schedule, Coworkers, and Ask Jeffery in one sidebar; main windows have Home buttons |
| Startup and return | Home opens at normal startup; tray click and buddy double-click return to it; closing Home leaves Jeffery running |
| Workspace appearance | Light with teal accents, Dark, or Follow Windows; independent of monitor themes and buddy colors |
| Clearer settings | Section sidebar, draft changes, Apply to save, Cancel to leave saved preferences intact |
| Focused entry browser | Writing, checklist, or order records follow the chosen page; Hide entries / Show entries makes room for the task |
| Optional order details | Expand Vehicle & pickup details or Part numbers, supplier, and bin when needed; hidden fields retain their values |
| Less clutter in editors | Save changes stays visible; More contains Delete entry and Reload saved entry; failed shared edits expose Reload saved |
| Separate conversations | Coworkers has sign-in/account creation, Team Room, direct messages, and coworker search; Ask Jeffery has AI setup and optional privacy preferences |
| Files page | PDF/web import and reviewed saving replace the old Sources / Import label |
| Office accounts and sharing | Individual usernames/passwords; shared desktop orders/checklists through the same office server as chat |
| JMD orders and quotes | Exact totals, cumulative payments, balance/credit, sourcing details, customer history, pickup summaries, and operational templates |
| Parts-counter animations | Stock check, part scan, packing, wrench, high five, and coffee; 55 desktop states |
| Groq greetings | Startup, mouse greetings and interaction remarks use live business context and recent phrases |
| Fresh reminders | Each actual desktop reminder requests new wording; local fallbacks also vary |
| Customer orders | Customer/contact, order number, pickup deadline, status and details |
| Preparation checklists | Items with quantities and completion checks; Groq sees the remaining work |
| Order progress | New → Preparing → Ready → Delivered; Cancelled and Delivered stop reminders |
| Business overview | Open, ready and past-deadline orders; customer/item/reference search |
| Mobile app | Today, Orders, Notes, Chat and Settings; three characters and 49 animation states |
| Phone reminders | Local notifications with Open, Later and Done actions |
| Notebook transfer | JSON backup import/export between desktop and phone; newer entries merge |
| Groq controls | Business name, personality, greetings, saved-note sharing and protected device keys |
| Existing upgrades | Sliding replies, tiny HUD, Windows folders/tabs/letters, mouse play, parachute, sticky notes, launcher and focus timer |

Groq needs a working key tested in **Ask Jeffery → AI setup** or mobile Settings. Advice does
not invent stock or payment status, and suggested dates need your review.
The mobile folder contains runnable Expo source and build instructions; signed
phone installers and store releases are not included. Mobile Jeffery lives
inside his app. Windows folder/tab/letter features remain desktop features.

Useful next upgrades:

1. **Phone-to-office sync.** Connect the mobile app to the shared office accounts
   and records. The Windows clients already share orders with conflict handling.
2. **Invoices and stock.** Link a chosen business system so availability, payment
   and order totals come from real records.
3. **Closed-app AI reminders.** Add a backend to refresh Groq wording and send
   notifications without reopening the phone app.

These are future options. The mobile app continues to use manual notebook
transfer and local phone reminders, with fresh Groq requests while it is open.
The desktop office server continues to support certificate-free HTTP on the
trusted LAN; usernames, JMD amounts, and server data are unchanged by the renewal.
