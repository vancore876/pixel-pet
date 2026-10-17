# Jeffery 6.0 verification

The office expansion, shared-note sync repair, and notification sounds passed
all **967 unit tests**, all **15 Qt GUI workflows**, and isolated desktop startup
with Python 3.12/Linux offscreen Qt. Three shared-business HTTP integration cases
and two background-notification cases also passed. Dependency checks are clean.

Independent three-PC profiles reproduce the reported “current revisions” error:
a local edit after a coworker's deletion now becomes a separate recovered note.
Tests also cover lost revision caches, simultaneous conflicts, unavailable
ancestry, missing baselines, and interrupted full-document uploads. Unrelated
notes continue in both directions, complete document text survives recovery,
and cross-record revision validation remains strict. Recovery also works at
the notebook entry limit while preserving edits made during a sync.

Stock checks cover keyboard-wedge Enter input, leading zeros, ambiguous codes,
cancelled confirmations, nonnegative quantities, stale revisions, durable server
retry receipts, account changes, employee audit attribution, and bounded history.
Late catalogue responses cannot replace confirmed quantities. Narrow scanner
layouts scroll without overlapping controls. Backup checks exercise real SQLite
WAL snapshots, verification, retention, corruption, stopped-server restoration,
safety copies, session revocation, and bounded worker shutdown with database
locking. A Windows-compatible writable-fsync regression passed on Linux.

Desktop read receipts require the selected foreground conversation at the bottom;
notification previews and other windows do not mark it read. Overdue customer
orders use account-scoped tiers and snoozes, complete-snapshot caching, quiet
deferral, and completion cancellation. Eight new animations preserve the old
state order and render across seven characters at small and large sizes in both
directions; the new contact sheet was visually inspected.

The combined HTTP/BuddyApp workflow imported a real Excel file, scanned codes
with keyboard input and Enter, cancelled and confirmed a stock receipt, and
checked the saved quantity from another employee account. It also exercised
personal/team read receipts, order snooze/escalation/completion, and a verified
manual IT backup containing the resulting receipts and stock audit. Notification
sound tests use mocked playback to check the native Windows API, settings,
Quiet mode, burst limits, failed audio devices, and accepted visible deliveries.
The six affected GUI workflows and isolated startup passed again after sounds
were added. Actual sound output on a Windows work PC was not tested here.

No dependencies changed. Update/restart the server and every desktop client,
retaining existing data and the same writable shared notes folder. These tests
use disposable local servers and folders; the real Windows SMB share, physical
USB scanner, native Windows titlebars, and office deployment were not exercised.
No new Windows executable was built in this cloud. Earlier checks follow.

The IT administration update passed all **810 unit tests**, all **14 Qt GUI
workflows**, and isolated desktop startup on Python 3.12/Linux offscreen Qt.
Nineteen new server tests cover separate admin/employee identities and tokens,
loopback-only initial setup, forced password rotation, hashing, expiry, rate
limits, local recovery, atomic audit entries, and concurrent revocation checks.
Fourteen new UI tests cover session lifecycle, inactivity lock, stale replies,
address changes, validation, explicit confirmations, and secret clearing.

The real BuddyApp HTTP workflow exercised initial setup, employee password
reset, invalidation of two employee sessions and an old recovery code, remote
sign-out, unchanged message history, and an ordinary employee named `admin`
remaining outside IT access. It also delayed a login response until after the
window closed and verified that its newly issued token was revoked. Home/menu
navigation, light/dark presentation, compact layouts, and native window-control
flags passed; the IT dashboard preview was visually inspected. Existing
work-chat, inventory, recovery, deletion, shared-folder, and companion checks
remain green. CI includes the new server and real HTTP GUI checks.

No dependencies changed. Update/restart both server and clients while retaining
the existing server database. Windows installation/titlebar rendering and the
real office network remain unverified in this Linux cloud. No new Windows
executable was built here. Older feature-specific validation follows.

The companion and desktop-play update passed all 777 desktop unit tests and
all 13 Qt GUI workflows on Python 3.12/Linux offscreen Qt. The real BuddyApp
workflow selected all seven characters through the menu, played all ten new
companion poses, saved/reloaded the selection, and routed a visible file icon
through the coordinator to an inspection animation. Render checks exercised
fractional frames and gaze, all 65 states on every character, distinctive
silhouettes, lit surfaces, both directions, small/large sizes, parachutes, and
the tiny tray footprint. New gallery and animation previews were inspected.

