"""Export the desktop character renderer into the mobile animation sheets.

Run from any directory with the desktop dependencies installed:
    python tools/export_pet_sprites.py
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from characters import ANIMATION_STATES, BUSINESS_ANIMATIONS, draw_character

FRAME_SIZE = 96
FRAME_COUNT = 8
# The phone renderer has a fixed 49-row contract; business poses are desktop-only.
MOBILE_STATES = tuple(state for state in ANIMATION_STATES
                      if state not in {key for _, key in BUSINESS_ANIMATIONS})


def export_sheets(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for character in ("robot", "cat", "knight"):
        sheet = QImage(
            FRAME_SIZE * FRAME_COUNT,
            FRAME_SIZE * len(MOBILE_STATES),
            QImage.Format_ARGB32_Premultiplied,
        )
        sheet.fill(Qt.transparent)
        painter = QPainter(sheet)
        try:
            for row, state in enumerate(MOBILE_STATES):
                for frame in range(FRAME_COUNT):
                    painter.save()
                    painter.translate(frame * FRAME_SIZE, row * FRAME_SIZE)
                    # Keep effects inside their cell, including the parachute.
                    painter.setClipRect(0, 0, FRAME_SIZE, FRAME_SIZE)
                    if state == "PARACHUTE":
                        full_height = FRAME_SIZE + int(FRAME_SIZE * 0.6)
                        scale = FRAME_SIZE / full_height
                        painter.translate((FRAME_SIZE - FRAME_SIZE * scale) / 2, 0)
                        painter.scale(scale, scale)
                        draw_character(painter, FRAME_SIZE, height=full_height, frame=frame,
                                       state=state, character=character)
                    else:
                        draw_character(painter, FRAME_SIZE, frame=frame,
                                       state=state, character=character,
                                       direction=-1 if state == "WALK_LEFT" else 1)
                    painter.restore()
        finally:
            painter.end()
        path = destination / f"{character}.png"
        if not sheet.save(str(path)):
            raise OSError(f"Could not save {path}")
        print(f"Exported {character}: {len(MOBILE_STATES)} states, {FRAME_COUNT} frames")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "mobile" / "assets")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    export_sheets(args.output_dir.resolve())


if __name__ == "__main__":
    main()
