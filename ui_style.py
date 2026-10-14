"""The desktop workspace design system, independent of the HUD and pet skins."""
from __future__ import annotations

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication

LIGHT = {
    'bg': '#f3f6f8', 'panel': '#ffffff', 'border': '#d3dfe5',
    'text': '#19323d', 'muted': '#526b77', 'accent': '#117c70',
    'accent_text': '#ffffff', 'hover': '#edf3f5', 'soft': '#e4f3ef',
    'primary_hover': '#08675d', 'danger': '#b33344', 'disabled': '#82949d',
}
DARK = {
    'bg': '#111e28', 'panel': '#1a2c38', 'border': '#365260',
    'text': '#eff7fb', 'muted': '#a9bfca', 'accent': '#71d9be',
    'accent_text': '#102e28', 'hover': '#263e4b', 'soft': '#203f3b',
    'primary_hover': '#9de8d4', 'danger': '#ffadb6', 'disabled': '#78909d',
}


def ui_palette(settings):
    try:
        appearance = settings['interface_appearance']
    except KeyError:
        appearance = 'light'
    if appearance == 'system':
        app = QApplication.instance()
        appearance = 'dark' if app and app.styleHints().colorScheme() == Qt.ColorScheme.Dark else 'light'
    return DARK if appearance == 'dark' else LIGHT


def window_style(settings):
    c = ui_palette(settings)
    return f"""
        QDialog, QMainWindow {{ background: {c['bg']}; }}
        QWidget {{ color: {c['text']}; font-family: 'Segoe UI'; font-size: 13px; }}
        QLabel {{ background: transparent; }}
        QLabel#pageHeading {{ font-size: 25px; font-weight: 700; }}
        QLabel#sectionTitle {{ font-size: 16px; font-weight: 600; }}
        QLabel#hint, QLabel#subtitle {{ color: {c['muted']}; }}
        QLabel#eyebrow {{ color: {c['accent']}; font-size: 11px; font-weight: 700; }}
        QLabel#statusBadge {{ background: {c['soft']}; color: {c['accent']}; border-radius: 12px; padding: 5px 10px; }}
        QLabel#statusBadge[connected="false"] {{ background: {c['hover']}; color: {c['muted']}; }}
        QLabel#metricValue {{ font-size: 26px; font-weight: 700; }}
        QFrame#surface, QWidget#surface, QFrame#workspaceSidebar,
        QWidget#workspaceSidebar, QFrame#workspaceBrowser, QWidget#workspaceBrowser,
        QFrame#workspaceEditor, QWidget#workspaceEditor, QWidget#settingsPage,
        QFrame#authCard, QFrame#conversationPanel {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 12px; }}
        QFrame#hero {{ background: {c['soft']}; border: 1px solid {c['border']}; border-radius: 14px; }}
        QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: 0; }}
        QWidget#scrollViewport, QWidget#launcherViewport, QWidget#shortcutGrid {{ background: transparent; }}
        QLineEdit, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QComboBox {{
            background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 7px;
            padding: 7px 9px; min-height: 18px; selection-background-color: {c['accent']}; selection-color: {c['accent_text']};
        }}
        QLineEdit:focus, QSpinBox:focus, QDateTimeEdit:focus, QComboBox:focus,
        QTextEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {c['accent']}; }}
        QComboBox QAbstractItemView {{ background: {c['panel']}; color: {c['text']}; selection-background-color: {c['soft']}; selection-color: {c['text']}; border: 1px solid {c['border']}; padding: 4px; }}
        QTextEdit, QPlainTextEdit, QTextBrowser {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 9px; selection-background-color: {c['accent']}; selection-color: {c['accent_text']}; }}
        QPushButton, QToolButton {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 7px; padding: 8px 12px; min-height: 18px; }}
        QPushButton:hover, QToolButton:hover {{ background: {c['hover']}; border-color: {c['muted']}; }}
        QPushButton:focus, QToolButton:focus {{ border: 1px solid {c['accent']}; }}
        QPushButton:checked, QToolButton:checked {{ background: {c['soft']}; color: {c['accent']}; border-color: {c['accent']}; }}
        QPushButton#primary, QToolButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']}; font-weight: 600; }}
        QPushButton#primary:hover, QToolButton#primary:hover {{ background: {c['primary_hover']}; }}
        QPushButton#danger, QToolButton#danger {{ color: {c['danger']}; }}
        QPushButton#navButton {{ text-align: left; padding: 10px 12px; border-color: transparent; background: transparent; }}
        QPushButton#navButton:checked {{ background: {c['soft']}; color: {c['accent']}; }}
        QPushButton#metricCard {{ text-align: left; padding: 15px; font-size: 15px; }}
        QCheckBox, QRadioButton {{ spacing: 8px; padding: 4px 0; }}
        QGroupBox {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 9px; margin-top: 14px; padding: 12px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; font-weight: 600; }}
        QListWidget {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 8px; outline: 0; }}
        QListWidget::item {{ padding: 10px 8px; border-bottom: 1px solid {c['border']}; }}
        QListWidget::item:selected {{ background: {c['soft']}; color: {c['text']}; }}
        QListWidget::item:hover {{ background: {c['hover']}; }}
        QListWidget#navList, QListWidget#workspaceSections {{ border: 0; background: transparent; }}
        QListWidget#navList::item, QListWidget#workspaceSections::item {{ border: 0; border-radius: 7px; padding: 11px 10px; margin: 2px; }}
        QTableWidget, QTableView {{ background: {c['panel']}; alternate-background-color: {c['hover']}; gridline-color: {c['border']}; border: 1px solid {c['border']}; border-radius: 8px; selection-background-color: {c['soft']}; selection-color: {c['text']}; outline: 0; }}
        QHeaderView::section {{ background: {c['hover']}; color: {c['muted']}; border: 0; border-bottom: 1px solid {c['border']}; padding: 9px 6px; font-size: 12px; font-weight: 600; }}
        QTableWidget::item {{ padding: 5px; }}
        QTableWidget QLineEdit, QTableWidget QComboBox, QTableWidget QSpinBox {{ padding: 2px; min-height: 0; border-radius: 3px; }}
        QTabWidget::pane {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 9px; }}
        QTabBar::tab {{ background: {c['bg']}; color: {c['muted']}; padding: 10px 16px; }}
        QTabBar::tab:selected {{ background: {c['panel']}; color: {c['accent']}; font-weight: 600; }}
        QTabBar QToolButton {{ background: {c['panel']}; }}
        QProgressBar {{ background: {c['hover']}; border: 1px solid {c['border']}; border-radius: 5px; text-align: center; min-height: 12px; }}
        QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
        QMenu {{ background: {c['panel']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 5px; }}
        QMenu::item {{ padding: 9px 25px; border-radius: 5px; }}
        QMenu::item:selected {{ background: {c['soft']}; }}
        QMenu::separator {{ height: 1px; background: {c['border']}; margin: 5px 8px; }}
        QToolTip {{ background: {c['text']}; color: {c['panel']}; border: 0; padding: 6px; }}
        QScrollBar:vertical {{ background: transparent; width: 9px; margin: 3px; }}
        QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 3px; }}
        QScrollBar::handle {{ background: {c['border']}; border-radius: 3px; min-height: 25px; min-width: 25px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
        QWidget:disabled {{ color: {c['disabled']}; }}
        QPushButton:disabled, QPushButton#primary:disabled {{ background: {c['hover']}; color: {c['disabled']}; border-color: {c['border']}; }}
    """


