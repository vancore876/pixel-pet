"""A stock catalogue with explicit spreadsheet mapping, review, and import."""
from __future__ import annotations

from PySide6.QtCore import Qt, QSignalBlocker, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLayout, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox,
    QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ui_style import apply_window_style

FIELDS = ("sku", "description", "quantity", "location", "reorder_point",
          "reorder_quantity", "target_stock", "supplier", "barcode",
          "alternate_part_number", "substitute_part_number", "retail_price", "cost_price")
LABELS = {"sku": "Part number / SKU", "description": "Description", "quantity": "Quantity on hand",
          "location": "Aisle / shelf / bin", "reorder_point": "Restock at or below",
          "reorder_quantity": "Restock quantity", "target_stock": "Target stock", "supplier": "Supplier", "barcode": "Barcode", "alternate_part_number": "AlternatePartNumber",
          "substitute_part_number": "SubstitutePartNumber", "retail_price": "Retail Price", "cost_price": "Cost Price"}
IMPORT_FIELDS = ("sku", "quantity", "location", "alternate_part_number", "substitute_part_number", "retail_price", "cost_price")
IMPORT_LABELS = {"sku": "Part Number", "quantity": "Quantity", "location": "Location"}
TABLE_FIELDS = ("sku", "description", "quantity", "location", "reorder_point", "reorder_quantity", "supplier", "barcode",
                "alternate_part_number", "substitute_part_number", "retail_price", "cost_price")
TABLE_LABELS = ("Part number", "Description", "Qty", "Location", "Restock at", "Order qty", "Supplier", "Barcode", "AlternatePartNumber", "SubstitutePartNumber", "Retail Price", "Cost Price")


def _label(text, name=""):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(True)
    if name:
        widget.setObjectName(name)
    return widget


def _button(text, callback, name=""):
    widget = QPushButton(text)
    widget.setAutoDefault(False)
    widget.setDefault(False)
    if name:
        widget.setObjectName(name)
    widget.clicked.connect(callback)
    return widget


