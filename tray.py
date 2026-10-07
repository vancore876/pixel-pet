"""A permanent recovery path, including when the overlay passes clicks through."""
from PySide6.QtCore import Signal
from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtWidgets import QSystemTrayIcon, QMenu
from characters import draw_character


def buddy_icon(skin="mint", character="robot"):
    from PySide6.QtCore import Qt
    pixmap = QPixmap(96, 96)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    draw_character(p, 96, frame=1, skin=skin, character=character)
    p.end()
    return QIcon(pixmap)


class SystemTray(QSystemTrayIcon):
    open_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(buddy_icon(), parent)
        self.setToolTip("PixelSystem Buddy")
        self.menu = QMenu()
        self.setContextMenu(self.menu)
        self.activated.connect(self.on_activated)

    def on_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_requested.emit()
