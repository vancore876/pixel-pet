# Jeffery Work Chat

IT **Active sessions** counts unexpired employee login tokens across the office,
not computers currently online. Separate browser/desktop logins and repeated
sign-ins can each contribute a token; the default employee expiry is eight hours.
Select an employee and choose **Sign employee out of all PCs** to revoke their
sessions without deleting the account.

IT **Delete employee account** requires selecting one employee, entering the
current IT password, and typing that employee's exact username. It removes the
account from the employee directory and revokes all its sessions/recovery codes.
Company notes, orders, stock, and messages remain; retained author identities are
anonymized. Deletion is audited. This option requires the updated desktop and
the updated server endpoint `/api/it/users/{id}/delete`.

IT login is authenticated by `/api/it/login`. HTTP 404 on that endpoint means
the responding service has no IT login handler, not that a password or version
check failed. Desktop compatibility cannot supply a missing authentication
backend. Check_Work_Server.bat checks the loaded IT module files and probes
the running IT overview without credentials (401 is expected when supported).
Server startup verifies the IT routes before listening and prints its project
folder. IT passwords, session checks, and employee permissions remain enforced.

One conflicting or unreadable API record does not block newer notes: it stays
queued for retry while the download cursor advances through other records.
Local conflicting Writing is preserved. Missing API cache entries are rebuilt
from the latest confirmed record without uploading a new note. When folder and
API connections are both enabled, an API error appears alongside folder status;
a healthy folder connection no longer hides server-note delivery problems.

New Writing notes follow the same active transport as orders and checklists.
With shared-folder sync enabled, they are saved through that workspace rather
than being forced onto the separate Work Chat API. Automatic API migration is
disabled while folder sync owns new notes. Existing API records retain their
ownership; viewing those records on another PC requires signing into the same
Work Chat server. Folder-owned notes require the same shared-folder workspace.

When an older office server rejects Writing notes or highlight fields with a
validation error, current desktops retry once using a tagged compatibility body
inside the server's existing checklist format. The same ID and expected revision
are retained. Current desktops decode that record back into its original entry
type, text, and highlights, including when coworkers load or edit it. Older
desktops may display the tagged text; update desktops that use these records.
The older server's 10,000-character body limit includes the compatibility data.
An entry that exceeds it remains an unsaved draft and is never truncated.
Native notebook support remains the preferred server format.

If updating appears to have no effect, run **Check_Work_Server.bat on the server
computer**. It reports the project folder, loaded schema file, Python interpreter,
Git revision, and running server's notebook capabilities without reading or
changing notes or accounts. Start_Work_Server.bat checks for an already occupied
port before launch and reports the existing response instead of trying to start
a second server. It never stops that existing process automatically.

The `/api/health` response includes `notebook_schema: 2` and notebook feature
names for Writing notes, highlights, and server authorship. Desktop saves send
only supported note fields; cached author names and timestamps are not echoed
back because the authenticated server owns attribution. A field-schema rejection
preserves the draft and reports the server/desktop format mismatch explicitly.

Writing notes, shared orders and checklists save highlights on the office server. Select a list
item or text in the note, click **Highlight**, then **Save**. Coworkers receive
the highlights through normal synchronization. Concurrent edits require reloading
the latest version before saving. Select multiple lines or list items to highlight
them together. Each entry supports up to 200 phrases; exceeding the limit leaves
existing highlights unchanged and explains how to proceed. Overlapping phrases
share one painted range. A server response that drops saved highlights preserves
the editor draft and asks you to update the office server instead of clearing
the highlights silently.

