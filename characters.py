"""Pixel-inspired companions with soft materials and locally drawn interaction poses."""
import math
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath, QLinearGradient, QRadialGradient
from themes import PET_PALETTES

ANIMATION_STATES = ("IDLE", "WALK_LEFT", "WALK_RIGHT", "SLEEP", "EXCITED", "DRAG",
                    "PARACHUTE", "LANDING", "WAVE", "PET", "EAT", "DANCE", "JUMP",
                    "YAWN", "READ", "REMINDER", "CELEBRATE", "HOP", "PEEK",
                    "CHASE", "SHY", "PUSH", "THINK", "TALK", "LOOK",
                    "STRETCH", "SPIN", "FLIP", "ROLL", "SNEEZE", "SCARED", "LAUGH",
                    "SIT", "BALANCE", "TIPTOE", "RUN", "CLIMB", "SLIDE", "SKATE",
                    "BOUNCE", "MAGIC", "UMBRELLA", "JUGGLE", "GROOM", "SALUTE",
                    "FACEPALM", "LEAN", "HANG", "SNEAK",
                    "CHECK_STOCK", "SCAN_PART", "PACK_ORDER", "WRENCH", "HIGH_FIVE", "COFFEE")

# These desktop poses are drawn locally, so every character can use them even
# when a sprite folder only contains the original animation states.
BUSINESS_ANIMATIONS = (("Check stock", "CHECK_STOCK"), ("Scan a part", "SCAN_PART"),
                       ("Pack an order", "PACK_ORDER"), ("Turn a wrench", "WRENCH"),
                       ("High five", "HIGH_FIVE"), ("Coffee break", "COFFEE"))


