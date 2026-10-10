# Famous Twins auto-parts workspace

Start at **Jeffery Home**. Choose **New order**, **New checklist**, or **Write a
note**, or use the sidebar for **Orders & quotes**, **Checklists**, **Writing**,
and **Schedule**. Open a business summary count for **Overview**. The **Files**
page holds PDF/web imports, and Jeffery's advice remains available alongside
the task pages.

Home opens at normal startup unless its setting is disabled or **Launch with
monitor hidden** is enabled. Click the tray icon or double-click the buddy to
return to it. Each main window also has a **Home** button. Closing Home leaves
Jeffery running; use the tray menu to exit.

Choose **Settings → Appearance → Workspace appearance** for Light, Dark, or
Follow Windows, then Apply. Workspace appearance is separate from desktop
monitor themes and buddy colors. Settings use a sidebar and keep edits as a
draft until Apply; Cancel keeps the saved preferences.

## Find and edit work

Home search finds saved work by customer, part, reference, or text. Within the
workspace, the saved-entry browser follows the selected page: orders/quotes,
checklists, or writing. Choose an entry to edit it, or use the page's creation
button. **Hide entries** gives the editor more room; **Show entries** brings
the browser back. Smaller windows keep the task page usable by collapsing the
entry browser.

Use **Save changes** or **Ctrl+S** to keep your edits. **More → Delete entry**
and **More → Reload saved entry** hold the less frequent actions. When a shared
edit fails, **Reload saved** also appears beside the save controls. Your
unsaved draft stays available for review; copy any needed edits before
discarding it to reload another coworker's version.

## Connect the office team

The main Windows PC runs **Start_Work_Server.bat**. Coworkers open
**Start_Work_Buddy.bat** from the read-only shared project folder; each Windows
profile keeps its own settings, writing, memory, and Python environment.
See [Work Chat setup](WORK_CHAT.md) for the share, firewall, and server steps.

On each workstation, open **Home → Coworkers**, enter the confirmed
server IP (proposed **192.168.50.194**), and create or sign in to that coworker's
own username and password. Wait for the workspace to show **Connected to your
team**. The same login enables team chat, direct messages, and shared
orders/checklists; there is no second business login. The separate **Ask
Jeffery** page is the optional AI assistant, whose key is configured in
**AI setup**.

Every authenticated coworker can read, edit, complete, or delete every shared
order and checklist. These business records are not private direct messages.
The server stores the authoritative records in the same SQLite database as
accounts and chat. Desktop workstations poll for changes approximately every
2.5 seconds while signed in. The browser interface provides chat; use desktop
Jeffery for the business workspace.

Writing and imported full documents remain local. Old local orders/checklists
are not uploaded just by signing in: open one and explicitly choose **Save
changes** to publish it. Once published, it is visible to all signed-in coworkers.
New orders and checklists need the server connection and login to save. Explicit
status updates, such as Done, also save and publish an existing local entry.

If another coworker changes an entry you are editing, saving your older version
shows a conflict instead of replacing their work. Your unsaved editor draft
stays in the window. Copy any changes you want to keep, choose **Reload saved**,
and choose **Discard** at the unsaved-change prompt to review the current
server version. Reapply the needed changes and save. A failed or interrupted
save is not a confirmed save; check the latest server version before retrying.

The selected office setup uses HTTP on port **8765**, without certificates.
Passwords, session tokens, chat, and business data travel unencrypted over the
office network. Keep the service on the trusted office LAN. Verified HTTPS is
available later through the setup guide.

## Writing

Use **Home → Write a note** or **Writing → + New writing** for notes, ideas,
reference text, and documents. This page keeps the text editor separate from
customer fields and checklist controls. **Save changes** or **Ctrl+S** saves
locally. **Ctrl+N** starts a new writing entry.

Search finds saved text and business details such as customers, references,
vehicle registrations, VINs, part numbers, suppliers, and bins. The entry filter
can show writing, checklists, orders/quotes, open entries, or completed entries.
Use **Files** to review PDF or webpage text before saving it. Full
documents retain their existing paged reader and complete ZIP backup support.

## Checklists

Use **Home → New checklist** or **Checklists → + New checklist** for operational
tasks. This page has its own task table,
quantities, completion checkboxes, progress bar, and checklist notes; it does
not show the order-pricing table.

Choose **Opening**, **Closing**, or **Parts handover**, then **Use template**.
Review and adapt the suggested tasks to the shop's procedures. Existing
matching task descriptions are not added twice. Check tasks as they are done
and choose **Save changes** to share the change with coworkers.

Each entry supports up to 100 rows. Completing a task does not adjust inventory
or record a payment. Use **Done** for the whole checklist when it is finished.

## Orders

Use **Home → New order** or **Orders → + New order**. Start with the customer,
contact, and reference, then choose **Order** or **Quote** and **Normal** or
**Urgent** priority. Expand **Vehicle & pickup details** when you need vehicle/
engine details, registration, a manually entered VIN/chassis number, or a
pickup deadline.

