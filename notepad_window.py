"""Jeffery's notebook, desktop sticky notes, and actionable reminder cards."""
from __future__ import annotations

import time
import uuid
import json
import shutil
import tempfile
from pathlib import Path
from PySide6.QtCore import Qt, Signal, QDateTime, QTimer, QSignalBlocker, QEvent, QSize
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QSplitter, QListWidget, QListWidgetItem, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QCheckBox, QSpinBox, QDateTimeEdit, QFileDialog,
    QMessageBox, QComboBox, QTabWidget, QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView, QScrollArea, QProgressBar, QGridLayout, QFrame, QToolButton, QMenu)
from config import clamp_position
from notes import current_guidance, ORDER_STATUSES, business_summary, note_search_text
from auto_parts import (STOCK_STATUSES, CHECKLIST_TEMPLATES, money_to_cents, format_money,
                        order_totals, dashboard_summary, parts_to_source, customer_history)
from sliding_text import SlidingText
from content_intake import IntakeService, cleanup_result, own_text_file
from notebook_backup import notebook_snapshot
from notebook_ui_jobs import prepare_document, prepare_notebook_backup, export_notebook
from ui_style import window_style, apply_window_style, ui_palette, ui_icon


def reminder_time(note):
    due = note.get("next_due")
    if note.get("done"):
        return "Done"
    if due is None:
        return "Timed reminders off"
    remaining = due - time.time()
    if remaining <= 0:
        return "Reminder due now"
    if remaining < 3600:
        return f"In {max(1, int(remaining / 60))} min"
    return QDateTime.fromSecsSinceEpoch(int(due)).toString("ddd, MMM d · h:mm AP")


def notes_style(settings):
    c = ui_palette(settings)
    return window_style(settings) + f"\nQWidget#noteCard {{ background: {c['bg']}; border: 1px solid {c['border']}; border-radius: 10px; }}"