Shared-folder sync also distinguishes omitted highlights from an explicit empty
list. Events from older clients inherit the nearest recorded highlight value;
current clients send an empty list only when highlights are removed. Equivalent
versions do not generate conflict copies, and received notes use a normalized
local baseline so idle synchronization does not republish unchanged content.
Previously created conflict copies remain available for review.
Deleting a displayed conflict version removes only that version; other versions
and unrelated notes remain. Deleting an ordinary note still deletes that note
across the workspace. Only a persistent marker from an explicit Delete action
authorizes a shared deletion. A missing local cache or a retired conflict copy
does not authorize deleting the remote note. A missing local sync
baseline alone does not recreate an unchanged deleted note; genuine unpublished
local edits can still be preserved as a separate recovered entry.
Local note deletions also retain a persistent deletion marker. An older response
already in flight cannot reinsert that note, and deletion still publishes when
the local sync baseline is missing. Removing a checklist or order item records
its stable item identity as deleted in shared history; stale versions cannot
restore that item. Add it again as a new item if it is needed later.

Each newly saved note and list or order item shows **Added by**, assigned by the server
from the signed-in employee. Editing an item keeps its original contributor.
Older items without recorded attribution show **Unknown**. Update and restart
the office server and desktop clients to use these fields.

Work Chat gives each coworker a username and password, a shared **Team Room**,
and private direct messages. The same login also enables shared Famous Twins
orders and checklists in desktop Jeffery. A Python service stores accounts,
messages, and shared business records on the work server. People can use their
browser for chat, or **Home → Coworkers** in Jeffery opened from the office's
shared application folder. Each coworker
creates a different chat username and password. Chat updates approximately
every two seconds while open. A separate desktop notification feed runs while
signed in, including with the chat window hidden or another conversation open.
This chat is separate from the existing Groq AI
conversation under **Home → Ask Jeffery**.

The desktop interface renewal adds a central Home, readable sign-in and
create-account forms, and **Find a coworker** search after signing in. The
office server, account credentials, private-message checks, shared business
records, and certificate-free LAN setup continue to work as before. An AI
key is configured separately in **Ask Jeffery → AI setup**; coworkers do not
need one for office chat or shared orders/checklists.

## Desktop notifications and account controls

Incoming personal messages and Team Room messages show a passive popup without
moving keyboard focus. Your own messages and direct messages between other
employees do not produce popups. Open goes to the correct conversation and
preserves an unfinished reply elsewhere. Quiet mode defers notifications rather
than marking unseen messages delivered. Acknowledge and snooze affect your own
current-session reminders; Settings controls popups and the repeat interval
(0 disables repeats). Message bodies and passwords are not stored in the local
notification file. Sign out clears its in-memory message content.

Cards start small with the sender and a two-line message preview. Use the arrow
to expand the full, scrollable message or collapse it again. **More** offers
5-, 15-, 30-, and 60-minute snoozes and **Acknowledge**. Choose **Settings →
Getting started → Message popup size** to start every card expanded instead.
This setting also applies to shared-folder team notifications. An expanded card
stays open while you read; another incoming card waits until you close or act on
the current one. Expanding and collapsing do not acknowledge or snooze anything.

Notifications also appear when you are in a different personal chat or the Team
Room. They leave the selected conversation and its unsent reply in place; only
**Open** switches to the message's conversation. Normal workspace windows,
including Work Chat, have native minimize, maximize/restore, and close buttons.
Closing a workspace keeps the background app running and retains its draft.

Shown message and reminder popups play the Windows notification sound by
default. **Settings → Getting started → Play a sound for message and reminder
popups** turns this off without hiding the popups. Quiet mode also silences it.
Expanding a card or refreshing chat does not replay the sound, and a shared
two-second interval prevents a burst of popups from overlapping sounds.

Outgoing desktop messages now show **Read** in direct chats and **Read by N**
in Team Room. A receipt is recorded only when the recipient's selected
conversation is visible, active, and scrolled to the bottom, with no modal
dialog covering it. A hidden/minimized chat, another open conversation, or a
notification preview does not count as reading. Receipts cover the latest 50
sent messages requested for the conversation, with up to 20 reader names.
They are separate from delivery and **Acknowledge**: reading does not stop
message reminders. The browser chat currently does not mark messages read.

