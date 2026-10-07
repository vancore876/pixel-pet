"""Read-only, on-demand process list. Sampling runs in the existing worker."""
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView)
from system_stats import format_bytes
from themes import palette


class ProcessWindow(QDialog):
    monitoring_changed = Signal(bool)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.setWindowTitle("PixelSystem Buddy · Top Apps")
        self.resize(560, 420)
        self.rows = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        title = QLabel("What's using your PC?")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(title)
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
        layout.addWidget(self.table)
        self.status = QLabel("CPU readings are ready after the second sample.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.configure()

    def configure(self):
        c = palette(self.settings)
        self.setStyleSheet(f"""
            QDialog, QTableWidget {{ background: {c['bg']}; color: {c['text']}; }}
            QWidget {{ color: {c['text']}; font-family: 'Segoe UI'; font-size: 12px; }}
            QHeaderView::section {{ background: {c['panel']}; padding: 7px; border: 0; }}
            QTableWidget {{ gridline-color: {c['border']}; border: 1px solid {c['border']}; }}
            QTableWidget::item:selected {{ background: {c['border']}; }}
            QComboBox, QComboBox QAbstractItemView {{ background: {c['panel']}; padding: 5px; }}
            QScrollBar:vertical {{ background: {c['panel']}; width: 10px; }}
            QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

    def accept_rows(self, rows):
        self.rows = rows
        if self.isVisible():
            self.render()

    def render(self, *args):
        key = self.order.currentData()
        selected = sorted(self.rows, key=lambda r: (r[key] or 0, r["memory"]), reverse=True)[:20]
        self.table.setRowCount(len(selected))
        for row_index, row in enumerate(selected):
            values = [row["name"], f"{row['cpu']:.1f}%" if row['cpu'] is not None else "…", format_bytes(row["memory"]), str(row["pid"])]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                if column:
                    cell.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row_index, column, cell)
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