class NotepadWindow(QDialog):
    home_requested = Signal()
    open_text_requested = Signal()
    reading = Signal()
    ai_preferences_requested = Signal(object)
    ai_requested = Signal(str)
    connection_requested = Signal()
    chat_requested = Signal()
    business_event = Signal(str)
    connection_work_chat_requested = Signal()

    def __init__(self, service, settings):
        super().__init__()
        self.service, self.settings = service, settings
        self.editing_id = None
        self.shared_business = None
        self.business_pending = False
        self._loaded_note_signature = None
        self.loading = False
        self.dirty = False
        self.import_result = None
        self.import_saved = False
        self.import_loading = False
        self.document_page_index = 0
        self.section_indices = {}
        self._workspace_ready = False
        self._record_scope = None
        self._browser_expanded = True
        self.intake = IntakeService(self)
        self.intake.completed.connect(self.imported)
        self.intake.failed.connect(self.import_failed)
        self.intake.busy_changed.connect(self.set_import_busy)
        self.transfer = IntakeService(self)
        self.transfer.completed.connect(self.transfer_received)
        self.transfer.failed.connect(lambda message: self.status.setText('Notebook transfer failed: ' + message))
        self.transfer.busy_changed.connect(self.set_transfer_busy)
        name = settings['business_name'] or 'Famous Twins'
        self.setWindowTitle(name + ' · Auto Parts Workspace')
        self.resize(1180, min(780, QApplication.primaryScreen().availableGeometry().height() - 50))
        self.setMinimumSize(760, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 12)
        layout.setSpacing(12)
        header = QHBoxLayout()
        brand = QVBoxLayout()
        self.brand_title = QLabel(name)
        self.brand_title.setObjectName('pageHeading')
        self.brand_subtitle = QLabel('Your team workspace')
        self.brand_subtitle.setObjectName('hint')
        brand.addWidget(self.brand_title)
        brand.addWidget(self.brand_subtitle)
        header.addLayout(brand, 1)
        self.home_button = QPushButton('Home')
        self.home_button.clicked.connect(self.home_requested.emit)
        header.addWidget(self.home_button)
        self.browser_toggle = QPushButton('Show entries')
        self.browser_toggle.setCheckable(True)
        self.browser_toggle.setChecked(True)
        self.browser_toggle.setToolTip('Show or hide the saved-entry browser')
        self.browser_toggle.toggled.connect(self.toggle_browser)
        header.addWidget(self.browser_toggle)
        self.talk_button = QPushButton('Talk to Jeffery')
        self.talk_button.clicked.connect(self.chat_requested.emit)
        header.addWidget(self.talk_button)
        layout.addLayout(header)
        self.summary = QLabel('')
        self.summary.setObjectName('hint')
        self.summary.setWordWrap(True)
        self.summary.hide()
        self.workspace_splitter = QSplitter()
        splitter = self.workspace_splitter
        self.navigation_panel = QWidget()
        self.navigation_panel.setObjectName('workspaceSidebar')
        self.navigation_panel.setMinimumWidth(146)
        self.navigation_panel.setMaximumWidth(190)
        navigation = QVBoxLayout(self.navigation_panel)
        navigation.setContentsMargins(10, 14, 10, 12)
        navigation.setSpacing(10)
        section_label = QLabel('WORKSPACE')
        section_label.setObjectName('hint')
        navigation.addWidget(section_label)
        self.sections = QListWidget()
        self.sections.setObjectName('workspaceSections')
        self.sections.setIconSize(QSize(18, 18))
        self.sections.setMinimumHeight(245)
        self.sections.setStyleSheet('QListWidget#workspaceSections::item { padding: 7px 6px; margin: 1px 0; }')
        self.sections.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        navigation.addWidget(self.sections, 1)
        self.shared_status = QLabel('Sign in to share orders and checklists.')
        self.shared_status.setObjectName('hint')
        self.shared_status.setWordWrap(True)
        navigation.addWidget(self.shared_status)
        self.shared_login_button = QPushButton('Team sign in')
        self.shared_login_button.clicked.connect(self.connection_work_chat_requested.emit)
        navigation.addWidget(self.shared_login_button)
        splitter.addWidget(self.navigation_panel)
        self.browser_panel = QWidget()
        self.browser_panel.setObjectName('workspaceBrowser')
        self.browser_panel.setMinimumWidth(188)
        self.browser_panel.setMaximumWidth(290)
        browse = QVBoxLayout(self.browser_panel)
        browse.setContentsMargins(12, 14, 12, 12)
        browse.setSpacing(10)
        browser_heading = QHBoxLayout()
        self.browser_title = QLabel('Writing')
        self.browser_title.setObjectName('sectionTitle')
        self.record_count = QLabel('0')
        self.record_count.setObjectName('hint')
        browser_heading.addWidget(self.browser_title, 1)
        browser_heading.addWidget(self.record_count)
        browse.addLayout(browser_heading)
        self.create_button = QPushButton('+ New writing')
        self.create_button.setObjectName('primary')
        self.create_button.clicked.connect(self.create_current_entry)
        browse.addWidget(self.create_button)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search entries')
        self.search.textChanged.connect(self.refresh)
        self.filter = QComboBox()
        for label, value in (('All entries', 'all'), ('Open entries', 'open'), ('Completed', 'done'),
                             ('Orders & quotes', 'order'), ('Checklists', 'list'), ('Writing', 'note')):
            self.filter.addItem(label, value)
        self.filter.currentIndexChanged.connect(self.refresh)
        browse.addWidget(self.search)
        browse.addWidget(self.filter)
        self.list = QListWidget()
        self.list.setObjectName('entryList')
        self.list.setTextElideMode(Qt.ElideRight)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setMinimumWidth(155)
        self.list.setMinimumHeight(60)
        self.list.currentItemChanged.connect(self.selection_changed)
        browse.addWidget(self.list, 1)
        self.browser_empty = QLabel('No entries yet. Create one to get started.')
        self.browser_empty.setObjectName('hint')
        self.browser_empty.setWordWrap(True)
        browse.addWidget(self.browser_empty)
        splitter.addWidget(self.browser_panel)
        editor = QWidget()
        editor.setObjectName('workspaceEditor')
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(12, 14, 12, 12)
        editor_layout.setSpacing(10)
        self.page_title = QLabel('Overview')
        self.page_title.setObjectName('sectionTitle')
        editor_layout.addWidget(self.page_title)
        self.entry_heading = QWidget()
        title_row = QHBoxLayout(self.entry_heading)
        title_row.setContentsMargins(0, 0, 0, 0)
        self.title = QLineEdit()
        self.title.setMaxLength(100)
        self.title.setPlaceholderText('Entry title')
        self.title.setObjectName('entryTitle')
        self.kind = QComboBox()
        for label, value in (('Writing', 'note'), ('Checklist', 'list'), ('Customer order', 'order')):
            self.kind.addItem(label, value)
        self.kind.hide()  # Entry types have their own pages and creation controls.
        self.entry_type = QLabel('WRITING')
        self.entry_type.setObjectName('hint')
        title_row.addWidget(self.title, 1)
        title_row.addWidget(self.entry_type)
        editor_layout.addWidget(self.entry_heading)
        self.entry_mismatch = QWidget()
        empty = QVBoxLayout(self.entry_mismatch)
        empty.addStretch(1)
        self.empty_title = QLabel('Choose an entry to get started')
        self.empty_title.setObjectName('sectionTitle')
        self.empty_title.setWordWrap(True)
        self.empty_title.setAlignment(Qt.AlignCenter)
        empty.addWidget(self.empty_title)
        self.empty_hint = QLabel('')
        self.empty_hint.setObjectName('hint')
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setAlignment(Qt.AlignCenter)
        empty.addWidget(self.empty_hint)
        self.empty_create = QPushButton('+ Create entry')
        self.empty_create.setObjectName('primary')
        self.empty_create.clicked.connect(self.create_current_entry)
        empty.addWidget(self.empty_create, 0, Qt.AlignCenter)
        self.return_to_entry = QPushButton('Return to your draft')
        self.return_to_entry.clicked.connect(self.show_current_entry)
        empty.addWidget(self.return_to_entry, 0, Qt.AlignCenter)
        empty.addStretch(1)
        self.entry_mismatch.hide()
        editor_layout.addWidget(self.entry_mismatch, 1)
        self.editor_tabs = QTabWidget()
        self.editor_tabs.tabBar().hide()
        self.editor_tabs.currentChanged.connect(self.section_changed)
        editor_layout.addWidget(self.editor_tabs, 1)
        self.build_overview()
        note_page, form = self.make_page()
        self.add_section('writing', 'Writing', note_page)
        intro = QLabel('A clear space for notes, ideas and full documents.')
        intro.setObjectName('hint')
        form.addWidget(intro)
        self.document_origin = QLabel('')
        self.document_origin.setObjectName('hint')
        self.document_origin.setTextFormat(Qt.PlainText)
        self.document_origin.setWordWrap(True)
        self.document_origin.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.document_origin.hide()
        form.addWidget(self.document_origin)
        self.body = QPlainTextEdit()
        self.body.setPlaceholderText('Write freely here. Use Schedule for reminders or Files to import a document.')
        self.body.setMinimumHeight(180)
        form.addWidget(self.body, 1)
        self.document_pages = QWidget()
        page_row = QHBoxLayout(self.document_pages)
        page_row.setContentsMargins(0, 0, 0, 0)
        self.document_previous = QPushButton('Previous')
        self.document_previous.clicked.connect(lambda: self.document_page.setValue(self.document_page.value() - 1))
        self.document_page = QSpinBox()
        self.document_page.setPrefix('Text page ')
        self.document_page.valueChanged.connect(self.show_document_page)
        self.document_next = QPushButton('Next')
        self.document_next.clicked.connect(lambda: self.document_page.setValue(self.document_page.value() + 1))
        self.document_size = QLabel('')
        self.document_size.setObjectName('hint')
        page_row.addWidget(self.document_previous)
        page_row.addWidget(self.document_page)
        page_row.addWidget(self.document_next)
        page_row.addWidget(self.document_size, 1)
        self.document_pages.hide()
        form.addWidget(self.document_pages)
        self.build_checklists()
        self.build_orders()
        self.build_schedule()
        self.build_advice()
        self.build_import_tab()
        self.kind.currentIndexChanged.connect(self.kind_changed)
        self.kind_changed()
        self.sections.currentRowChanged.connect(self.editor_tabs.setCurrentIndex)
        splitter.addWidget(editor)
        splitter.setChildrenCollapsible(False)
        splitter.setSizes([165, 230, 785])
        layout.addWidget(splitter, 1)
        self.editor_actions = QWidget()
        actions = QHBoxLayout(self.editor_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        self.save_button = QPushButton('Save changes')
        self.save_button.setObjectName('primary')
        self.save_button.clicked.connect(self.save_note)
        self.pin = QPushButton('Pin')
        self.pin.clicked.connect(self.toggle_pin)
        self.done_button = QPushButton('Done')
        self.done_button.clicked.connect(self.toggle_done)
        self.remove = QPushButton('Delete', self.editor_actions)
        self.remove.clicked.connect(self.delete_note)
        self.edit_state = QLabel('Unsaved entry')
        self.edit_state.setObjectName('hint')
        actions.addWidget(self.edit_state, 1)
        self.reload_button = QPushButton('Reload saved')
        self.reload_button.clicked.connect(self.reload_saved)
        for button in (self.pin, self.done_button):
            actions.addWidget(button)
        actions.addWidget(self.reload_button)
        self.more_actions = QToolButton()
        self.more_actions.setText('More')
        self.more_actions.setPopupMode(QToolButton.InstantPopup)
        more_menu = QMenu(self.more_actions)
        self.reload_action = more_menu.addAction('Reload saved entry', self.reload_button.click)
        self.delete_action = more_menu.addAction('Delete entry', self.remove.click)
        self.more_actions.setMenu(more_menu)
        actions.addWidget(self.more_actions)
        actions.addWidget(self.save_button)
        self.reload_button.hide()
        self.remove.hide()
        layout.addWidget(self.editor_actions)
        self.status = QLabel('Ctrl+S to save  |  Personal writing stays on this computer')
        self.status.setObjectName('hint')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.transfer_cancel = QPushButton('Cancel notebook transfer')
        self.transfer_cancel.clicked.connect(self.cancel_transfer)
        self.transfer_cancel.hide()
        layout.addWidget(self.transfer_cancel)
        self.shortcuts = [QShortcut(QKeySequence.Save, self), QShortcut(QKeySequence.New, self)]
        self.shortcuts[0].activated.connect(self.save_current_tab)
        self.shortcuts[1].activated.connect(self.new_note)
        for signal in (self.title.textChanged, self.body.textChanged, self.repeat.valueChanged,
                       self.scheduled.toggled, self.due.dateTimeChanged, self.kind.currentIndexChanged,
                       self.customer.textChanged, self.contact.textChanged, self.order_ref.textChanged,
                       self.order_status.currentIndexChanged, self.order_timed.toggled,
                       self.order_due.dateTimeChanged, self.task_table.itemChanged, self.order_table.itemChanged,
                       self.vehicle.textChanged, self.registration.textChanged, self.vin.textChanged,
                       self.priority.currentIndexChanged, self.order_type.currentIndexChanged,
                       self.payment_received.textChanged):
            signal.connect(self.mark_dirty)
        self.task_table.itemChanged.connect(self.update_checklist_progress)
        self.order_table.itemChanged.connect(self.update_order_totals)
        self.payment_received.textChanged.connect(self.update_order_totals)
        self.body.textChanged.connect(lambda: self.sync_body(self.body))
        self.checklist_notes.textChanged.connect(lambda: self.sync_body(self.checklist_notes))
        self.order_notes.textChanged.connect(lambda: self.sync_body(self.order_notes))
        service.changed.connect(self.refresh)
        self.configure()
        self._workspace_ready = True
        self.refresh()
        self.show_section('overview')
        self.update_buttons()

    def make_page(self, scroll=False):
        page = QWidget()
        page.setObjectName('notePage')
        box = QVBoxLayout(page)
        box.setContentsMargins(14, 14, 14, 14)
        box.setSpacing(10)
        if not scroll:
            return page, box
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(page)
        return area, box

    def add_section(self, key, title, page):
        index = self.editor_tabs.addTab(page, title)
        self.section_indices[key] = index
        item = QListWidgetItem(title)
        item.setIcon(ui_icon({'overview': 'home', 'writing': 'write', 'checklists': 'list',
                              'orders': 'orders', 'schedule': 'calendar', 'advice': 'spark',
                              'import': 'write'}.get(key, 'home'), self.settings, 18))
        self.sections.addItem(item)
        return index

    def set_shared_status(self, text, connected=False):
        message = str(text)
        if connected:
            compact = 'Saving shared work...' if message.startswith('Saving') else 'Connected to your team'
        else:
            compact = ('Sign in to share orders and checklists.' if message.startswith('Sign in') else
                       'Connecting to your team...' if message.startswith('Loading') else 'Team server unavailable')
        self.shared_status.setText(compact)
        self.shared_login_button.setText('Team chat' if connected else 'Team sign in')
        self.shared_status.setToolTip(message)
        self.shared_status.setAccessibleDescription(message)

    def show_section(self, section):
        index = self.section_indices.get(section)
        if index is not None:
            self.editor_tabs.setCurrentIndex(index)
            self.sections.setCurrentRow(index)

    def section_changed(self, index):
        if hasattr(self, 'sections'):
            with QSignalBlocker(self.sections):
                self.sections.setCurrentRow(index)
        section = next((key for key, value in self.section_indices.items() if value == index), '')
        if not self._workspace_ready:
            return
        self._record_scope = {'writing': 'note', 'checklists': 'list', 'orders': 'order'}.get(section)
        title = self.editor_tabs.tabText(index)
        self.page_title.setText(title)
        self.browser_title.setText(title if self._record_scope else 'Saved entries')
        label = {'writing': 'writing', 'checklists': 'checklist', 'orders': 'order'}.get(section, 'writing')
        self.create_button.setText('+ New ' + label)
        self.empty_create.setText('+ Create ' + label)
        self.search.setPlaceholderText({'orders': 'Customer, part, reference', 'checklists': 'Search checklists',
                                        'writing': 'Search writing'}.get(section, 'Search all entries'))
        matching_kind = self._record_scope is None or self._record_scope == self.kind.currentData()
        self.entry_mismatch.setVisible(not matching_kind)
        self.editor_tabs.setVisible(matching_kind)
        self.entry_heading.setVisible(matching_kind and section not in ('overview', 'import'))
        self.editor_actions.setVisible(matching_kind and section not in ('overview', 'import'))
        self.empty_title.setText('Choose a ' + label + ' or create one')
        self.empty_hint.setText('Your current draft is safe. Use the entries button to choose saved work.' if self.dirty else
                                'Open a saved entry from the list, or start a new one here.')
        self.return_to_entry.setVisible(self.dirty)
        # These filters refine the chosen section; cross-type filters remain available for the agenda.
        with QSignalBlocker(self.filter):
            self.filter.setCurrentIndex(0)
            self.filter.setItemText(0, 'All ' + (title.lower() if self._record_scope else 'entries'))
            for filter_index in (3, 4, 5):
                item = self.filter.model().item(filter_index)
                item.setEnabled(self._record_scope is None)
        self.update_browser_visibility()
        self.refresh()

    def current_section(self):
        return next((key for key, value in self.section_indices.items()
                     if value == self.editor_tabs.currentIndex()), 'writing')

    def create_current_entry(self):
        kind = {'checklists': 'list', 'orders': 'order'}.get(self.current_section(), 'note')
        return self.new_note() if kind == 'note' else self.new_entry(kind)

    def show_current_entry(self):
        self.show_section({'order': 'orders', 'list': 'checklists'}.get(self.kind.currentData(), 'writing'))

    def toggle_browser(self, expanded):
        self._browser_expanded = bool(expanded)
        if hasattr(self, 'browser_panel'):
            self.update_browser_visibility()

    def update_browser_visibility(self):
        if not self._workspace_ready:
            return
        available = self.current_section() not in ('overview', 'import')
        self.browser_panel.setVisible(available and self._browser_expanded)
        self.browser_toggle.setVisible(available)
        self.browser_toggle.setText('Hide entries' if self._browser_expanded else 'Show entries')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._workspace_ready:
            return
        # Small displays default to a full-width form; entries remain one click away.
        narrow = self.width() < 1000
        if getattr(self, '_last_narrow', None) != narrow:
            self._last_narrow = narrow
            with QSignalBlocker(self.browser_toggle):
                self.browser_toggle.setChecked(not narrow)
            self._browser_expanded = not narrow
            self.update_browser_visibility()

    def build_overview(self):
        page, box = self.make_page(scroll=True)
        heading = QLabel('A good day starts with a clear counter')
        heading.setObjectName('sectionTitle')
        box.addWidget(heading)
        hint = QLabel('Follow up on orders, prepare pickups, and keep your team on the same page.')
        hint.setWordWrap(True)
        hint.setObjectName('hint')
        box.addWidget(hint)
        start = QHBoxLayout()
        for label, kind in (('+ Customer order', 'order'), ('+ Daily checklist', 'list')):
            button = QPushButton(label)
            button.setObjectName('primary' if kind == 'order' else 'secondary')
            button.clicked.connect(lambda checked=False, value=kind: self.new_entry(value))
            start.addWidget(button)
        start.addStretch(1)
        box.addLayout(start)
        cards = QGridLayout()
        self.dashboard_cards = {}
        for index, (key, title) in enumerate((('open_orders', 'Open orders'), ('ready_orders', 'Ready for pickup'),
                                            ('late_orders', 'Past deadline'), ('urgent_orders', 'Urgent orders'))):
            button = QPushButton('0\n' + title)
            button.setMinimumHeight(64)
            button.setStyleSheet('font-size: 15px; text-align: left; padding: 12px;')
            button.clicked.connect(lambda checked=False, value=key: self.dashboard_focus(value))
            cards.addWidget(button, index // 2, index % 2)
            self.dashboard_cards[key] = (button, title)
        box.addLayout(cards)
        self.dashboard_money = QLabel('')
        self.dashboard_money.setWordWrap(True)
        box.addWidget(self.dashboard_money)
        self.order_queue_title = QLabel('Orders to follow up')
        self.order_queue_title.setStyleSheet('font-weight: 600;')
        box.addWidget(self.order_queue_title)
        self.order_queue = QListWidget()
        self.order_queue.setMinimumHeight(120)
        self.order_queue.setMaximumHeight(140)
        self.order_queue.itemActivated.connect(lambda item: self.open_workspace_note(item.data(Qt.UserRole)))
        self.order_queue.itemClicked.connect(lambda item: self.open_workspace_note(item.data(Qt.UserRole)))
        box.addWidget(self.order_queue)
        self.sourcing_toggle = QToolButton()
        self.sourcing_toggle.setText('Parts to source')
        self.sourcing_toggle.setCheckable(True)
        self.sourcing_toggle.setChecked(True)
        self.sourcing_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.sourcing_toggle.setArrowType(Qt.DownArrow)
        box.addWidget(self.sourcing_toggle)
        self.sourcing_table = QTableWidget(0, 5)
        self.sourcing_table.setHorizontalHeaderLabels(['Part / number', 'Qty', 'Supplier', 'Stock', 'Customer'])
        self.sourcing_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.sourcing_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sourcing_table.verticalHeader().hide()
        self.sourcing_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.sourcing_table.setMinimumHeight(135)
        self.sourcing_table.cellDoubleClicked.connect(self.open_sourcing_order)
        self.sourcing_toggle.toggled.connect(self.sourcing_table.setVisible)
        self.sourcing_toggle.toggled.connect(lambda value: self.sourcing_toggle.setArrowType(Qt.DownArrow if value else Qt.RightArrow))
        box.addWidget(self.sourcing_table)
        self.history_toggle = QToolButton()
        self.history_toggle.setText('Customer history')
        self.history_toggle.setCheckable(True)
        self.history_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.history_toggle.setArrowType(Qt.RightArrow)
        box.addWidget(self.history_toggle)
        self.history_panel = QWidget()
        history_box = QVBoxLayout(self.history_panel)
        history_box.setContentsMargins(0, 0, 0, 0)
        history_bar = QHBoxLayout()
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText('Customer, phone, registration or VIN')
        self.history_search.textChanged.connect(self.refresh_customer_history)
        history_bar.addWidget(self.history_search, 1)
        history_box.addLayout(history_bar)
        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(['Customer / reference', 'Vehicle', 'Status', 'Total JMD', 'Balance JMD'])
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.verticalHeader().hide()
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.history_table.setMinimumHeight(140)
        self.history_table.cellDoubleClicked.connect(self.open_history_order)
        history_box.addWidget(self.history_table)
        self.history_panel.hide()
        self.history_toggle.toggled.connect(self.history_panel.setVisible)
        self.history_toggle.toggled.connect(lambda value: self.history_toggle.setArrowType(Qt.DownArrow if value else Qt.RightArrow))
        box.addWidget(self.history_panel)
        box.addStretch(1)
        self.add_section('overview', 'Overview', page)
        self.dashboard_mode = 'open_orders'

    def build_checklists(self):
        page, box = self.make_page(scroll=True)
        intro = QLabel('A simple shared list for opening, closing, and handovers.')
        intro.setWordWrap(True)
        intro.setObjectName('hint')
        box.addWidget(intro)
        template_bar = QHBoxLayout()
        self.checklist_template = QComboBox()
        self.checklist_template.addItems(CHECKLIST_TEMPLATES.keys())
        template_bar.addWidget(self.checklist_template, 1)
        self.apply_template_button = QPushButton('Use template')
        self.apply_template_button.clicked.connect(self.apply_checklist_template)
        template_bar.addWidget(self.apply_template_button)
        box.addLayout(template_bar)
        self.checklist_progress = QProgressBar()
        self.checklist_progress.setFormat('No tasks yet')
        box.addWidget(self.checklist_progress)
        self.task_table = QTableWidget(0, 3)
        self.task_table.setHorizontalHeaderLabels(['Done', 'Task', 'Qty'])
        self.task_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.task_table.verticalHeader().hide()
        self.task_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.task_table.setColumnWidth(0, 52)
        self.task_table.setColumnWidth(2, 62)
        self.task_table.setColumnHidden(2, True)
        self.task_table.setMinimumHeight(210)
        box.addWidget(self.task_table, 1)
        row = QHBoxLayout()
        self.item_text = QLineEdit()
        self.item_text.setMaxLength(200)
        self.item_text.setPlaceholderText('Add a task')
        self.item_quantity = QSpinBox()
        self.item_quantity.setRange(1, 9999)
        self.item_quantity.hide()
        add = QPushButton('Add task')
        add.clicked.connect(self.add_checklist_item)
        self.item_text.returnPressed.connect(self.add_checklist_item)
        row.addWidget(self.item_text, 1)
        row.addWidget(self.item_quantity)
        row.addWidget(add)
        box.addLayout(row)
        remove = QPushButton('Remove task')
        remove.clicked.connect(lambda: self.remove_checklist_item(self.task_table))
        task_actions = QHBoxLayout()
        quantities = QCheckBox('Track quantities')
        quantities.toggled.connect(self.item_quantity.setVisible)
        quantities.toggled.connect(lambda value: self.task_table.setColumnHidden(2, not value))
        task_actions.addWidget(quantities)
        task_actions.addStretch(1)
        task_actions.addWidget(remove)
        box.addLayout(task_actions)
        self.checklist_notes = QPlainTextEdit()
        self.checklist_notes.setPlaceholderText('Checklist notes (optional)')
        self.checklist_notes.setMinimumHeight(80)
        self.checklist_notes.setMaximumHeight(120)
        box.addWidget(self.checklist_notes)
        self.add_section('checklists', 'Checklists', page)

    def build_orders(self):
        page, box = self.make_page(scroll=True)
        intro = QLabel('Customer details, parts, and payment in one place. All amounts are JMD.')
        intro.setWordWrap(True)
        intro.setObjectName('hint')
        box.addWidget(intro)
        info = QGridLayout()
        self.customer = self.business_input('Customer name or Walk-in', 100)
        self.contact = self.business_input('Phone / email', 100)
        self.order_ref = self.business_input('ORD-001', 80)
        self.vehicle = self.business_input('Year, make, model / engine', 160)
        self.registration = self.business_input('Registration', 32)
        self.vin = self.business_input('VIN / chassis number', 32)
        self.priority = QComboBox()
        self.priority.addItem('Normal', 'normal')
        self.priority.addItem('Urgent', 'urgent')
        self.order_type = QComboBox()
        self.order_type.addItem('Order', 'order')
        self.order_type.addItem('Quote', 'quote')
        self.order_status = QComboBox()
        for status in ORDER_STATUSES:
            self.order_status.addItem(status.title(), status)
        for row, (label, field, label2, field2) in enumerate((
                ('Customer', self.customer, 'Contact', self.contact),
                ('Reference', self.order_ref, 'Type', self.order_type),
                ('Status', self.order_status, 'Priority', self.priority))):
            info.addWidget(QLabel(label), row, 0)
            info.addWidget(field, row, 1)
            info.addWidget(QLabel(label2), row, 2)
            info.addWidget(field2, row, 3)
        self.order_timed = QCheckBox('Pickup deadline')
        self.order_due = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self.order_due.setCalendarPopup(True)
        self.order_due.setDisplayFormat('MMM d, yyyy h:mm AP')
        self.order_due.setEnabled(False)
        self.order_timed.toggled.connect(self.order_due.setEnabled)
        box.addLayout(info)
        self.vehicle_toggle = QToolButton()
        self.vehicle_toggle.setText('Vehicle && pickup details')
        self.vehicle_toggle.setCheckable(True)
        self.vehicle_toggle.setArrowType(Qt.RightArrow)
        self.vehicle_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        box.addWidget(self.vehicle_toggle)
        self.vehicle_panel = QWidget()
        vehicle_form = QFormLayout(self.vehicle_panel)
        vehicle_form.setContentsMargins(0, 0, 0, 0)
        vehicle_form.addRow('Vehicle', self.vehicle)
        vehicle_form.addRow('Registration', self.registration)
        vehicle_form.addRow('VIN / chassis', self.vin)
        pickup_row = QHBoxLayout()
        pickup_row.addWidget(self.order_timed)
        pickup_row.addWidget(self.order_due, 1)
        vehicle_form.addRow(pickup_row)
        self.vehicle_panel.hide()
        self.vehicle_toggle.toggled.connect(self.vehicle_panel.setVisible)
        self.vehicle_toggle.toggled.connect(lambda value: self.vehicle_toggle.setArrowType(Qt.DownArrow if value else Qt.RightArrow))
        box.addWidget(self.vehicle_panel)
        parts_heading = QLabel('Parts')
        parts_heading.setObjectName('sectionTitle')
        box.addWidget(parts_heading)
        self.order_fields = [self.customer, self.contact, self.order_ref, self.order_status, self.order_timed, self.order_due]
        self.order_table = QTableWidget(0, 9)
        self.order_table.setHorizontalHeaderLabels(['Done', 'Part / description', 'Qty', 'Part no.', 'Supplier', 'Bin', 'Unit JMD', 'Stock', 'Line JMD'])
        self.order_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.order_table.verticalHeader().hide()
        self.order_table.setMinimumHeight(170)
        widths = (44, 170, 44, 100, 105, 65, 82, 100, 95)
        for column, width in enumerate(widths):
            self.order_table.setColumnWidth(column, width)
        self.order_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for column in (3, 4, 5):
            self.order_table.setColumnHidden(column, True)
        box.addWidget(self.order_table)
        inputs = QGridLayout()
        self.part_text = self.business_input('Part description', 200)
        self.part_number = self.business_input('Part number', 80)
        self.part_supplier = self.business_input('Supplier', 100)
        self.part_bin = self.business_input('Shelf / bin', 60)
        self.part_quantity = QSpinBox()
        self.part_quantity.setRange(1, 9999)
        self.part_price = self.business_input('Unit price JMD', 20)
        self.part_price.setText('0.00')
        self.part_stock = QComboBox()
        for status in STOCK_STATUSES:
            self.part_stock.addItem(status.replace('_', ' ').title(), status)
        inputs.addWidget(self.part_text, 0, 0, 1, 4)
        inputs.addWidget(QLabel('Quantity'), 1, 0)
        inputs.addWidget(self.part_quantity, 1, 1)
        inputs.addWidget(QLabel('Unit JMD'), 1, 2)
        inputs.addWidget(self.part_price, 1, 3)
        inputs.addWidget(self.part_stock, 2, 0, 1, 3)
        add = QPushButton('Add part')
        add.clicked.connect(self.add_order_item)
        self.part_text.returnPressed.connect(self.add_order_item)
        add.setObjectName('primary')
        inputs.addWidget(add, 2, 3)
        box.addLayout(inputs)
        self.part_details_toggle = QCheckBox('Part numbers, supplier, and bin')
        self.part_details_panel = QWidget()
        detail_form = QFormLayout(self.part_details_panel)
        detail_form.setContentsMargins(0, 0, 0, 0)
        detail_form.addRow('Part number', self.part_number)
        detail_form.addRow('Supplier', self.part_supplier)
        detail_form.addRow('Shelf / bin', self.part_bin)
        self.part_details_panel.hide()
        self.part_details_toggle.toggled.connect(self.part_details_panel.setVisible)
        self.part_details_toggle.toggled.connect(lambda value: [self.order_table.setColumnHidden(column, not value) for column in (3, 4, 5)])
        box.addWidget(self.part_details_toggle)
        box.addWidget(self.part_details_panel)
        row = QHBoxLayout()
        remove = QPushButton('Remove part')
        remove.clicked.connect(lambda: self.remove_checklist_item(self.order_table))
        remind = QPushButton('Remind at pickup')
        remind.clicked.connect(self.remind_at_order_deadline)
        row.addWidget(remove)
        row.addWidget(remind)
        box.addLayout(row)
        money = QHBoxLayout()
        money.addWidget(QLabel('Payment received JMD'))
        self.payment_received = self.business_input('0.00', 20)
        self.payment_received.setText('0.00')
        self.payment_received.setMaximumWidth(150)
        money.addWidget(self.payment_received)
        self.order_total_label = QLabel('')
        self.order_total_label.setWordWrap(True)
        money.addWidget(self.order_total_label, 1)
        box.addLayout(money)
        self.copy_pickup_button = QPushButton('Copy pickup summary')
        self.copy_pickup_button.clicked.connect(self.copy_pickup_summary)
        box.addWidget(self.copy_pickup_button)
        follow = QPushButton('Schedule follow-up')
        follow.clicked.connect(self.schedule_follow_up)
        box.addWidget(follow)
        self.order_notes = QPlainTextEdit()
        self.order_notes.setPlaceholderText('Order notes, fitment checks or supplier follow-up')
        self.order_notes.setMinimumHeight(90)
        self.order_notes.setMaximumHeight(140)
        box.addWidget(self.order_notes)
        hint = QLabel('Confirm fitment before handover. Delivered and Cancelled orders stop reminders.')
        hint.setWordWrap(True)
        hint.setObjectName('hint')
        box.addWidget(hint)
        self.add_section('orders', 'Orders', page)

    @staticmethod
    def business_input(placeholder, limit):
        field = QLineEdit()
        field.setMaxLength(limit)
        field.setPlaceholderText(placeholder)
        return field

    def build_schedule(self):
        page, box = self.make_page(scroll=True)
        label = QLabel('Reminders for the selected entry')
        label.setStyleSheet('font-size: 18px; font-weight: 600;')
        box.addWidget(label)
        schedule = QFormLayout()
        self.repeat = QSpinBox()
        self.repeat.setRange(0, 1440)
        self.repeat.setSuffix(' min')
        self.repeat.setValue(self.settings['note_repeat_minutes'])
        repeat_row = QHBoxLayout()
        self.repeat_choice = QComboBox()
        for label, value in (('One time / no repeat', 0), ('Every 15 minutes', 15),
                             ('Every 30 minutes', 30), ('Every hour', 60), ('Every day', 1440), ('Custom', -1)):
            self.repeat_choice.addItem(label, value)
        self.repeat_choice.currentIndexChanged.connect(self.choose_repeat)
        self.repeat.valueChanged.connect(self.sync_repeat_choice)
        repeat_row.addWidget(self.repeat_choice, 1)
        repeat_row.addWidget(self.repeat)
        schedule.addRow('Repeat', repeat_row)
        self.sync_repeat_choice()
        self.scheduled = QCheckBox('Enable a timed reminder')
        self.due = QDateTimeEdit(QDateTime.currentDateTime().addSecs(1800))
        self.due.setCalendarPopup(True)
        self.due.setDisplayFormat('MMM d, yyyy  h:mm AP')
        self.due.setEnabled(False)
        self.scheduled.toggled.connect(self.due.setEnabled)
        schedule.addRow(self.scheduled)
        schedule.addRow('Next reminder', self.due)
        box.addLayout(schedule)
        quick = QHBoxLayout()
        for label, seconds in (('In 15 min', 900), ('In 1 hour', 3600), ('Tomorrow 9 AM', -1)):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, value=seconds: self.quick_time(value))
            quick.addWidget(button)
        box.addLayout(quick)
        hint = QLabel('Save this entry to apply its schedule. Mark Done to stop reminders. Keep Jeffery running.')
        hint.setWordWrap(True)
        hint.setObjectName('hint')
        box.addWidget(hint)
        box.addWidget(QLabel('Upcoming and overdue'))
        self.agenda = QListWidget()
        self.agenda.setMinimumHeight(180)
        self.agenda.itemClicked.connect(lambda item: self.open_workspace_note(item.data(Qt.UserRole), 'schedule'))
        box.addWidget(self.agenda, 1)
        self.add_section('schedule', 'Schedule', page)

    def build_advice(self):
        page, advice = self.make_page(scroll=True)
        self.smart_enabled = QCheckBox('Use Groq for smarter reminders')
        self.smart_enabled.setChecked(self.settings['ai_share_notes'])
        self.smart_enabled.toggled.connect(lambda value: self.ai_preferences_requested.emit({'ai_share_notes': value}))
        advice.addWidget(self.smart_enabled)
        self.setup_ai = QPushButton('Set up Groq')
        self.setup_ai.clicked.connect(self.connection_requested.emit)
        advice.addWidget(self.setup_ai)
        self.ai_status = QLabel('Saved notes and linked .txt lines help Groq write useful reminders.')
        self.ai_status.setObjectName('hint')
        self.ai_status.setWordWrap(True)
        self.ai_status.setTextFormat(Qt.PlainText)
        advice.addWidget(self.ai_status)
        self.advice_message = QLabel('Save an entry and Jeffery will suggest a helpful reminder.')
        self.advice_step = QLabel('')
        self.advice_time = QLabel('')
        for field in (self.advice_message, self.advice_step, self.advice_time):
            field.setWordWrap(True)
            field.setTextFormat(Qt.PlainText)
            advice.addWidget(field)
        self.use_time = QPushButton('Use this time')
        self.use_time.clicked.connect(self.apply_suggested_time)
        advice.addWidget(self.use_time)
        self.refresh_ai = QPushButton("Refresh Jeffery's advice")
        self.refresh_ai.clicked.connect(self.request_advice)
        advice.addWidget(self.refresh_ai)
        privacy = QLabel("Groq reads this saved entry's details when enabled. Suggested times apply after you choose one and save.")
        privacy.setObjectName('hint')
        privacy.setWordWrap(True)
        advice.addWidget(privacy)
        advice.addStretch(1)
        self.add_section('advice', 'Advice', page)

    def build_import_tab(self):
        page = QWidget()
        page.setObjectName('notePage')
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 14, 12, 12)
        heading = QLabel('Read a PDF or webpage, review its text, then save it for Jeffery.')
        heading.setWordWrap(True)
        layout.addWidget(heading)
        pdf_row = QHBoxLayout()
        self.import_pdf_button = QPushButton('Choose PDF…')
        self.import_pdf_button.clicked.connect(lambda: self.import_pdf())
        pdf_row.addWidget(self.import_pdf_button)
        pdf_hint = QLabel('PDFs up to 500 MB are supported. Turn on OCR to read scanned pages.')
        pdf_hint.setObjectName('hint')
        pdf_hint.setWordWrap(True)
        pdf_row.addWidget(pdf_hint, 1)
        layout.addLayout(pdf_row)
        self.import_ocr = QCheckBox('Read scanned pages with OCR (slower)')
        self.import_ocr.setChecked(self.settings['pdf_ocr'])
        self.import_ocr.setToolTip('Requires Tesseract. OCR runs locally; use Documents & memory settings to choose languages and its executable.')
        layout.addWidget(self.import_ocr)
        web_row = QHBoxLayout()
        self.web_address = QLineEdit()
        self.web_address.setPlaceholderText('https://example.com/article')
        self.web_address.setMaxLength(2048)
        self.web_address.returnPressed.connect(self.read_webpage)
        self.read_web_button = QPushButton('Read webpage')
        self.read_web_button.clicked.connect(self.read_webpage)
        web_row.addWidget(self.web_address, 1)
        web_row.addWidget(self.read_web_button)
        layout.addLayout(web_row)
        progress_row = QHBoxLayout()
        self.import_progress = QProgressBar()
        self.import_progress.setRange(0, 0)
        self.import_progress.setTextVisible(False)
        self.import_progress.hide()
        self.cancel_import_button = QPushButton('Cancel')
        self.cancel_import_button.clicked.connect(self.cancel_import)
        self.cancel_import_button.hide()
        progress_row.addWidget(self.import_progress, 1)
        progress_row.addWidget(self.cancel_import_button)
        layout.addLayout(progress_row)
        self.import_status = QLabel('Nothing imported yet. Reading stays on your computer until you save and share notes with Groq.')
        self.import_status.setObjectName('hint')
        self.import_status.setTextFormat(Qt.PlainText)
        self.import_status.setWordWrap(True)
        layout.addWidget(self.import_status)
        self.import_title = QLineEdit()
        self.import_title.setMaxLength(100)
        self.import_title.setPlaceholderText('Title for the saved document')
        layout.addWidget(self.import_title)
        self.import_review = QPlainTextEdit()
        self.import_review.setPlaceholderText('Extracted text appears here. Edit or remove anything before saving.')
        self.import_review.setMinimumHeight(140)
        self.import_review.textChanged.connect(self.update_import_save)
        layout.addWidget(self.import_review, 1)
        self.import_preview_only = QCheckBox('Save only this preview instead of the full document')
        self.import_preview_only.toggled.connect(self.import_selection_changed)
        self.import_preview_only.hide()
        layout.addWidget(self.import_preview_only)
        self.import_sources = QLabel('')
        self.import_sources.setObjectName('hint')
        self.import_sources.setTextFormat(Qt.PlainText)
        self.import_sources.setWordWrap(True)
        self.import_sources.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.import_sources)
        self.save_import_button = QPushButton('Save to notebook')
        self.save_import_button.setObjectName('primary')
        self.save_import_button.setEnabled(False)
        self.save_import_button.clicked.connect(self.save_import)
        layout.addWidget(self.save_import_button)
        reminder_hint = QLabel('Imports have timed reminders off. Open a saved section to schedule a reminder or review Jeffery’s advice. Groq uses saved notes when smarter reminders is enabled.')
        reminder_hint.setObjectName('hint')
        reminder_hint.setWordWrap(True)
        layout.addWidget(reminder_hint)
        tools = QHBoxLayout()
        for label, callback in (('Link .txt', self.choose_link), ('Open linked', self.open_text_requested.emit),
                                ('Unlink', self.unlink)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            tools.addWidget(button)
        layout.addLayout(tools)
        self.link_status = QLabel('')
        self.link_status.setObjectName('hint')
        self.link_status.setWordWrap(True)
        layout.addWidget(self.link_status)
        transfers = QHBoxLayout()
        self.transfer_buttons = []
        for label, callback in (('Export .txt', self.export_notes), ('Backup', self.export_backup), ('Import backup', self.import_backup)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            transfers.addWidget(button)
            self.transfer_buttons.append(button)
        layout.addLayout(transfers)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(page)
        self.add_section('import', 'Files', area)

    def set_import_busy(self, busy, status=''):
        self.import_loading = busy
        self.import_pdf_button.setEnabled(not busy)
        self.import_ocr.setEnabled(not busy)
        self.read_web_button.setEnabled(not busy)
        self.web_address.setEnabled(not busy)
        self.import_review.setEnabled(not busy)
        self.import_title.setEnabled(not busy)
        self.import_progress.setVisible(busy)
        self.cancel_import_button.setVisible(busy)
        if status:
            self.import_status.setText(status)
        self.update_import_save()

    def update_import_save(self):
        self.save_import_button.setEnabled(bool(self.import_result and not self.import_saved and
            not self.import_loading and not self.intake.busy and self.import_review.toPlainText().strip()))

    def import_selection_changed(self):
        full_document = bool(self.import_result and self.import_result.get('text_file'))
        full_save = full_document and not self.import_preview_only.isChecked()
        self.import_review.setReadOnly(full_save)
        self.save_import_button.setText('Save full document' if full_save else 'Save to notebook')

    def may_replace_import(self):
        if not self.import_result or self.import_saved:
            return True
        return QMessageBox.question(self, 'Unsaved import',
            'Replace the current document preview? It has not been saved to your notebook.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def import_pdf(self, path=None):
        if self.intake.busy:
            return
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, 'Read a PDF', '', 'PDF documents (*.pdf)')
        if not path or not self.may_replace_import():
            return
        self.set_import_busy(True, 'Reading PDF… You can cancel while Jeffery extracts its text.')
        try:
            if not self.intake.import_pdf(str(path), engine=self.settings['pdf_engine'], ocr=self.import_ocr.isChecked(),
                    ocr_language=self.settings['ocr_language'], tesseract_path=self.settings['tesseract_path']):
                self.set_import_busy(False, 'Reading could not start. Try again when the current import finishes.')
        except (OSError, ValueError) as exc:
            self.import_failed(str(exc))

    def read_webpage(self):
        if self.intake.busy:
            return
        address = self.web_address.text().strip()
        if not address:
            self.import_status.setText('Enter an http:// or https:// webpage address.')
            return
        if not self.may_replace_import():
            return
        self.set_import_busy(True, 'Reading webpage… Review the extracted text before saving.')
        try:
            if not self.intake.fetch_url(address):
                self.set_import_busy(False, 'Reading could not start. Try again when the current import finishes.')
        except (OSError, ValueError) as exc:
            self.import_failed(str(exc))

    def imported(self, result):
        if isinstance(result, dict) and result.get('kind') == 'document_prepared':
            try:
                note = self.service.add_prepared_document(result['document'], result['title'], result['source'])
            except (OSError, ValueError) as exc:
                cleanup_result(result)
                self.import_failed('Nothing was saved: ' + str(exc))
                return
            result.pop('_cleanup', None)
            cleanup_result(self.import_result)
            self.import_saved = True
            self.update_import_save()
            self.import_status.setText(f"Saved the full document ({note['body_characters']:,} characters). Open it to read text pages or choose a reminder.")
            self.status.setText('Full document saved. Your current writing draft is unchanged.')
            return
        if not isinstance(result, dict) or not isinstance(result.get('text'), str) or not result['text'].strip():
            cleanup_result(result)
            self.import_failed('No readable text was found. Your notebook was left unchanged.')
            return
        if self.import_result is not result:
            cleanup_result(self.import_result)
        self.import_result = result
        self.import_saved = False
        self.import_title.setText(str(result.get('title') or 'Imported document')[:100])
        self.import_review.setPlainText(result['text'])
        with QSignalBlocker(self.import_preview_only):
            self.import_preview_only.setChecked(False)
        self.import_preview_only.setVisible(bool(result.get('text_file')))
        self.import_selection_changed()
        sources = result.get('sources') or []
        self.import_sources.setText('\n'.join(str(source.get('url') or source.get('title') or '')
            for source in sources[:10] if isinstance(source, dict)))
        warning = ' Only part of the document was extracted; review the included text.' if result.get('truncated') else ''
        if result.get('preview_truncated') and not result.get('truncated'):
            warning = f" Showing the first {len(result['text']):,} of {result.get('character_count', 0):,} characters. Save full document keeps all extracted text; saved documents open in text pages."
        self.set_import_busy(False, 'Ready to review. Nothing has been added to your notebook yet.' + warning)

    def review_document(self, result):
        """Accept a source read by chat without replacing an unsaved preview."""
        if not self.may_replace_import():
            return False
        if self.intake.busy:
            self.cancel_import()
        # Chat owns its web spool. Keep an independent copy for this review.
        if result.get('text_file'):
            owned = dict(result)
            with tempfile.NamedTemporaryFile(prefix='jeffery-review-', suffix='.txt', delete=False) as output:
                path = Path(output.name)
            try:
                shutil.copyfile(result['text_file'], path)
            except OSError:
                path.unlink(missing_ok=True)
                self.import_failed('The full source is no longer available. Read it again.')
                return False
            owned['text_file'] = str(path)
            owned.pop('_cleanup', None)
            own_text_file(path)
            result = owned
        self.imported(result)
        self.show_section('import')
        return True

    def import_failed(self, message):
        self.set_import_busy(False, 'Could not read the source: ' + str(message))

    def cancel_import(self):
        self.intake.cancel()
        self.set_import_busy(False, 'Reading cancelled. Your notebook was left unchanged.')

    def save_import(self):
        if not self.import_result or self.import_saved or self.intake.busy:
            return False
        text = self.import_review.toPlainText().strip()
        title = self.import_title.text().strip() or 'Imported document'
        result = self.import_result
        source = result.get('url') or str(result.get('filename') or result.get('title') or title)
        if result.get('text_file') and not self.import_preview_only.isChecked():
            if self.transfer.busy:
                self.import_status.setText('Finish or cancel the notebook transfer before saving this document.')
                return False
            self.set_import_busy(True, 'Saving full document… You can cancel while Jeffery stores its text.')
            argument = {'store': self.service.store, 'text_file': result['text_file'], 'title': title, 'source': source,
                'character_count': result.get('character_count'), 'full_text_sha256': result.get('full_text_sha256')}
            if not self.intake.run(prepare_document, argument):
                self.set_import_busy(False, 'The full document could not start saving. Try again.')
                return False
            return True
        # Keep original page/section titles when the review text is unchanged.
        sections = result.get('sections') if (text == result['text'].strip() and
            title == str(result.get('title') or 'Imported document')[:100]) else None
        if not isinstance(sections, list) or not sections:
            sections = [{'title': title, 'body': text}]
        try:
            added = self.service.add_documents(sections, source)
        except (OSError, ValueError) as exc:
            self.import_status.setText('Nothing was saved: ' + str(exc))
            return False
        self.import_saved = True
        cleanup_result(result)
        self.update_import_save()
        self.import_status.setText(f'Saved {len(added)} document section(s). Timed reminders are off until you choose a schedule.')
        self.status.setText('Document saved. Your current note or writing draft is unchanged.')
        return True

    def save_current_tab(self):
        if self.editor_tabs.currentIndex() == self.section_indices['import']:
            return self.save_import()
        return self.save_note()

    def shutdown(self):
        self.intake.shutdown()
        self.transfer.shutdown()
        cleanup_result(self.import_result)
        self.service.store.close()

    def configure(self):
        apply_window_style(self, self.settings)
        for key, index in self.section_indices.items():
            self.sections.item(index).setIcon(ui_icon({'overview': 'home', 'writing': 'write', 'checklists': 'list',
                'orders': 'orders', 'schedule': 'calendar', 'advice': 'spark', 'import': 'write'}.get(key, 'home'), self.settings, 18))
        name = self.settings['business_name'] or 'Famous Twins'
        self.brand_title.setText(name)
        self.setWindowTitle(name + ' · Auto Parts Workspace')
        if not self.intake.busy:
            self.import_ocr.setChecked(self.settings['pdf_ocr'])
        with QSignalBlocker(self.smart_enabled):
            self.smart_enabled.setChecked(self.settings["ai_share_notes"])
        self.refresh_advice()

    @property
    def checklist(self):
        # Legacy callers use this name for the currently edited item table.
        return self.order_table if self.kind.currentData() == 'order' else self.task_table

    def kind_changed(self, *args):
        kind = self.kind.currentData()
        self.entry_type.setText({'note': 'WRITING', 'list': 'CHECKLIST', 'order': 'ORDER / QUOTE'}.get(kind, 'WRITING'))
        for field in (self.task_table, self.item_text, self.item_quantity, self.checklist_notes):
            field.setEnabled(kind == 'list')
        for field in (self.order_table, self.customer, self.contact, self.order_ref, self.vehicle, self.registration,
                      self.vin, self.priority, self.order_type, self.order_status, self.order_timed,
                      self.part_text, self.part_number, self.part_supplier, self.part_bin, self.part_quantity,
                      self.part_price, self.part_stock, self.payment_received, self.order_notes, self.copy_pickup_button):
            field.setEnabled(kind == 'order')
        self.order_due.setEnabled(kind == 'order' and self.order_timed.isChecked())

    def sync_body(self, source):
        if self.loading:
            return
        text = source.toPlainText()
        for field in (self.body, self.checklist_notes, self.order_notes):
            if field is not source and field.toPlainText() != text:
                with QSignalBlocker(field):
                    field.setPlainText(text)
        self.mark_dirty()

    def add_checklist_item(self):
        text = self.item_text.text().strip()
        if not text or self.checklist.rowCount() >= 100:
            return
        self.insert_checklist_row({'id': uuid.uuid4().hex, 'text': text, 'quantity': self.item_quantity.value(), 'done': False})
        self.item_text.clear()
        self.item_quantity.setValue(1)
        self.mark_dirty()
        self.update_checklist_progress()
        self.update_order_totals()

    def insert_checklist_row(self, item, table=None):
        table = table if table is not None else self.checklist
        row = table.rowCount()
        table.insertRow(row)
        done = QTableWidgetItem('')
        done.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        done.setCheckState(Qt.Checked if item.get('done') else Qt.Unchecked)
        done.setData(Qt.UserRole, item.get('id') or uuid.uuid4().hex)
        # Preserve rich item fields even when an old order is changed to a list.
        done.setData(Qt.UserRole + 1, dict(item))
        table.setItem(row, 0, done)
        table.setItem(row, 1, QTableWidgetItem(item.get('text', '')))
        table.setItem(row, 2, QTableWidgetItem(str(item.get('quantity', 1))))
        if table is self.order_table:
            for column, key in ((3, 'part_number'), (4, 'supplier'), (5, 'bin_location')):
                table.setItem(row, column, QTableWidgetItem(item.get(key, '')))
            table.setItem(row, 6, QTableWidgetItem(f"{item.get('unit_price_cents', 0) // 100}.{item.get('unit_price_cents', 0) % 100:02d}"))
            stock = QComboBox()
            for status in STOCK_STATUSES:
                stock.addItem(status.replace('_', ' ').title(), status)
            stock.setCurrentIndex(max(0, stock.findData(item.get('stock_status', 'check_stock'))))
            stock.currentIndexChanged.connect(self.mark_dirty)
            table.setCellWidget(row, 7, stock)
            total = QTableWidgetItem('')
            total.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            table.setItem(row, 8, total)

    def add_order_item(self):
        text = self.part_text.text().strip()
        if not text:
            self.status.setText('Enter a part description first.')
            return
        if self.kind.currentData() != 'order':
            self.status.setText('Create or open an order before adding parts.')
            return
        if self.order_table.rowCount() >= 100:
            self.status.setText('An order supports up to 100 parts.')
            return
        try:
            price = money_to_cents(self.part_price.text())
            if price > 1_000_000_000:
                raise ValueError('The unit price must be JMD 10,000,000.00 or less.')
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.insert_checklist_row({'id': uuid.uuid4().hex, 'text': text, 'quantity': self.part_quantity.value(),
            'done': False, 'part_number': self.part_number.text(), 'supplier': self.part_supplier.text(),
            'bin_location': self.part_bin.text(), 'unit_price_cents': price, 'stock_status': self.part_stock.currentData()}, self.order_table)
        for field in (self.part_text, self.part_number, self.part_supplier, self.part_bin):
            field.clear()
        self.part_quantity.setValue(1)
        self.part_price.setText('0.00')
        self.part_stock.setCurrentIndex(0)
        self.mark_dirty()
        self.update_order_totals()

    def remove_checklist_item(self, table=None):
        table = table if table is not None else self.checklist
        row = table.currentRow()
        if row >= 0:
            table.removeRow(row)
            self.mark_dirty()
            self.update_checklist_progress()
            self.update_order_totals()

    def collect_items(self, table=None):
        table = table if table is not None else self.checklist
        items = []
        for row in range(table.rowCount()):
            done, text, quantity = (table.item(row, column) for column in range(3))
            try:
                qty = int(quantity.text())
                if not 1 <= qty <= 9999 or not text.text().strip():
                    raise ValueError()
            except (ValueError, AttributeError):
                raise ValueError('Each item needs text and a quantity from 1 to 9,999.')
            item = dict(done.data(Qt.UserRole + 1) or {})
            item.update({'id': done.data(Qt.UserRole), 'text': text.text().strip(), 'quantity': qty,
                         'done': done.checkState() == Qt.Checked})
            if table is self.order_table:
                for column, key in ((3, 'part_number'), (4, 'supplier'), (5, 'bin_location')):
                    cell = table.item(row, column)
                    item[key] = cell.text().strip() if cell else ''
                cell = table.item(row, 6)
                item['unit_price_cents'] = money_to_cents(cell.text() if cell else '0')
                if item['unit_price_cents'] > 1_000_000_000:
                    raise ValueError('A unit price must be JMD 10,000,000.00 or less.')
                item['stock_status'] = table.cellWidget(row, 7).currentData()
            items.append(item)
        return items

    def collect_business(self):
        return {'kind': self.kind.currentData(), 'customer': self.customer.text(), 'contact': self.contact.text(),
                'order_ref': self.order_ref.text(), 'order_status': self.order_status.currentData(),
                'order_due': self.order_due.dateTime().toSecsSinceEpoch() if self.order_timed.isChecked() else None,
                'checklist': self.collect_items(), 'vehicle': self.vehicle.text(),
                'registration': self.registration.text(), 'vin': self.vin.text(),
                'priority': self.priority.currentData(), 'order_type': self.order_type.currentData(),
                'currency': 'JMD', 'payment_received_cents': money_to_cents(self.payment_received.text())}

    def load_business(self, note=None):
        note = note or {}
        self.kind.setCurrentIndex(max(0, self.kind.findData(note.get('kind', 'note'))))
        for field, key in ((self.customer, 'customer'), (self.contact, 'contact'), (self.order_ref, 'order_ref'),
                           (self.vehicle, 'vehicle'), (self.registration, 'registration'), (self.vin, 'vin')):
            field.setText(note.get(key, ''))
        for field, key, default in ((self.order_status, 'order_status', 'new'), (self.priority, 'priority', 'normal'),
                                    (self.order_type, 'order_type', 'order')):
            field.setCurrentIndex(max(0, field.findData(note.get(key, default))))
        self.payment_received.setText(f"{note.get('payment_received_cents', 0) // 100}.{note.get('payment_received_cents', 0) % 100:02d}")
        self.order_timed.setChecked(note.get('order_due') is not None)
        self.order_due.setDateTime(QDateTime.fromSecsSinceEpoch(int(note['order_due'])) if note.get('order_due') else QDateTime.currentDateTime().addSecs(3600))
        self.task_table.setRowCount(0)
        self.order_table.setRowCount(0)
        for item in note.get('checklist', []):
            self.insert_checklist_row(item)
        for field in (self.checklist_notes, self.order_notes):
            with QSignalBlocker(field):
                field.setPlainText(note.get('body', ''))
        self.update_checklist_progress()
        self.update_order_totals()

    def new_entry(self, kind):
        if kind not in ('list', 'order'):
            return self.new_note()
        if self.new_note():
            self.kind.setCurrentIndex(self.kind.findData(kind))
            self.title.setText('New order' if kind == 'order' else 'Checklist')
            self.show_section('orders' if kind == 'order' else 'checklists')
            (self.customer if kind == 'order' else self.item_text).setFocus()
            return True
        return False

    def apply_checklist_template(self):
        if self.kind.currentData() != 'list':
            if not self.new_entry('list'):
                return
        name = self.checklist_template.currentText()
        tasks = CHECKLIST_TEMPLATES.get(name, ())
        existing = {self.task_table.item(row, 1).text().casefold() for row in range(self.task_table.rowCount())}
        for text in tasks:
            if text.casefold() not in existing and self.task_table.rowCount() < 100:
                self.insert_checklist_row({'id': uuid.uuid4().hex, 'text': text, 'quantity': 1, 'done': False}, self.task_table)
        if self.title.text() in ('', 'Checklist'):
            self.title.setText(name + ' checklist')
        self.mark_dirty()
        self.update_checklist_progress()
        self.status.setText(name + ' tasks added. Save the checklist to keep them.')

    def update_checklist_progress(self, *args):
        count = self.task_table.rowCount()
        done = sum(bool(self.task_table.item(row, 0) and self.task_table.item(row, 0).checkState() == Qt.Checked) for row in range(count))
        self.checklist_progress.setRange(0, max(1, count))
        self.checklist_progress.setValue(done)
        self.checklist_progress.setFormat(f'{done} of {count} tasks done' if count else 'No tasks yet')

    def update_order_totals(self, *args):
        if not hasattr(self, 'order_total_label'):
            return
        try:
            items = self.collect_items(self.order_table)
            paid = money_to_cents(self.payment_received.text())
            totals = order_totals({'checklist': items, 'payment_received_cents': paid})
            with QSignalBlocker(self.order_table):
                for row, item in enumerate(items):
                    cell = self.order_table.item(row, 8)
                    if cell:
                        cell.setText(format_money(item.get('unit_price_cents', 0) * item['quantity']))
            credit = ' · Credit ' + format_money(totals['credit_cents']) if totals['credit_cents'] else ''
            self.order_total_label.setText('Total ' + format_money(totals['subtotal_cents']) + '\nBalance ' + format_money(totals['balance_cents']) + credit)
        except (ValueError, AttributeError) as exc:
            self.order_total_label.setText('Check prices / quantities: ' + str(exc))

    def remind_at_order_deadline(self):
        if self.order_timed.isChecked():
            self.due.setDateTime(self.order_due.dateTime())
            self.scheduled.setChecked(True)
            self.show_section('schedule')
            self.status.setText('Reminder set to the pickup deadline. Save to keep it.')
        else:
            self.status.setText('Enable a pickup deadline on the order first.')

    def schedule_follow_up(self):
        self.quick_time(3600)
        self.show_section('schedule')
        self.status.setText('Follow-up set for one hour from now. Choose another time if needed, then save.')

    def copy_pickup_summary(self):
        try:
            details = self.collect_business()
            totals = order_totals(details)
        except ValueError as exc:
            self.status.setText(str(exc))
            return False
        if details['kind'] != 'order':
            self.status.setText('Open an order to copy a pickup summary.')
            return False
        lines = [self.settings['business_name'] or 'Famous Twins',
                 (details['order_type'].title() + ' ' + (details['order_ref'] or self.title.text())).strip(),
                 'Customer: ' + (details['customer'] or 'Walk-in')]
        for label, key in (('Contact', 'contact'), ('Vehicle', 'vehicle'), ('Registration', 'registration'), ('VIN / chassis', 'vin')):
            if details[key]:
                lines.append(label + ': ' + details[key])
        lines.append('Status: ' + details['order_status'].title())
        if details['order_due']:
            lines.append('Pickup: ' + self.order_due.dateTime().toString('ddd, MMM d yyyy h:mm AP'))
        for item in details['checklist']:
            number = ' [' + item.get('part_number', '') + ']' if item.get('part_number') else ''
            lines.append(f"{item['quantity']} x {item['text']}{number} - {format_money(item['quantity'] * item.get('unit_price_cents', 0))}")
        lines.extend(('Total: ' + format_money(totals['subtotal_cents']), 'Paid: ' + format_money(totals['paid_cents']),
                      'Balance: ' + format_money(totals['balance_cents'])))
        if totals['credit_cents']:
            lines.append('Credit: ' + format_money(totals['credit_cents']))
        QApplication.clipboard().setText('\n'.join(lines))
        self.status.setText('Pickup summary copied. Review it before sharing with the customer.')
        return True

    def open_workspace_note(self, identifier, section=None):
        if not identifier:
            return
        if identifier == self.editing_id:
            note = self.service.store.find(identifier)
            self.show_section(section or {'order': 'orders', 'list': 'checklists'}.get((note or {}).get('kind'), 'writing'))
            return
        if not self.maybe_leave():
            return
        self.load_note(identifier)
        if section:
            self.show_section(section)

    def dashboard_focus(self, mode):
        self.dashboard_mode = mode
        self.refresh_dashboard()

    def refresh_dashboard(self):
        notes = self.service.store.notes
        summary = dashboard_summary(notes)
        for key, (button, title) in self.dashboard_cards.items():
            button.setText(str(summary[key]) + '\n' + title)
        self.dashboard_money.setText('Outstanding: ' + format_money(summary['balance_cents']) +
            f"  ·  {summary['quotes']} quotes  ·  {summary['parts_to_source']} parts to source")
        now = time.time()
        active = [n for n in notes if n.get('kind') == 'order' and not n['done'] and n.get('order_type', 'order') != 'quote' and n.get('order_status') not in ('delivered', 'cancelled')]
        if self.dashboard_mode == 'ready_orders':
            active = [n for n in active if n['order_status'] == 'ready']
        elif self.dashboard_mode == 'late_orders':
            active = [n for n in active if n.get('order_due') is not None and n['order_due'] < now]
        elif self.dashboard_mode == 'urgent_orders':
            active = [n for n in active if n.get('priority') == 'urgent']
        self.order_queue_title.setText({'open_orders': 'Open orders', 'ready_orders': 'Ready for pickup',
            'late_orders': 'Orders past deadline', 'urgent_orders': 'Urgent orders'}.get(self.dashboard_mode, 'Open orders'))
        self.order_queue.clear()
        for note in sorted(active, key=lambda n: (n.get('priority') != 'urgent', n.get('order_due') or float('inf'), -n['created'])):
            text = (note.get('order_ref') or note['title']) + ' · ' + (note.get('customer') or 'Walk-in') + ' · ' + note['order_status'].title()
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, note['id'])
            self.order_queue.addItem(item)
        if not active:
            item = QListWidgetItem('No orders in this view')
            item.setFlags(Qt.NoItemFlags)
            self.order_queue.addItem(item)
        sources = parts_to_source(notes)
        self.sourcing_table.setRowCount(len(sources))
        for row, source in enumerate(sources):
            orders = source['orders']
            values = (source['text'] + (' · ' + source['part_number'] if source['part_number'] else ''),
                      str(source['quantity']), source['supplier'], source['stock_status'].replace('_', ' ').title(),
                      ', '.join(dict.fromkeys(o['customer'] or 'Walk-in' for o in orders)))
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.UserRole, orders[0]['id'] if orders else None)
                self.sourcing_table.setItem(row, column, cell)
        self.refresh_customer_history()
        with QSignalBlocker(self.agenda):
            self.agenda.clear()
            for note in sorted((n for n in notes if not n['done'] and n.get('next_due') is not None), key=lambda n: n['next_due']):
                when = QDateTime.fromSecsSinceEpoch(int(note['next_due'])).toString('ddd, MMM d · h:mm AP')
                item = QListWidgetItem(('OVERDUE · ' if note['next_due'] <= now else '') + when + '\n' + note['title'])
                item.setData(Qt.UserRole, note['id'])
                self.agenda.addItem(item)
            if not self.agenda.count():
                item = QListWidgetItem('No timed reminders. Open an entry, choose a time above and save.')
                item.setFlags(Qt.NoItemFlags)
                self.agenda.addItem(item)

    def refresh_customer_history(self, *args):
        history = customer_history(self.service.store.notes, self.history_search.text())
        self.history_table.setRowCount(len(history))
        for row, note in enumerate(history):
            values = ((note['customer'] or 'Walk-in') + ' · ' + (note['order_ref'] or note['title']),
                      note['vehicle'], note['order_type'].title() + ' / ' + note['order_status'].title(),
                      format_money(note['subtotal_cents']), format_money(note['balance_cents']))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, note['id'])
                self.history_table.setItem(row, column, item)

    def open_sourcing_order(self, row, column):
        item = self.sourcing_table.item(row, column)
        if item:
            self.open_workspace_note(item.data(Qt.UserRole))

    def open_history_order(self, row, column):
        item = self.history_table.item(row, column)
        if item:
            self.open_workspace_note(item.data(Qt.UserRole))

    def set_transfer_busy(self, busy):
        for button in self.transfer_buttons:
            button.setEnabled(not busy)
        self.transfer_cancel.setVisible(busy)

    def cancel_transfer(self):
        self.transfer.cancel()
        self.status.setText('Notebook transfer cancelled. Your saved notes are unchanged.')

    def transfer_received(self, result):
        if result.get('kind') == 'backup_prepared':
            try:
                count = self.service.store.import_backup(result['payload'], allow_documents=True)
            except (OSError, ValueError) as exc:
                cleanup_result(result)
                self.status.setText('Nothing was imported: ' + str(exc))
                return
            # Once committed, cleanup may only touch IDs absent from the notebook.
            result.pop('_cleanup', None)
            self.service.store._discard_unreferenced(result.get('prepared_ids', []))
            self.service.changed.emit('')
            self.status.setText(f'Imported {count} new or newer notes, including their full documents.')
        else:
            self.status.setText('Notebook exported: ' + str(result.get('path', '')))

    def start_export(self, file, format):
        if self.transfer.busy:
            return False
        snapshot = notebook_snapshot(self.service.store)
        if format == 'json' and any(note.get('document_id') for note in snapshot['notes']):
            self.status.setText('Choose a desktop ZIP backup to include full documents. Mobile JSON supports small notes only.')
            return False
        self.status.setText('Exporting notebook… Use Cancel notebook transfer to stop.')
        return self.transfer.run(export_notebook, {'store': self.service.store, 'path': file,
            'snapshot': snapshot, 'format': format})

    def export_backup(self, file=None):
        if not file:
            file, _ = QFileDialog.getSaveFileName(self, 'Export notebook backup', 'JefferyNotebook.zip',
                'Desktop full backup (*.zip);;Mobile small-note backup (*.json)')
        if file:
            return self.start_export(file, 'json' if Path(file).suffix.lower() == '.json' else 'zip')
        return False

    def import_backup(self, file=None):
        if self.transfer.busy or self.intake.busy:
            self.status.setText('Finish or cancel the current document operation before importing a backup.')
            return False
        if not file:
            file, _ = QFileDialog.getOpenFileName(self, 'Import Jeffery backup', '', 'Jeffery backup (*.zip *.json)')
        if file:
            self.status.setText('Reading notebook backup… You can cancel before it is added.')
            return self.transfer.run(prepare_notebook_backup, {'store': self.service.store, 'path': file})
        return False

    def mark_dirty(self, *args):
        if not self.loading:
            self.dirty = True
            self.refresh_advice()
            self.edit_state.setText('Unsaved changes')
            self.return_to_entry.setVisible(self.entry_mismatch.isVisible())
        self.save_button.setEnabled(bool(self.title.text().strip() or self.body.toPlainText().strip()))

    def choose_repeat(self):
        value = self.repeat_choice.currentData()
        if value >= 0:
            self.repeat.setValue(value)
        self.repeat.setVisible(value < 0)

    def sync_repeat_choice(self, *args):
        index = self.repeat_choice.findData(self.repeat.value())
        with QSignalBlocker(self.repeat_choice):
            self.repeat_choice.setCurrentIndex(index if index >= 0 else self.repeat_choice.count() - 1)
        self.repeat.setVisible(index < 0)

    def quick_time(self, seconds):
        when = QDateTime.currentDateTime()
        if seconds < 0:
            when = when.addDays(1)
            when.setTime(when.time().fromString("09:00", "HH:mm"))
        else:
            when = when.addSecs(seconds)
        self.due.setDateTime(when)
        self.scheduled.setChecked(True)

    def refresh_advice(self):
        note = self.service.store.find(self.editing_id)
        source = note.get('document_source', '') if note else ''
        self.document_origin.setText('Document source: ' + source if source else '')
        self.document_origin.setVisible(bool(source))
        guidance = current_guidance(note) if note else {}
        self.refresh_ai.setEnabled(bool(note and not note["done"] and not self.dirty and self.settings["ai_share_notes"]))
        self.use_time.setVisible(bool(guidance.get("suggested_due") and guidance["suggested_due"] > time.time()))
        self.use_time.setEnabled(not self.dirty and bool(note and not note["done"]))
        self.editor_tabs.setTabText(self.section_indices['advice'], "Jeffery's advice ✓" if guidance else "Jeffery's advice")
        if self.dirty:
            self.advice_message.setText("Save your changes so Jeffery can read the latest note.")
            self.advice_step.clear()
            self.advice_time.clear()
            self.use_time.hide()
        elif guidance:
            self.advice_message.setText(guidance["reminder"])
            self.advice_step.setText("Next step: " + guidance["next_step"] if guidance["next_step"] else "")
            stamp = guidance.get("suggested_due")
            when = QDateTime.fromSecsSinceEpoch(int(stamp)).toString("ddd, MMM d · h:mm AP") if stamp else ""
            self.advice_time.setText(("Suggested time: " + when + "\n" + guidance["reason"]) if when else guidance["reason"])
        else:
            self.advice_message.setText("Save a note and Groq will read its details for a helpful reminder." if self.settings["ai_share_notes"] else "Turn on smarter reminders to connect your saved notes to Groq.")
            self.advice_step.clear()
            self.advice_time.clear()

    def request_advice(self):
        if self.editing_id and not self.dirty:
            self.ai_requested.emit(self.editing_id)

    def apply_suggested_time(self):
        note = self.service.store.find(self.editing_id)
        stamp = current_guidance(note).get("suggested_due") if note else None
        if stamp and stamp > time.time() and not self.dirty:
            self.due.setDateTime(QDateTime.fromSecsSinceEpoch(int(stamp)))
            self.scheduled.setChecked(True)
            self.show_section('schedule')
            self.status.setText("Suggested time selected. Save note to keep it.")

    def refresh(self, *args):
        with QSignalBlocker(self.list):
            self.list.clear()
            search = self.search.text().strip().casefold()
            try:
                matched = self.service.store.search_ids(search) if search else None
            except (ValueError, OSError) as exc:
                matched = set()
                self.status.setText('Search could not finish: ' + str(exc))
            mode = self.filter.currentIndex()
            for note in sorted(self.service.store.notes, key=lambda n: (n["done"], -n["created"])):
                if self._record_scope is not None and note['kind'] != self._record_scope:
                    continue
                if (matched is not None and note['id'] not in matched) or (mode == 1 and note["done"]) or (mode == 2 and not note["done"]) or (mode == 3 and note['kind'] != 'order') or (mode == 4 and note['kind'] != 'list') or (mode == 5 and note['kind'] != 'note'):
                    continue
                prefix = "✓ " if note["done"] else "• "
                label = note['order_status'].title() + ' · ' + (note['customer'] or 'Walk-in') if note['kind'] == 'order' else reminder_time(note)
                checks = note['checklist']
                progress = f" · {sum(i['done'] for i in checks)}/{len(checks)} checked" if checks else ''
                item = QListWidgetItem(prefix + note["title"] + "\n" + label + progress)
                item.setData(Qt.UserRole, note["id"])
                item.setToolTip(note["body"][:500])
                self.list.addItem(item)
                if note["id"] == self.editing_id:
                    self.list.setCurrentItem(item)
        self.record_count.setText(str(self.list.count()))
        self.browser_empty.setVisible(self.list.count() == 0)
        self.browser_empty.setText('No matching entries. Try another search.' if search else 'No entries yet. Create one to get started.')
        summary = dashboard_summary(self.service.store.notes)
        self.summary.setText(f"{summary['open_orders']} open orders · {summary['ready_orders']} ready · {summary['late_orders']} past deadline · {self.service.store.character_count():,} characters saved")
        linked = self.service.store.state["linked_file"]
        self.link_status.setText("Linked file: " + linked if linked else "No text file linked. You can use this notebook on its own.")
        note = self.service.store.find(self.editing_id)
        if note and not self.dirty and not self.business_pending:
            signature = json.dumps(note, sort_keys=True, ensure_ascii=False)
            if signature != self._loaded_note_signature:
                section = next((key for key, index in self.section_indices.items() if index == self.editor_tabs.currentIndex()), 'writing')
                document_page = self.document_page.value() if note.get('document_id') else None
                self.load_note(note['id'])
                if document_page is not None:
                    self.document_page.setValue(min(document_page, self.document_page.maximum()))
                self.show_section(section)
        elif not note and self.editing_id and not self.dirty and not self.business_pending:
            self.new_note()
            self.status.setText('The selected shared entry was removed. Create or open another entry.')
        self.update_buttons()
        self.refresh_advice()
        self.refresh_dashboard()

    def maybe_leave(self):
        if self.business_pending:
            self.status.setText('Wait for the server to finish saving this entry.')
            return False
        if not self.dirty:
            return True
        answer = QMessageBox.question(self, "Unsaved note", "Save this note before switching?", QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if answer == QMessageBox.Cancel:
            return False
        if answer == QMessageBox.Save:
            saved = self.save_note()
            return saved and not self.business_pending
        return True

    def new_note(self):
        if not self.maybe_leave():
            return False
        self.editing_id = None
        self.reload_button.hide()
        self._loaded_note_signature = None
        if self.shared_business is not None:
            self.shared_business.mark_editing(None)
        self.document_pages.hide()
        self.body.setReadOnly(False)
        self.loading = True
        self.title.clear()
        self.body.clear()
        self.load_business()
        self.repeat.setValue(self.settings["note_repeat_minutes"])
        self.scheduled.setChecked(False)
        self.due.setDateTime(QDateTime.currentDateTime().addSecs(1800))
        self.loading = False
        self.dirty = False
        with QSignalBlocker(self.list):
            self.list.clearSelection()
            self.list.setCurrentRow(-1)
        self.title.setFocus()
        self.show_section('writing')
        self.update_buttons()
        self.refresh_advice()
        return True

    def selection_changed(self, current, previous):
        if current and not self.loading:
            identifier = current.data(Qt.UserRole)
            if identifier == self.editing_id:
                return
            if not self.maybe_leave():
                self.refresh()
                return
            self.load_note(identifier)

    def load_note(self, identifier):
        note = self.service.store.find(identifier)
        if not note:
            return
        self.editing_id = identifier
        self.reload_button.hide()
        if self.shared_business is not None:
            self.shared_business.mark_editing(identifier)
        self.loading = True
        self.title.setText(note["title"])
        self.body.setPlainText(note['body'])
        self.body.setReadOnly(bool(note.get('document_id')))
        self.document_pages.setVisible(bool(note.get('document_id')))
        if note.get('document_id'):
            try:
                count = self.service.store.documents.page_count(note['document_id'])
            except (OSError, ValueError) as exc:
                count = 0
                self.status.setText('The full document could not be opened: ' + str(exc))
            with QSignalBlocker(self.document_page):
                self.document_page.setRange(1, max(1, count))
                self.document_page.setValue(1)
            self.document_size.setText(f"of {count:,} · {note.get('body_characters', 0):,} characters · read only")
            if count:
                self.show_document_page(1)
            else:
                self.document_previous.setEnabled(False)
                self.document_next.setEnabled(False)
        self.load_business(note)
        self.repeat.setValue(note["repeat_minutes"])
        self.scheduled.setChecked(note["next_due"] is not None)
        if note["next_due"] is not None:
            self.due.setDateTime(QDateTime.fromSecsSinceEpoch(int(note["next_due"])))
        self.loading = False
        self.dirty = False
        self.update_buttons()
        self.refresh_advice()
        self._loaded_note_signature = json.dumps(note, sort_keys=True, ensure_ascii=False)
        self.show_section({'order': 'orders', 'list': 'checklists'}.get(note.get('kind'), 'writing'))
        self.reading.emit()

    def show_document_page(self, value):
        note = self.service.store.find(self.editing_id)
        if not note or not note.get('document_id'):
            return
        try:
            text = self.service.store.documents.read_page(note['document_id'], max(0, value - 1))
        except (OSError, ValueError) as exc:
            self.status.setText('Document could not be opened: ' + str(exc))
            return
        with QSignalBlocker(self.body):
            self.body.setPlainText(text)
        if not text:
            self.status.setText('The full document is unavailable. Keep your notebook files and restore a desktop ZIP backup.')
        self.document_previous.setEnabled(value > 1)
        self.document_next.setEnabled(value < self.document_page.maximum())

    def set_business_pending(self, pending):
        self.business_pending = bool(pending)
        for widget in (self.editor_tabs, self.entry_heading, self.editor_actions, self.list, self.sections,
                       self.create_button, self.empty_create, self.return_to_entry):
            widget.setEnabled(not pending)
        self.update_buttons()

    def finish_saved_note(self, note, was_document=False):
        self.loading = True
        self.title.setText(note['title'])
        self.loading = False
        self.editing_id = note['id']
        self.reload_button.hide()
        self.dirty = False
        if self.shared_business is not None and note['kind'] in ('list', 'order'):
            self.shared_business.mark_editing(note['id'])
        if note.get('document_id') and not was_document:
            self.load_note(note['id'])
        self.refresh()
        self.status.setText('Saved · ' + reminder_time(note) + '.')
        if note['kind'] == 'order':
            self.business_event.emit('order_ready' if note['order_status'] == 'ready' else 'order_saved')
        elif note['kind'] == 'list':
            self.business_event.emit('checklist_saved')

    def save_note(self):
        if self.business_pending:
            return False
        try:
            due = self.due.dateTime().toSecsSinceEpoch() if self.scheduled.isChecked() else None
            details = self.collect_business()
            if details['kind'] in ('list', 'order') and not details['checklist'] and not self.body.toPlainText().strip():
                raise ValueError('Add at least one item or write the entry details before saving.')
            title = self.title.text().strip() or (details['order_ref'] or ('Order for ' + (details['customer'] or 'Walk-in')) if details['kind'] == 'order' else 'Checklist' if details['kind'] == 'list' else '')
            current = self.service.store.find(self.editing_id)
            if current and current['kind'] in ('list', 'order') and details['kind'] == 'note' and self.shared_business is not None:
                raise ValueError('Shared orders and checklists keep their business type. Create a new Writing entry for personal notes.')
            was_document = bool(current and current.get('document_id'))
            body = current['body'] if was_document else self.body.toPlainText()
            if len(body) > 10000 and (self.intake.busy or self.transfer.busy):
                self.status.setText('Finish or cancel the document operation before saving this long draft.')
                return False
            if details['kind'] in ('list', 'order') and self.shared_business is not None:
                self.set_business_pending(True)
                self.status.setText('Saving shared entry to the office server…')
                callback_received = False
                def completed(note, error):
                    nonlocal callback_received
                    callback_received = True
                    self.set_business_pending(False)
                    if error or not note:
                        self.status.setText('Not saved: ' + str(error or 'The server did not return the saved entry.') + ' Your draft is preserved.')
                        self.reload_button.setVisible(self.editing_id is not None)
                        return
                    self.finish_saved_note(note, was_document)
                begun = self.shared_business.save(self.editing_id, title, body, self.repeat.value(), due, details, completed)
                if not begun and not callback_received:
                    self.set_business_pending(False)
                    self.status.setText('Not saved. Sign in to Work Chat and check the office server connection. Your draft is preserved.')
                return bool(begun)
            note = self.service.save_note(self.editing_id, title, body, self.repeat.value(), due, details)
            self.finish_saved_note(note, was_document)
            return True
        except (OSError, ValueError) as exc:
            self.set_business_pending(False)
            QMessageBox.warning(self, 'Save entry', str(exc))
            return False

    def reload_saved(self):
        identifier = self.editing_id
        if identifier and self.maybe_leave():
            if self.service.store.find(identifier) is None:
                self.dirty = False
                self.new_note()
                self.status.setText('This shared entry was deleted. Your editor is ready for a new entry.')
                return
            self.load_note(identifier)
            self.status.setText('Loaded the latest saved entry.')

    def update_buttons(self):
        note = self.service.store.find(self.editing_id)
        self.reload_button.setEnabled(self.editing_id is not None)
        self.pin.setEnabled(note is not None and not note["done"])
        self.done_button.setEnabled(note is not None)
        self.remove.setEnabled(note is not None)
        self.reload_action.setEnabled(self.editing_id is not None)
        self.delete_action.setEnabled(note is not None)
        self.save_button.setEnabled(bool(self.title.text().strip() or self.body.toPlainText().strip()))
        self.pin.setText("Unpin" if note and note["pinned"] else "Pin")
        self.done_button.setText("Restore" if note and note["done"] else "Done")
        self.edit_state.setText('Saving to team server...' if self.business_pending else
                                'Unsaved changes' if self.dirty else 'Saved' if note else 'New entry')
        self.save_button.setText('Save ' + {'note': 'writing', 'list': 'checklist', 'order': 'order'}.get(self.kind.currentData(), 'changes'))

    def business_change(self, operation, *args, after=None):
        if self.business_pending:
            return False
        self.set_business_pending(True)
        self.status.setText('Updating shared entry on the office server…')
        callback_received = False
        def completed(note, error):
            nonlocal callback_received
            callback_received = True
            self.set_business_pending(False)
            if error:
                self.status.setText('Not updated: ' + str(error) + '. Your draft is preserved.')
                self.reload_button.setVisible(self.editing_id is not None)
                return
            if after:
                after()
            else:
                self.refresh()
                self.status.setText('Shared entry updated.')
        begun = operation(*args, completed)
        if not begun and not callback_received:
            self.set_business_pending(False)
            self.status.setText('Not updated. Sign in to Work Chat and check the office server connection.')
        return bool(begun)

    def delete_note(self):
        if not self.editing_id or self.business_pending:
            return
        if self.intake.busy or self.transfer.busy:
            self.status.setText('Finish or cancel the notebook operation before deleting an entry.')
            return
        if QMessageBox.question(self, 'Delete entry', 'Permanently delete this entry?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        note = self.service.store.find(self.editing_id)
        def deleted():
            self.dirty = False
            self.new_note()
            self.refresh()
        if note and note['kind'] in ('list', 'order') and self.shared_business is not None:
            self.business_change(self.shared_business.delete, note['id'], after=deleted)
            return
        try:
            self.service.delete(self.editing_id)
            deleted()
        except OSError as exc:
            QMessageBox.warning(self, 'Delete entry', str(exc))

    def export_notes(self, file=None):
        if not file:
            file, _ = QFileDialog.getSaveFileName(self, "Export notebook", "JefferyNotebook.txt", "Text files (*.txt)")
        return self.start_export(file, 'text') if file else False

    def toggle_pin(self):
        note = self.service.store.find(self.editing_id)
        if note:
            if note['kind'] in ('list', 'order') and self.shared_business is not None:
                self.business_change(self.shared_business.modify, note['id'], {'pinned': not note['pinned']})
                return
            try:
                self.service.modify(note['id'], pinned=not note['pinned'])
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, 'Pin entry', str(exc))

    def toggle_done(self):
        note = self.service.store.find(self.editing_id)
        if note:
            if note['kind'] in ('list', 'order') and self.shared_business is not None:
                self.business_change(self.shared_business.complete, note['id'], not note['done'])
                return
            try:
                self.service.complete(note['id'], not note['done'])
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, 'Update entry', str(exc))

    def choose_link(self):
        file, _ = QFileDialog.getOpenFileName(self, "Link a notepad text file", "", "Text files (*.txt)")
        if file:
            try:
                self.service.link(file)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Link text file", str(exc))

    def unlink(self):
        try:
            self.service.unlink()
        except OSError as exc:
            QMessageBox.warning(self, "Unlink file", str(exc))


