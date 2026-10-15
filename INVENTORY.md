# Parts inventory

Open **Home → Inventory**. The default is one office stock list shared through
the Work Chat server. Sign in through **Coworkers** before importing or editing
office stock. All signed-in employees see the same saved quantities and locations.
The **This PC only** option keeps a separate local list.

Update and restart the office server as well as the desktop clients for these
features. Keep its database on the server's local disk and retain your existing
database when updating. Back it up with the server stopped, as described in
[WORK_CHAT.md](WORK_CHAT.md).

## Import an Excel stock list

1. Choose **Import stock list**, then select an `.xlsx`, `.xlsm`, `.xls`, or
   `.csv` file. Select the worksheet when the workbook has several sheets.
2. Match the spreadsheet columns to the fields below. Jeffery suggests matches,
   and lets you correct them before reading the complete list.
3. Review the proposed new and updated items and any row errors. Stock has not
   changed yet. Correct duplicate part numbers or invalid quantities in the file.
4. Confirm the import. Office imports become visible together after the server
   confirms the complete batch; uploading a partial batch does not change stock.

| Field | Meaning |
| --- | --- |
| Part number / SKU | Required unique item code; use Excel text cells to retain leading zeros |
| Quantity | Required current quantity, as a nonnegative whole number |
| Description | Item name or description |
| Bin location | Shelf, aisle, rack, room, or other storage location |
| Reorder point | Quantity at or below which the item needs restocking |
| Reorder quantity | Minimum suggested order quantity |
| Target stock | Desired quantity after restocking |
| Supplier | Supplier name |

Imports update matching part numbers and add new ones. Unmapped optional fields
on existing items are preserved, and items absent from the spreadsheet remain
in the inventory. A concurrent stock change requires a fresh review rather than
silently overwriting another employee's work. Part numbers match without regard
to letter case; duplicate codes in one import are rejected.

Each import supports up to 100,000 data rows, 100 columns, and a 50 MiB source
file; the combined inventory also supports 100,000 items. Workbook contents are
read as values: formulas and macros are not executed. Save calculated values
from Excel before importing a workbook that uses formulas.

## Find items and plan restocking

Search by part number, description, location, or supplier. **Needs restocking**
shows items whose quantity is at or below their reorder point. The suggested
quantity uses the larger of the configured reorder quantity or the amount
needed to reach target stock. Review supplier orders yourself.

Select an item to update its location, quantity, or restock settings explicitly.
Updates retain the revision you opened, so a stale editor cannot overwrite a
newer count. Completing customer orders or ticking checklist items does not
automatically deduct stock.

## Jeffery's inventory memory

Stock is durable structured data, separate from personal likes and dislikes.
For questions in **Ask Jeffery**, enable **Settings → Documents & memory → Allow
Groq to use matching inventory records**. Jeffery then receives a small selection
of matching items or items needing restocking, including their saved locations
and update times. This is a separate choice from sharing notebook notes.

Without this option, the inventory search and restock view still work locally.
An offline office cache is marked as a saved snapshot and is read-only. Signing
out clears the visible office list and pending AI context; another account uses
its own cache. No raw workbook, complete stock list, password, or recovery code
is automatically sent to Groq.

## Later stock automation

This update does not connect to Automate or infer live sales/stock changes.
The authenticated inventory API provides a future connection point. A stock
script can read `/api/inventory`, create an import stage with the current
revision at `/api/inventory/imports`, upload bounded row batches, and explicitly
commit the stage. Revision checks and idempotent commit responses protect the
shared list. Build and authorize that automation separately when its data source
and update rules are known.
