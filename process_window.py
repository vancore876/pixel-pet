"""Read-only, on-demand process list. Sampling runs in the existing worker."""
from heapq import nlargest
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton)
from system_stats import format_bytes
from ui_style import apply_window_style


class ProcessWindow(QDialog):
    monitoring_changed = Signal(bool)
    home_requested = Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.setWindowTitle("Jeffery · PC activity")
        self.resize(560, 420)
        self.rows = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        title = QLabel("What's using your PC?")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        heading = QHBoxLayout()
        heading.addWidget(title, 1)
        home = QPushButton("Home")
        home.clicked.connect(self.home_requested.emit)
        heading.addWidget(home)
        layout.addLayout(heading)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Show top 20 by"))
        self.order = QComboBox()
        self.order.addItem("CPU", "cpu")
        self.order.addItem("Memory", "memory")
        self.order.currentIndexChanged.connect(self.render)
        bar.addWidget(self.order)
        bar.addStretch()
        layout.addLayout(bar)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["App / process", "CPU", "Memory", "PID"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)
        self.status = QLabel("CPU readings are ready after the second sample.")
        self.status.setWordWrap(True)
        self.status.setObjectName("subtitle")
        layout.addWidget(self.status)
        self.configure()

    def configure(self):
        apply_window_style(self, self.settings)

    def accept_rows(self, rows):
        self.rows = rows
        if self.isVisible():
            self.render()

    def render(self, *args):
        key = self.order.currentData()
        selected = nlargest(20, self.rows, key=lambda r: (r[key] or 0, r["memory"]))
        selected_rows = self.table.selectionModel().selectedRows()
        current_pid = self.table.item(selected_rows[0].row(), 3) if selected_rows else None
        current_pid = current_pid.text() if current_pid else None
        updates_enabled = self.table.updatesEnabled()
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(selected))
            self.table.clearSelection()
            for row_index, row in enumerate(selected):
                values = [row["name"], f"{row['cpu']:.1f}%" if row['cpu'] is not None else "…", format_bytes(row["memory"]), str(row["pid"])]
                for column, value in enumerate(values):
                    cell = self.table.item(row_index, column)
                    if cell is None:
                        cell = QTableWidgetItem(value)
                        if column:
                            cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                        self.table.setItem(row_index, column, cell)
                    elif cell.text() != value:
                        cell.setText(value)
                    cell.setToolTip(value)
                if str(row["pid"]) == current_pid:
                    self.table.selectRow(row_index)
        finally:
            self.table.setUpdatesEnabled(updates_enabled)
        ready = any(row["cpu"] is not None for row in self.rows)
        self.status.setText("Refreshes every 2 seconds while open. CPU is a share of total capacity." if ready else "Reading processes… CPU needs a second sample. Inaccessible processes are skipped.")

    def showEvent(self, event):
        self.rows = []
        self.render()
        self.monitoring_changed.emit(True)
        super().showEvent(event)

    def hideEvent(self, event):
        self.monitoring_changed.emit(False)
        super().hideEvent(event)