class ReminderPopup(QWidget):
    open_requested = Signal(str)
    done_requested = Signal(str)
    snooze_requested = Signal(str)
    snooze_for_requested = Signal(str, int)

    def __init__(self, settings):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.settings = settings
        self.identifier = ""
        self.setObjectName("noteCard")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("Jeffery's reminder")
        self.resize(400, 315)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        bar = QHBoxLayout()
        self.heading = QLabel("Jeffery remembers")
        self.heading.setStyleSheet("font-size: 15px; font-weight: 600;")
        bar.addWidget(self.heading, 1)
        dismiss = QPushButton("×")
        dismiss.setFixedWidth(32)
        dismiss.clicked.connect(self.hide)
        bar.addWidget(dismiss)
        layout.addLayout(bar)
        self.title = QLabel("")
        self.title.setTextFormat(Qt.PlainText)
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(self.title)
        self.guidance = SlidingText(width=366)
        self.guidance.setObjectName("smartReminder")
        layout.addWidget(self.guidance)
        self.guidance.hide()
        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        layout.addWidget(self.body, 1)
        actions = QHBoxLayout()
        self.snooze_minutes = QComboBox()
        for minutes in (5, 15, 30, 60):
            self.snooze_minutes.addItem(f"{minutes} min", minutes)
        actions.addWidget(self.snooze_minutes)
        self.snooze_button = QPushButton("Later")
        self.snooze_button.clicked.connect(self.snooze)
        actions.addWidget(self.snooze_button)
        for label, signal in (("Open", self.open_requested), ("Done", self.done_requested)):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, s=signal: self.act(s))
            actions.addWidget(button)
        layout.addLayout(actions)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)
        self.configure()

    def configure(self):
        visible = self.isVisible()
        self.setStyleSheet(notes_style(self.settings))
        self.guidance.setStyleSheet("color: " + ui_palette(self.settings)["text"] + ";")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self.settings["always_on_top"])
        if visible:
            self.show()

    def present(self, note, kind, anchor):
        self.identifier = note["id"]
        self.heading.setText(f"{self.settings['pet_name']} {'saved a note' if kind == 'added' else 'reminds you'}")
        self.title.setText(note["title"])
        details = note['body']
        if note.get('kind') == 'order':
            details = '\n'.join(filter(None, [note.get('customer', ''), note.get('contact', ''),
                'Status: ' + note.get('order_status', 'new').title(), details]))
        if note.get('checklist'):
            details += '\n' + '\n'.join(f"[{'✓' if i['done'] else ' '}] {i['quantity']} × {i['text']}" for i in note['checklist'])
        self.body.setPlainText(details.strip())
        self.update_guidance(note)
        screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
        rect = screen.availableGeometry()
        position = [rect.right() - self.width() - 20, rect.bottom() - self.height() - 20]
        self.move(clamp_position(position, self.width(), self.height(), QApplication.screens()))
        self.show()
        self.timer.start(20000)

    def update_guidance(self, note):
        if note["id"] != self.identifier:
            return
        guidance = current_guidance(note) if self.settings["ai_share_notes"] else {}
        self.guidance.setVisible(bool(guidance))
        if guidance:
            text = guidance["reminder"]
            if guidance["next_step"]:
                text += "\nNext step: " + guidance["next_step"]
            self.guidance.set_text(text)
        else:
            self.guidance.set_text("")

    def snooze(self):
        self.snooze_for_requested.emit(self.identifier, self.snooze_minutes.currentData())
        self.timer.stop()
        self.hide()

    def enterEvent(self, event):
        self.timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.isVisible():
            self.timer.start(15000)
        super().leaveEvent(event)

    def hideEvent(self, event):
        self.guidance.stop()
        super().hideEvent(event)

    def act(self, signal):
        signal.emit(self.identifier)
        self.timer.stop()
        self.hide()


