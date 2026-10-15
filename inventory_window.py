"""A stock catalogue with explicit spreadsheet mapping, review, and import."""
from __future__ import annotations

from PySide6.QtCore import Qt, QSignalBlocker, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox,
    QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ui_style import apply_window_style

FIELDS = ("sku", "description", "quantity", "location", "reorder_point",
          "reorder_quantity", "target_stock", "supplier")
LABELS = {"sku": "Part number / SKU", "description": "Description", "quantity": "Quantity on hand",
          "location": "Aisle / shelf / bin", "reorder_point": "Restock at or below",
          "reorder_quantity": "Restock quantity", "target_stock": "Target stock", "supplier": "Supplier"}
TABLE_FIELDS = ("sku", "description", "quantity", "location", "reorder_point", "reorder_quantity", "supplier")
TABLE_LABELS = ("Part number", "Description", "Qty", "Location", "Restock at", "Order qty", "Supplier")


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
        self._building = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        header = QHBoxLayout()
        header.addWidget(_label("Parts inventory", "pageHeading"), 1)
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
        self.configure()
        self.refresh()

    def _build_stock(self):
        page = QWidget()
        layout = QVBoxLayout(page)
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
        self.search.setPlaceholderText("Find a part number, description, shelf, bin, or supplier")
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
        for index, field in enumerate(("quantity", "location", "reorder_point", "reorder_quantity")):
            widget = QLineEdit() if field == "location" else QSpinBox()
            if isinstance(widget, QSpinBox):
                widget.setRange(0, 2**31 - 1)
                widget.setGroupSeparatorShown(True)
            widget.setEnabled(False)
            self.editors[field] = widget
            if isinstance(widget, QSpinBox):
                widget.valueChanged.connect(self._editor_changed)
            else:
                widget.textEdited.connect(self._editor_changed)
            form.addWidget(_label(LABELS[field], "hint"), 0, index)
            form.addWidget(widget, 1, index)
        editor_layout.addLayout(form)
        row = QHBoxLayout()
        row.addWidget(_label("Counts are manual. Customer orders do not change inventory automatically.", "hint"), 1)
        self.save_button = _button("Save stock changes", self._save_item, "primary")
        self.save_button.setEnabled(False)
        row.addWidget(self.save_button)
        editor_layout.addLayout(row)
        layout.addWidget(editor)
        self.tabs.addTab(page, "Stock & locations")

    def _build_import(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(_label("Import an Excel or CSV stock list", "sectionTitle"))
        layout.addWidget(_label("Choose a worksheet, match its columns, and review every error before confirming. "
            "Matching part numbers update existing parts; parts absent from the file remain in stock. "
            "Excel macros and formulas are never executed. Maximum 50 MiB and 100,000 rows.", "hint"))
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
        self.review_button = _button("Review import", self._review, "primary")
        self.review_button.setEnabled(False)
        sheet_row.addWidget(self.review_button)
        layout.addLayout(sheet_row)
        mapping = QGridLayout()
        self.mapping = {}
        for index, field in enumerate(FIELDS):
            combo = QComboBox()
            combo.addItem("Not mapped", None)
            combo.setEnabled(False)
            combo.currentIndexChanged.connect(self._mapping_changed)
            self.mapping[field] = combo
            column, row = (index % 4) * 2, index // 4
            mapping.addWidget(_label(LABELS[field] + (" *" if field in ("sku", "quantity") else "")), row * 2, column, 1, 2)
            mapping.addWidget(combo, row * 2 + 1, column, 1, 2)
        layout.addLayout(mapping)
        self.import_table = QTableWidget()
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
                selected = preview.mapping.get(field)
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
        self.review_text.setPlainText(f"{preview.total_rows:,} spreadsheet rows found. Match the required part number and quantity columns.")
        self._mapping_changed()

    def _mapping_changed(self):
        if self._building:
            return
        self.import_button.setEnabled(False)
        self.service.plan = None
        required = all(self.mapping[field].currentData() is not None for field in ("sku", "quantity"))
        self.review_button.setEnabled(bool(self._preview and required and not self.service.busy and self.service.can_write))

    def _sheet_changed(self, sheet):
        if self._preview and sheet and sheet != self._preview.sheet:
            self._clear_import()
            self.service.inspect_file(self.service._file, sheet=sheet)

    def _review(self):
        mapping = {field: combo.currentData() for field, combo in self.mapping.items() if combo.currentData() is not None}
        self.service.prepare_import(mapping, sheet=self._preview.sheet if self._preview else None)

    def _show_plan(self, result):
        plan, counts = result["plan"], result["preview"]
        self.import_table.setColumnCount(len(FIELDS))
        self.import_table.setHorizontalHeaderLabels([LABELS[field] for field in FIELDS])
        self.import_table.setRowCount(min(len(plan.rows), 25))
        for row_index, row in enumerate(plan.rows[:25]):
            for column, field in enumerate(FIELDS):
                value = row.get(field, "")
                self.import_table.setItem(row_index, column, QTableWidgetItem("" if value is None else str(value)))
        rows = [f"{len(plan.rows):,} valid part rows · {counts.get('created', counts.get('inserted', 0)):,} new · "
                f"{counts.get('updated', 0):,} existing updates."]
        if plan.errors:
            rows.append("Fix these errors in the spreadsheet and review again:")
            rows.extend(plan.errors[:50])
        if plan.warnings:
            rows.append("Warnings:")
            rows.extend(plan.warnings[:25])
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
        self.review_button.setEnabled(not busy and self.service.can_write and bool(self._preview) and
            all(self.mapping[field].currentData() is not None for field in ("sku", "quantity")))
        self.import_button.setEnabled(not busy and self.service.can_write and bool(self.service.plan and self.service.plan.valid))
        self.save_button.setEnabled(not busy and self.service.can_write and self._selected is not None)
        for widget in self.editors.values():
            widget.setEnabled(not busy and self.service.can_write and self._selected is not None)

    def showEvent(self, event):
        super().showEvent(event)
        self.service.refresh()

    def closeEvent(self, event):
        self.hide()
        event.ignore()

    def shutdown(self):
        self._search_timer.stop()
        self.hide()