The parts table records description, quantity, part number, supplier, shelf/bin,
unit price, and manual stock status. Add a part with the fields below the table;
enable **Part numbers, supplier, and bin** to show the optional sourcing fields
and table columns. Edit saved rows directly and save the entry. Collapsing
these details keeps their values. Quantities range from 1 to 9,999.
The stock choices are **Check Stock**, **In Stock**, **To Order**, **Ordered**,
and **Picked**. These are staff-entered labels, not a connection to a supplier
or live inventory system. A VIN is stored for reference; Jeffery does not decode
it or guarantee vehicle fitment. Confirm fitment in the appropriate catalogue.

All amounts are **JMD**. Enter plain amounts such as `1250` or `1250.50`, with
no currency symbol or grouping comma and at most two decimal places. Prices
and payments use integer cents, so totals do not introduce floating-point
rounding. Unit prices support up to JMD 10,000,000.00. **Payment received JMD**
is the cumulative amount staff have recorded for that order, not an additional
payment each time you save.

The total is quantity × unit price across all parts. Checking a part does not
remove its charge. Balance is the remaining amount after the recorded payment;
overpayment appears as credit. No tax, discount, automatic payment collection,
or accounting-ledger calculation is included.

**Quotes** remain searchable in customer history and show their own amounts,
but are excluded from open-order counts, outstanding receivables, and sourcing
requests. Change Type to **Order** and save when the customer confirms.

Status choices remain **New**, **Preparing**, **Ready**, **Delivered**, and
**Cancelled**. Ready means awaiting pickup. Delivered, Cancelled, or Done stops
that order's reminders; restoring a completed order returns it to New.

Enable **Pickup deadline** for the promised collection time. **Copy pickup
summary** copies the current editor's customer, vehicle, parts, quantities,
status, pickup time, total, payment, and balance to the clipboard. Review and
save changes before sharing the summary; copying does not send a message or
create an invoice.

## Schedule

Select an entry, then use **Schedule** to set a timed reminder, repeat interval,
or quick time such as **In 15 min**, **In 1 hour**, or **Tomorrow 9 AM**. Save the
entry to apply it. The agenda lists upcoming and overdue work. Jeffery must be
running, with note reminders enabled, to show reminders.

Pickup deadlines and reminder times are separate. **Remind at pickup** copies
the order deadline to Schedule. **Schedule follow-up**
sets a reminder for one hour from now; adjust it as needed and save. These
reminders prompt staff to follow up; they do not call or message customers.
Schedules saved on shared orders/checklists are shared business fields and can
appear on other signed-in workstations.

## Overview

The overview shows open, ready, overdue, and urgent orders, plus outstanding JMD
balances and the number of active quotes. Select a count to inspect its order
queue, and open a row to review the record. **Famous Twins → Today's Business
Brief** opens the overview and has Jeffery read a short summary of saved work.

**Parts to source** groups unchecked parts marked Check Stock, To Order, or
Ordered from active orders. It retains the relevant customer/order details and
sums requested quantities. Quotes, completed/cancelled/delivered orders, checked
parts, In Stock parts, and Picked parts are excluded. The count represents
requested units needing a sourcing check, not stock on hand.

**Customer history** searches saved orders and quotes by customer/contact,
reference, vehicle, registration, VIN, or title. It includes completed records
and displays their totals and balances. Double-click a row to open the order.

## Interactions and animations

Open **Play with Jeffery → At the Parts Counter** for six desktop animations:
**Check stock**, **Scan a part**, **Pack an order**, **Turn a wrench**, **High
five**, and **Coffee break**. Robot, cat, and knight all support these locally
drawn poses. Desktop Jeffery has 55 animation states; the mobile app retains its
existing 49-state layout.

With business mode enabled, saving an order triggers the scan pose, saving an
order marked Ready triggers packing, and saving a checklist triggers a stock-check pose.
These are visual reactions to saved work; they do not scan a barcode, reserve
stock, or perform a physical business operation. Existing quiet, pause, and
low-power controls still apply. Groq greetings/advice remain optional and follow
the existing saved-note sharing controls.

## Backups and limits

Back up the main server database to preserve shared orders/checklists,
revisions, deletions, accounts, and chat together. Stop the server and copy the
complete `%LOCALAPPDATA%\PixelSystemBuddy\work-server` folder under the Windows
account running it, including any SQLite WAL/SHM files. Keep it private and
restart the server after the copy. Restore while the server is stopped.

Desktop **Backup → Desktop full backup (.zip)** retains the local notebook and
loaded business records; **Export .txt** includes readable order details and JMD
totals. Neither replaces a server database backup. Importing a notebook does
not automatically publish its local orders to the office. Mobile small-note
transfers preserve the richer order metadata in JSON, but the current mobile
app does not provide the new shared business workflow or rich order editor.

The shared notebook supports up to 2,000 undeleted entries; completed entries
still use space until deleted. Each workstation also retains its existing
2,000-entry local notebook limit, including any loaded shared entries. There
is no offline business-save queue, per-order permission system, live inventory
integration, or automatic customer messaging.