class StickyNote(QWidget):
    open_requested = Signal(str)
    unpin_requested = Signal(str)
    done_requested = Signal(str)
    position_changed = Signal(str, object)

    def __init__(self, note, settings):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.settings, self.identifier = settings, note["id"]
        self.drag_offset = None
        self.setObjectName("noteCard")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(290, 210)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        header = QHBoxLayout()
        self.title = QLabel("")
        self.title.setTextFormat(Qt.PlainText)
        self.title.setStyleSheet("font-weight: 600;")
        self.title.setWordWrap(True)
        self.title.setToolTip("Drag this title to move the note")
        self.title.installEventFilter(self)
        header.addWidget(self.title, 1)
        unpin = QPushButton("×")
        unpin.setFixedWidth(30)
        unpin.clicked.connect(lambda: self.unpin_requested.emit(self.identifier))
        header.addWidget(unpin)
        layout.addLayout(header)
        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        layout.addWidget(self.body, 1)
        buttons = QHBoxLayout()
        edit = QPushButton("Edit")
        edit.clicked.connect(lambda: self.open_requested.emit(self.identifier))
        done = QPushButton("Done")
        done.clicked.connect(lambda: self.done_requested.emit(self.identifier))
        buttons.addWidget(edit)
        buttons.addWidget(done)
        layout.addLayout(buttons)
        self.update_note(note)
        self.configure()
        rect = QApplication.primaryScreen().availableGeometry()
        position = note["pin_position"] or [rect.left() + 30, rect.top() + 60]
        self.move(clamp_position(position, self.width(), self.height(), QApplication.screens()))

    def configure(self):
        visible = self.isVisible()
        self.setStyleSheet(notes_style(self.settings))
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self.settings["always_on_top"])
        if visible:
            self.show()

    def update_note(self, note):
        self.title.setText(note["title"])
        self.body.setPlainText(note["body"])

    def eventFilter(self, watched, event):
        if watched is self.title:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self.drag_offset = event.globalPosition().toPoint() - self.pos()
                return True
            if event.type() == QEvent.MouseMove and self.drag_offset is not None:
                point = event.globalPosition().toPoint() - self.drag_offset
                self.move(clamp_position([point.x(), point.y()], self.width(), self.height(), QApplication.screens()))
                return True
            if event.type() == QEvent.MouseButtonRelease and self.drag_offset is not None:
                self.drag_offset = None
                self.position_changed.emit(self.identifier, [self.x(), self.y()])
                return True
        return super().eventFilter(watched, event)
