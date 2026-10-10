"""A task-first front door to Jeffery's workspace and office conversations."""
from __future__ import annotations

import time
from PySide6.QtCore import Qt, Signal, QSignalBlocker, QTimer
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QToolButton, QMenu, QScrollArea, QWidget,
    QLineEdit, QListWidget, QListWidgetItem, QCheckBox)
from auto_parts import dashboard_summary, format_money
from characters import draw_character
from notepad_window import reminder_time
from ui_style import apply_window_style, ui_icon


def label(text, name='', wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(wrap)
    if name:
        widget.setObjectName(name)
    return widget


class HomeWindow(QDialog):
    navigate_requested = Signal(str)
    create_requested = Signal(str)
    record_requested = Signal(str)
    preference_requested = Signal(str, bool)

    def __init__(self, service, settings, focus):
        super().__init__()
        self.service, self.settings, self.focus = service, settings, focus
        self.connected = False
        self._connection_text = 'Connect to your office to share orders and checklists.'
        self._columns = 0
        self.setWindowTitle('Jeffery · Home')
        self.setMinimumSize(760, 560)
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1140, screen.width() - 60), min(790, screen.height() - 60))
        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(18)

        self.rail = QFrame()
        self.rail.setObjectName('workspaceSidebar')
        self.rail.setFixedWidth(210)
        rail = QVBoxLayout(self.rail)
        rail.setContentsMargins(14, 20, 14, 14)
        rail.setSpacing(5)
        brand = label('JEFFERY', 'eyebrow')
        brand.setStyleSheet('font-size: 17px; letter-spacing: 2px; padding: 2px 8px;')
        rail.addWidget(brand)
        self.business_name = label(settings['business_name'], 'hint', True)
        self.business_name.setContentsMargins(8, 0, 8, 18)
        rail.addWidget(self.business_name)
        self.nav_buttons = {}
        for title, key, icon in (
            ('Home', 'home', 'home'), ('Orders & quotes', 'orders', 'orders'),
            ('Checklists', 'checklists', 'list'), ('Writing', 'writing', 'write'),
            ('Schedule', 'schedule', 'calendar'), ('Coworkers', 'coworkers', 'team'),
            ('Ask Jeffery', 'assistant', 'spark')):
            button = QPushButton(title.replace('&', '&&'))
            button.setObjectName('navButton')
            button.setCheckable(key == 'home')
            button.setChecked(key == 'home')
            button.setIcon(ui_icon(icon, settings))
            button.clicked.connect(lambda checked=False, value=key: self.navigate_requested.emit(value))
            self.nav_buttons[key] = button
            rail.addWidget(button)
        rail.addStretch()
        self.quiet = QCheckBox('Quiet mode')
        self.quiet.setToolTip('Keep Jeffery visible while hiding speech and focus notifications.')
        self.quiet.toggled.connect(lambda value: self.preference_requested.emit('quiet_mode', value))
        rail.addWidget(self.quiet)
        settings_button = QPushButton('Settings')
        settings_button.setObjectName('navButton')
        settings_button.setIcon(ui_icon('settings', settings))
        settings_button.clicked.connect(lambda: self.navigate_requested.emit('settings'))
        rail.addWidget(settings_button)
        tools = QToolButton()
        tools.setText('More tools')
        tools.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(tools)
        for title, key in (('System monitor', 'monitor'), ('Top apps', 'processes'),
                           ('Quick launch', 'quick_launch'), ('Saved memory', 'memory'),
                           ('Office setup guide', 'setup')):
            menu.addAction(title, lambda checked=False, value=key: self.navigate_requested.emit(value))
        tools.setMenu(menu)
        rail.addWidget(tools)
        outer.addWidget(self.rail)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.viewport().setObjectName('scrollViewport')
        content = QWidget()
        main = QVBoxLayout(content)
        main.setContentsMargins(0, 4, 4, 4)
        main.setSpacing(18)
        heading = QHBoxLayout()
        heading.addWidget(label('Your day, in one place', 'pageHeading', True), 1)
        self.connection_badge = label('Office not connected', 'statusBadge', True)
        self.connection_badge.setProperty('connected', False)
        heading.addWidget(self.connection_badge)
        main.addLayout(heading)
        main.addWidget(label('A clear view of your work. A little help from Jeffery.', 'hint', True))

        self.hero = QFrame()
        self.hero.setObjectName('hero')
        hero = QHBoxLayout(self.hero)
        hero.setContentsMargins(20, 16, 20, 16)
        hero.setSpacing(18)
        introduction = QVBoxLayout()
        introduction.addWidget(label('What would you like to do?', 'sectionTitle', True))
        introduction.addWidget(label('Start an order, work through a checklist, or write something down.', 'hint', True))
        self.action_grid = QGridLayout()
        self.create_buttons = {}
        for text, key in (('New order', 'order'), ('New checklist', 'list'), ('Write a note', 'note')):
            button = QPushButton(text)
            button.setObjectName('primary' if key == 'order' else 'secondary')
            button.clicked.connect(lambda checked=False, value=key: self.create_requested.emit(value))
            self.create_buttons[key] = button
        introduction.addLayout(self.action_grid)
        hero.addLayout(introduction, 1)
        self.mascot = QLabel()
        self.mascot.setFixedSize(96, 96)
        hero.addWidget(self.mascot)
        main.addWidget(self.hero)

        self.metrics = {}
        self.metric_labels = {}
        self.metric_grid = QGridLayout()
        self.metric_grid.setSpacing(10)
        for key, text in (('open_orders', 'Open orders'), ('ready_orders', 'Ready for pickup'),
                          ('urgent_orders', 'Urgent orders'), ('balance_cents', 'Outstanding JMD')):
            button = QPushButton()
            button.setObjectName('metricCard')
            button.setMinimumHeight(84)
            button.setAccessibleName(text)
            stack = QVBoxLayout(button)
            stack.setContentsMargins(14, 12, 14, 12)
            stack.setSpacing(4)
            value_label = label('0', 'metricValue')
            if key == 'balance_cents':
                value_label.setStyleSheet('font-size: 18px; font-weight: 700;')
            title_label = label(text, 'hint')
            for child in (value_label, title_label):
                child.setAttribute(Qt.WA_TransparentForMouseEvents)
                stack.addWidget(child)
            self.metric_labels[key] = value_label
            button.clicked.connect(lambda checked=False: self.navigate_requested.emit('overview'))
            self.metrics[key] = (button, text)
        main.addLayout(self.metric_grid)

        connection = QFrame()
        connection.setObjectName('surface')
        row = QHBoxLayout(connection)
        row.setContentsMargins(16, 14, 16, 14)
        messages = QVBoxLayout()
        self.connection_heading = label('Bring your team together', 'sectionTitle', True)
        self.connection_hint = label(self._connection_text, 'hint', True)
        messages.addWidget(self.connection_heading)
        messages.addWidget(self.connection_hint)
        row.addLayout(messages, 1)
        self.connect_button = QPushButton('Connect to office')
        self.connect_button.clicked.connect(lambda: self.navigate_requested.emit('coworkers'))
        row.addWidget(self.connect_button)
        main.addWidget(connection)

        main.addWidget(label('Find your work', 'sectionTitle'))
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search customer, part, order reference or saved text…')
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName('Search saved workspace entries')
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(180)
        self.search_timer.timeout.connect(self.refresh_lists)
        self.search.textChanged.connect(lambda: self.search_timer.start())
        self.search.returnPressed.connect(self.open_first_result)
        main.addWidget(self.search)
        queues = QHBoxLayout()
        queues.setSpacing(14)
        self.attention_heading = label('Needs attention', 'sectionTitle')
        self.recent_heading = label('Recently saved', 'sectionTitle')
        self.attention = QListWidget()
        self.recent = QListWidget()
        self.attention.setAccessibleName('Orders and reminders needing attention')
        self.recent.setAccessibleName('Recent entries or search results')
        for title, listing in ((self.attention_heading, self.attention), (self.recent_heading, self.recent)):
            block = QVBoxLayout()
            block.setSpacing(8)
            block.addWidget(title)
            listing.setWordWrap(True)
            listing.setMinimumHeight(185)
            listing.setMaximumHeight(245)
            listing.itemClicked.connect(self.open_item)
            listing.itemActivated.connect(self.open_item)
            block.addWidget(listing)
            queues.addLayout(block, 1)
        main.addLayout(queues)

        focus_card = QFrame()
        focus_card.setObjectName('surface')
        focus_row = QHBoxLayout(focus_card)
        focus_row.setContentsMargins(16, 12, 16, 12)
        self.focus_label = label('Make time for one task', 'sectionTitle', True)
        focus_row.addWidget(self.focus_label, 1)
        self.focus_start = QPushButton('Start focus')
        self.focus_start.clicked.connect(lambda: focus.start(settings['focus_minutes']))
        self.focus_pause = QPushButton('Pause')
        self.focus_pause.clicked.connect(focus.toggle_pause)
        self.focus_reset = QPushButton('Reset')
        self.focus_reset.clicked.connect(focus.reset)
        for button in (self.focus_start, self.focus_pause, self.focus_reset):
            focus_row.addWidget(button)
        main.addWidget(focus_card)
        main.addWidget(label('Closing Home leaves Jeffery running. Use the tray menu to exit.', 'hint', True))
        main.addStretch()
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll, 1)
        service.changed.connect(self.refresh)
        focus.changed.connect(self.refresh_focus)
        self.configure()
        self.refresh()

    def configure(self):
        apply_window_style(self, self.settings)
        self.business_name.setText(self.settings['business_name'])
        with QSignalBlocker(self.quiet):
            self.quiet.setChecked(self.settings['quiet_mode'])
        pixmap = QPixmap(192, 192)
        pixmap.setDevicePixelRatio(2)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        draw_character(painter, 96, frame=2, state='WAVE', skin=self.settings['pet_palette'], character=self.settings['character'])
        painter.end()
        self.mascot.setPixmap(pixmap)
        for key, button in self.nav_buttons.items():
            icon = {'checklists': 'list', 'writing': 'write', 'schedule': 'calendar',
                    'coworkers': 'team', 'assistant': 'spark'}.get(key, key)
            button.setIcon(ui_icon(icon, self.settings))
        self.refresh_focus()

    def set_connection(self, text, connected=False):
        self.connected = bool(connected)
        self._connection_text = text
        self.connection_hint.setText(text[:220])
        self.connection_hint.setToolTip(text)
        self.connection_badge.setText('Office connected' if connected else 'Office not connected')
        self.connection_badge.setProperty('connected', bool(connected))
        self.connection_badge.style().unpolish(self.connection_badge)
        self.connection_badge.style().polish(self.connection_badge)
        self.connection_heading.setText('Your team workspace is connected' if connected else 'Bring your team together')
        self.connect_button.setText('Open team chat' if connected else 'Connect to office')

    def refresh(self, *_):
        summary = dashboard_summary(self.service.store.notes)
        for key, (button, title) in self.metrics.items():
            value = format_money(summary[key]) if key == 'balance_cents' else str(summary[key])
            self.metric_labels[key].setText(value)
            button.setAccessibleName(value + ' ' + title)
        self.refresh_lists()

    def refresh_lists(self):
        notes = self.service.store.notes
        query = self.search.text().strip()
        self.recent.clear()
        try:
            ids = self.service.store.search_ids(query) if query else None
            recent = sorted((note for note in notes if ids is None or note['id'] in ids),
                            key=lambda note: note.get('updated', note['created']), reverse=True)[:20 if query else 6]
        except (OSError, ValueError):
            recent = []
            self.add_empty(self.recent, 'Search could not finish. Try again or open the workspace.')
        self.recent_heading.setText('Search results' if query else 'Recently saved')
        for note in recent:
            self.add_entry(self.recent, note)
        if not self.recent.count():
            self.add_empty(self.recent, 'No matching entries.' if query else 'Your saved work will appear here. Start with a note or order above.')
        self.attention.clear()
        now = time.time()
        active = [note for note in notes if not note['done'] and (
            (note['kind'] == 'order' and note['order_status'] not in ('cancelled', 'delivered') and
             (note.get('priority') == 'urgent' or note['order_status'] == 'ready' or
              (note.get('order_due') is not None and note['order_due'] <= now))) or
            (note.get('next_due') is not None and note['next_due'] <= now))]
        active.sort(key=lambda note: note.get('order_due') or note.get('next_due') or float('inf'))
        for note in active[:6]:
            self.add_entry(self.attention, note)
        if not active:
            self.add_empty(self.attention, 'No urgent or overdue work in your saved entries.')

    @staticmethod
    def add_empty(listing, text):
        item = QListWidgetItem(text)
        item.setFlags(Qt.NoItemFlags)
        listing.addItem(item)

    @staticmethod
    def add_entry(listing, note):
        if note['kind'] == 'order':
            detail = ' · '.join(value for value in (note.get('order_ref'), note.get('customer'), note['order_status'].title()) if value)
        else:
            detail = ('Checklist' if note['kind'] == 'list' else 'Writing') + ' · ' + reminder_time(note)
        item = QListWidgetItem(note['title'] + '\n' + detail)
        item.setData(Qt.UserRole, note['id'])
        item.setToolTip('Open ' + note['title'])
        listing.addItem(item)

    def open_item(self, item):
        identifier = item.data(Qt.UserRole)
        if isinstance(identifier, str):
            self.record_requested.emit(identifier)

    def open_first_result(self):
        self.search_timer.stop()
        self.refresh_lists()
        if self.recent.count():
            self.open_item(self.recent.item(0))

    def refresh_focus(self, *_):
        state = self.focus.clock.state
        self.focus_label.setText(self.focus.clock.label() or f'Focus for {self.settings["focus_minutes"]} minutes')
        self.focus_start.setVisible(state in ('idle', 'complete'))
        self.focus_pause.setVisible(state in ('running', 'paused'))
        self.focus_pause.setText('Resume' if state == 'paused' else 'Pause')
        self.focus_reset.setVisible(state != 'idle')

    def resizeEvent(self, event):
        narrow = self.width() < 990
        self.rail.setFixedWidth(174 if narrow else 210)
        self.mascot.setVisible(not narrow)
        columns = 2 if narrow else 4
        if columns != self._columns:
            while self.metric_grid.count():
                self.metric_grid.takeAt(0)
            for index, (button, _) in enumerate(self.metrics.values()):
                self.metric_grid.addWidget(button, index // columns, index % columns)
            while self.action_grid.count():
                self.action_grid.takeAt(0)
            for index, button in enumerate(self.create_buttons.values()):
                self.action_grid.addWidget(button, index if narrow else 0, 0 if narrow else index)
            self._columns = columns
        super().resizeEvent(event)

    def showEvent(self, event):
        self.configure()
        self.refresh()
        super().showEvent(event)

    def shutdown(self):
        self.search_timer.stop()
        self.hide()