Motion checks verified equal travel speed and animation duration at smooth and
Low power cadences, resting/hidden timer behavior, and custom PNG playback.
Desktop interaction checks covered overlapping icons, moving targets, closure
during a hop, minimization, activity sequences, quiet/paused/busy deferral, and
cleanup. File-icon play performs no file operation or automatic tab selection.
File paths stay local; file names enter AI context only when name sharing is
enabled. File contents are never part of icon play.

Ten file-target checks included four executed PowerShell metadata tests for
hidden extensions, ambiguous labels, shortcuts, provider failures, bounds and
occlusion. They used mocked Shell objects in checksum-verified upstream
PowerShell 7.4.13 on Linux. Actual Windows Shell/UI Automation discovery, native
desktop coordinates and work-PC deployment still need validation on Windows.
Source startup smoke passed. Existing desktop dependencies suffice; the extra
PowerShell runtime is optional cloud test tooling. Mobile sprite ordering stays
at its existing 49 rows; mobile assets and behavior were not changed.

The compact notification and window controls update passed all 741 desktop
unit tests and all 13 Qt GUI workflows on Python 3.12/Linux offscreen Qt.
Two real HTTP background-notification cases and the full BuddyApp office workflow
exercised personal and Team Room messages while chat was hidden or another
conversation was selected. Expand/collapse kept queued messages, reminders,
conversation selection, and unsent drafts intact. Cards default to a bounded
350 × 196 logical-pixel preview with explicit expansion and a More menu;
small monitors, long Unicode text, live settings, and logout clearing passed.
Both compact and expanded layouts were visually inspected.

Twelve workspace windows passed minimize, maximize/restore, close, and theme
change checks with the background app and drafts preserved. Dialog semantics
and close guards remain intact. Passive cards set both Qt's show-without-activation
attribute and no-focus window flag. Headless Qt synthesizes activation even for
no-focus windows, so OS focus preservation is asserted only on native backends;
Windows titlebar rendering and native focus behavior still need checking on a
work PC. Source startup smoke passed. No new native Windows installer was built
in this cloud for this update.

The Home/chat/accounts/inventory update passed all 725 desktop tests and all 13
Qt GUI workflows on Python 3.12/Linux offscreen Qt. Actual disposable HTTP
accounts exercised personal and team popups while chat was hidden or another
conversation was selected, quiet deferral, recovery-code reset, and permanent
account deletion across two PCs. Deleted logins and recovery codes stopped
working; shared messages/orders retained anonymous authors and stock remained
unchanged. Excel review/import, editable column mapping, leading-zero codes,
shelf lookup, manual restock updates, two-employee shared lookup, stale-edit
protection, offline-account isolation, and separate inventory AI consent passed.
The Home card geometry was also checked at 100%, 150%, and 200% scaling.
The Linux application built and passed packaged startup. A frozen Excel probe
also passed workbook reading, zero-prefixed part codes, review before writes,
saved locations, restock calculations, indexed recall, and worker shutdown.
These checks do not
establish native Windows deployment, real office SMB behavior, or live stock
automation. The future automation connector remains separate work.

The shared-folder employee update passed all 621 desktop unit tests and 12 Qt
GUI workflows on Python 3.12/Linux offscreen Qt. Two independent local profiles
exercised actual message popups and later reminders, Enter-to-send without
another window, Shift+Enter newlines, recipient acknowledgements and snoozes,
assigned customer deadlines, Done/Delivered cancellation, full document sync,
offline restart/outbox recovery, conflicts, and copied-settings identity
isolation. Work notes stay out of Groq by default, including during their first
save and when earlier AI conversation history contains newly shared content.

Three real Qt office-server integration checks passed after merging the newer
Home/Famous Twins interface. API-owned business caches remain separate from
folder sync, including edits, logout, reminders, and colliding identifiers.
Mobile TypeScript validation and all 25 mobile tests passed. The Linux frozen
application built and started successfully; two packaged profiles connected
to a temporary shared directory, displayed actual employee-message popups,
and saved independent presentation receipts without auto-acknowledgement.
Native Windows SMB locking, actual workplace permissions, and Windows EXE
deployment were not tested in this cloud. Earlier platform results follow.

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
