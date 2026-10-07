# Jeffery 6.0 verification

The 500 MB PDF update passed 127 desktop unit tests and the import-window GUI
workflow. A valid synthetic PDF exactly 500 × 1024 × 1024 bytes was imported
without a whole-file read; one byte over the limit was rejected before parsing.
Checks also cover 121-page documents and cancellation during parser I/O with
the file handle closed. The saved-text preview limit remains 120,000 characters.

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
