# Parts inventory

Open **Home → Inventory**. The default is one office stock list shared through
the Work Chat server. Sign in through **Coworkers** before importing or editing
office stock. All signed-in employees see the same saved quantities and locations.
The **This PC only** option keeps a separate local list.

Update and restart the office server as well as the desktop clients for these
features. Keep its database on the server's local disk and retain your existing
database when updating. Daily verified office backups and an IT **Back up now**
action are described in [WORK_CHAT.md](WORK_CHAT.md#office-backups-and-recovery).

## Import an Excel stock list

1. Choose **Import stock list**, then select an `.xlsx`, `.xlsm`, `.xls`, or
   `.csv` file. Select the worksheet when the workbook has several sheets.
2. Confirm **Part Number**, **Quantity**, and **Location**. Jeffery also detects
   **AlternatePartNumber**, **SubstitutePartNumber**, **Retail Price**, and
   **Cost Price** when those columns are present, and checks the file
   automatically. Correct a column choice only
   if its heading was not recognized. Location can be left unmapped when absent.
3. Press **Confirm import**. There is no separate Review step. Invalid quantities
   or duplicate part numbers are shown before confirmation is enabled. Office
   imports become visible together after the server confirms the complete batch.

**Include zero-quantity parts** is checked by default, so parts with no stock can
still be imported and found in the catalogue. Uncheck it to skip those rows;
negative or missing quantities remain invalid.

If quantity errors block the file, enable **Skip rows with invalid quantities**.
Jeffery rechecks automatically and lets you confirm the valid rows. Skipped rows
are counted and listed; their existing stock stays unchanged. Quantities are never
silently rounded or replaced with zero. Other errors, such as duplicate part
numbers or invalid prices, still need correction.

Prices accept nonnegative amounts with up to two decimal places, including zero.
They are saved exactly to two decimal places. Existing databases gain the new
fields automatically without replacing stock. Update and restart both the office
server and desktop clients before sharing imports with the new fields.

Description, barcode, supplier, and restock settings are not required. Select
**Include optional details** only when you want to import those extra columns;
otherwise existing values stay unchanged.

| Field | Meaning |
| --- | --- |
| Part number / SKU | Required unique item code; use Excel text cells to retain leading zeros |
| Quantity | Required current quantity, as a nonnegative whole number; zero is allowed |
| AlternatePartNumber | Optional alternate part reference; stored as text to preserve leading zeros |
| SubstitutePartNumber | Optional substitute part reference; stored as text to preserve leading zeros |
| Retail Price | Optional retail amount, up to two decimal places |
| Cost Price | Optional purchase cost, up to two decimal places |
| Description | Item name or description |
| Bin location | Shelf, aisle, rack, room, or other storage location |
| Reorder point | Quantity at or below which the item needs restocking |
| Reorder quantity | Minimum suggested order quantity |
| Target stock | Desired quantity after restocking |
| Supplier | Supplier name |
| Barcode | Optional unique scanner code; Barcode, UPC, EAN, and GTIN columns can be mapped here |

Imports update matching part numbers and add new ones. Unmapped optional fields
on existing items are preserved, and items absent from the spreadsheet remain
in the inventory. A concurrent stock change requires a fresh review rather than
silently overwriting another employee's work. Part numbers match without regard
to letter case; duplicate codes in one import are rejected.
Keep barcodes, part numbers, alternate numbers and substitute numbers as text in Excel to preserve leading zeros. Inventory search also finds alternate and substitute numbers.
An import cannot recover zeros that Excel has already removed.

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

## USB barcode scanning and stock movements

1. Open **Home → Scan barcode**, or **Inventory → Scan barcode**. Click the
   barcode box and scan with a USB scanner configured to type its code and
   press Enter. You can also type the exact barcode or part number and press
   Enter. Leading zeros are retained; a scan only looks up the item.
2. Check the description, shelf/bin location, and quantity. An unknown or
   ambiguous code does not select a guessed item. For a new barcode, choose
   **Choose a part to assign this barcode…**, select its existing stock item,
   use **Use scanned barcode**, then review and **Save stock changes**.
3. Choose **Receive stock**, **Sale / stock out**, **Customer return**, or
   **Physical count**. Receive and return add units; sale removes units;
   physical count sets the actual total and requires a reason. Add an optional
   invoice/delivery reference.
4. Choose **Review stock change…**, check the before/after quantities, then
   **Confirm stock change**. Cancel leaves stock unchanged. Scan again before
   recording the next movement.

Shared stock updates use the item revision you reviewed. If another employee
changes it first, refresh/scan again and review the new count. Quantities cannot
go below zero. A connection failure can leave the result uncertain: use
**Check / retry original change…** while the same session is open. It reuses the
original request identifier, so an already-saved movement is returned without
applying it twice. This pending retry is not saved across app restarts or
sign-out; check **Movement history** before entering a replacement change.

History shows quantities before and after, the movement, employee, time, reason,
and reference. Imports and manual item edits are audited too. Filter by exact
part number and use **Load older movements** for more entries. All employees
share the office history; **This PC only** has its own local history. Cached
office lookups stay available when disconnected and are labelled as snapshots;
stock changes require a live server connection.

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
shared list. Exact lookups use `GET /api/inventory/scan?code=...`; individual
movements use `POST /api/inventory/movements` with the reviewed item revision
and a unique request identifier. History is available through
`GET /api/inventory/movements`. Build and authorize that automation separately
when its data source and update rules are known.
