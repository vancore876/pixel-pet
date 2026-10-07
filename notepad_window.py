"""Jeffery's notebook, desktop sticky notes, and actionable reminder cards."""
from __future__ import annotations

import time
import uuid
import json
from pathlib import Path
from PySide6.QtCore import Qt, Signal, QDateTime, QTimer, QSignalBlocker, QEvent
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QDialog, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QSplitter, QListWidget, QListWidgetItem, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QCheckBox, QSpinBox, QDateTimeEdit, QFileDialog,
    QMessageBox, QComboBox, QTabWidget, QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView, QScrollArea, QProgressBar)
from config import clamp_position
from themes import palette
from notes import current_guidance, ORDER_STATUSES, business_summary, note_search_text
from sliding_text import SlidingText
from content_intake import IntakeService


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
    c = palette(settings)
    return f"""
        QDialog, QWidget#noteCard, QWidget#notePage, QWidget#advicePage {{ background: {c['bg']}; color: {c['text']}; }}
        QWidget {{ color: {c['text']}; font-family: 'Segoe UI'; font-size: 12px; }}
        QLabel#hint {{ color: {c['muted']}; }}
        QPlainTextEdit, QLineEdit, QListWidget, QSpinBox, QDateTimeEdit, QComboBox, QTableWidget {{ background: {c['panel']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 6px; }}
        QLineEdit, QSpinBox, QDateTimeEdit, QComboBox {{ min-height: 18px; }}
        QScrollArea {{ background: {c['bg']}; border: 0; }}
        QHeaderView::section {{ background: {c['panel']}; color: {c['muted']}; border: 1px solid {c['border']}; padding: 5px; }}
        QComboBox QAbstractItemView {{ background: {c['panel']}; color: {c['text']}; selection-background-color: {c['border']}; }}
        QComboBox QLineEdit {{ border: 0; padding: 0; }}
        QListWidget::item {{ padding: 9px; }}
        QListWidget::item:selected {{ background: {c['border']}; }}
        QPushButton {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 7px 11px; }}
        QPushButton:hover {{ border-color: {c['accent']}; }}
        QPushButton#primary {{ background: {c['border']}; border-color: {c['accent']}; }}
        QCheckBox {{ spacing: 6px; }}
        QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {c['muted']}; border-radius: 3px; background: {c['bg']}; }}
        QCheckBox::indicator:checked {{ background: {c['accent']}; }}
        QTabWidget::pane {{ background: {c['bg']}; border: 1px solid {c['border']}; }}
        QTabBar::tab {{ background: {c['panel']}; color: {c['muted']}; padding: 8px 12px; }}
        QTabBar::tab:selected {{ color: {c['accent']}; }}
        QScrollBar:vertical {{ background: {c['panel']}; width: 10px; }}
        QScrollBar::handle:vertical {{ background: {c['border']}; min-height: 24px; border-radius: 4px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    """