New accounts receive a one-time recovery code. Save it privately. Existing
signed-in users can open **Account security** and enter their current password
to generate or replace a code. **Forgot password?** accepts the username,
recovery code, and a new password; it consumes the old code, provides a replacement,
and ends all existing sessions. If there is no recovery code, use the existing
server administrator password-reset procedure below. Knowing a username alone
cannot reset its password.

**Account security → Delete account permanently…** requires the current password,
exact username, and explicit final confirmation. It permanently removes the login,
revokes every session and recovery code, and removes the employee from the active
directory. Shared messages and work records remain with anonymized authors so
other employees keep their work history and stock. A new registration with the
old name is a new identity and does not regain the old account's private messages.
The server retains only an inactive anonymous author reference for shared history.

**Forget saved login** is different: it signs out and removes only this PC's saved
server address and username, leaving the server account intact.

The same office server now holds the shared [parts inventory](INVENTORY.md).
Update and restart the server before using the new desktop controls; the existing
database migrates without discarding accounts, notes, or messages.
**Home → Scan barcode** supports USB scanners that type a code followed by
Enter. Lookups never change quantities; receive, sale, return, and physical
count actions require review and confirmation. The server shares stock movement
history and checks revisions to prevent one PC overwriting another's count.
See [Inventory setup](INVENTORY.md#usb-barcode-scanning-and-stock-movements).

The proposed work server is Windows at **192.168.50.194**. Confirm its IPv4
address with `ipconfig` on that machine. The simple office setup uses:

```text
http://192.168.50.194:8765
```

In Jeffery, entering just **192.168.50.194** selects that address and port
automatically. This setup requires no certificates. Passwords, session tokens,
messages, and business data travel unencrypted over the office network; use it
only on the trusted office LAN. Verified HTTPS remains available as an optional
setup below.

The code has been tested locally. It has not been installed on that work server.

## Install on the Windows work server

1. Install 64-bit Python 3.11 or newer on the main PC.
2. Download the updated GitHub ZIP, or clone/update this repository, into a local
   folder such as `C:\Jeffery\pixel-pet`. Use the complete project files and
   exclude anyone's existing personal `data` or `server-data` folders. This same
   project folder can be shared with coworkers as described below.
3. Double-click **Start_Work_Server.bat** using its local disk path on the main
   PC. It creates `.server-venv` and installs or refreshes the server dependencies
   automatically. The first setup needs internet access. No certificate files
   are required. Accounts, messages, and shared business records are stored
   outside the project folder at
   `%LOCALAPPDATA%\PixelSystemBuddy\work-server\work-chat.sqlite`, under the
   Windows account running the server.
4. Leave its window open. Ctrl+C stops the service. Keep the main PC on and awake
   whenever coworkers need chat or shared business access. The desktop pet does
   not need to be open on the server for those services to work.

You can also prepare dependencies separately with **Setup_Work_Server.bat**.
For a manual PowerShell setup, run these commands from the project folder:

```powershell
py -3 -m venv .server-venv
.\.server-venv\Scripts\python.exe -m pip install -r requirements-server.txt
.\.server-venv\Scripts\python.exe -m pip check
.\.server-venv\Scripts\python.exe -m work_server --host 0.0.0.0 --port 8765 --database "$env:LOCALAPPDATA\PixelSystemBuddy\work-server\work-chat.sqlite" --allow-lan-http
```

The server has its own optional dependencies; desktop clients do not need them.
Keep the database on the server's local disk. Do not put SQLite on an SMB share
or give workstations access to it. Accounts, message history, and shared
business records belong in the private `work-server` folder in the server
account's local profile. Share only
the project folder, not the server account's profile or its `work-server` folder.

An existing database from an earlier manual setup in `server-data` is not moved
automatically. If you already have accounts, stop the old server and either
keep using its explicit `--database` path outside the share, or privately copy
the complete old database folder (including any SQLite WAL/SHM files) into the
new `work-server` location. Verify the accounts are present before removing the
old copy from any folder you plan to share. The CLI's default database path
remains `server-data\work-chat.sqlite`; use the explicit production command
above when starting it manually.

Allow incoming TCP port 8765 only from the actual office subnet. For example, if
your administrator confirms the office uses `192.168.50.0/24`, this elevated
PowerShell command adds a Windows firewall rule for that subnet:

```powershell
New-NetFirewallRule -DisplayName "Jeffery Work Chat - office LAN HTTP" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -RemoteAddress 192.168.50.0/24 -Profile Domain,Private
```

No router port forwarding is needed. The application rejects public socket
addresses and ignores forwarded-address headers even in HTTP mode. The
subnet-specific firewall rule determines which workplace network can reach it.
Do not expose this service to the internet. Keep the server IP consistent so
coworkers do not have to change their saved address.

For automatic startup later, have your administrator run the manual server
command through Windows Task Scheduler or your usual service manager, using
this repository as its working directory and a dedicated Windows account.
Keep using the same Windows account, or set an explicit private database path;
changing accounts changes `%LOCALAPPDATA%` and otherwise starts a new database.

## Share the application and connect coworkers

1. On the main PC, have your administrator share the same project folder,
   for example `C:\Jeffery\pixel-pet`, with coworkers as **read-only**. It should
   contain application code, not anyone's personal `data`, `server-data`, or
   credentials. The server's `.server-venv` contains dependencies and can stay
   there; coworkers use their own local environment. For example, a share
   named `Jeffery` would expose `\\192.168.50.194\Jeffery`; the administrator
   chooses the actual share name. Only administrators need write access to
   publish application updates.
2. On each coworker's PC, install 64-bit Python 3.11 or newer. Open
   **Start_Work_Buddy.bat** from that shared folder. A desktop shortcut can point
   to `\\192.168.50.194\Jeffery\Start_Work_Buddy.bat`. The same launcher also
   supports a mapped network drive. It installs desktop dependencies in that
   Windows user's local profile on first launch, which needs internet access.
   Coworkers do not need a separate project copy or access to the server's
   Python environment.
3. On Jeffery Home, choose **Coworkers** or **Connect to office**, and enter
   **192.168.50.194** in the server field. Use the actual server IP if different.
   Bare IPs use HTTP port 8765; for another port, enter `192.168.50.194:PORT`.
   An explicit URL such as `http://192.168.50.194:8765` also works.
4. Choose **Create account**, enter a unique username, a password, and its
   confirmation. Usernames contain 3–32 letters, numbers, underscores, dots, or
   dashes; they are case-insensitive. Passwords contain 12–128 characters.
5. Open **Team Room** for everyone, or select a coworker for direct messages.
   Use **Find a coworker** to narrow the list. Coworkers appear after creating
   accounts. Use Refresh if needed.
6. Choose **Home**, then **Orders & quotes** or **Checklists**. Wait for
   **Connected to your team** before saving an order or checklist. The login
   automatically loads shared
   records; there is no separate business account or database path to configure.

Home opens at normal desktop startup when **Open Home when Jeffery starts**
is enabled in **Settings → Getting started** and **Launch with monitor hidden**
is off. Click the tray icon, double-click Jeffery, or use a main window's Home
button to return. Closing Home leaves Jeffery running. Appearance can be
changed under **Settings → Appearance → Workspace appearance**; Light, Dark,
and Follow Windows are separate from desktop monitor themes. Click Apply to
save settings.

Only the main PC runs **Start_Work_Server.bat**. Everyone connects to that one
server; they do not start their own chat servers. Accounts, message history,
and shared business records stay in the server Windows account's
`%LOCALAPPDATA%\PixelSystemBuddy\work-server` folder and survive restarts.

As an alternative to installing Jeffery, coworkers can open
`http://192.168.50.194:8765` in a browser and use the same accounts and chats.

**Start_Work_Buddy.bat** runs the shared application code while keeping its
Python environment at `%LOCALAPPDATA%\PixelSystemBuddy\work-runtime` and each
Windows user's notebook, memory, and settings at
`%LOCALAPPDATA%\PixelSystemBuddy\work-data`. It does not write personal data or
install dependencies into the shared folder. Keep the share available while
Buddy is running. Close Buddy before publishing a new version of the shared
code, then have coworkers reopen it.

Chat usernames are separate from Windows logins. Different Windows user
profiles keep local notebooks and settings separate. If coworkers use the
same Windows profile, they share those local files and must **Sign out** of
Work Chat between people. Each person signs in with their own chat account;
Writing notes are now office-wide. Shared notebook
records use a disposable local cache that is cleared on sign-out/account
changes and reloaded from the server on sign-in.

**Start_Buddy.bat** remains available for a separate local, portable project
copy on an individual PC. Do not use that launcher from the shared folder:
its portable `data` folder could be shared between coworkers. Work Chat itself
is shared through the Python API. The current mobile app has not been connected
to this API; its existing Groq chat and manual notebook transfers continue to
work. Existing packaged EXEs need rebuilding to include these changes; use the
new source files and **Start_Work_Buddy.bat** for this shared-folder setup.

## Check the connection at work

Create different accounts on two PCs, select each other in the coworker list,
and send a message in each direction. New messages should appear in about two
seconds while the conversation is open. Then send a Team Room message and
check that both accounts see it. Direct messages should stay out of Team Room.

Then create an order or checklist on one PC and save it. Check that it appears
on the other PC, make a change there, and verify that the first PC receives it.
To check conflict handling, open the same order on both PCs, save a change on
one, then try to save an older editor draft on the other. The older save should
be rejected without overwriting the coworker's change; its draft stays in the
editor until reviewed or discarded.

If shared notebook sync reports **“Review this record's current revisions before
changing it”**, update Jeffery on every PC and restart it using the same writable
team folder. The fix replays missing shared history, preserves an edited note
deleted elsewhere as a separate note, and allows unrelated notes to continue
syncing when one note needs recovery. Keep the existing local notebooks,
`documents.sqlite`, and `JefferyWorkspace`; do not delete them to clear the
message. Shared-folder notebook sync is separate from the Work Chat API:
starting the web server alone does not enable it. Follow
[shared-folder setup](README.md#work-team-messages-and-shared-notes-on-windows)
and verify a new note and an edit on two PCs after updating.

## Shared Famous Twins records

The redesigned desktop workspace separates **Writing**, **Checklists**,
**Orders**, **Schedule**, and **Overview**. See [Famous Twins workflow](FAMOUS_TWINS.md)
for JMD totals, quotes, supplier/bin details, manual sourcing, customer history,
pickup summaries, templates, and animations.

- Every authenticated coworker can read, edit, complete, and delete every shared
  Writing note, order, and checklist. These are team business records; direct-message privacy does
  not apply to them.
- Signing in loads records from the office server. New and edited records need
  a live connection to save; changes appear on other signed-in desktop clients
  approximately every 2.5 seconds. The browser currently offers chat, not the
  desktop business workspace.
- Existing plain-text Writing notes upload to the office server after sign-in.
  An open editor waits for **Save**; failed uploads preserve the local original.
  The employee who first shares an older note is recorded as its contributor;
  the app cannot recover its historical author. New notes save directly to the
  office server, with shared highlights and author attribution. Full imported
  documents retain their separate local document storage. Notes already assigned
  to folder-based team synchronization retain that ownership. Existing local
  orders/checklists are published when saved.
- Concurrent changes use version checks. A stale save/delete returns a conflict
  instead of overwriting another coworker. An unsaved editor draft is preserved
  in the current window. **Reload saved** appears beside the save controls after
  a failed shared edit; it is also available under **More → Reload saved entry**.
  Copy desired edits before choosing it and **Discard**, then review the latest
  version and reapply them. **More → Delete entry** uses the same version checks.
- Prices, payments, stock status, VIN, and fitment notes are entered by staff.
  This is not live inventory, a supplier catalogue, or an automatic payment
  system. Quotes do not count toward open orders or receivables.
- The server supports up to 2,000 undeleted business entries and 100 rows per
  entry. Completed records still count until deleted. Each desktop's existing
  local notebook limit also includes loaded business records. There is no
  offline business-save queue or per-order access control.

Open **Home → More tools → Overdue customer orders** for the shared order list.
The desktop shows a small internal reminder when an active customer order is
due, one hour overdue, or one day overdue. It shows the current tier once per
due date; **Later** can repeat it after 15 minutes, one hour, or one day. Open
takes you to the order. Quiet mode and the note-reminder setting apply. Quotes,
completed, delivered, and cancelled orders are excluded; a new due date rearms
the reminder. State belongs to the signed-in employee on that PC, so snoozing
does not change another employee's reminder. Offline results use the last
complete office snapshot and are labelled as cached. These are staff reminders;
Jeffery does not contact customers automatically.

If another PC cannot connect, open `http://192.168.50.194:8765/api/health` in its
browser. It should show a JSON response with `"status":"ok"`. Confirm the actual
server IP, that the server window is still open, the Windows network profile,
and the port 8765 firewall rule. From that PC, PowerShell can also check:

```powershell
Test-NetConnection 192.168.50.194 -Port 8765
```

## Accounts, messages, and business storage

- Passwords use salted Argon2id hashes. Session tokens are random, stored only
  as hashes on the server, and expire after eight hours. Sign out revokes the
  current token. Passwords and tokens are never saved in desktop settings or
  browser local/session storage. Refreshing the browser requires signing in.
- Anyone on the allowed office network can create an account. The directory
  has a 1,000-account limit. The desktop IT section manages password resets and
  session revocation. Desktop account controls provide recovery-code password
  resets and permanent self-service deletion. Account approval and different
  staff permission levels are not included yet.
- Direct messages are visible through the API only to their sender and
  recipient; Team Room messages are visible to every signed-in user. Message
  history is stored in the server database. Administrators with database access
  can read it; this is not end-to-end encryption.
- Shared orders/checklists, audit usernames, revisions, and deletion markers are
  stored in the same database. Business API reads/writes require a valid work
  login. All staff accounts have the same shared-business permissions.
- In certificate-free HTTP mode, these access checks do not protect against
  someone intercepting traffic on the network. Password hashes protect stored
  passwords; they do not encrypt passwords sent during sign-in.
- The clients initially load the latest 50 messages in the selected conversation
  and retain up to 500 visible messages as new ones arrive. Older messages remain
  in the database; this version has no scroll-back browser or retention policy.
- Messages are plain text, up to 4,000 characters. Desktop popups and read receipts
  require the signed-in app. Uploads, mobile push notifications, and automatic
  AI replies to coworker messages are not included.
- Sign-in/sign-up, sending, and business writes have rate limits. Ordinary
  requests are bounded to 16 KiB; business API requests allow up to 256 KiB.
  Business delta pages contain up to three records so desktop responses remain
  below the client's 1 MiB cap. Request bodies must finish within ten seconds.
  FastAPI telemetry is disabled.

To reset a forgotten password without an IT login or recovery code, stop the server and run the following on the
server itself. The command prompts for the new password without echoing it and
revokes all sessions for that account. Restart the server afterward.

```powershell
.\.server-venv\Scripts\python.exe -m work_server reset-password USERNAME --database "$env:LOCALAPPDATA\PixelSystemBuddy\work-server\work-chat.sqlite"
```

Run the reset command under the same Windows account that runs the server, or
replace the database argument with its actual private path.

## Office backups and recovery

**Start_Work_Server.bat** now enables automatic database snapshots. While the
server runs, it takes one per UTC date and catches up after downtime. Snapshots
use SQLite's online backup operation, so normal office work can continue. Each
snapshot is checked for database integrity and written with a SHA-256 manifest.
The default keeps the newest successful snapshot for each of the last **14
backup dates**; a successful manual snapshot replaces that date's older copy.

The default folder is `office-backups` beside the office database, normally
`%LOCALAPPDATA%\PixelSystemBuddy\work-server\office-backups`. Keep it private
and on a local disk. A custom CLI start can use `--backup-directory` and
`--backup-keep-days 14` (1–365 backup dates); `--no-auto-backup` disables the
schedule while preserving manual backups. Network-share and linked-directory
backup paths are rejected. Copy completed `.sqlite` snapshots **and their
adjacent `.sqlite.json` manifests** to a protected separate backup location for
protection against failure of the server disk.

Open **Home → IT administration** after completing the initial IT password
change. The backup panel shows status, last success, next due time, errors,
location, and saved snapshots. **Back up now** requires the current IT password
again and starts a server job. Closing the window does not cancel that job.
The list reports verification performed at creation; the verification command
below checks the current file contents again.

These snapshots include office accounts, password hashes, chat/read receipts,
shared orders/checklists, inventory/movement history, and IT records. They do
not include individual PCs' notebooks/documents, the shared-folder notebook
workspace, or TLS certificate/private-key files. Back those up separately;
desktop notebook exports do not replace an office database backup.

To verify a snapshot on the server, run this from the project folder, replacing
`SNAPSHOT.sqlite` with the actual filename shown in IT:

```powershell
.\.server-venv\Scripts\python.exe -m work_server verify-backup "$env:LOCALAPPDATA\PixelSystemBuddy\work-server\office-backups\SNAPSHOT.sqlite"
```

To restore, stop every office server process first. Choose the snapshot and
confirm the existing destination database path before running:

```powershell
.\.server-venv\Scripts\python.exe -m work_server restore-backup "$env:LOCALAPPDATA\PixelSystemBuddy\work-server\office-backups\SNAPSHOT.sqlite" --database "$env:LOCALAPPDATA\PixelSystemBuddy\work-server\work-chat.sqlite" --confirm-restore
```

Restore verifies the snapshot, creates a verified copy of the current database
under `restore-safety-backups`, then replaces the database. It refuses a running
server or a nonexistent destination. Restart the server afterward and have
everyone sign in again: restored employee and IT sessions are revoked. Accounts
and passwords return to their snapshot values, and work saved afterward is only
in the safety copy. Restore is an offline server command, not a live IT button.

## IT administration

Update and restart both the office server and Jeffery desktop clients, keeping
the existing server database. Open **Home → IT administration** (also available
from Jeffery's right-click menu).

1. On the **server PC**, set the IT server address to **127.0.0.1:8765**. If your
   server uses another port or HTTPS, use its matching local address and a
   trusted certificate that covers that address.
2. Sign in with username **admin** and initial password **admin123**.
3. Enter that current password again and choose/confirm a private password of
   **12–128 characters**. The initial login cannot manage employees or read the
   dashboard. Initial setup is accepted only from the server's loopback
   connection; other office PCs cannot claim the starter account.
4. After changing the password, IT can sign in from office PCs using the normal
   office server address. The starter password stops working and is never
   restored automatically when the server restarts.

IT shows employee accounts, active session counts (not a live PC-presence list),
message and inventory totals, and recent administrative actions. Select an
employee to **reset their password** or **sign out all their PCs**; confirm their
username and re-enter the IT password. Resets revoke all of that employee's
sessions and previous recovery code. Give the new password to that employee
privately; they can then generate a fresh recovery code in Account security.
Messages and shared records are preserved. IT does not display direct-message
bodies, employee passwords, or personal notebooks.

The IT identity is separate from employee Work Chat accounts. An existing
employee named `admin` keeps their normal employee access and gains no IT
privileges. Employee sessions cannot call IT APIs, and IT sessions cannot read
employee chat APIs. IT passwords are hashed and tokens remain in memory only.
Closing the IT window signs it out; leaving it idle locks it after 15 minutes,
and the server expires an ordinary IT session after one hour. Initial setup
sessions expire after ten minutes. Changing the IT password revokes older IT
sessions. Locking clears this PC immediately; server sign-out is best effort
when the network is unavailable, with server expiry as the fallback. The
recent-actions view is limited to the latest 100 entries.

If IT forgets its changed password, stop the server and run this **on the server
PC**, under the Windows account that runs the server:

```powershell
.\.server-venv\Scripts\python.exe -m work_server reset-it-password --database "$env:LOCALAPPDATA\PixelSystemBuddy\work-server\work-chat.sqlite"
```

The command prompts privately for a new password and confirmation. It revokes
IT sessions without changing employee passwords; restart the server afterward.
For certificate-enabled servers whose certificates do not include loopback,
this command can also set the initial private IT password locally before the
first network sign-in. The browser chat page does not include the IT console.
Office HTTP remains unencrypted; use the documented HTTPS setup to protect
administrative credentials in transit between PCs.

## Optional HTTPS later

For encrypted traffic, ask your administrator for a TLS certificate and
private key in PEM format. The certificate must include `192.168.50.194` as an
IP subject alternative name, or use an internal DNS name with a matching DNS
subject alternative name. Every client must trust the issuing CA. The Windows
browser and Buddy's Qt TLS backend can use different trust configuration;
verify both clients on the actual work PCs. HTTPS certificate checks remain
enforced and cannot be bypassed.

Store the certificate chain as
`%LOCALAPPDATA%\PixelSystemBuddy\work-server\tls\server.crt` and the key as
`%LOCALAPPDATA%\PixelSystemBuddy\work-server\tls\server.key` under the server
Windows account. Protect that private folder so only the server service account
and administrators can access it; keep both files outside the application share.
Stop the HTTP server and use this command instead of the HTTP launcher:

```powershell
.\.server-venv\Scripts\python.exe -m work_server --host 0.0.0.0 --port 8443 --database "$env:LOCALAPPDATA\PixelSystemBuddy\work-server\work-chat.sqlite" --certfile "$env:LOCALAPPDATA\PixelSystemBuddy\work-server\tls\server.crt" --keyfile "$env:LOCALAPPDATA\PixelSystemBuddy\work-server\tls\server.key"
```

Allow TCP port 8443 from the office subnet, enter
`https://192.168.50.194:8443` in each client, and close the old port 8765 firewall
rule if it is no longer needed. The same database preserves accounts and
history and shared business records. Direct CLI LAN starts without
`--allow-lan-http` still require HTTPS.

## Local development and checks

The default CLI starts a loopback-only development server without certificates:

```powershell
.\.server-venv\Scripts\python.exe -m work_server --port 8765 --database server-data\development.sqlite
```

Open `http://127.0.0.1:8765` locally. LAN HTTP requires the explicit
`--allow-lan-http` option used by **Start_Work_Server.bat**.
To run the backend and desktop checks:

```powershell
.\.server-venv\Scripts\python.exe -m unittest discover -s tests -p test_work_server.py -v
.\.server-venv\Scripts\python.exe -m unittest discover -s tests -p test_work_business.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_work_chat.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_shared_business.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_auto_parts.py -v
.\.venv\Scripts\python.exe tests\check_work_chat_integration.py
.\.venv\Scripts\python.exe tests\check_shared_business_integration.py
```

To exercise the same two-client Qt workflow through a private IPv4 socket on
this computer, set `WORK_CHAT_TEST_LAN_IP` to its actual LAN IP before running
the integration script. It uses a disposable database and temporary port, then
stops its own server. It does not connect to the work server or existing accounts.

The optional browser workflow creates its own temporary server, database, and
synthetic accounts, then shuts them down:

```powershell
.\.server-venv\Scripts\python.exe -m pip install -r requirements-server-dev.txt
.\.server-venv\Scripts\python.exe -m playwright install chromium
.\.server-venv\Scripts\python.exe tests\check_work_chat_web.py
```

The server and browser checks also run in their own GitHub Actions job. Office
firewall configuration, concurrent real-workplace use, and Windows service
deployment still need validation on the actual work network. Certificate trust
needs validation only if you enable HTTPS later.

References: [Argon2 password storage guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html),
[SQLite on network filesystems](https://www.sqlite.org/useovernet.html),
[Uvicorn deployment settings](https://uvicorn.dev/settings/).