class InventoryWindow(QDialog):
    home_requested = Signal()
    preference_requested = Signal(str, object)
    preferences_requested = Signal(dict)

    def __init__(self, service, settings, parent=None):
        super().__init__(parent)
        self.service, self.settings = service, settings
        self.setWindowTitle("Jeffery · Parts inventory")
        self.resize(1120, 760)
        self.setMinimumSize(760, 540)
        self._selected = None
        self._editor_dirty = False
        self._edit_revision = 0
        self._scope_stamp = self._current_scope()
        self._preview = None
        self._review_timer = QTimer(self)
        self._review_timer.setSingleShot(True)
        self._review_timer.setInterval(150)
        self._review_timer.timeout.connect(self._review)
        self._building = False
        self._scan_item = None
        self._scan_cached = False
        self._unknown_code = ""
        self._history_before = None
        self._history_sku = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        header = QHBoxLayout()
        header.addWidget(_label("Parts inventory", "pageHeading"), 1)
        self.scanner_button = _button("Scan barcode", self.show_scanner, "primary")
        header.addWidget(self.scanner_button)
        self.home_button = _button("Home", self.home_requested.emit)
        header.addWidget(self.home_button)
        layout.addLayout(header)
        self.scope = QComboBox()
        self.scope.addItem("Office inventory · shared through Work Chat", True)
        self.scope.addItem("This PC only · local stock list", False)
        self.scope.setCurrentIndex(0 if service.shared else 1)
        self.scope.currentIndexChanged.connect(self._scope_changed)
        layout.addWidget(self.scope)
        self.scope_hint = _label(service.scope_label, "subtitle")
        layout.addWidget(self.scope_hint)
        self.status = _label(service.status or "Loading inventory…", "hint")
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._build_stock()
        self._build_import()
        self._build_scanner()
        self._build_history()
        footer = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        footer.addWidget(self.progress, 1)
        self.cancel_button = _button("Cancel operation", service.cancel)
        self.cancel_button.hide()
        footer.addWidget(self.cancel_button)
        layout.addLayout(footer)
        service.changed.connect(self.refresh)
        service.status_changed.connect(self.status.setText)
        service.error.connect(self._error)
        service.busy_changed.connect(self._busy_changed)
        service.progress.connect(self._progress)
        service.preview_ready.connect(self._show_preview)
        service.import_ready.connect(self._show_plan)
        service.import_finished.connect(self._import_finished)
        service.scan_ready.connect(self._show_scan)
        service.movement_finished.connect(self._movement_finished)
        service.history_ready.connect(self._show_history)
        service.scope_reset.connect(self._clear_scanner)
        service.scope_reset.connect(self._clear_import)
        self.configure()
        self.refresh()

    @staticmethod
    def _scroll_page():
        page = QScrollArea()
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        page.setWidget(body)
        return page, layout

    def _build_stock(self):
        page, layout = self._scroll_page()
        metrics = QHBoxLayout()
        self.metrics = {}
        for key, label in (("item_count", "Parts"), ("total_quantity", "Units on hand"),
                           ("low_stock_count", "Need restocking")):
            card = QFrame()
            card.setObjectName("surface")
            stack = QVBoxLayout(card)
            stack.setContentsMargins(14, 10, 14, 12)
            value = _label("0", "metricValue")
            value.setMinimumHeight(38)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            stack.addWidget(value)
            stack.addWidget(_label(label, "hint"))
            self.metrics[key] = value
            metrics.addWidget(card, 1)
        layout.addLayout(metrics)
        actions = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a part, alternate or substitute number, description, location, or supplier")
        self.search.setClearButtonEnabled(True)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self.search.textChanged.connect(lambda: self._search_timer.start())
        self._search_timer.timeout.connect(self._filter)
        self.search.returnPressed.connect(self._filter)
        actions.addWidget(self.search, 1)
        self.low_stock = QCheckBox("Needs restocking")
        self.low_stock.toggled.connect(self._filter)
        actions.addWidget(self.low_stock)
        self.refresh_button = _button("Refresh", self.service.refresh)
        actions.addWidget(self.refresh_button)
        layout.addLayout(actions)
        self.stock_table = QTableWidget(0, len(TABLE_FIELDS))
        self.stock_table.setMinimumHeight(150)
        self.stock_table.setHorizontalHeaderLabels(TABLE_LABELS)
        self.stock_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.stock_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.stock_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.stock_table.setAlternatingRowColors(True)
        self.stock_table.verticalHeader().hide()
        self.stock_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.stock_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.stock_table.setColumnWidth(0, 135)
        self.stock_table.setColumnWidth(2, 90)
        self.stock_table.setColumnWidth(3, 150)
        self.stock_table.setColumnWidth(4, 100)
        self.stock_table.setColumnWidth(5, 100)
        self.stock_table.itemSelectionChanged.connect(self._select_item)
        layout.addWidget(self.stock_table, 1)
        self.result_hint = _label("", "hint")
        layout.addWidget(self.result_hint)
        editor = QFrame()
        editor.setObjectName("surface")
        editor_layout = QVBoxLayout(editor)
        self.edit_title = _label("Select a part to update its location or manual stock count", "sectionTitle")
        editor_layout.addWidget(self.edit_title)
        form = QGridLayout()
        self.editors = {}
        for index, field in enumerate(("quantity", "location", "reorder_point", "reorder_quantity", "barcode",
                                        "alternate_part_number", "substitute_part_number", "retail_price", "cost_price")):
            widget = QSpinBox() if field in ("quantity", "reorder_point", "reorder_quantity") else QLineEdit()
            widget.setMinimumHeight(36)
            if field in ("barcode", "alternate_part_number", "substitute_part_number"):
                widget.setMaxLength(128)
            if field in ("retail_price", "cost_price"):
                widget.setPlaceholderText("0.00")
            if isinstance(widget, QSpinBox):
                widget.setRange(0, 2**31 - 1)
                widget.setGroupSeparatorShown(True)
            widget.setEnabled(False)
            self.editors[field] = widget
            if isinstance(widget, QSpinBox):
                widget.valueChanged.connect(self._editor_changed)
            else:
                widget.textEdited.connect(self._editor_changed)
            column, row = index % 3, index // 3
            form.addWidget(_label(LABELS[field], "hint"), row * 2, column)
            form.addWidget(widget, row * 2 + 1, column)
        editor_layout.addLayout(form)
        row = QHBoxLayout()
        row.addWidget(_label("Counts are manual. Customer orders do not change inventory automatically.", "hint"), 1)
        self.save_button = _button("Save stock changes", self._save_item, "primary")
        self.save_button.setEnabled(False)
        row.addWidget(self.save_button)
        self.assign_barcode_button = _button("Use scanned barcode", self._assign_barcode)
        self.assign_barcode_button.setEnabled(False)
        row.addWidget(self.assign_barcode_button)
        editor_layout.addLayout(row)
        layout.addWidget(editor)
        self.tabs.addTab(page, "Stock & locations")

    def _build_import(self):
        page, layout = self._scroll_page()
        layout.addWidget(_label("Import an Excel or CSV stock list", "sectionTitle"))
        layout.addWidget(_label("Choose your file, confirm Part Number, Quantity and Location, then press Confirm import. "
            "Alternate/substitute part numbers and retail/cost prices are detected when present. Rows are checked automatically. Matching part numbers update existing stock; "
            "parts absent from the file remain unchanged.", "hint"))
        choose = QHBoxLayout()
        self.file_label = _label("No stock file selected", "hint")
        choose.addWidget(self.file_label, 1)
        self.choose_button = _button("Choose stock file…", self.choose_file)
        choose.addWidget(self.choose_button)
        layout.addLayout(choose)
        sheet_row = QHBoxLayout()
        sheet_row.addWidget(_label("Worksheet"))
        self.sheet = QComboBox()
        self.sheet.setEnabled(False)
        self.sheet.currentTextChanged.connect(self._sheet_changed)
        sheet_row.addWidget(self.sheet, 1)
        self.review_button = _button("Check file again", self._review)
        self.review_button.setEnabled(False)
        self.review_button.hide()
        layout.addLayout(sheet_row)
        self.include_zero = QCheckBox('Include zero-quantity parts')
        self.include_zero.setChecked(True)
        self.include_zero.toggled.connect(self._mapping_changed)
        layout.addWidget(self.include_zero)
        self.skip_invalid_quantities = QCheckBox('Skip rows with invalid quantities')
        self.skip_invalid_quantities.setToolTip('Import the valid rows and leave skipped parts unchanged. Negative, fractional, missing and oversized quantities are listed below.')
        self.skip_invalid_quantities.toggled.connect(self._mapping_changed)
        layout.addWidget(self.skip_invalid_quantities)
        mapping = QGridLayout()
        self.optional_import = QCheckBox('Include optional details')
        self.optional_import.setToolTip('Description, barcode, supplier and restock settings are optional.')
        self.optional_import.toggled.connect(self._optional_import_changed)
        self.optional_mapping = QWidget()
        optional_layout = QGridLayout(self.optional_mapping)
        optional_layout.setContentsMargins(0, 0, 0, 0)
        self.optional_mapping.hide()
        self.mapping = {}
        optional_index = 0
        for field in FIELDS:
            combo = QComboBox()
            combo.addItem("Not mapped", None)
            combo.setEnabled(False)
            combo.currentIndexChanged.connect(self._mapping_changed)
            self.mapping[field] = combo
            if field in IMPORT_FIELDS:
                index = IMPORT_FIELDS.index(field)
                column, row = index % 3, index // 3
                label = IMPORT_LABELS.get(field, LABELS[field])
                mapping.addWidget(_label(label), row * 2, column)
                mapping.addWidget(combo, row * 2 + 1, column)
            else:
                column, row = optional_index % 3, optional_index // 3
                optional_layout.addWidget(_label(LABELS[field]), row * 2, column)
                optional_layout.addWidget(combo, row * 2 + 1, column)
                optional_index += 1
        layout.addLayout(mapping)
        layout.addWidget(self.optional_import)
        layout.addWidget(self.optional_mapping)
        layout.addWidget(self.review_button)
        self.import_table = QTableWidget()
        self.import_table.setMinimumHeight(150)
        self.import_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.import_table.setAlternatingRowColors(True)
        self.import_table.verticalHeader().hide()
        self.import_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        layout.addWidget(self.import_table, 1)
        self.review_text = QPlainTextEdit()
        self.review_text.setReadOnly(True)
        self.review_text.setMaximumHeight(135)
        self.review_text.setPlaceholderText("Import results and row errors will appear here.")
        layout.addWidget(self.review_text)
        self.import_button = _button("Confirm import into office inventory", self.service.commit_import, "primary")
        self.import_button.setEnabled(False)
        layout.addWidget(self.import_button)
        self.tabs.addTab(page, "Import stock list")

    def _build_scanner(self):
        self.scanner_page, layout = self._scroll_page()
        layout.addWidget(_label("Scan a part", "sectionTitle"))
        layout.addWidget(_label("Click the barcode box, then scan with your USB scanner. Enter looks up the exact barcode or part number. "
            "Scanning never changes stock. Your scanner should send Enter after each code.", "hint"))
        scan_row = QHBoxLayout()
        self.scan_input = QLineEdit()
        self.scan_input.setObjectName("barcodeInput")
        self.scan_input.setPlaceholderText("Scan barcode or type exact part number, then press Enter")
        self.scan_input.setMaxLength(128)
        self.scan_input.setMinimumHeight(44)
        self.scan_input.returnPressed.connect(self._scan)
        self.scan_input.textEdited.connect(self._scan_edited)
        scan_row.addWidget(self.scan_input, 1)
        self.scan_button = _button("Look up", self._scan, "primary")
        scan_row.addWidget(self.scan_button)
        layout.addLayout(scan_row)
        self.scan_result = _label("Ready for a barcode.", "sectionTitle")
        self.scan_result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.scan_result)
        self.scan_detail = _label("", "hint")
        self.scan_detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.scan_detail)
        self.scan_stock_button = _button("Choose a part to assign this barcode…", self._choose_barcode_part)
        self.scan_stock_button.hide()
        layout.addWidget(self.scan_stock_button)
        form = QGridLayout()
        self.movement_kind = QComboBox()
        for label, kind in (("Receive stock", "receive"), ("Sale / stock out", "sale"),
                            ("Customer return", "return"), ("Physical count", "count")):
            self.movement_kind.addItem(label, kind)
        self.movement_kind.currentIndexChanged.connect(self._movement_kind_changed)
        self.movement_quantity = QSpinBox()
        self.movement_quantity.setRange(1, 2**31 - 1)
        self.movement_quantity.setValue(1)
        self.movement_quantity.setGroupSeparatorShown(True)
        self.movement_quantity_label = _label("Units to add")
        self.movement_reason = QLineEdit()
        self.movement_reason.setMaxLength(500)
        self.movement_reason.setPlaceholderText("Delivery, sale, return, or count explanation")
        self.movement_reference = QLineEdit()
        self.movement_reference.setMaxLength(200)
        self.movement_reference.setPlaceholderText("Invoice, delivery, or customer reference (optional)")
        for row, (label, widget) in enumerate(((_label("Stock action"), self.movement_kind),
                (self.movement_quantity_label, self.movement_quantity),
                (_label("Reason"), self.movement_reason), (_label("Reference"), self.movement_reference))):
            widget.setMinimumHeight(36)
            form.addWidget(label, row, 0)
            form.addWidget(widget, row, 1)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.movement_button = _button("Review stock change…", self._review_movement, "primary")
        actions.addWidget(self.movement_button)
        self.scan_history_button = _button("View this part’s history", self._part_history)
        actions.addWidget(self.scan_history_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.movement_status = _label("", "hint")
        self.movement_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.movement_status)
        self.retry_movement_button = _button("Check / retry original change…", self._retry_movement)
        self.retry_movement_button.hide()
        layout.addWidget(self.retry_movement_button)
        layout.addStretch(1)
        self.tabs.addTab(self.scanner_page, "Scan barcode")

    def _build_history(self):
        self.history_page = QWidget()
        layout = QVBoxLayout(self.history_page)
        layout.addWidget(_label("Stock movement history", "sectionTitle"))
        controls = QHBoxLayout()
        self.history_filter = QLineEdit()
        self.history_filter.setPlaceholderText("Exact part number, or leave blank for all parts")
        self.history_filter.setMaxLength(128)
        self.history_filter.returnPressed.connect(self._reload_history)
        controls.addWidget(self.history_filter, 1)
        self.history_refresh_button = _button("Load history", self._reload_history)
        controls.addWidget(self.history_refresh_button)
        layout.addLayout(controls)
        self.history_hint = _label("Load history to see who changed stock, when, and why.", "hint")
        layout.addWidget(self.history_hint)
        columns = ("When", "Part", "Action", "Before → after", "Employee", "Reason", "Reference", "Details")
        self.history_table = QTableWidget(0, len(columns))
        self.history_table.setHorizontalHeaderLabels(columns)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.verticalHeader().hide()
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.history_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.history_table.setColumnWidth(0, 175)
        self.history_table.setColumnWidth(3, 130)
        layout.addWidget(self.history_table, 1)
        self.history_more_button = _button("Load older movements", self._more_history)
        self.history_more_button.setEnabled(False)
        layout.addWidget(self.history_more_button)
        self.tabs.addTab(self.history_page, "Movement history")

    def show_scanner(self):
        self.tabs.setCurrentWidget(self.scanner_page)
        self.show()
        self.raise_()
        self.activateWindow()
        self.scan_input.setFocus(Qt.OtherFocusReason)
        self.scan_input.selectAll()

    def _scan_edited(self):
        self.service.invalidate_scan()
        self._scan_item = None
        self._scan_cached = False
        self._unknown_code = ""
        self.scan_result.setText("Press Enter to look up this code.")
        self.scan_detail.clear()
        self.scan_stock_button.hide()
        self._scanner_controls()

    def _scan(self):
        self._scan_item = None
        self._scan_cached = False
        self._unknown_code = ""
        self.scan_result.setText("Looking up part…")
        self.scan_detail.clear()
        self.scan_stock_button.hide()
        if not self.service.pending_movement:
            self.movement_status.clear()
        self._scanner_controls()
        self.service.scan_code(self.scan_input.text().strip())
        self.scan_input.selectAll()

    def _show_scan(self, result):
        self._scan_item = result.get("item")
        self._scan_cached = bool(result.get("cached"))
        self._unknown_code = ""
        if self._scan_item:
            item = self._scan_item
            self.scan_result.setText(f"{item['sku']} · {item.get('description', '')}")
            details = f"On hand: {item.get('quantity', 0):,}    Location: {item.get('location') or 'Not set'}\nBarcode: {item.get('barcode') or 'Not assigned'}"
            if self._scan_cached:
                details += f"\nOFFLINE · Cached stock saved {result.get('saved_at', 'at an unknown time')}. May be out of date; stock changes are disabled."
            else:
                details += "\nExact match · Review a stock action below, or scan the next part."
            self.scan_detail.setText(details)
        else:
            self.scan_result.setText("Part not found" if not self._scan_cached else "No cached part found")
            detail = str(result.get("error", "No exact barcode or part number matched."))
            if self._scan_cached:
                detail += "\nOFFLINE · Cached stock may be out of date. Reconnect and scan again."
            else:
                self._unknown_code = str(result.get("code", ""))
                detail += "\nFor an unassigned barcode, choose its part in Stock & locations, use the scanned barcode, then save the reviewed edit."
            self.scan_detail.setText(detail)
        self.scan_stock_button.setVisible(bool(self._unknown_code))
        self._scanner_controls()

    def _choose_barcode_part(self):
        self.tabs.setCurrentIndex(0)
        self.search.setFocus()
        self.search.selectAll()
        self.status.setText("Choose the matching part, click Use scanned barcode, review the Barcode field, then Save stock changes.")

    def _assign_barcode(self):
        if not self._selected or not self._unknown_code or not self.service.can_write:
            return
        self.editors["barcode"].setText(self._unknown_code)
        self._editor_dirty = True
        self.status.setText(f"Barcode {self._unknown_code} prepared for {self._selected['sku']}. Review it, then Save stock changes.")
        self.editors["barcode"].setFocus()

    def _movement_kind_changed(self):
        kind = self.movement_kind.currentData()
        self.movement_quantity.setMinimum(0 if kind == "count" else 1)
        self.movement_quantity_label.setText("Actual units on hand" if kind == "count" else
                                            "Units to remove" if kind == "sale" else "Units to add")
        self.movement_reason.setPlaceholderText("Required: why this count was corrected" if kind == "count" else
                                                "Delivery, sale, or return explanation (optional)")

    def _confirm(self, title, text):
        box = QMessageBox(QMessageBox.Question, title, text, QMessageBox.Yes | QMessageBox.Cancel, self)
        box.setTextFormat(Qt.PlainText)
        box.setDefaultButton(QMessageBox.Cancel)
        box.button(QMessageBox.Yes).setText("Confirm stock change")
        return box.exec() == QMessageBox.Yes

    def _review_movement(self):
        item = self._scan_item
        if not item or self._scan_cached or not self.service.can_write or self.service.busy:
            return
        kind, quantity = self.movement_kind.currentData(), self.movement_quantity.value()
        reason, reference = self.movement_reason.text().strip(), self.movement_reference.text().strip()
        before = item["quantity"]
        after = quantity if kind == "count" else before + (-quantity if kind == "sale" else quantity)
        if kind == "count" and not reason:
            self.movement_status.setText("Enter a reason before recording a physical count.")
            self.movement_reason.setFocus()
            return
        if after < 0 or after > 2**31 - 1:
            self.movement_status.setText("This change would put the stock count outside its allowed range. Check the quantity.")
            return
        text = (f"{self.movement_kind.currentText()}\nPart: {item['sku']} · {item.get('description', '')}\n"
                f"Quantity: {quantity:,}\nStock: {before:,} → {after:,}\n"
                f"Reason: {reason or '(none)'}\nReference: {reference or '(none)'}\n\n"
                f"Save this change to {'office inventory for all PCs' if self.service.shared else 'this PC'}?")
        generation = self.service._generation
        # The item object and payload remain the reviewed snapshot while the
        # modal dialog is open; a concurrent update is rejected by its revision.
        if self._confirm("Review stock change", text) and generation == self.service._generation:
            if self.service.record_movement(item, kind, quantity, reason, reference):
                self.movement_status.setText("Submitting reviewed stock change…")
                self._scanner_controls()

    def _movement_finished(self, result):
        if result.get("error"):
            if result.get("uncertain"):
                self.movement_status.setText("The result is not confirmed. Use Check / retry original change to safely check the SAME change. "
                    "Do not create a replacement transaction.\n" + result["error"])
            else:
                self._scan_item = None
                self.scan_result.setText("Scan again before changing stock")
                self.scan_detail.clear()
                self.movement_status.setText(("Stock changed on another PC. " if result.get("status") == 409 else "Change was not saved. ") +
                                             result["error"] + " Scan the part again and review its current quantity.")
        else:
            item, movement = result["item"], result["movement"]
            self.movement_status.setText(f"Saved {movement.get('kind', 'stock change')} for {item['sku']}: "
                f"{movement.get('before_quantity', 0):,} → {movement.get('after_quantity', item['quantity']):,}. Scan again for the next change.")
            # A success may arrive after a different barcode was scanned. Never
            # replace that different part with this transaction's response.
            if self._scan_item and self._scan_item.get("sku") == item.get("sku"):
                self._show_scan({"item": item, "cached": False})
                self._scan_item = None
            self.movement_reason.clear()
            self.movement_reference.clear()
            self.movement_quantity.setValue(1)
            if self._history_sku in ("", item["sku"]):
                self.service.load_history(self._history_sku)
        self._scanner_controls()

    def _retry_movement(self):
        payload = self.service.pending_movement
        if not payload or not self.service.movement_uncertain:
            return
        generation = self.service._generation
        if self._confirm("Check original stock change", f"Part: {payload['sku']}\nAction: {payload['kind']}\nQuantity: {payload['quantity']:,}\n"
                f"Reason: {payload.get('reason') or '(none)'}\nReference: {payload.get('reference') or '(none)'}\n\n"
                "Check the original request with its original ID. The server applies it at most once, even if the earlier response was lost.") and generation == self.service._generation:
            self.service.retry_movement()
            self._scanner_controls()

    def _scanner_controls(self):
        writable = bool(self._scan_item and not self._scan_cached and self.service.can_write and not self.service.busy)
        for widget in (self.movement_kind, self.movement_quantity, self.movement_reason,
                       self.movement_reference, self.movement_button):
            widget.setEnabled(writable)
        self.scan_history_button.setEnabled(bool(self._scan_item and not self._scan_cached))
        self.retry_movement_button.setVisible(bool(self.service.pending_movement and self.service.movement_uncertain))
        self.retry_movement_button.setEnabled(not self.service.busy)
        self.assign_barcode_button.setEnabled(bool(self._selected and self._unknown_code and self.service.can_write and not self.service.busy))

    def _part_history(self):
        if self._scan_item:
            self.history_filter.setText(self._scan_item["sku"])
            self.tabs.setCurrentWidget(self.history_page)
            self._reload_history()

    def _reload_history(self):
        self._history_sku = self.history_filter.text().strip()
        self._history_before = None
        self.history_table.setRowCount(0)
        self.history_more_button.setEnabled(False)
        self.history_hint.setText("Loading stock history…")
        self.service.load_history(self._history_sku)

    def _more_history(self):
        if self._history_before is not None:
            self.history_more_button.setEnabled(False)
            self.service.load_history(self._history_sku, self._history_before)

    def _show_history(self, result):
        if result.get("error"):
            self.history_hint.setText(result["error"])
            self.history_more_button.setEnabled(self._history_before is not None)
            return
        if not result.get("append"):
            self.history_table.setRowCount(0)
        for movement in result["movements"]:
            row = self.history_table.rowCount()
            self.history_table.insertRow(row)
            changes = "; ".join(f"{key}: {value.get('before', '')} → {value.get('after', '')}"
                                for key, value in movement.get("changes", {}).items() if isinstance(value, dict))
            values = (movement.get("created_at", ""), movement.get("sku", ""), movement.get("kind", ""),
                      f"{movement.get('before_quantity', 0):,} → {movement.get('after_quantity', 0):,}",
                      movement.get("actor", ""), movement.get("reason", ""), movement.get("reference", ""), changes)
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setToolTip(str(value))
                self.history_table.setItem(row, column, cell)
        self._history_before = result.get("next_before_id") if result.get("has_more") else None
        self.history_more_button.setEnabled(self._history_before is not None)
        self.history_hint.setText(f"{self.history_table.rowCount():,} movements shown · newest first. "
            "Imports, manual edits, receipts, sales, returns, and counts appear here.")

    def _clear_scanner(self):
        self._scan_item = None
        self._scan_cached = False
        self._unknown_code = ""
        self._history_before = None
        self._history_sku = ""
        self.scan_input.clear()
        self.scan_result.setText("Ready for a barcode.")
        self.scan_detail.clear()
        self.scan_stock_button.hide()
        self.movement_status.clear()
        self.movement_reason.clear()
        self.movement_reference.clear()
        self.movement_quantity.setValue(1)
        self.history_filter.clear()
        self.history_table.setRowCount(0)
        self.history_hint.setText("Load history to see who changed stock, when, and why.")
        self.history_more_button.setEnabled(False)
        self._scanner_controls()

    def configure(self):
        apply_window_style(self, self.settings)
        self.scope_hint.setText(self.service.scope_label)
        with QSignalBlocker(self.scope):
            self.scope.setCurrentIndex(0 if self.service.shared else 1)
        self.import_button.setText("Confirm import into office inventory" if self.service.shared else "Confirm import on this PC")
        self._busy_changed(self.service.busy)

    def _scope_changed(self):
        value = bool(self.scope.currentData())
        self.preference_requested.emit("inventory_shared", value)
        self.preferences_requested.emit({"inventory_shared": value})
        self.service.set_shared(value)
        self._clear_import()
        self.configure()

    def refresh(self):
        scope = self._current_scope()
        if scope != self._scope_stamp:
            self._scope_stamp = scope
            self._selected, self._editor_dirty = None, False
            self._clear_import()
        selected = self._selected.get("sku") if self._selected else None
        self.scope_hint.setText(self.service.scope_label)
        for key, value in self.metrics.items():
            number = self.service.summary.get(key, 0)
            value.setText(f"{number:,}" if isinstance(number, (int, float)) else str(number))
        self._building = True
        self.stock_table.setRowCount(len(self.service.rows))
        for index, row in enumerate(self.service.rows):
            for column, field in enumerate(TABLE_FIELDS):
                value = row.get(field, "")
                text = f"{value:,}" if isinstance(value, (int, float)) else str(value or "")
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, row)
                if field in ("quantity", "reorder_point", "reorder_quantity"):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.stock_table.setItem(index, column, item)
            if row.get("sku") == selected:
                self.stock_table.selectRow(index)
        self._building = False
        self._select_item()
        count = getattr(self.service, "match_count", len(self.service.rows))
        updated = self.service.summary.get("last_import_at", "")
        self.result_hint.setText(f"Showing {len(self.service.rows):,} of {count:,} matching parts" +
            (f" · Last stock import: {updated}" if updated else " · No stock import yet"))
        self._busy_changed(self.service.busy)

    def _filter(self):
        self.service.search(self.search.text(), self.low_stock.isChecked())

    def _select_item(self):
        if self._building:
            return
        items = self.stock_table.selectedItems()
        selected = dict(items[0].data(Qt.UserRole)) if items else None
        same = selected and self._selected and selected.get("sku") == self._selected.get("sku")
        if same and self._editor_dirty:
            return
        self._editor_dirty = False
        self._selected = selected
        self._edit_revision = self.service.summary.get("revision", 0)
        enabled = bool(self._selected and self.service.can_write and not self.service.busy)
        self.edit_title.setText((self._selected.get("sku", "") + " · " + self._selected.get("description", ""))
            if self._selected else "Select a part to update its location or manual stock count")
        self._building = True
        for field, widget in self.editors.items():
            widget.setEnabled(enabled)
            value = self._selected.get(field, 0 if isinstance(widget, QSpinBox) else "") if self._selected else (0 if isinstance(widget, QSpinBox) else "")
            if isinstance(widget, QSpinBox):
                widget.setValue(int(value or 0))
            else:
                widget.setText(str(value or ""))
        self._building = False
        self.save_button.setEnabled(enabled)
        self._scanner_controls()

    def _save_item(self):
        if not self._selected:
            return
        row = {key: self._selected[key] for key in FIELDS if key in self._selected}
        for key, widget in self.editors.items():
            row[key] = widget.value() if isinstance(widget, QSpinBox) else widget.text().strip()
        self.service.update_item(row, expected_revision=self._edit_revision)

    def _editor_changed(self):
        if not self._building and self._selected:
            self._editor_dirty = True

    def _current_scope(self):
        user = self.service.user
        return self.service.shared, self.service.origin, user.get("id") if user else None

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose an auto-parts stock list", "",
            "Stock spreadsheets (*.xlsx *.xlsm *.xls *.csv);;Excel (*.xlsx *.xlsm *.xls);;CSV (*.csv)")
        if path:
            self.load_file(path)

    def load_file(self, path):
        self._clear_import()
        self.file_label.setText(str(path).replace("\\", "/").rsplit("/", 1)[-1])
        self.service.inspect_file(path)

    def _clear_import(self):
        self._review_timer.stop()
        self._preview = None
        self.import_button.setEnabled(False)
        self.review_button.setEnabled(False)
        self.review_text.clear()
        self.import_table.setRowCount(0)
        self.file_label.setText("No stock file selected")
        self.sheet.setEnabled(False)
        for combo in self.mapping.values():
            combo.setEnabled(False)

    def _show_preview(self, preview):
        self._preview = preview
        self.file_label.setText(preview.source or self.file_label.text())
        self._building = True
        with QSignalBlocker(self.sheet):
            self.sheet.clear()
            self.sheet.addItems(preview.sheet_names or ["CSV"])
            if preview.sheet:
                self.sheet.setCurrentText(preview.sheet)
        self.sheet.setEnabled(len(preview.sheet_names) > 1)
        for field, combo in self.mapping.items():
            with QSignalBlocker(combo):
                combo.clear()
                combo.addItem("Not mapped", None)
                for index, header in enumerate(preview.headers):
                    combo.addItem(header or f"Column {index + 1}", index)
                selected = preview.mapping.get(field) if field in IMPORT_FIELDS or self.optional_import.isChecked() else None
                combo.setCurrentIndex(combo.findData(selected) if selected is not None else 0)
            combo.setEnabled(True)
        self.import_table.setColumnCount(len(preview.headers))
        self.import_table.setHorizontalHeaderLabels(preview.headers)
        self.import_table.setRowCount(len(preview.sample_rows))
        for row_index, row in enumerate(preview.sample_rows):
            for column in range(len(preview.headers)):
                value = row[column] if column < len(row) else ""
                self.import_table.setItem(row_index, column, QTableWidgetItem("" if value is None else str(value)))
        self._building = False
        self.review_text.setPlainText(f"{preview.total_rows:,} rows found. Confirm Part Number, Quantity and Location. Checking the file automatically...")
        self._mapping_changed()

    def _optional_import_changed(self, enabled):
        self.optional_mapping.setVisible(enabled)
        self.review_button.setVisible(enabled)
        for field, combo in self.mapping.items():
            if field not in IMPORT_FIELDS:
                with QSignalBlocker(combo):
                    selected = self._preview.mapping.get(field) if enabled and self._preview else None
                    combo.setCurrentIndex(combo.findData(selected) if selected is not None else 0)
        self._mapping_changed()

    def _mapping_changed(self):
        if self._building:
            return
        self._review_timer.stop()
        self.import_button.setEnabled(False)
        self.service.plan = None
        required = all(self.mapping[field].currentData() is not None for field in ("sku", "quantity"))
        ready = bool(self._preview and required and not self.service.busy and self.service.can_write)
        self.review_button.setEnabled(ready)
        if ready:
            self._review_timer.start()
        elif self._preview and not required:
            self.review_text.setPlainText('Choose the Part Number and Quantity columns. Location can be left unmapped if it is not in this file.')

    def _sheet_changed(self, sheet):
        if self._preview and sheet and sheet != self._preview.sheet:
            self._clear_import()
            self.service.inspect_file(self.service._file, sheet=sheet)

    def _review(self):
        self._review_timer.stop()
        mapping = {field: combo.currentData() for field, combo in self.mapping.items() if combo.currentData() is not None}
        self.service.prepare_import(mapping, sheet=self._preview.sheet if self._preview else None,
                                    include_zero=self.include_zero.isChecked(),
                                    skip_invalid_quantities=self.skip_invalid_quantities.isChecked())

    def _show_plan(self, result):
        plan, counts = result["plan"], result["preview"]
        fields = FIELDS if self.optional_import.isChecked() else tuple(field for field in IMPORT_FIELDS
            if field in ("sku", "quantity", "location") or field in plan.mapping)
        self.import_table.setColumnCount(len(fields))
        self.import_table.setHorizontalHeaderLabels([IMPORT_LABELS.get(field, LABELS[field]) for field in fields])
        self.import_table.setRowCount(min(len(plan.rows), 25))
        for row_index, row in enumerate(plan.rows[:25]):
            for column, field in enumerate(fields):
                value = row.get(field, "")
                self.import_table.setItem(row_index, column, QTableWidgetItem("" if value is None else str(value)))
        rows = [f"{len(plan.rows):,} valid part rows · {counts.get('created', counts.get('inserted', 0)):,} new · "
                f"{counts.get('updated', 0):,} existing updates."]
        if plan.errors:
            rows.append("Fix these errors in the spreadsheet, then choose the file again:")
            rows.extend(plan.errors[:50])
            if not self.skip_invalid_quantities.isChecked() and any(error.split(': ', 1)[-1].startswith('Quantity ') for error in plan.errors):
                rows.append('To import the valid rows now, enable Skip rows with invalid quantities above.')
        warnings = [warning for warning in plan.warnings
                    if self.optional_import.isChecked() or not warning.startswith('Columns not mapped:')]
        if warnings:
            rows.append("Warnings:")
            rows.extend(warnings[:25])
        self.review_text.setPlainText("\n".join(rows))
        self.import_button.setEnabled(bool(plan.valid and self.service.can_write and not self.service.busy))

    def _import_finished(self, result):
        self._editor_dirty = False
        self.import_button.setEnabled(False)
        self.review_text.setPlainText("Stock import saved. " +
            f"{result.get('created', 0):,} new parts, {result.get('updated', 0):,} updated, {result.get('unchanged', 0):,} unchanged.")
        self.tabs.setCurrentIndex(0)

    def _error(self, message):
        self.status.setText(str(message))
        if self.tabs.currentIndex() == 1:
            self.review_text.setPlainText(str(message))

    def _progress(self, value, message):
        self.progress.setValue(max(0, min(int(value), 100)))
        self.progress.setFormat(str(message) + " %p%")

    def _busy_changed(self, busy):
        self.progress.setVisible(bool(busy))
        self.cancel_button.setVisible(bool(busy))
        self.choose_button.setEnabled(not busy and self.service.can_write)
        self.scope.setEnabled(not busy)
        self.optional_import.setEnabled(not busy)
        self.include_zero.setEnabled(not busy)
        self.skip_invalid_quantities.setEnabled(not busy)
        self.sheet.setEnabled(not busy and bool(self._preview and len(self._preview.sheet_names) > 1))
        for combo in self.mapping.values():
            combo.setEnabled(not busy and self._preview is not None)
        self.review_button.setEnabled(not busy and self.service.can_write and bool(self._preview) and
            all(self.mapping[field].currentData() is not None for field in ("sku", "quantity")))
        self.import_button.setEnabled(not busy and self.service.can_write and bool(self.service.plan and self.service.plan.valid))
        self.save_button.setEnabled(not busy and self.service.can_write and self._selected is not None)
        for widget in self.editors.values():
            widget.setEnabled(not busy and self.service.can_write and self._selected is not None)
        self._scanner_controls()

    def showEvent(self, event):
        super().showEvent(event)
        self.service.refresh()

    def closeEvent(self, event):
        self.hide()
        event.ignore()

    def hideEvent(self, event):
        self.service.invalidate_scan()
        self.service._history_sequence += 1
        self._scan_item = None
        self._scanner_controls()
        super().hideEvent(event)

    def shutdown(self):
        self._review_timer.stop()
        self._search_timer.stop()
        self.hide()
