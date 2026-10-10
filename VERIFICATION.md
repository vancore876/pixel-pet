# Jeffery 6.0 verification

The interface renewal passed Home navigation and draft-preservation integration,
light/dark/compact workspace and chat workflows, eight notebook-import tests,
twelve scaled-document tests, four appearance/settings tests, five Home tests,
and nine shared-client tests. The 23 Work Chat checks and 14 AI memory checks
passed. Two real Qt accounts exercised direct/team messages, shared orders and
checklists, stale-edit conflicts, deletion, and sign-out with personal writing
preserved. Browser chat passed desktop and 390-pixel phone workflows. Screenshots
were inspected for Home, orders, checklists, settings, utilities, both desktop
chats, and browser chat. Desktop play integration and the scripted AI/advanced
play workflow also passed after the shared style update. No office deployment
or new packaged installer is claimed by
these source-level checks. Existing Windows near-epoch and process-monitor
timing test failures described below remain outside this interface change.
The existing Windows desktop-helper cleanup warning also occurs after the
advanced-play check exits successfully.

The shared-office launcher passed six profile/startup checks, 13 core regression
checks, and 23 Work Chat client checks. Two real app startup/shutdown smoke runs
used separate local profiles and an unrelated working directory, preserving each
user's saved chat name without changing the portable settings. Windows startup
commands retain the work entrypoint, including quoted shared paths. The actual
server batch started HTTP on port 8765 with a disposable database in its private
local profile, outside the application folder, and was stopped afterward. The
office's real Windows share, permissions, and concurrent workstation launches
still need validation at work.

The office HTTP Work Chat update passed 22 backend authentication/privacy/storage
checks, 23 desktop client/transport checks, and disposable two-account Qt and
browser workflows. Two real Qt clients connected through this development PC's
private IPv4 socket without certificates, using bare-IP entry and receiving
direct messages in both directions, including normal polling. Checks include
unauthorized private-message reads, session expiration/revocation, password
reset, request/rate limits, default HTTPS and opted-in LAN HTTP boundaries,
literal message rendering, late conversation replies, and preserved drafts.
The Windows server dependency setup batch completed successfully with both an
existing environment and a fresh temporary folder, creating its environment
and installing the pinned packages from scratch.
Windows and browser layouts were visually inspected. The full desktop unit
run completed 353 tests with 15 errors and 19 skips on this Windows/Python 3.14
host. Errors concern existing Windows epoch-time conversion and locked SQLite
files during test cleanup; representative failures reproduce on the pristine
pre-chat commit. Office deployment, firewall/service setup, and real workplace
load remain unverified. Certificate trust needs validation if optional HTTPS is
enabled. See WORK_CHAT.md for setup.

The one-billion-character notebook update passed 213 desktop unit tests, seven
Qt GUI workflows, and the desktop startup smoke test. Checks cover streamed
full text beyond the preview, Unicode character quotas, indexed recall after
restart, bounded text pages, safe title/schedule edits, complete ZIP/text
backups, restore at capacity, cancellation, missing storage, committed-restore
cleanup, and background-cleanup shutdown. Visual QA retained all 149,999
extracted characters from a synthetic document across 15 text pages while its
notes.json metadata stayed about 11 KB. A 2,010,000-character import also
retained its final fact and used one notebook entry. Capacity boundaries were
exercised with reduced test quotas; a full billion-character file was not
benchmarked. Windows native integration and live Groq remain unverified.

The 500 MB PDF update passed 127 desktop unit tests and the import-window GUI
workflow. A valid synthetic PDF exactly 500 × 1024 × 1024 bytes was imported
without a whole-file read; one byte over the limit was rejected before parsing.
Checks also cover 121-page documents and cancellation during parser I/O with
the file handle closed. The import preview remains 120,000 characters; the
latest notebook update now stores full extracted text separately.

Windows memory/document/monitor update validated in the cloud workspace:
124 desktop unit tests and seven Qt GUI workflows passed, along with runtime
dependency checks and the desktop startup smoke test. New checks exercise
memory persistence, preference conflicts and older-reply rejection, sharing
controls, full-document recall, editable PDF/web imports, atomic save failures,
cancel/close behavior, and the medieval monitor's real readings and animation
lifecycle. Public Node.js and GitHub webpage extraction passed with verified
TLS and source URLs. Groq replies were scripted; a live Groq key is still needed
for online answers. DuckDuckGo search was blocked by the cloud proxy until its
saved network additions are applied. Windows native integration and EXE builds
still need native validation. Mobile behavior was unchanged in this update.

Validated in the development workspace on 2026-10-07.

| Check | Result |
| --- | --- |
| Desktop unit tests | 65 passed |
| Desktop Qt workflows | Six passed: monitor/settings, pet/notes, smart notes, desktop controls, advanced play/chat, business orders |
| Mobile business/Groq/reminder tests | 14 passed |
| Mobile TypeScript | Passed |
| Expo web export | Passed |
| Expo Android Hermes bundle export | Passed |
| Expo iOS Hermes bundle export | Passed |
| Mobile phone-size browser workflow | Passed: customer/item/quantity checks, save/reload, Ready/Done/Restore, backup transfer, AI advice queue, business greetings, repeat filtering and key-free notebook storage |
| Actual mobile JSON export imported by desktop | Passed; order/customer/quantities/checks/status preserved |
| Desktop JSON export imported by mobile | Passed; existing mobile entries preserved |

Desktop checks used Python 3.12.14, PySide6 6.11.2 and psutil 7.2.2 with Linux
offscreen Qt. Mobile checks used Node 24.19.0, Expo 57.0.27, React Native 0.86.3
and React 19.2.3. The browser workflow ran the exported app at 390 × 844.

Groq replies and Windows targets were scripted. A live working Groq key,
Windows native detection/text editing, native phone notifications, protected
phone storage, APK/IPA compilation, device installation and signing were not
verified in this workspace. Android/iOS bundle exports are JavaScript/Hermes
outputs, not installable native applications. See README.md and mobile/README.md
for startup, build instructions and practical limits.