def apply_window_style(widget, settings):
    widget.setStyleSheet(window_style(settings))


def ui_icon(name, settings, size=22):
    """Small code-drawn icons stay crisp without a downloaded icon dependency."""
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(size / 24, size / 24)
    painter.setPen(QPen(QColor(ui_palette(settings)['muted']), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    shapes = {
        'home': [(3, 11, 12, 3), (12, 3, 21, 11), (5, 10, 5, 21), (5, 21, 19, 21), (19, 21, 19, 10), (10, 21, 10, 15), (10, 15, 14, 15), (14, 15, 14, 21)],
        'list': [(9, 6, 21, 6), (9, 12, 21, 12), (9, 18, 21, 18), (3, 6, 5, 6), (3, 12, 5, 12), (3, 18, 5, 18)],
        'write': [(4, 20, 9, 19), (9, 19, 21, 7), (21, 7, 17, 3), (17, 3, 5, 15), (5, 15, 4, 20), (14, 6, 18, 10)],
        'orders': [(4, 7, 12, 3), (12, 3, 20, 7), (20, 7, 20, 17), (20, 17, 12, 21), (12, 21, 4, 17), (4, 17, 4, 7), (4, 7, 12, 11), (12, 11, 20, 7), (12, 11, 12, 21)],
        'calendar': [(4, 5, 20, 5), (20, 5, 20, 21), (20, 21, 4, 21), (4, 21, 4, 5), (4, 10, 20, 10), (8, 3, 8, 7), (16, 3, 16, 7), (8, 14, 10, 14), (14, 14, 16, 14)],
        'team': [(4, 4, 20, 4), (20, 4, 20, 16), (20, 16, 10, 16), (10, 16, 5, 21), (5, 21, 5, 16), (5, 16, 4, 16), (4, 16, 4, 4), (8, 9, 16, 9), (8, 12, 13, 12)],
        'spark': [(12, 3, 15, 9), (15, 9, 21, 12), (21, 12, 15, 15), (15, 15, 12, 21), (12, 21, 9, 15), (9, 15, 3, 12), (3, 12, 9, 9), (9, 9, 12, 3)],
        'settings': [(4, 7, 20, 7), (4, 17, 20, 17), (9, 4, 9, 10), (15, 14, 15, 20)],
    }
    for x1, y1, x2, y2 in shapes.get(name, shapes['home']):
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    painter.end()
    return QIcon(pixmap)
