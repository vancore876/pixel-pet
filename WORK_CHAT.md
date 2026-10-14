# Jeffery Work Chat

Work Chat gives each coworker a username and password, a shared **Team Room**,
and private direct messages. The same login also enables shared Famous Twins
orders and checklists in desktop Jeffery. A Python service stores accounts,
messages, and shared business records on the work server. People can use their
browser for chat, or **Home → Coworkers** in Jeffery opened from the office's
shared application folder. Each coworker
creates a different chat username and password. Chat updates approximately
every two seconds while open. This chat is separate from the existing Groq AI
conversation under **Home → Ask Jeffery**.

The desktop interface renewal adds a central Home, readable sign-in and
create-account forms, and **Find a coworker** search after signing in. The
office server, account credentials, private-message checks, shared business
records, and certificate-free LAN setup continue to work as before. An AI
key is configured separately in **Ask Jeffery → AI setup**; coworkers do not
need one for office chat or shared orders/checklists.

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
changing the chat account does not change personal writing. Shared business
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

## Shared Famous Twins records

The redesigned desktop workspace separates **Writing**, **Checklists**,
**Orders**, **Schedule**, and **Overview**. See [Famous Twins workflow](FAMOUS_TWINS.md)
for JMD totals, quotes, supplier/bin details, manual sourcing, customer history,
pickup summaries, templates, and animations.

- Every authenticated coworker can read, edit, complete, and delete every shared
  order/checklist. These are team business records; direct-message privacy does
  not apply to them.
- Signing in loads records from the office server. New and edited records need
  a live connection to save; changes appear on other signed-in desktop clients
  approximately every 2.5 seconds. The browser currently offers chat, not the
  desktop business workspace.
- Personal Writing entries and full documents remain local. Existing local
  orders/checklists are not uploaded at login. Opening one and choosing **Save
  changes** explicitly publishes it to coworkers.
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
  has a 1,000-account limit. There is no administrator dashboard, account
  approval, self-service password recovery, or account deletion interface yet.
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
- Messages are plain text, up to 4,000 characters. Uploads, push notifications,
  read receipts, and automatic AI replies to coworker messages are not included.
- Sign-in/sign-up, sending, and business writes have rate limits. Ordinary
  requests are bounded to 16 KiB; business API requests allow up to 256 KiB.
  Business delta pages contain up to three records so desktop responses remain
  below the client's 1 MiB cap. Request bodies must finish within ten seconds.
  FastAPI telemetry is disabled.

To reset a forgotten password, stop the server and run the following on the
server itself. The command prompts for the new password without echoing it and
revokes all sessions for that account. Restart the server afterward.

```powershell
.\.server-venv\Scripts\python.exe -m work_server reset-password USERNAME --database "$env:LOCALAPPDATA\PixelSystemBuddy\work-server\work-chat.sqlite"
```

Run the reset command under the same Windows account that runs the server, or
replace the database argument with its actual private path.

For backups, stop the server and copy the complete
`%LOCALAPPDATA%\PixelSystemBuddy\work-server` folder from that server Windows
account to a protected backup location, then restart. Preserve the database and
any existing SQLite WAL/SHM files together. Backups contain workplace accounts,
chat, shared orders/checklists, revisions, deletion markers, and TLS private
keys if HTTPS is configured; limit access accordingly. Desktop notebook exports
do not replace this server backup.
Restore while the server is stopped.

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
