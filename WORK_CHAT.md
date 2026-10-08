# Jeffery Work Chat

Work Chat gives each coworker a username and password, a shared **Team Room**,
and private direct messages. A Python service stores accounts and messages on
the work server. People can use their browser, or **Chat → Work Chat · Coworkers**
in a locally installed copy of Buddy. Chat updates approximately every two seconds
while open. This chat is separate from the existing Groq AI conversation.

The proposed work server is Windows at **192.168.50.194**. Confirm its IPv4
address with `ipconfig` on that machine before setting up DNS, certificates, or
client connections. The intended workplace address is:

```text
https://192.168.50.194:8443
```

The code has been tested locally. It has not been installed on that work server.

## Install on the Windows work server

Install 64-bit Python 3.11 or newer. Clone or update this repository into a local
folder such as `C:\Jeffery\pixel-pet`, then open PowerShell in that folder:

```powershell
py -3 -m venv .server-venv
.\.server-venv\Scripts\python.exe -m pip install -r requirements-server.txt
.\.server-venv\Scripts\python.exe -m pip check
```

The server has its own optional dependencies; desktop clients do not need them.
Keep the database on the server's local disk. Do not put SQLite on an SMB share
or give workstations write access to it.

Ask your administrator for a TLS certificate and private key in PEM format.
The certificate must include `192.168.50.194` as an IP subject alternative name,
or use an internal DNS name with a matching DNS subject alternative name instead.
Every client must trust the issuing CA. The Windows browser and Buddy's Qt TLS
backend can use different trust configuration; verify both clients on the actual
work PCs. Certificate checks are enforced and cannot be bypassed in Work Chat.
Store the certificate chain as `server-data\tls\server.crt` and the key as
`server-data\tls\server.key`; protect that folder so only the server service
account and administrators can access it. These files and the database are
excluded from Git.

Start the server in PowerShell:

```powershell
.\.server-venv\Scripts\python.exe -m work_server --host 0.0.0.0 --port 8443 --database server-data\work-chat.sqlite --certfile server-data\tls\server.crt --keyfile server-data\tls\server.key
```

Alternatively, after completing the dependency and certificate setup, run
**Start_Work_Server.bat** from the server's local copy. Keep its window open while
testing; Ctrl+C stops the service. For regular use, have your administrator run
the same command through Windows Task Scheduler or your usual service manager,
using this repository as its working directory and a dedicated Windows account.
Start the chat service rather than the desktop pet on the server.

Allow incoming TCP port 8443 only from the actual office subnet. For example, if
your administrator confirms the office uses `192.168.50.0/24`, this elevated
PowerShell command adds a Windows firewall rule for that subnet:

```powershell
New-NetFirewallRule -DisplayName "Jeffery Work Chat - office LAN" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8443 -RemoteAddress 192.168.50.0/24 -Profile Domain,Private
```

No router port forwarding is needed. The application rejects public socket
addresses, ignores forwarded-address headers, and requires HTTPS for connections
from another computer. The subnet-specific firewall rule determines which
workplace network can reach it. This version is intended for the office LAN.

## Connect coworkers

1. On each work PC, open `https://192.168.50.194:8443` in a browser. If you used
   internal DNS, use that matching HTTPS name instead.
2. Choose **Create account**, enter a unique username, a password, and its
   confirmation. Usernames contain 3–32 letters, numbers, underscores, dots, or
   dashes; they are case-insensitive. Passwords contain 12–128 characters.
3. Open **Team Room** for everyone, or select a coworker for direct messages.
   Coworkers appear after creating accounts. Use Refresh if needed.
4. To use the desktop pet as well, install a separate local Buddy copy on each
   workstation. Open **Chat → Work Chat · Coworkers**, enter the same server
   address, and sign in with the same account.

Each desktop copy keeps its existing notebook, memory, and settings locally.
Do not have everyone launch the portable desktop app from the same writable
server folder: that would share its `data` folder. Work Chat itself is shared
through the Python API. The current mobile app has not been connected to this
API; its existing Groq chat and manual notebook transfers continue to work.

## Accounts and message storage

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
- The clients initially load the latest 50 messages in the selected conversation
  and retain up to 500 visible messages as new ones arrive. Older messages remain
  in the database; this version has no scroll-back browser or retention policy.
- Messages are plain text, up to 4,000 characters. Uploads, push notifications,
  read receipts, and automatic AI replies to coworker messages are not included.
- Sign-in/sign-up and sending have rate limits. Requests are bounded to 16 KiB
  and must finish their body within ten seconds. FastAPI telemetry is disabled.

To reset a forgotten password, stop the server and run the following on the
server itself. The command prompts for the new password without echoing it and
revokes all sessions for that account. Restart the server afterward.

```powershell
.\.server-venv\Scripts\python.exe -m work_server reset-password USERNAME --database server-data\work-chat.sqlite
```

For backups, stop the server, copy the complete `server-data` folder to a
protected backup location, then restart. Preserve the database and any existing
SQLite WAL/SHM files together. Backups contain workplace chat history and TLS
private keys; limit access accordingly. Restore while the server is stopped.

## Local development and checks

Plain HTTP is permitted only on the same computer for development:

```powershell
.\.server-venv\Scripts\python.exe -m work_server --port 8765 --database server-data\development.sqlite
```

Open `http://127.0.0.1:8765` locally; other computers must use the HTTPS setup.
To run the backend and desktop checks:

```powershell
.\.server-venv\Scripts\python.exe -m unittest discover -s tests -p test_work_server.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_work_chat.py -v
.\.venv\Scripts\python.exe tests\check_work_chat_integration.py
```

The optional browser workflow creates its own temporary server, database, and
synthetic accounts, then shuts them down:

```powershell
.\.server-venv\Scripts\python.exe -m pip install -r requirements-server-dev.txt
.\.server-venv\Scripts\python.exe -m playwright install chromium
.\.server-venv\Scripts\python.exe tests\check_work_chat_web.py
```

The server and browser checks also run in their own GitHub Actions job. Office
certificate trust, firewall configuration, concurrent real-workplace use, and
Windows service deployment still need validation on the actual work network.

References: [Argon2 password storage guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html),
[SQLite on network filesystems](https://www.sqlite.org/useovernet.html),
[Uvicorn deployment settings](https://uvicorn.dev/settings/).