def draw_character(p: QPainter, size: int, height=None, frame=0, state="IDLE", direction=1,
                   skin="mint", character="robot", gaze=(0, 0)):
    """Keep the original 32-unit footprint and extra parachute space at every size."""
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
    def tint(color, alpha):
        color = QColor(color)
        color.setAlpha(alpha)
        return color
    def panel(x, y, w, h, top, center, bottom, radius=2, outline=None):
        """A small beveled shell, lit from the upper left."""
        material = QLinearGradient(x, y, x + w * 0.7, y + h)
        material.setColorAt(0, QColor(top))
        material.setColorAt(0.38, QColor(center))
        material.setColorAt(1, QColor(bottom))
        p.setPen(QPen(QColor(outline or dark), 0.5))
        p.setBrush(material)
        p.drawRoundedRect(QRectF(x, y, w, h), radius, radius)
    def oval(x, y, w, h, color):
        p.setPen(Qt.NoPen)
        p.setBrush(color if isinstance(color, QColor) else QColor(color))
        p.drawEllipse(QRectF(x, y, w, h))
    def glow(x, y, radius, color, alpha=100):
        halo = QRadialGradient(QPointF(x, y), radius)
        halo.setColorAt(0, tint(color, alpha))
        halo.setColorAt(0.5, tint(color, alpha // 3))
        halo.setColorAt(1, tint(color, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QRectF(x - radius, y - radius, radius * 2, radius * 2))
    def stroke(path, color, width=0.4):
        pen = QPen(color if isinstance(color, QColor) else QColor(color), width)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
    def curve(x1, y1, cx, cy, x2, y2, color, width=0.4):
        path = QPainterPath(QPointF(x1, y1))
        path.quadTo(cx, cy, x2, y2)
        stroke(path, color, width)
    def eyes(y, closed, organic=False):
        """Glass eyes remain readable at the smallest supported pet size."""
        for x in (9.3, 14.7):
            x += gx * 0.65
            eye_y = y + gy * 0.4
            if closed:
                curve(x - 0.8, eye_y + 0.5, x, eye_y + (0 if state == "SLEEP" else -0.5),
                      x + 0.8, eye_y + 0.5, dark if organic else "#a0f7ed", 0.6)
            elif organic:
                oval(x - 1.05, eye_y - 0.3, 2.1, 2.6, "#f0fff5")
                oval(x - 0.85, eye_y + 0.05, 1.7, 2.05, "#50a88f")
                oval(x - 0.35, eye_y + 0.05, 0.7, 1.9, "#122a32")
                oval(x - 0.45, eye_y, 0.48, 0.6, "#ffffff")
            else:
                glow(x, eye_y + 0.8, 2, "#74edff", 100)
                panel(x - 0.8, eye_y - 0.2, 1.6, 2.3, "#e5ffff", "#91f8ee", "#36becf",
                      0.7, "#91f8ee")
                oval(x - 0.45, eye_y - 0.02, 0.4, 0.45, "#ffffff")
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
    p.setRenderHint(QPainter.Antialiasing, True)
    # A translucent contact shadow and floor light ground the virtual companion.
    # Keep both inside the existing widget so transparent desktop edges stay clean.
    airborne = state in ("JUMP", "HOP", "BOUNCE", "FLIP", "PARACHUTE", "DRAG")
    oval(5, 29.6, 22, 2, tint("#092333", 26 if airborne else 45))
    oval(8, 30, 16, 1.3, tint("#0c1b28", 35 if airborne else 80))
    if state != "SLEEP":
        p.setPen(QPen(tint("#79e9ef", 32 if airborne else 85), 0.3))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(7.5, 29.9, 17, 1.4))
    p.save()
    jump = -round(abs(math.sin(frame * math.pi / 10)) * 6) if state in ("JUMP", "HOP", "BOUNCE", "FLIP") else 0
    bob = -(frame % 2) if state in ("EXCITED", "DANCE", "CELEBRATE") else -abs(math.sin(frame * 0.4)) * 0.8 if state == "HIGH_FIVE" else 0
    p.translate(4 + ((frame // 2) % 3 - 1 if state == "DANCE" else 0), 8 + jump + bob)
    if state in ("IDLE", "LOOK", "THINK", "TALK", "PET", "READ", "SLEEP", "CHECK_STOCK", "COFFEE"):
        # Fractional motion gives a quiet breathing rhythm without moving the feet.
        breath = math.sin(frame * math.tau / 24) * 0.012
        p.translate(12, 22)
        p.scale(1 - breath * 0.35, 1 + breath)
        p.translate(-12, -22)
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
        panel(3.8, 15.8, 16.4, 6, highlight if character != "knight" else "#e4f2ff",
              light if character != "knight" else "#b3c2d8",
              middle if character != "knight" else "#647789", 2.7)
        if character != "cat":
            panel(5.4, 17.2, 13, 3.3, "#294d5d", "#182f41", "#0a1c2b", 1.4)
        for x in (8, 14):
            curve(x - 0.7, 18.8, x + 0.5, 19.4, x + 1.8, 18.8,
                  dark if character == "cat" else "#91ebdb", 0.5)
        if character == "cat":
            curve(4.5, 20.8, 0.8, 19.1, 3.2, 16, middle, 1.7)
            panel(5.8, 14.4, 2.6, 3, highlight, light, middle, 0.8)
            oval(10.3, 19.8, 2.2, 1, "#dc90aa")
        curve(5.7, 16.5, 9.5, 15.8, 13.8, 16.5, tint("#ffffff", 100), 0.3)
        block(18, 8 - frame % 2, 3, 1, "#b29aff")
        block(20, 9 - frame % 2, 1, 1, "#b29aff")
        block(18, 10 - frame % 2, 3, 1, "#b29aff")
    else:
        moving = state.startswith("WALK") or state in ("EXCITED", "DANCE", "CELEBRATE", "DRAG", "CHASE", "SHY", "PUSH", "RUN", "TIPTOE", "SKATE", "CLIMB", "GROOM", "SNEAK")
        swing = 1 if frame % 2 else -1
        blink = state in ("IDLE", "PET", "YAWN", "LAUGH", "SNEEZE") and frame % (4 if state == "LAUGH" else 20) < 2
        if character == "cat":
            # A curved tail, tapered ears and soft cheek/muzzle shapes suggest fur.
            tail_y = math.sin(frame * 0.45) * (1 if moving else 0.35)
            curve(7, 18.6, 0.8, 21 + tail_y, 3, 12.8 + tail_y, dark, 2.4)
            curve(7, 18.3, 1.4, 20.5 + tail_y, 3, 12.8 + tail_y, middle, 1.7)
            panel(7, 14, 10, 6.7, highlight, light, middle, 3)
            oval(9.2, 15.7, 5.5, 4.3, tint(highlight, 210))
            for x, lean in ((5.7, -0.5), (15.2, 0.5)):
                ear = QPainterPath(QPointF(x, 8))
                ear.lineTo(x + lean, 2.3)
                ear.quadTo(x + 0.4, 1.5, x + 4, 6)
                ear.closeSubpath()
                p.setPen(QPen(QColor(dark), 0.5))
                p.setBrush(QColor(light))
                p.drawPath(ear)
                inner = QPainterPath(QPointF(x + 0.8, 6.6))
                inner.lineTo(x + 0.7 + lean, 3.4)
                inner.lineTo(x + 2.8, 6)
                inner.closeSubpath()
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#e9a9bf"))
                p.drawPath(inner)
            panel(5, 5.7, 14, 10, highlight, light, middle, 4)
            oval(7.5, 11.8, 9, 3.3, tint(highlight, 225))
            eyes(9, blink or state == "PET", organic=True)
            # Short forehead markings and whiskers add detail without visual noise.
            for x in (10.7, 12, 13.3):
                curve(x, 6.4, x, 7.2, x + (x - 12) * 0.2, 7.7, tint(dark, 80), 0.5)
            nose = QPainterPath(QPointF(10.9, 12.3))
            nose.lineTo(13.1, 12.3)
            nose.quadTo(12, 14.1, 10.9, 12.3)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#cf7e9f"))
            p.drawPath(nose)
            curve(12, 13.1, 11.5, 14.2, 10.3, 13.6, dark, 0.3)
            curve(12, 13.1, 12.5, 14.2, 13.7, 13.6, dark, 0.3)
            for side in (-1, 1):
                for y in (12.3, 13.3):
                    curve(12 + side * 4, y, 12 + side * 6, y - 0.2,
                          12 + side * 7.9, y - 0.5, tint(highlight, 235), 0.3)
            panel(9, 15.6, 6, 1, "#c5fbff", "#69c9ce", "#399ca8", 0.4)
            glow(12, 17.2, 1.7, "#81eff7", 70)
            oval(11.35, 16.4, 1.3, 1.3, "#a6f9ff")
        elif character == "knight":
            # Cool metal highlights, visor recess and small rivets retain the knight.
            panel(7.2, 14, 9.6, 6.5, "#deebf5", "#a0b1c3", "#536b81", 1.7, "#455c71")
            panel(5.5, 3.6, 13, 11.8, "#f0f8ff", "#b9c9db", "#60788e", 3, "#455c71")
            curve(7.4, 5.2, 11.7, 3.8, 15.7, 5.1, "#f4fcff", 0.5)
            panel(6.8, 8, 10.4, 4, "#304e60", "#183348", "#0b2031", 1.3, "#607d92")
            eyes(8.9, blink)
            panel(11.35, 11.8, 1.3, 3.2, "#f5fbff", "#d0dce8", "#71899c", 0.4, "#8298ad")
            for x in (7.5, 15.7):
                oval(x, 12.6, 0.8, 0.8, "#e3f0fa")
            panel(10.3, 0.1, 3.1, 4.4, highlight, light, dark, 1)
            curve(12, 0.8, 14.3, 0.3, 15.7, 2, light, 1.7)
            panel(8.2, 15.5, 7.6, 3.8, highlight, middle, dark, 1.2)
            curve(12, 16.1, 12, 17.5, 12, 18.7, tint(highlight, 225), 0.6)
            curve(10.6, 17, 12, 17.5, 13.4, 17, tint(highlight, 225), 0.6)
            glow(12, 17.1, 2.4, "#78e6ff", 40)
        else:
            panel(10.6, 1.8, 2.8, 4.2, highlight, middle, dark, 0.8)
            antenna = "#ffd786" if state == "EXCITED" else "#a7ffff"
            glow(12, 1.7, 2.5, antenna, 80 + round(math.sin(frame * 0.4) * 20))
            panel(10.2, 0.6, 3.6, 2.2, "#efffff", antenna, middle, 1)
            panel(7, 14.8, 10, 5.9, highlight, middle, dark, 2)
            panel(4.8, 5, 14.4, 10.9, highlight, light, middle, 3)
            panel(3.9, 8.5, 1.5, 3.7, highlight, middle, dark, 0.7)
            panel(18.6, 8.5, 1.5, 3.7, light, middle, dark, 0.7)
            panel(6.4, 7.6, 11.2, 6.1, "#31586a", "#182f42", "#081a2b", 2, dark)
            curve(7.6, 8.5, 10, 7.7, 13, 8.3, tint("#c3fcff", 90), 0.4)
            eyes(9.5, blink or state == "PET")
            curve(10.5, 12.5, 12, 13.4, 13.5, 12.5, "#82dbde", 0.35)
            curve(6.4, 6.4, 11.1, 5.3, 15.9, 6.3, tint("#ffffff", 165), 0.35)
            panel(9.6, 16.6, 4.8, 2.9, "#ffebb5", "#ffd18a", "#d99452", 1, dark)
            glow(12, 18, 2.8, "#ffcb77", 50)
            for x in (10.5, 11.5, 12.5):
                curve(x, 17.3, x, 17.9, x, 18.4, "#fff5d6", 0.35)
            oval(15, 17.8, 0.7, 0.7, "#a6ffef")
        raised = state in ("WAVE", "DANCE", "CELEBRATE", "PARACHUTE", "DRAG", "HOP", "PEEK", "PUSH", "STRETCH", "JUGGLE", "MAGIC", "HANG", "SCARED", "SALUTE", "UMBRELLA", "HIGH_FIVE", "WRENCH", "SCAN_PART", "CHECK_STOCK")
        for x, y in ((4, 12 if raised else 16 + (swing if moving else 0)),
                     (18, 9 + frame % 3 if raised else 16 - (swing if moving else 0))):
            panel(x, y, 2.2, 3.8, highlight, limbs, middle, 1)
            oval(x + 0.45, y + 0.65, 0.8, 0.8, tint(highlight, 155))
        for x in (8 - (swing if moving else 0), 14 + (swing if moving else 0)):
            panel(x - 0.1, 19.8, 3.2, 2.1, middle, dark, dark, 0.8)
            curve(x + 0.55, 20.4, x + 1.5, 20.1, x + 2.4, 20.4, tint(highlight, 150), 0.3)
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
        if state == "CHECK_STOCK":
            # A stock sheet, with a marker moving between three counted rows.
            panel(16.4, 8.5, 9, 13.5, "#ffd79a", "#bd8957", "#805837", 1, "#64482e")
            panel(17.3, 9.8, 7.2, 11.1, "#fff9df", "#f4edce", "#dccca8", 0.4, "#d3be98")
            panel(19.1, 8, 3.5, 2, "#d8e9ef", "#91adb8", "#526b7c", 0.5)
            row = (frame // 4) % 3
            for i in range(3):
                y = 12.3 + i * 2.5
                panel(18.2, y, 1.2, 1.2, "#ffffff", "#e9e4d0", "#c2b492", 0.1)
                curve(20.2, y + 0.5, 21.4, y + 0.5, 23.5, y + 0.5, "#80928c", 0.45)
                if i <= row:
                    mark = QPainterPath(QPointF(18.1, y + 0.4))
                    mark.lineTo(18.7, y + 1)
                    mark.lineTo(19.7, y - 0.1)
                    stroke(mark, "#258b75", 0.6)
            curve(23.8, 12.7 + row * 2.5, 24.9, 12.1 + row * 2.5,
                  26.1, 11.4 + row * 2.5, "#ffd36c", 1)
        elif state == "SCAN_PART":
            # The moving scan line is a visual prop, never an inventory lookup.
            panel(12.8, 18.2, 12.4, 4.8, "#d8e8ef", "#9aafb9", "#4f6877", 0.9)
            panel(18.2, 18.6, 6.2, 3.4, "#fffdf0", "#fff6d9", "#e3d4aa", 0.3)
            for i, width in enumerate((0.4, 0.8, 0.4, 0.4, 0.8)):
                curve(19 + i, 19.1, 19 + i, 20, 19 + i, 21.2, "#314953", width)
            panel(20.2, 10.5, 4.4, 3.6, "#8ba9bc", "#47677c", "#203b50", 0.8)
            panel(21.2, 13.1, 1.5, 3.3, "#b7cbd4", "#5c7c8e", "#2e4859", 0.5)
            panel(21, 11.1, 2.8, 1.1, "#ffcfba", "#f28c80", "#d45964", 0.3)
            scan_y = 18.8 + (math.sin(frame * 0.45) + 1) * 1.4
            beam = QPainterPath(QPointF(22.4, 14))
            beam.lineTo(18.7, scan_y)
            beam.lineTo(24.1, scan_y)
            beam.closeSubpath()
            p.setPen(Qt.NoPen)
            p.setBrush(tint("#ff716d", 38))
            p.drawPath(beam)
            curve(18.7, scan_y, 21.3, scan_y, 24.1, scan_y, "#ff8c82", 0.55)
        elif state == "PACK_ORDER":
            # Fold a flap, tape the parcel and check its label.
            panel(6, 16.3, 13.5, 6.7, "#ecc18a", "#c8935d", "#8d603d", 0.8, "#765039")
            flap = math.sin(frame * 0.4) * 1.2
            fold = QPainterPath(QPointF(6, 16.6))
            fold.lineTo(8.2, 14.6 + flap)
            fold.lineTo(12.7, 15.3 + flap)
            fold.lineTo(12.7, 17)
            fold.closeSubpath()
            p.setPen(QPen(QColor("#9a6941"), 0.4))
            p.setBrush(QColor("#efc995"))
            p.drawPath(fold)
            panel(11.9, 16.2, 1.8, 6.6, "#fff2bd", "#e6cc8e", "#bf9f67", 0.1, "#b99661")
            panel(14.7, 18.2, 3.7, 2.8, "#fffdf2", "#f3eedb", "#d8c8a7", 0.2)
            mark = QPainterPath(QPointF(15.3, 19.5))
            mark.lineTo(16.1, 20.2)
            mark.lineTo(17.7, 18.8)
            stroke(mark, "#2a8f74", 0.6)
            panel(8.4 + math.sin(frame * 0.4) * 2.4, 14.9, 3.4, 1.7,
                  highlight, limbs, middle, 0.7)
        elif state == "WRENCH":
            # An open-ended spanner rocks over a small hexagonal bolt.
            bolt = QPainterPath()
            for i in range(6):
                angle = math.tau * i / 6
                point = QPointF(21.1 + math.cos(angle) * 2.4, 18.5 + math.sin(angle) * 2.4)
                bolt.moveTo(point) if i == 0 else bolt.lineTo(point)
            bolt.closeSubpath()
            p.setPen(QPen(QColor("#49677b"), 0.45))
            p.setBrush(QColor("#9eb5c4"))
            p.drawPath(bolt)
            oval(20.2, 17.6, 1.8, 1.8, "#4a6579")
            p.save()
            p.translate(21.1, 18.5)
            p.rotate(math.sin(frame * 0.35) * 27 - 25)
            panel(-0.8, -8.4, 1.6, 8, "#f0fbff", "#a6bece", "#59778c", 0.6, "#526f84")
            oval(-1.3, -8.7, 2.6, 2.6, "#c9dce8")
            oval(-0.55, -8.05, 1.1, 1.1, "#496579")
            head = QPainterPath(QPointF(-2.3, -1.3))
            for point in ((-2.3, 1.3), (-0.8, 2.2), (0.8, 2.2), (2.3, 1.3),
                          (2.3, -1.3), (0.9, 0.1), (-0.9, 0.1)):
                head.lineTo(*point)
            head.closeSubpath()
            p.setPen(QPen(QColor("#526f84"), 0.4))
            p.setBrush(QColor("#d8e7f1"))
            p.drawPath(head)
            p.restore()
        elif state == "HIGH_FIVE":
            lift = math.sin(frame * 0.45) * 1.2
            curve(19.2, 12, 21.4, 10, 23.2, 7 + lift, limbs, 1.9)
            panel(21.5, 4.9 + lift, 3.6, 4.1, "#fff1bd", "#ffd58a", "#c99455", 1)
            for i in range(3):
                curve(22 + i, 5.5 + lift, 22 + i, 3.6 + lift,
                      22 + i, 3.1 + lift, "#ffdda0", 0.75)
            curve(21.6, 6.6 + lift, 20.4, 5.8 + lift, 20.7, 5.2 + lift, "#ffdda0", 0.8)
            for i in range(3):
                angle = -math.pi * (0.2 + i * 0.3)
                reach = 3.4 + (frame % 4) * 0.25
                x, y = 23 + math.cos(angle) * reach, 5 + lift + math.sin(angle) * reach
                curve(x, y, x + math.cos(angle) * 0.5, y + math.sin(angle) * 0.5,
                      x + math.cos(angle), y + math.sin(angle), "#ffe8a1", 0.5)
        elif state == "COFFEE":
            sip = max(0, math.sin(frame * 0.3))
            p.save()
            p.translate(-sip * 1.6, -sip * 2.4)
            curve(21.6, 15.3, 25, 14.6, 24.1, 18.7, "#dbc0a0", 0.9)
            panel(17.5, 14.8, 5.3, 5.1, "#fff2d4", "#e9cba8", "#b88c6b", 0.9, "#956f58")
            oval(17.8, 14.5, 4.7, 1.2, "#9a644b")
            oval(18.1, 14.5, 4.1, 0.7, "#5b4037")
            for i in range(2):
                drift = math.sin(frame * 0.35 + i) * 0.55
                curve(18.6 + i * 2, 13.8, 17.9 + i * 2 + drift, 12.4,
                      18.8 + i * 2 + drift, 10.9, tint("#fff1d9", 150), 0.45)
            p.restore()
    p.restore()
    p.restore()