class NotepadWindow(QDialog):
    open_text_requested = Signal()
    reading = Signal()
    ai_preferences_requested = Signal(object)
    ai_requested = Signal(str)
    connection_requested = Signal()
    chat_requested = Signal()

    def __init__(self, service, settings):
        super().__init__()
        self.service, self.settings = service, settings
        self.editing_id = None
        self.loading = False
        self.dirty = False
        self.import_result = None
        self.import_saved = False
        self.import_loading = False
        self.intake = IntakeService(self)
        self.intake.completed.connect(self.imported)
        self.intake.failed.connect(self.import_failed)
        self.intake.busy_changed.connect(self.set_import_busy)
        self.setWindowTitle("Jeffery's Notepad")
        self.resize(880, min(700, QApplication.primaryScreen().availableGeometry().height() - 60))
        self.setMinimumSize(680, 540)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        header = QHBoxLayout()
        title = QLabel("Jeffery's Notepad")
        title.setStyleSheet("font-size: 21px; font-weight: 600;")
        header.addWidget(title, 1)
        self.talk_button = QPushButton("Talk to Jeffery")
        self.talk_button.clicked.connect(self.chat_requested.emit)
        header.addWidget(self.talk_button)
        new = QPushButton("+ New note")
        new.clicked.connect(self.new_note)
        header.addWidget(new)
        for label, kind in (('+ List', 'list'), ('+ Order', 'order')):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, value=kind: self.new_entry(value))
            header.addWidget(button)
        layout.addLayout(header)
        self.summary = QLabel("")
        self.summary.setObjectName("hint")
        layout.addWidget(self.summary)
        ai_bar = QHBoxLayout()
        self.smart_enabled = QCheckBox("Use Groq for smarter reminders")
        self.smart_enabled.setChecked(settings["ai_share_notes"])
        self.smart_enabled.toggled.connect(lambda value: self.ai_preferences_requested.emit({"ai_share_notes": value}))
        ai_bar.addWidget(self.smart_enabled, 1)
        self.setup_ai = QPushButton("Set up Groq")
        self.setup_ai.clicked.connect(self.connection_requested.emit)
        ai_bar.addWidget(self.setup_ai)
        layout.addLayout(ai_bar)
        self.ai_status = QLabel("Saved notes and linked .txt lines help Groq write useful reminders.")
        self.ai_status.setObjectName("hint")
        self.ai_status.setTextFormat(Qt.PlainText)
        self.ai_status.setWordWrap(True)
        layout.addWidget(self.ai_status)
        splitter = QSplitter()
        browser = QWidget()
        browse = QVBoxLayout(browser)
        browse.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search notes…")
        self.search.textChanged.connect(self.refresh)
        self.filter = QComboBox()
        self.filter.addItems(["All notes", "To do", "Done", "Orders", "Lists"])
        self.filter.currentIndexChanged.connect(self.refresh)
        browse.addWidget(self.search)
        browse.addWidget(self.filter)
        self.list = QListWidget()
        self.list.setMinimumWidth(190)
        self.list.currentItemChanged.connect(self.selection_changed)
        browse.addWidget(self.list, 1)
        splitter.addWidget(browser)
        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(8, 0, 0, 0)
        self.editor_tabs = QTabWidget()
        editor_layout.addWidget(self.editor_tabs)
        note_page = QWidget()
        note_page.setObjectName("notePage")
        form = QVBoxLayout(note_page)
        form.setContentsMargins(8, 10, 8, 8)
        self.editor_tabs.addTab(note_page, "Write and schedule")
        self.title = QLineEdit()
        self.title.setMaxLength(100)
        self.title.setPlaceholderText("Title — e.g. Finish the website")
        self.body = QPlainTextEdit()
        self.body.setPlaceholderText("What do you need to remember? Include any date, time or useful details.")
        self.body.setMinimumHeight(100)
        form.addWidget(self.title)
        self.document_origin = QLabel('')
        self.document_origin.setObjectName('hint')
        self.document_origin.setTextFormat(Qt.PlainText)
        self.document_origin.setWordWrap(True)
        self.document_origin.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.document_origin.hide()
        form.addWidget(self.document_origin)
        form.addWidget(self.body, 1)
        schedule = QFormLayout()
        self.repeat = QSpinBox()
        self.repeat.setRange(0, 1440)
        self.repeat.setSuffix(" min")
        self.repeat.setValue(settings["note_repeat_minutes"])
        repeat_row = QHBoxLayout()
        self.repeat_choice = QComboBox()
        for label, value in (("One time / no repeat", 0), ("Every 15 minutes", 15),
                             ("Every 30 minutes", 30), ("Every hour", 60), ("Every day", 1440), ("Custom", -1)):
            self.repeat_choice.addItem(label, value)
        self.repeat_choice.currentIndexChanged.connect(self.choose_repeat)
        self.repeat.valueChanged.connect(self.sync_repeat_choice)
        repeat_row.addWidget(self.repeat_choice, 1)
        repeat_row.addWidget(self.repeat)
        schedule.addRow("Repeat", repeat_row)
        self.sync_repeat_choice()
        self.scheduled = QCheckBox("Remind me at a specific time")
        self.due = QDateTimeEdit(QDateTime.currentDateTime().addSecs(1800))
        self.due.setCalendarPopup(True)
        self.due.setDisplayFormat("MMM d, yyyy  h:mm AP")
        self.due.setEnabled(False)
        self.scheduled.toggled.connect(self.due.setEnabled)
        schedule.addRow(self.scheduled)
        schedule.addRow("Next reminder", self.due)
        form.addLayout(schedule)
        quick_times = QHBoxLayout()
        for label, seconds in (("In 15 min", 900), ("In 1 hour", 3600), ("Tomorrow 9 AM", -1)):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, value=seconds: self.quick_time(value))
            quick_times.addWidget(button)
        form.addLayout(quick_times)
        hint = QLabel("Save to start reminders. Mark Done to stop them. Keep Jeffery running.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addWidget(hint)
        actions = QHBoxLayout()
        self.save_button = QPushButton("Save note")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.save_note)
        self.pin = QPushButton("Pin")
        self.pin.clicked.connect(self.toggle_pin)
        self.done_button = QPushButton("Done")
        self.done_button.clicked.connect(self.toggle_done)
        actions.addWidget(self.save_button)
        actions.addWidget(self.pin)
        actions.addWidget(self.done_button)
        self.remove = QPushButton("Delete")
        self.remove.clicked.connect(self.delete_note)
        actions.addWidget(self.remove)
        form.addLayout(actions)
        advice_page = QWidget()
        advice_page.setObjectName("advicePage")
        advice = QVBoxLayout(advice_page)
        advice.setContentsMargins(12, 14, 12, 12)
        self.advice_message = QLabel("Save a note and Jeffery will suggest a helpful reminder.")
        self.advice_message.setWordWrap(True)
        self.advice_message.setTextFormat(Qt.PlainText)
        advice.addWidget(self.advice_message)
        self.advice_step = QLabel("")
        self.advice_step.setWordWrap(True)
        self.advice_step.setTextFormat(Qt.PlainText)
        advice.addWidget(self.advice_step)
        self.advice_time = QLabel("")
        self.advice_time.setWordWrap(True)
        self.advice_time.setTextFormat(Qt.PlainText)
        advice.addWidget(self.advice_time)
        self.use_time = QPushButton("Use this time")
        self.use_time.clicked.connect(self.apply_suggested_time)
        advice.addWidget(self.use_time)
        self.refresh_ai = QPushButton("Refresh Jeffery's advice")
        self.refresh_ai.clicked.connect(self.request_advice)
        advice.addWidget(self.refresh_ai)
        privacy = QLabel("Groq reads this saved note's details. Suggested times take effect after you choose one and save the note.")
        privacy.setObjectName("hint")
        privacy.setWordWrap(True)
        advice.addWidget(privacy)
        advice.addStretch(1)
        self.editor_tabs.addTab(advice_page, "Jeffery's advice")
        business_page = QWidget()
        business_page.setObjectName('notePage')
        business = QVBoxLayout(business_page)
        info = QFormLayout()
        self.kind = QComboBox()
        for label, value in (('Note', 'note'), ('Checklist', 'list'), ('Customer order', 'order')):
            self.kind.addItem(label, value)
        info.addRow('Type', self.kind)
        self.customer = QLineEdit()
        self.customer.setMaxLength(100)
        self.customer.setPlaceholderText('Customer name or Walk-in')
        self.contact = QLineEdit()
        self.contact.setMaxLength(100)
        self.contact.setPlaceholderText('Phone or email (optional)')
        self.order_ref = QLineEdit()
        self.order_ref.setMaxLength(80)
        self.order_ref.setPlaceholderText('e.g. ORD-001')
        self.order_status = QComboBox()
        for status in ORDER_STATUSES:
            self.order_status.addItem(status.title(), status)
        self.order_timed = QCheckBox('Pickup / delivery deadline')
        self.order_due = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self.order_due.setCalendarPopup(True)
        self.order_due.setDisplayFormat('MMM d, yyyy h:mm AP')
        self.order_due.setEnabled(False)
        self.order_timed.toggled.connect(self.order_due.setEnabled)
        self.order_fields = [self.customer, self.contact, self.order_ref, self.order_status, self.order_timed, self.order_due]
        for label, field in zip(('Customer', 'Contact', 'Order number', 'Status', 'Deadline', 'At'), self.order_fields):
            info.addRow(label, field)
        self.order_form = info
        business.addLayout(info)
        self.checklist = QTableWidget(0, 3)
        self.checklist.setHorizontalHeaderLabels(['Done', 'Item / task', 'Qty'])
        self.checklist.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.checklist.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.checklist.setColumnWidth(0, 48)
        self.checklist.setColumnWidth(2, 55)
        self.checklist.setMinimumHeight(100)
        business.addWidget(self.checklist, 1)
        item_row = QHBoxLayout()
        self.item_text = QLineEdit()
        self.item_text.setMaxLength(200)
        self.item_text.setPlaceholderText('Add an item or task…')
        self.item_quantity = QSpinBox()
        self.item_quantity.setRange(1, 9999)
        add_item = QPushButton('Add')
        add_item.clicked.connect(self.add_checklist_item)
        self.item_text.returnPressed.connect(self.add_checklist_item)
        item_row.addWidget(self.item_text, 1)
        item_row.addWidget(self.item_quantity)
        item_row.addWidget(add_item)
        business.addLayout(item_row)
        line_actions = QHBoxLayout()
        remove_item = QPushButton('Remove selected item')
        remove_item.clicked.connect(self.remove_checklist_item)
        remind_pickup = QPushButton('Remind at deadline')
        remind_pickup.clicked.connect(self.remind_at_order_deadline)
        line_actions.addWidget(remove_item)
        line_actions.addWidget(remind_pickup)
        business.addLayout(line_actions)
        checklist_hint = QLabel('Check items as you prepare them, then Save note. Ready means awaiting handover; Delivered or Cancelled stops reminders.')
        checklist_hint.setWordWrap(True)
        checklist_hint.setObjectName('hint')
        business.addWidget(checklist_hint)
        save_order = QPushButton('Save note / order')
        save_order.setObjectName('primary')
        save_order.clicked.connect(self.save_note)
        business.addWidget(save_order)
        business_scroll = QScrollArea()
        business_scroll.setWidgetResizable(True)
        business_scroll.setWidget(business_page)
        self.editor_tabs.addTab(business_scroll, 'Checklist and order')
        self.build_import_tab()
        self.kind.currentIndexChanged.connect(self.kind_changed)
        self.kind_changed()
        splitter.addWidget(editor)
        splitter.setSizes([220, 540])
        layout.addWidget(splitter, 1)
        link_bar = QHBoxLayout()
        link = QPushButton("Link .txt")
        link.clicked.connect(self.choose_link)
        open_text = QPushButton("Open linked file")
        open_text.clicked.connect(self.open_text_requested.emit)
        unlink = QPushButton("Unlink")
        unlink.clicked.connect(self.unlink)
        link_bar.addWidget(link)
        link_bar.addWidget(open_text)
        link_bar.addWidget(unlink)
        export = QPushButton("Export .txt")
        export.clicked.connect(self.export_notes)
        link_bar.addWidget(export)
        transfer = QPushButton('Mobile backup')
        transfer.clicked.connect(self.export_backup)
        link_bar.addWidget(transfer)
        import_book = QPushButton('Import backup')
        import_book.clicked.connect(self.import_backup)
        link_bar.addWidget(import_book)
        layout.addLayout(link_bar)
        self.link_status = QLabel("")
        self.link_status.setObjectName("hint")
        self.link_status.setWordWrap(True)
        layout.addWidget(self.link_status)
        self.status = QLabel("Ctrl+S saves · Ctrl+N starts a note · Each new saved .txt line becomes a note")
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.shortcuts = [QShortcut(QKeySequence.Save, self), QShortcut(QKeySequence.New, self)]
        self.shortcuts[0].activated.connect(self.save_current_tab)
        self.shortcuts[1].activated.connect(self.new_note)
        for signal in (self.title.textChanged, self.body.textChanged, self.repeat.valueChanged,
                       self.scheduled.toggled, self.due.dateTimeChanged, self.kind.currentIndexChanged,
                       self.customer.textChanged, self.contact.textChanged, self.order_ref.textChanged,
                       self.order_status.currentIndexChanged, self.order_timed.toggled,
                       self.order_due.dateTimeChanged, self.checklist.itemChanged):
            signal.connect(self.mark_dirty)
        service.changed.connect(self.refresh)
        self.configure()
        self.refresh()
        self.update_buttons()

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
        pdf_hint = QLabel('Text PDFs up to 500 MB are supported. Scanned pages need OCR first.')
        pdf_hint.setObjectName('hint')
        pdf_hint.setWordWrap(True)
        pdf_row.addWidget(pdf_hint, 1)
        layout.addLayout(pdf_row)
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
        self.editor_tabs.addTab(page, 'Sources / Import')

    def set_import_busy(self, busy, status=''):
        self.import_loading = busy
        self.import_pdf_button.setEnabled(not busy)
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
            if not self.intake.import_pdf(str(path)):
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
        if not isinstance(result, dict) or not isinstance(result.get('text'), str) or not result['text'].strip():
            self.import_failed('No readable text was found. Your notebook was left unchanged.')
            return
        self.import_result = result
        self.import_saved = False
        self.import_title.setText(str(result.get('title') or 'Imported document')[:100])
        self.import_review.setPlainText(result['text'])
        sources = result.get('sources') or []
        self.import_sources.setText('\n'.join(str(source.get('url') or source.get('title') or '')
            for source in sources[:10] if isinstance(source, dict)))
        warning = ' Only part of the document was extracted; review the included text.' if result.get('truncated') else ''
        self.set_import_busy(False, 'Ready to review. Nothing has been added to your notebook yet.' + warning)

    def review_document(self, result):
        """Accept a source read by chat without replacing an unsaved preview."""
        if not self.may_replace_import():
            return False
        if self.intake.busy:
            self.cancel_import()
        self.imported(result)
        self.editor_tabs.setCurrentIndex(3)
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
        # Keep original page/section titles when the review text is unchanged.
        sections = result.get('sections') if (text == result['text'].strip() and
            title == str(result.get('title') or 'Imported document')[:100]) else None
        if not isinstance(sections, list) or not sections:
            sections = [{'title': title, 'body': text}]
        source = result.get('url') or str(result.get('filename') or result.get('title') or title)
        try:
            added = self.service.add_documents(sections, source)
        except (OSError, ValueError) as exc:
            self.import_status.setText('Nothing was saved: ' + str(exc))
            return False
        self.import_saved = True
        self.update_import_save()
        self.import_status.setText(f'Saved {len(added)} document section(s). Timed reminders are off until you choose a schedule.')
        self.status.setText('Document saved. Your current note or writing draft is unchanged.')
        return True

    def save_current_tab(self):
        if self.editor_tabs.currentIndex() == 3:
            return self.save_import()
        return self.save_note()

    def shutdown(self):
        self.intake.shutdown()

    def configure(self):
        self.setStyleSheet(notes_style(self.settings))
        with QSignalBlocker(self.smart_enabled):
            self.smart_enabled.setChecked(self.settings["ai_share_notes"])
        self.refresh_advice()

    def kind_changed(self, *args):
        is_order = self.kind.currentData() == 'order'
        for field in self.order_fields:
            field.setVisible(is_order)
            self.order_form.labelForField(field).setVisible(is_order)

    def add_checklist_item(self):
        text = self.item_text.text().strip()
        if not text or self.checklist.rowCount() >= 100:
            return
        self.insert_checklist_row({'id': uuid.uuid4().hex, 'text': text, 'quantity': self.item_quantity.value(), 'done': False})
        self.item_text.clear()
        self.item_quantity.setValue(1)
        self.mark_dirty()

    def insert_checklist_row(self, item):
        row = self.checklist.rowCount()
        self.checklist.insertRow(row)
        done = QTableWidgetItem('')
        done.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        done.setCheckState(Qt.Checked if item['done'] else Qt.Unchecked)
        done.setData(Qt.UserRole, item['id'])
        self.checklist.setItem(row, 0, done)
        self.checklist.setItem(row, 1, QTableWidgetItem(item['text']))
        self.checklist.setItem(row, 2, QTableWidgetItem(str(item['quantity'])))

    def remove_checklist_item(self):
        row = self.checklist.currentRow()
        if row >= 0:
            self.checklist.removeRow(row)
            self.mark_dirty()

    def collect_business(self):
        items = []
        for row in range(self.checklist.rowCount()):
            done, text, quantity = (self.checklist.item(row, column) for column in range(3))
            try:
                qty = int(quantity.text())
                if not 1 <= qty <= 9999 or not text.text().strip():
                    raise ValueError()
            except (ValueError, AttributeError):
                raise ValueError('Each checklist item needs text and a quantity from 1 to 9,999.')
            items.append({'id': done.data(Qt.UserRole), 'text': text.text().strip(), 'quantity': qty,
                          'done': done.checkState() == Qt.Checked})
        return {'kind': self.kind.currentData(), 'customer': self.customer.text(), 'contact': self.contact.text(),
                'order_ref': self.order_ref.text(), 'order_status': self.order_status.currentData(),
                'order_due': self.order_due.dateTime().toSecsSinceEpoch() if self.order_timed.isChecked() else None,
                'checklist': items}

    def load_business(self, note=None):
        note = note or {}
        self.kind.setCurrentIndex(max(0, self.kind.findData(note.get('kind', 'note'))))
        for field, key in ((self.customer, 'customer'), (self.contact, 'contact'), (self.order_ref, 'order_ref')):
            field.setText(note.get(key, ''))
        self.order_status.setCurrentIndex(max(0, self.order_status.findData(note.get('order_status', 'new'))))
        self.order_timed.setChecked(note.get('order_due') is not None)
        self.order_due.setDateTime(QDateTime.fromSecsSinceEpoch(int(note['order_due'])) if note.get('order_due') else QDateTime.currentDateTime().addSecs(3600))
        self.checklist.setRowCount(0)
        for item in note.get('checklist', []):
            self.insert_checklist_row(item)

    def new_entry(self, kind):
        if self.new_note():
            self.kind.setCurrentIndex(self.kind.findData(kind))
            self.title.setText('New order' if kind == 'order' else 'Checklist')
            self.editor_tabs.setCurrentIndex(2)
            self.item_text.setFocus()

    def remind_at_order_deadline(self):
        if self.order_timed.isChecked():
            self.due.setDateTime(self.order_due.dateTime())
            self.scheduled.setChecked(True)
            self.status.setText('Reminder set to the deadline. Save to keep it.')

    def export_backup(self):
        file, _ = QFileDialog.getSaveFileName(self, 'Export for Jeffery Mobile', 'JefferyNotebook.json', 'Jeffery backup (*.json)')
        if file:
            try:
                Path(file).write_text(json.dumps({'version': 2, 'notes': self.service.store.notes}, indent=2, ensure_ascii=False), encoding='utf-8')
                self.status.setText('Backup exported. Import this JSON in the mobile app.')
            except OSError as exc:
                QMessageBox.warning(self, 'Export backup', str(exc))

    def import_backup(self):
        file, _ = QFileDialog.getOpenFileName(self, 'Import Jeffery backup', '', 'Jeffery backup (*.json)')
        if file:
            try:
                path = Path(file)
                if path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError('Use a backup smaller than 8 MB.')
                count = self.service.store.import_backup(json.loads(path.read_text(encoding='utf-8')))
                self.service.changed.emit('')
                self.status.setText(f'Imported {count} new or newer notes and orders.')
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, 'Import backup', str(exc))

    def mark_dirty(self, *args):
        if not self.loading:
            self.dirty = True
            self.refresh_advice()
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
        self.editor_tabs.setTabText(1, "Jeffery's advice ✓" if guidance else "Jeffery's advice")
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
            self.editor_tabs.setCurrentIndex(0)
            self.status.setText("Suggested time selected. Save note to keep it.")

    def refresh(self, *args):
        with QSignalBlocker(self.list):
            self.list.clear()
            search = self.search.text().strip().casefold()
            mode = self.filter.currentIndex()
            for note in sorted(self.service.store.notes, key=lambda n: (n["done"], -n["created"])):
                if (search and search not in note_search_text(note).casefold()) or (mode == 1 and note["done"]) or (mode == 2 and not note["done"]) or (mode == 3 and note['kind'] != 'order') or (mode == 4 and note['kind'] != 'list'):
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
        active = sum(not n["done"] for n in self.service.store.notes)
        summary = business_summary(self.service.store.notes)
        self.summary.setText(f"{active} to do · {summary['open_orders']} open orders · {summary['ready_orders']} ready · {summary['late_orders']} past deadline")
        linked = self.service.store.state["linked_file"]
        self.link_status.setText("Linked file: " + linked if linked else "No text file linked. You can use this notebook on its own.")
        note = self.service.store.find(self.editing_id)
        if note and not self.dirty:
            self.loading = True
            self.repeat.setValue(note['repeat_minutes'])
            self.scheduled.setChecked(note['next_due'] is not None)
            if note['next_due'] is not None:
                self.due.setDateTime(QDateTime.fromSecsSinceEpoch(int(note['next_due'])))
            self.load_business(note)
            self.loading = False
        self.update_buttons()
        self.refresh_advice()

    def maybe_leave(self):
        if not self.dirty:
            return True
        answer = QMessageBox.question(self, "Unsaved note", "Save this note before switching?", QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if answer == QMessageBox.Cancel:
            return False
        return self.save_note() if answer == QMessageBox.Save else True

    def new_note(self):
        if not self.maybe_leave():
            return False
        self.editing_id = None
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
        self.editor_tabs.setCurrentIndex(0)
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
        self.loading = True
        self.title.setText(note["title"])
        self.body.setPlainText(note['body'])
        self.load_business(note)
        self.repeat.setValue(note["repeat_minutes"])
        self.scheduled.setChecked(note["next_due"] is not None)
        if note["next_due"] is not None:
            self.due.setDateTime(QDateTime.fromSecsSinceEpoch(int(note["next_due"])))
        self.loading = False
        self.dirty = False
        self.update_buttons()
        self.refresh_advice()
        self.reading.emit()

    def save_note(self):
        try:
            due = self.due.dateTime().toSecsSinceEpoch() if self.scheduled.isChecked() else None
            details = self.collect_business()
            if details['kind'] in ('list', 'order') and not details['checklist'] and not self.body.toPlainText().strip():
                raise ValueError('Add at least one item or write the order details before saving.')
            title = self.title.text().strip() or (details['order_ref'] or ('Order for ' + (details['customer'] or 'Walk-in')) if details['kind'] == 'order' else 'Checklist' if details['kind'] == 'list' else '')
            note = self.service.save_note(self.editing_id, title, self.body.toPlainText(), self.repeat.value(), due, details)
            self.loading = True
            self.title.setText(note['title'])
            self.loading = False
            self.editing_id = note["id"]
            self.dirty = False
            self.refresh()
            self.status.setText("Saved · " + reminder_time(note) + ". Jeffery's advice updates when Groq is connected.")
            return True
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Save note", str(exc))
            return False

    def update_buttons(self):
        note = self.service.store.find(self.editing_id)
        self.pin.setEnabled(note is not None and not note["done"])
        self.done_button.setEnabled(note is not None)
        self.remove.setEnabled(note is not None)
        self.save_button.setEnabled(bool(self.title.text().strip() or self.body.toPlainText().strip()))
        self.pin.setText("Unpin" if note and note["pinned"] else "Pin")
        self.done_button.setText("Restore" if note and note["done"] else "Done")

    def delete_note(self):
        if not self.editing_id:
            return
        if QMessageBox.question(self, "Delete note", "Permanently delete this note?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self.service.delete(self.editing_id)
            self.dirty = False
            self.new_note()
            self.refresh()
        except OSError as exc:
            QMessageBox.warning(self, "Delete note", str(exc))

    def export_notes(self):
        file, _ = QFileDialog.getSaveFileName(self, "Export notebook", "JefferyNotebook.txt", "Text files (*.txt)")
        if file:
            try:
                sections = [f"{'DONE' if n['done'] else 'ACTIVE'}: {n['title']}\n{n['body']}\n" + '\n'.join(f"[{'x' if i['done'] else ' '}] {i['quantity']} × {i['text']}" for i in n['checklist']) for n in self.service.store.notes]
                Path(file).write_text("\n\n".join(sections) + "\n", encoding="utf-8")
                self.status.setText("Notebook exported.")
            except OSError as exc:
                QMessageBox.warning(self, "Export notebook", str(exc))

    def toggle_pin(self):
        note = self.service.store.find(self.editing_id)
        if note:
            try:
                self.service.modify(note["id"], pinned=not note["pinned"])
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Pin note", str(exc))

    def toggle_done(self):
        note = self.service.store.find(self.editing_id)
        if note:
            try:
                self.service.complete(note["id"], not note["done"])
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Update note", str(exc))

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
        self.guidance.setStyleSheet("color: " + palette(self.settings)["text"] + ";")
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
