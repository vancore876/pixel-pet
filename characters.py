"""Original pixel characters and visible interaction poses, drawn locally in Qt."""
import math
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from themes import PET_PALETTES

ANIMATION_STATES = ("IDLE", "WALK_LEFT", "WALK_RIGHT", "SLEEP", "EXCITED", "DRAG",
                    "PARACHUTE", "LANDING", "WAVE", "PET", "EAT", "DANCE", "JUMP",
                    "YAWN", "READ", "REMINDER", "CELEBRATE", "HOP", "PEEK",
                    "CHASE", "SHY", "PUSH", "THINK", "TALK", "LOOK",
                    "STRETCH", "SPIN", "FLIP", "ROLL", "SNEEZE", "SCARED", "LAUGH",
                    "SIT", "BALANCE", "TIPTOE", "RUN", "CLIMB", "SLIDE", "SKATE",
                    "BOUNCE", "MAGIC", "UMBRELLA", "JUGGLE", "GROOM", "SALUTE",
                    "FACEPALM", "LEAN", "HANG", "SNEAK")


def draw_character(p: QPainter, size: int, height=None, frame=0, state="IDLE", direction=1,
                   skin="mint", character="robot", gaze=(0, 0)):
    """32×32 canvas, with extra space above for a parachute when requested."""
    height = height or size
    unit = max(1, size // 32)
    p.save()
    p.setRenderHint(QPainter.Antialiasing, False)
    p.translate((size - unit * 32) // 2, height - unit * 32)
    p.scale(unit, unit)
    base = PET_PALETTES.get(skin, PET_PALETTES["mint"])
    dark, light, highlight, middle, limbs = base[:5]
    gx, gy = (max(-1, min(1, int(v))) for v in gaze)
    def block(x, y, w, h, color):
        p.fillRect(round(x), round(y), w, h, QColor(color))
    def heart(x, y):
        block(x, y, 2, 1, "#f4a4c7")
        block(x + 3, y, 2, 1, "#f4a4c7")
        block(x, y + 1, 5, 2, "#f4a4c7")
        block(x + 1, y + 3, 3, 1, "#f4a4c7")
        block(x + 2, y + 4, 1, 1, "#f4a4c7")
    if state == "PARACHUTE":
        top = -(height - size) // unit + 2
        sway = (frame // 2) % 3 - 1
        for x, y, w in ((8, 0, 16), (4, 2, 24), (2, 4, 28), (1, 6, 30), (1, 8, 30)):
            block(x + sway, top + y, w, 2, dark)
        for x, color in ((3, "#ffbd76"), (9, light), (15, "#8bc8f5"), (21, "#e9a9df")):
            block(x + sway, top + 6, 6, 5, color)
        block(8 + sway, top + 2, 16, 2, highlight)
        p.setPen(QPen(QColor("#d8e5ed"), 1))
        p.drawLine(3 + sway, top + 11, 9, 22)
        p.drawLine(28 + sway, top + 11, 23, 22)
        p.drawLine(10 + sway, top + 11, 11, 22)
        p.drawLine(22 + sway, top + 11, 21, 22)
    elif state in ("PET", "CELEBRATE"):
        heart(4, 3 - frame % 2)
        heart(23, 2 + frame % 2)
    elif state == "REMINDER":
        block(24, 2, 4, 1, "#ffe194")
        block(23, 3, 6, 5, "#ffcb6d")
        block(22, 8, 8, 1, "#ffcb6d")
        block(25, 9, 2, 1, "#fff0bc")
        block(18, 1, 1, 3, "#f6a08d")
        block(30, 2, 1, 3, "#f6a08d")
    elif state == "DANCE":
        block(3, 2 + frame % 3, 1, 4, "#b29aff")
        block(1, 6 + frame % 3, 3, 2, "#b29aff")
        block(28, 1, 1, 4, "#ffd786")
        block(26, 5, 3, 2, "#ffd786")
    elif state == "THINK":
        block(25, 2 + frame % 2, 3, 3, "#d4c5ff")
        block(22, 7, 2, 2, "#a699d5")
        block(20, 10, 1, 1, "#a699d5")
    elif state == "TALK":
        block(23, 2, 7, 5, "#c5ebff")
        block(22, 6, 2, 3, "#c5ebff")
        block(24, 4, 1, 1, "#476881")
        block(26, 4, 1, 1, "#476881")
        block(28, 4, 1, 1, "#476881")
    elif state == "MAGIC":
        for i in range(4):
            x, y = (frame * 3 + i * 9) % 29, (i * 7 - frame) % 13
            block(x, y, 1, 3, "#dac1ff")
            block(x - 1, y + 1, 3, 1, "#ffe194")
    elif state == "JUGGLE":
        for i, color in enumerate(("#ffbf7b", "#b29aff", "#83dbaf")):
            angle = frame * 0.55 + i * math.tau / 3
            block(15 + round(math.cos(angle) * 10), 5 + round(math.sin(angle) * 4), 3, 3, color)
    elif state == "UMBRELLA":
        for x, y, w in ((9, 1, 14), (5, 3, 22), (3, 5, 26)):
            block(x, y, w, 2, "#8bc8f5")
        block(21, 7, 1, 16, "#dceeff")
        for i in range(3):
            block((i * 11 + 1) % 31, (frame * 3 + i * 5) % 22, 1, 2, "#759fe7")
    elif state == "SCARED":
        block(3, 3 + frame % 2, 1, 5, "#ffbf7b")
        block(28, 4, 1, 4, "#ffbf7b")
    elif state == "SNEEZE":
        if frame % 8 > 4:
            for i in range(3):
                block(26 + i * 2, 13 - i * 3, 1, 2, "#b6e1ef")
    elif state == "SKATE":
        block(5, 28, 22, 2, "#b29aff")
        block(7, 30, 3, 2, "#ffc977")
        block(23, 30, 3, 2, "#ffc977")
    block(9, 31, 14, 1, "#263647")
    p.save()
    jump = -round(abs(math.sin(frame * math.pi / 10)) * 6) if state in ("JUMP", "HOP", "BOUNCE", "FLIP") else 0
    bob = -(frame % 2) if state in ("EXCITED", "DANCE", "CELEBRATE") else 0
    p.translate(4 + ((frame // 2) % 3 - 1 if state == "DANCE" else 0), 8 + jump + bob)
    if state in ("FLIP", "ROLL"):
        p.translate(12, 12)
        p.rotate((frame * (36 if state == "FLIP" else 24)) % 360)
        p.scale(0.76, 0.76)
        p.translate(-12, -12)
    elif state == "SPIN":
        p.translate(12, 0)
        p.scale(math.cos(frame * math.pi / 8) or 0.05, 1)
        p.translate(-12, 0)
    elif state in ("LEAN", "SLIDE", "BALANCE"):
        p.translate(12, 20)
        p.rotate(math.sin(frame * 0.4) * (14 if state == "BALANCE" else 22))
        p.translate(-12, -20)
    elif state == "STRETCH":
        stretch = 1 + abs(math.sin(frame * 0.3)) * 0.12
        p.translate(0, 23 * (1 - stretch))
        p.scale(1 / stretch, stretch)
    elif state in ("SIT", "SNEAK"):
        p.translate(0, 6)
        p.scale(1, 0.75)
    elif state == "SNEEZE":
        p.translate(math.sin(frame * 2) * 1.2, (frame % 8 > 4) * 2)
    elif state == "SCARED":
        p.translate((-1 if frame % 2 else 1), -1)
    if direction < 0:
        p.translate(24, 0)
        p.scale(-1, 1)
    if state == "LANDING":
        squish = 0.8 if frame % 2 == 0 else 0.9
        p.translate(0, 23 * (1 - squish))
        p.scale(1, squish)
    if state == "SLEEP":
        block(4, 16, 16, 6, light if character != "knight" else "#b3c2d8")
        block(6, 17, 12, 3, "#17394a")
        block(8, 18, 3, 1, "#91ebdb")
        block(14, 18, 3, 1, "#91ebdb")
        if character == "cat":
            block(3, 17, 3, 4, dark)
            block(6, 15, 2, 2, light)
        block(18, 8 - frame % 2, 3, 1, "#b29aff")
        block(20, 9 - frame % 2, 1, 1, "#b29aff")
        block(18, 10 - frame % 2, 3, 1, "#b29aff")
    else:
        moving = state.startswith("WALK") or state in ("EXCITED", "DANCE", "CELEBRATE", "DRAG", "CHASE", "SHY", "PUSH", "RUN", "TIPTOE", "SKATE", "CLIMB", "GROOM", "SNEAK")
        swing = 1 if frame % 2 else -1
        blink = state in ("IDLE", "PET", "YAWN", "LAUGH", "SNEEZE") and frame % (4 if state == "LAUGH" else 20) < 2
        if character == "cat":
            block(5, 6, 14, 10, dark)
            block(6, 5, 12, 10, light)
            block(6, 2, 3, 5, light)
            block(15, 2, 3, 5, light)
            block(7, 3, 1, 3, "#e9a9bf")
            block(16, 3, 1, 3, "#e9a9bf")
            block(8 + gx, 9 + gy, 2, 1 if blink else 2, "#203041")
            block(14 + gx, 9 + gy, 2, 1 if blink else 2, "#203041")
            block(11, 12, 2, 1, "#d77fa7")
            block(4, 12, 4, 1, highlight)
            block(16, 12, 4, 1, highlight)
            block(7, 15, 10, 5, light)
            block(10, 16, 4, 4, highlight)
            block(3, 15, 3, 4, dark)
            block(2, 12 + (frame % 2 if moving else 0), 2, 5, middle)
        elif character == "knight":
            block(6, 4, 12, 11, "#647789")
            block(7, 3, 10, 11, "#b9c9db")
            block(8, 4, 8, 1, "#e4f2ff")
            block(7, 8, 10, 3, "#253b4b")
            block(9 + gx, 9 + gy, 2, 1, "#8decf6")
            block(14 + gx, 9 + gy, 2, 1, "#8decf6")
            block(11, 0, 3, 4, light)
            block(14, 1, 3, 2, dark)
            block(7, 15, 10, 5, middle)
            block(11, 16, 2, 4, highlight)
            block(9, 17, 6, 1, highlight)
        else:
            block(11, 2, 2, 4, dark)
            block(10, 1, 4, 2, "#ffd786" if state == "EXCITED" else highlight)
            block(5, 6, 14, 10, dark)
            block(6, 5, 12, 10, light)
            block(7, 6, 10, 1, highlight)
            block(7, 8, 10, 5, "#182e40")
            block(9 + gx, 10 + gy, 2, 1 if blink else 2, "#8decf6")
            block(14 + gx, 10 + gy, 2, 1 if blink else 2, "#8decf6")
            block(10, 14, 4, 1, dark)
            block(7, 16, 10, 4, middle)
            block(10, 17, 4, 2, "#ffc977")
        raised = state in ("WAVE", "DANCE", "CELEBRATE", "PARACHUTE", "DRAG", "HOP", "PEEK", "PUSH", "STRETCH", "JUGGLE", "MAGIC", "HANG", "SCARED", "SALUTE", "UMBRELLA")
        block(4, 12 if raised else 16 + (swing if moving else 0), 2, 4, limbs)
        block(18, 9 + frame % 3 if raised else 16 - (swing if moving else 0), 2, 4, limbs)
        block(8 - (swing if moving else 0), 20, 3, 2, dark)
        block(14 + (swing if moving else 0), 20, 3, 2, dark)
        if state == "LAUGH":
            block(10, 13, 5, 2, "#243647")
            block(11, 14, 3, 1, "#f4a4c7")
        if state in ("FACEPALM", "SALUTE", "GROOM"):
            block(13 + frame % 2, 7 if state == "SALUTE" else 10, 5, 2, limbs)
        if state == "MAGIC":
            block(18, 12, 1, 7, "#ffbf7b")
            block(17, 10, 3, 3, "#dac1ff")
        if state == "TIPTOE":
            block(8, 22, 2, 1, limbs)
            block(15, 22, 2, 1, limbs)
        if state in ("CLIMB", "HANG"):
            block(3, 5 + frame % 4, 2, 5, limbs)
            block(19, 6 - frame % 3, 2, 5, limbs)
        if state == "SCARED":
            block(10, 12, 4, 3, "#243647")
        if state == "YAWN":
            block(11, 12, 3, 3, "#243647")
            block(17, 12, 2, 3, limbs)
        if state == "PET":
            block(9, 10, 2, 1, "#8decf6")
            block(14, 10, 2, 1, "#8decf6")
        if state == "READ":
            block(5, 16, 14, 5, "#476881")
            block(6, 16, 5, 4, "#e2ecd7")
            block(13, 16, 5, 4, "#e2ecd7")
            block(7, 17, 3, 1, "#7b9a87")
            block(14, 18, 3, 1, "#7b9a87")
        if state == "EAT":
            block(7, 19, 10, 3, "#8bc8f5")
            block(6, 18, 12, 1, "#c5ebff")
            block(10, 16 - frame % 2, 4, 2, "#e4b478")
    p.restore()
    p.restore()
