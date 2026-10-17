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
                    "CHECK_STOCK", "SCAN_PART", "PACK_ORDER", "WRENCH", "HIGH_FIVE", "COFFEE",
                    "HEAD_TILT", "SNIFF", "LISTEN", "KNOCK", "DUST", "INSPECT",
                    "PERCH", "WING_FLAP", "FETCH", "CURL_UP",
                    "CARRY_BOX", "STOCK_OK", "FIND_SHELF", "PAPER_PLANE",
                    "BLOW_BUBBLES", "TAI_CHI", "STARGAZE", "TAP_FOOT")

CHARACTER_CHOICES = (("Robot", "robot"), ("Cat", "cat"), ("Knight", "knight"),
                     ("Dog", "dog"), ("Fox", "fox"), ("Owl", "owl"), ("Dragon", "dragon"))

COMPANION_ANIMATIONS = (("Tilt head", "HEAD_TILT"), ("Sniff around", "SNIFF"),
                        ("Listen", "LISTEN"), ("Knock", "KNOCK"), ("Dust off", "DUST"),
                        ("Inspect", "INSPECT"), ("Perch", "PERCH"),
                        ("Flutter", "WING_FLAP"), ("Fetch", "FETCH"), ("Curl up", "CURL_UP"),
                        ("Carry a delivery", "CARRY_BOX"), ("Stock celebration", "STOCK_OK"),
                        ("Find a shelf", "FIND_SHELF"), ("Paper plane", "PAPER_PLANE"),
                        ("Blow bubbles", "BLOW_BUBBLES"), ("Slow stretch", "TAI_CHI"),
                        ("Stargaze", "STARGAZE"), ("Tap a foot", "TAP_FOOT"))

# These desktop poses are drawn locally, so every character can use them even
# when a sprite folder only contains the original animation states.
BUSINESS_ANIMATIONS = (("Check stock", "CHECK_STOCK"), ("Scan a part", "SCAN_PART"),
                       ("Pack an order", "PACK_ORDER"), ("Turn a wrench", "WRENCH"),
                       ("High five", "HIGH_FIVE"), ("Coffee break", "COFFEE"))


def draw_character(p: QPainter, size: int, height=None, frame=0, state="IDLE", direction=1,
                   skin="mint", character="robot", gaze=(0, 0)):
    """Keep the original 32-unit footprint and extra parachute space at every size."""
    height = height or size
    # Fractional scale keeps curves and details clear at every desktop/DPI size.
    # Tiny tray icons need half a physical pixel for antialiasing at the edge.
    inset = 0.5 if size < 32 else 0
    unit = max(1, size - inset * 2) / 32
    p.save()
    p.setRenderHint(QPainter.Antialiasing, False)
    p.translate(inset, height - inset - unit * 32)
    p.scale(unit, unit)
    base = PET_PALETTES.get(skin, PET_PALETTES["mint"])
    dark, light, highlight, middle, limbs = base[:5]
    gx, gy = (max(-1, min(1, float(v))) for v in gaze)
    if state == "FIND_SHELF":
        gx, gy = math.sin(frame * 0.25) * 0.35, 0.65
    elif state in ("PAPER_PLANE", "BLOW_BUBBLES", "STARGAZE"):
        gx, gy = 0.7, -0.45 + math.sin(frame * 0.3) * 0.2
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
    def soft_oval(x, y, w, h, top, center, bottom, outline=None):
        material = QRadialGradient(QPointF(x + w * 0.35, y + h * 0.22), max(w, h) * 0.85)
        material.setColorAt(0, QColor(top))
        material.setColorAt(0.55, QColor(center))
        material.setColorAt(1, QColor(bottom))
        p.setBrush(material)
        p.setPen(QPen(QColor(outline), 0.35) if outline else Qt.NoPen)
        p.drawEllipse(QRectF(x, y, w, h))
    def shape(path, top, center, bottom, outline=None):
        bounds = path.boundingRect()
        material = QLinearGradient(bounds.topLeft(), bounds.bottomRight())
        material.setColorAt(0, QColor(top))
        material.setColorAt(0.5, QColor(center))
        material.setColorAt(1, QColor(bottom))
        p.setPen(QPen(QColor(outline), 0.4) if outline else Qt.NoPen)
        p.setBrush(material)
        p.drawPath(path)
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
    def eyes(y, closed, organic=False, iris="#50a88f", slit=True):
        """Glass eyes remain readable at the smallest supported pet size."""
        for x in (9.3, 14.7):
            x += gx * 0.65
            eye_y = y + gy * 0.4
            if closed:
                curve(x - 0.8, eye_y + 0.5, x, eye_y + (0 if state == "SLEEP" else -0.5),
                      x + 0.8, eye_y + 0.5, dark if organic else "#a0f7ed", 0.6)
            elif organic:
                oval(x - 1.05, eye_y - 0.3, 2.1, 2.6, "#f0fff5")
                oval(x - 0.85, eye_y + 0.05, 1.7, 2.05, iris)
                oval(x - (0.35 if slit else 0.6), eye_y + 0.05,
                     0.7 if slit else 1.2, 1.9, "#122a32")
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
        top = -(height - size) / unit + 2
        sway = math.sin(frame * 0.3) * 0.65
        for x, y, w in ((8, 0, 16), (4, 2, 24), (2, 4, 28), (1, 6, 30), (1, 8, 30)):
            block(x + sway, top + y, w, 2, dark)
        for x, color in ((3, "#ffbd76"), (9, light), (15, "#8bc8f5"), (21, "#e9a9df")):
            block(x + sway, top + 6, 6, 5, color)
        block(8 + sway, top + 2, 16, 2, highlight)
        p.setPen(QPen(QColor("#d8e5ed"), 1))
        p.drawLine(QPointF(3 + sway, top + 11), QPointF(9, 22))
        p.drawLine(QPointF(28 + sway, top + 11), QPointF(23, 22))
        p.drawLine(QPointF(10 + sway, top + 11), QPointF(11, 22))
        p.drawLine(QPointF(22 + sway, top + 11), QPointF(21, 22))
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
    jump = -abs(math.sin(frame * math.pi / 10)) * 6 if state in ("JUMP", "HOP", "BOUNCE", "FLIP") else 0
    bob = -abs(math.sin(frame * math.pi / 2)) if state in ("EXCITED", "DANCE", "CELEBRATE") else -abs(math.sin(frame * 0.4)) * 0.8 if state == "HIGH_FIVE" else 0
    p.translate(4 + (math.sin(frame * 0.6) if state == "DANCE" else 0), 8 + jump + bob)
    if state in ("IDLE", "LOOK", "THINK", "TALK", "PET", "READ", "SLEEP", "CHECK_STOCK", "COFFEE", "HEAD_TILT", "LISTEN", "CURL_UP", "PERCH", "INSPECT", "FIND_SHELF", "PAPER_PLANE", "BLOW_BUBBLES", "TAI_CHI", "STARGAZE", "TAP_FOOT"):
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
        p.translate(math.sin(frame * math.pi) * 0.8, -1)
    elif state in ("HEAD_TILT", "LISTEN", "SNIFF", "KNOCK"):
        p.translate(12, 20)
        tilt = {"HEAD_TILT": 8, "LISTEN": -6, "SNIFF": 7, "KNOCK": 3}[state]
        p.rotate(tilt * (0.55 + math.sin(frame * 0.3) * 0.45))
        p.translate(-12, -20)
        if state == "SNIFF":
            p.translate(math.sin(frame * 0.75) * 0.4, 1.2)
            p.scale(1, 0.94)
    elif state == "PERCH":
        p.translate(0, -1.2 + math.sin(frame * 0.28) * 0.2)
        p.scale(1, 0.92)
    elif state == "FETCH":
        p.translate(math.sin(frame * 0.5) * 0.5, -abs(math.sin(frame * 0.55)) * 0.8)
    elif state == "WING_FLAP":
        p.translate(0, -abs(math.sin(frame * 0.4)) * 1.5)
    elif state == "CARRY_BOX":
        p.translate(math.sin(frame * 0.5) * 0.25, -abs(math.sin(frame * 0.5)) * 0.55)
    elif state == "STOCK_OK":
        p.translate(0, -abs(math.sin(frame * 0.45)) * 0.8)
    elif state == "TAI_CHI":
        p.translate(12, 20)
        p.rotate(math.sin(frame * 0.2) * 3)
        p.translate(-12, -20)
    if direction < 0:
        p.translate(24, 0)
        p.scale(-1, 1)
    if state == "LANDING":
        squish = 0.85 - math.cos(frame * math.pi) * 0.05
        p.translate(0, 23 * (1 - squish))
        p.scale(1, squish)
    organic = character in ("cat", "dog", "fox", "owl", "dragon")
    coats = {"cat": (highlight, light, middle),
             "dog": ("#e7c497", "#c39463", "#765238"),
             "fox": ("#ffd59e", "#e7954e", "#a84f29"),
             "owl": ("#e2ccab", "#ac8966", "#675040"),
             "dragon": ("#b6dfc1", "#71b59a", "#366b68")}
    coat = coats.get(character, (highlight, light, middle))
    if state in ("SLEEP", "CURL_UP") and organic:
        # Curled spines, folded limbs and a rising flank keep sleeping animals
        # recognisable instead of turning every species into a sleeping box.
        soft_oval(4.2, 12.4, 16.6, 9.5, *coat, outline=dark)
        soft_oval(5.2, 14.8, 9, 6.7, *coat)
        if character == "owl":
            soft_oval(12, 13.8, 7.3, 6.7, coat[0], coat[1], coat[2])
            for i in range(3):
                curve(14 + i, 15, 16 + i, 17, 14 + i, 19.5, tint(coat[2], 155), 0.4)
            soft_oval(5.4, 15.2, 7.7, 5.6, "#fff0d7", "#e5d0ac", coat[1])
            oval(8.7, 18.8, 1.4, 1.5, "#c68d47")
        else:
            ear = QPainterPath(QPointF(5.7, 16.2))
            ear.quadTo(4.8, 12.2 if character != "dog" else 15.1, 7.8, 14)
            ear.lineTo(9, 16.3)
            ear.closeSubpath()
            shape(ear, *coat, outline=dark)
            if character == "dog":
                soft_oval(4.1, 15.2, 3.2, 5.7, coat[1], coat[2], "#594334")
            if character == "dragon":
                curve(12, 13.7, 12, 10.4, 13.3, 11.7, "#ede0ab", 1.2)
                curve(14, 14.2, 17, 11.2, 19.5, 16, coat[2], 1)
            soft_oval(7, 18.1, 6.4, 2.6, "#fff0d6", "#e5cfad", coat[1])
            oval(8.2, 18.4, 1.8, 1.1, "#775148" if character == "cat" else "#343b39")
            tail = QPainterPath(QPointF(18, 16))
            tail.cubicTo(24, 18, 21, 23, 11, 21)
            stroke(tail, coat[2], 3.1 if character == "fox" else 2)
            stroke(tail, coat[0], 2.1 if character == "fox" else 1)
        curve(6.7, 17.8, 7.5, 18.4, 8.3, 17.8, "#473f38", 0.5)
        curve(10.4, 17.8, 11.2, 18.4, 12, 17.8, "#473f38", 0.5)
        oval(9, 20.7, 3, 1, tint(coat[0], 220))
        drift = math.sin(frame * 0.25) * 0.7
        curve(18, 8 + drift, 20, 7.5 + drift, 20.5, 9 + drift, "#afa2e3", 0.65)
        curve(20.5, 9 + drift, 20, 10 + drift, 18.5, 11 + drift, "#afa2e3", 0.65)
        curve(18.5, 11 + drift, 20, 11.2 + drift, 21, 10.7 + drift, "#afa2e3", 0.65)
        if state == "CURL_UP":
            curve(5, 11.7, 8 + math.sin(frame * 0.3), 10.7, 11, 11.5, tint(coat[0], 180), 0.5)
    elif state in ("SLEEP", "CURL_UP"):
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
        moving = state.startswith("WALK") or state in ("EXCITED", "DANCE", "CELEBRATE", "DRAG", "CHASE", "SHY", "PUSH", "RUN", "TIPTOE", "SKATE", "CLIMB", "GROOM", "SNEAK", "FETCH", "CARRY_BOX", "STOCK_OK")
        swing = math.sin(frame * math.pi / 2)
        blink = state in ("IDLE", "PET", "YAWN", "LAUGH", "SNEEZE") and frame % (4 if state == "LAUGH" else 20) < 2
        if character == "cat":
            # A curved tail, tapered ears and soft cheek/muzzle shapes suggest fur.
            tail_y = math.sin(frame * 0.45) * (1 if moving else 0.35)
            curve(7, 18.6, 0.8, 21 + tail_y, 3, 12.8 + tail_y, dark, 2.4)
            curve(7, 18.3, 1.4, 20.5 + tail_y, 3, 12.8 + tail_y, middle, 1.7)
            soft_oval(6.8, 12.5, 10.4, 8.5, highlight, light, middle, dark)
            soft_oval(9.2, 15, 5.5, 5.5, highlight, light, middle)
            for x, lean in ((5.7, -0.5), (15.2, 0.5)):
                lean += math.sin(frame * 0.26 + x) * (0.45 if state == "LISTEN" else 0.12)
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
            head = QPainterPath(QPointF(5.5, 8))
            head.cubicTo(5, 3.7, 18.5, 3.7, 18.6, 8.3)
            head.cubicTo(20, 11.2, 18.3, 15.5, 12, 15.9)
            head.cubicTo(6.3, 15.7, 4.2, 11.8, 5.5, 8)
            shape(head, highlight, light, middle, dark)
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
        elif character in ("dog", "fox"):
            fox = character == "fox"
            # Weight-bearing haunches and a curved muzzle make both animals read
            # as animals even with a folder or a work prop in their front paws.
            tail = QPainterPath(QPointF(16.2, 18.2))
            wag = math.sin(frame * (0.7 if state in ("EXCITED", "FETCH") else 0.3))
            tail.cubicTo(22, 21, 25.5, 14 + wag, 21, 11.5 + wag)
            stroke(tail, coat[2], 3.3 if fox else 1.8)
            stroke(tail, coat[1], 2.5 if fox else 1.1)
            curve(23, 15.3 + wag, 23.4, 12.7 + wag, 21, 11.5 + wag,
                  "#fff0d3" if fox else coat[0], 2 if fox else 0.9)
            soft_oval(6.7, 12.6, 10.8, 8.7, *coat, outline=coat[2])
            soft_oval(8.3, 14.5, 6.8, 6.6, "#fff1dc", "#e5cdad", coat[1])
            ear_wag = math.sin(frame * 0.35) * (0.6 if state == "LISTEN" else 0.25)
            if fox:
                for left in (True, False):
                    x = 5.6 if left else 15.1
                    tip = x - 0.7 - ear_wag if left else x + 3.9 + ear_wag
                    ear = QPainterPath(QPointF(x, 8))
                    ear.quadTo(tip - 0.2, 0, tip + 0.5, 1)
                    ear.lineTo(x + 4, 6.5)
                    ear.closeSubpath()
                    shape(ear, *coat, outline=coat[2])
                    curve(x + 1.2, 6.3, tip + 0.8, 2.9, tip + 0.5, 2.3, "#6b453e", 1)
            else:
                for x, angle in ((4.5, -1), (16.1, 1)):
                    p.save()
                    p.translate(x + 1.8, 6)
                    p.rotate(angle * (8 + ear_wag * 8))
                    soft_oval(-1.8, -1, 4.3, 10.7, coat[1], coat[2], "#523a2b", coat[2])
                    curve(-0.5, 1, -0.8, 5, 0.2, 7.2, tint(coat[0], 110), 0.45)
                    p.restore()
            head = QPainterPath(QPointF(5.8, 8.2))
            head.cubicTo(5.4, 2.7, 18.6, 2.7, 18.2, 8.5)
            head.cubicTo(20.4, 11.2, 16.1, 14.5, 12, 16)
            head.cubicTo(7.9, 14.5, 3.6, 11.2, 5.8, 8.2)
            shape(head, *coat, outline=coat[2])
            if fox:
                cheeks = QPainterPath(QPointF(5.5, 10.7))
                cheeks.cubicTo(8, 10.4, 10.4, 12.3, 12, 12.7)
                cheeks.cubicTo(13.6, 12.3, 16, 10.4, 18.5, 10.7)
                cheeks.quadTo(17, 14.6, 12, 16)
                cheeks.quadTo(7, 14.6, 5.5, 10.7)
                shape(cheeks, "#fff6e1", "#f6dfbb", "#c3a886")
            else:
                soft_oval(6, 6.4, 4.8, 6.1, coat[0], "#a4734b", coat[2])
            eyes(8.1, blink or state == "PET", True,
                 "#7b5a31" if fox else "#784d2d", slit=fox)
            soft_oval(8.5, 11.1, 7, 4.4, "#fff3df", "#e8ccaa", "#b58e69")
            soft_oval(10.5 + gx * 0.3, 11.5, 3, 1.9, "#76817a", "#303d3c", "#1d2929")
            curve(12, 13.1, 11.5, 14.2, 10.2, 13.7, "#725445", 0.35)
            curve(12, 13.1, 12.5, 14.2, 13.8, 13.7, "#725445", 0.35)
            if not fox and state in ("EXCITED", "FETCH", "PET", "WAVE"):
                soft_oval(11.2, 13.8, 1.6, 2.3, "#fac4bc", "#d98f8b", "#b16e74")
            curve(8, 15.3, 12, 16.9, 16, 15.3, middle, 1)
            soft_oval(11.4, 16.1, 1.3, 1.5, "#fff0b8", "#edc870", "#ae7d39")
        elif character == "owl":
            # A compact feathered pear silhouette, facial discs and folded wings.
            soft_oval(5.5, 5.1, 13, 16.4, *coat, outline=coat[2])
            soft_oval(7.2, 12, 9.5, 9, "#fff2d8", "#d9bd92", coat[1])
            for x in (8.4, 11.3, 14.2):
                for y in (15.2, 17.6):
                    curve(x, y, x + 0.6, y + 1.2, x + 1.2, y, "#aa855f", 0.5)
            for side in (-1, 1):
                tuft = QPainterPath(QPointF(12 + side * 2.8, 6.3))
                tuft.quadTo(12 + side * 5.4, 2.8, 12 + side * 6.3, 2.6)
                tuft.lineTo(12 + side * 6.1, 8)
                tuft.closeSubpath()
                shape(tuft, *coat, outline=coat[2])
            soft_oval(5.8, 5.8, 6.4, 9.3, "#fff3d8", "#ebd4ae", "#b69774")
            soft_oval(11.8, 5.8, 6.4, 9.3, "#fff3d8", "#ebd4ae", "#b69774")
            for x in (9.1, 14.9):
                soft_oval(x - 2, 7.5, 4, 4.9, "#846340", "#5c4939", "#332d2a")
                if blink or state == "PET":
                    curve(x - 1.1, 10, x, 10.8, x + 1.1, 10, "#e9c57f", 0.6)
                else:
                    soft_oval(x - 1.55 + gx * 0.3, 8.1 + gy * 0.2, 3.1, 3.7,
                              "#f5da8f", "#ce9a3f", "#885728")
                    oval(x - 0.8 + gx * 0.45, 8.5 + gy * 0.2, 1.6, 2.8, "#1d282a")
                    oval(x - 0.7 + gx * 0.45, 8.6 + gy * 0.2, 0.65, 0.8, "#ffffff")
            beak = QPainterPath(QPointF(11, 12))
            beak.quadTo(12, 10.8, 13, 12)
            beak.quadTo(12.7, 13.8, 12, 14.4)
            beak.closeSubpath()
            shape(beak, "#f6d18a", "#c78d42", "#87562e")
        elif character == "dragon":
            # Membrane wings, a long counterbalancing tail and soft belly plates.
            tail = QPainterPath(QPointF(16, 18.6))
            tail.cubicTo(23, 24, 26, 17, 22, 13.1 + math.sin(frame * 0.35))
            stroke(tail, coat[2], 2.4)
            stroke(tail, coat[1], 1.5)
            for side in (-1, 1):
                flap = math.sin(frame * (0.7 if state == "WING_FLAP" else 0.25))
                tip_x = 12 + side * (10.5 + (0.7 * flap if state == "WING_FLAP" else 0))
                tip_y = 6 + (flap * 3 if state == "WING_FLAP" else flap * 0.4)
                wing = QPainterPath(QPointF(12 + side * 4, 17))
                wing.quadTo(12 + side * 9, 7, tip_x, tip_y)
                wing.quadTo(tip_x - side, 12, 12 + side * 10, 16)
                wing.quadTo(12 + side * 7, 13.3, 12 + side * 4, 17)
                shape(wing, "#d9c3ab", "#ad8a80", "#705d62", coat[2])
                curve(12 + side * 4, 17, 12 + side * 8, 11, tip_x, tip_y, coat[2], 0.55)
            soft_oval(6.9, 12, 10.2, 9.1, *coat, outline=coat[2])
            soft_oval(9.1, 13.6, 5.8, 7.2, "#eee3b4", "#d1c895", "#929c75")
            for y in (15.3, 17.3, 19.3):
                curve(9.6, y, 12, y + 0.7, 14.4, y, "#a3ad81", 0.4)
            for x, side in ((7, -1), (17, 1)):
                horn = QPainterPath(QPointF(x - 1, 6))
                horn.quadTo(x + side * 1.2, 3, x + side * 1.3, 1.7)
                horn.quadTo(x - side * 1.3, 3, x + 1, 6)
                horn.closeSubpath()
                shape(horn, "#fff4d3", "#daceaa", "#9b946e", coat[2])
            soft_oval(5, 4.5, 14, 10.7, *coat, outline=coat[2])
            for x in (10.2, 12, 13.8):
                soft_oval(x - 0.5, 5.3, 1, 1.8, coat[0], coat[1], coat[2])
            eyes(8.2, blink or state == "PET", True, "#d5a74e")
            soft_oval(7.5, 11.2, 9, 4.3, "#c9e3bd", "#92c4a0", "#618c7e")
            for x in (9.8, 14.2):
                oval(x - 0.4, 12.1, 0.8, 0.5, "#3b6d63")
            curve(9.5, 13.6, 12, 14.9, 14.5, 13.6, "#436b63", 0.4)
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
        raised = state in ("WAVE", "DANCE", "CELEBRATE", "PARACHUTE", "DRAG", "HOP", "PEEK", "PUSH", "STRETCH", "JUGGLE", "MAGIC", "HANG", "SCARED", "SALUTE", "UMBRELLA", "HIGH_FIVE", "WRENCH", "SCAN_PART", "CHECK_STOCK", "KNOCK", "DUST", "INSPECT", "WING_FLAP", "LISTEN", "STOCK_OK", "FIND_SHELF", "PAPER_PLANE", "BLOW_BUBBLES", "STARGAZE")
        for x, y in ((4, 12 if raised else 16 + (swing if moving else 0)),
                     (18, 10 + math.sin(frame * 0.65) if raised else 16 - (swing if moving else 0))):
            if state == "TAI_CHI":
                y = 10.5 + math.sin(frame * 0.2 + (math.pi if x < 12 else 0)) * 2
            elif state == "TAP_FOOT":
                y = 15.5 if x < 12 else 13.5
            if character == "owl":
                side = -1 if x < 12 else 1
                extent = 9 + math.sin(frame * 0.7) * 1.3 if state == "WING_FLAP" else 7
                tip_y = 11.5 + math.sin(frame * 0.7) * 3.5 if state == "WING_FLAP" else y + 4
                wing = QPainterPath(QPointF(12 + side * 4.5, 11.5))
                if state == "WING_FLAP":
                    wing.quadTo(12 + side * 7, tip_y - 2.5, 12 + side * 11.2, tip_y)
                    wing.quadTo(12 + side * 10.9, tip_y + 2, 12 + side * 9.8, tip_y + 2.6)
                    wing.lineTo(12 + side * 10.2, tip_y + 3.3)
                    wing.quadTo(12 + side * 8.7, tip_y + 4.6, 12 + side * 7.7, tip_y + 4.6)
                    wing.quadTo(12 + side * 6.2, 20, 12 + side * 3.8, 19)
                else:
                    wing.quadTo(12 + side * (extent + 1), 12, 12 + side * extent, tip_y)
                    wing.quadTo(12 + side * 6.2, 21, 12 + side * 3.8, 19)
                wing.closeSubpath()
                shape(wing, *coat, outline=coat[2])
                for i in (0, 1):
                    curve(12 + side * (5.5 + i * 0.8), 13.8,
                          12 + side * (6.8 + i * 0.8), tip_y,
                          12 + side * (5.7 + i * 0.6), 18.7, tint(coat[0], 150), 0.45)
            elif organic:
                paw_y = y - (math.sin(frame * 0.7) * 2 if state == "WING_FLAP" else 0)
                arm = QPainterPath(QPointF(x + (3 if x < 12 else -1.3), 15.1))
                arm.quadTo(x + 0.4, paw_y - 0.4, x + 0.1, paw_y + 2.5)
                arm.quadTo(x + 1.6, paw_y + 5, x + 3, paw_y + 2)
                arm.quadTo(x + 2.4, 17, x + (3 if x < 12 else -1.3), 15.1)
                shape(arm, *coat, outline=coat[2])
                curve(x + 0.9, paw_y + 2.1, x + 1, paw_y + 2.6, x + 1.1, paw_y + 3,
                      tint(coat[2], 170), 0.3)
            else:
                y -= math.sin(frame * 0.7) * 2 if state == "WING_FLAP" else 0
                soft_oval(x - 0.3, y - 0.5, 2.8, 2.8, highlight, middle, dark, dark)
                panel(x, y, 2.2, 3.8, highlight, limbs, middle, 1)
                oval(x + 0.45, y + 0.65, 0.8, 0.8, tint(highlight, 155))
        for foot_index, x in enumerate((8 - (swing if moving else 0), 14 + (swing if moving else 0))):
            p.save()
            if state == "TAP_FOOT" and foot_index == 1:
                p.translate(0, -max(0, math.sin(frame * 0.9)) * 1.5)
            if organic:
                soft_oval(x - 0.4, 19.4, 3.8, 2.6, *coat, outline=coat[2])
                for toe in (0.8, 1.8):
                    curve(x + toe, 20.4, x + toe, 21.2, x + toe - 0.1, 21.5,
                          "#d1af6b" if character == "owl" else tint(coat[2], 180),
                          0.6 if character == "owl" else 0.3)
            else:
                panel(x - 0.1, 19.8, 3.2, 2.1, middle, dark, dark, 0.8)
                curve(x + 0.55, 20.4, x + 1.5, 20.1, x + 2.4, 20.4, tint(highlight, 150), 0.3)
            p.restore()
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
        elif state == "HEAD_TILT":
            drift = math.sin(frame * 0.3) * 0.5
            curve(21.2, 4.5 + drift, 24, 2.2 + drift, 23.2, 5.1 + drift, "#e4c889", 0.7)
            curve(23.2, 5.1 + drift, 22.1, 5.8 + drift, 22.2, 6.5 + drift, "#e4c889", 0.7)
            oval(21.9, 7.8 + drift, 0.7, 0.7, "#e4c889")
        elif state == "SNIFF":
            for i in range(3):
                drift = math.sin(frame * 0.6 + i) * 0.8
                curve(19.3 + i * 1.8, 13 - i * 1.2,
                      20.1 + i * 1.8 + drift, 11.8 - i * 1.2,
                      19.5 + i * 1.8, 10.3 - i * 1.2, tint("#bddec6", 190 - i * 35), 0.5)
        elif state == "LISTEN":
            for i in range(2):
                pulse = math.sin(frame * 0.5 + i) * 0.4
                curve(22 + i * 1.8, 7 - i * 1.3,
                      24 + i * 1.4 + pulse, 10, 22 + i * 1.8, 13 + i * 1.3,
                      tint("#8bc9e2", 190 - i * 50), 0.6)
        elif state == "KNOCK":
            # A tiny pane and tapping knuckle suit a window edge interaction.
            panel(23.3, 5.7, 2.1, 15.6, "#d2e6f1", "#8fb2c4", "#537b92", 0.6)
            tap = max(0, math.sin(frame * 0.8)) * 1.2
            curve(18.9, 13.5, 20.2 + tap, 11.8, 22 + tap, 11.5,
                  coat[1] if organic else limbs, 1.8)
            soft_oval(20.8 + tap, 10.7, 1.8, 1.9, *coat)
            if math.sin(frame * 0.8) > 0:
                curve(24.8, 8.3, 25.5, 7.9, 25.8, 7.7, "#f5d596", 0.5)
                curve(25.5, 11.6, 26.1, 11.6, 26.5, 11.6, "#f5d596", 0.5)
        elif state == "DUST":
            p.save()
            p.translate(19.8, 17.5)
            p.rotate(math.sin(frame * 0.5) * 22)
            curve(0, 0, 1, -4, 1.5, -7.3, "#b98e62", 1)
            for i in range(4):
                curve(1.5, -6.8, -1.5 + i * 1.8, -8.5,
                      -0.8 + i * 1.6, -10, "#e5d8c4" if i % 2 else "#a4bbbd", 1.1)
            p.restore()
            for i in range(3):
                drift = (frame * 0.2 + i * 0.6) % 2
                oval(22 + i * 1.1, 17.2 - drift * 1.4 - i,
                     0.55, 0.55, tint("#e1d4b9", round(180 - drift * 60)))
        elif state == "INSPECT":
            # The glass sweeps naturally as the character examines a target.
            glass_x = 19.2 + math.sin(frame * 0.3) * 0.8
            glass_y = 10.6 + math.sin(frame * 0.45) * 0.5
            curve(glass_x - 1.8, glass_y + 2, glass_x - 3, glass_y + 4,
                  glass_x - 4, glass_y + 5.7, "#aa805a", 1.3)
            soft_oval(glass_x - 3, glass_y - 3, 6, 6,
                      "#c9f1ef", "#85b9c4", "#658696", "#d7c48a")
            oval(glass_x - 2.35, glass_y - 2.35, 4.7, 4.7, tint("#d2f2ef", 95))
            curve(glass_x - 1.7, glass_y - 0.4, glass_x - 1.3, glass_y - 1.8,
                  glass_x + 0.3, glass_y - 1.9, tint("#ffffff", 210), 0.55)
        elif state == "PERCH":
            curve(4, 23, 12, 22.7, 21, 23.5, "#705447", 1.5)
            curve(5, 22.6, 13, 22.3, 20.8, 23, "#b99770", 0.55)
            curve(18, 23, 20.6, 21.8, 22.1, 20.2, "#80634f", 0.65)
            leaf_y = math.sin(frame * 0.4) * 0.35
            soft_oval(20.6, 19.3 + leaf_y, 3.1, 1.6, "#bad79c", "#86b082", "#54795e")
        elif state == "WING_FLAP":
            for side in (-1, 1):
                drift = math.sin(frame * 0.7) * 0.5
                curve(12 + side * 9, 20 + drift, 12 + side * 11, 19.4 + drift,
                      12 + side * 11.7, 18.4 + drift, tint("#b4dbe3", 150), 0.4)
        elif state == "FETCH":
            bob = math.sin(frame * 0.55) * 0.4
            panel(8.4, 15.2 + bob, 4.5, 2, "#ffe3a3", "#dfb76e", "#b78347", 0.5)
            panel(7.4, 16.2 + bob, 10.6, 5.1, "#ffe7b4", "#e4c287", "#ba8e56", 0.7)
            curve(8.7, 17.3 + bob, 12.7, 17 + bob, 16.7, 17.3 + bob, "#fff0cf", 0.4)
            soft_oval(6.7, 16.6 + bob, 2.7, 2.3, *coat)
            soft_oval(16.1, 16.6 + bob, 2.7, 2.3, *coat)
        elif state == "CARRY_BOX":
            # A closed parcel rides in the arms while the feet march in place.
            # This is a prop; stock is changed only by the inventory controls.
            panel(5.7, 13.9, 13.1, 7.3, "#f1d0a0", "#c39a68", "#89613d", 0.6, "#77583f")
            lid = QPainterPath(QPointF(5.7, 14.2))
            lid.lineTo(8.1, 11.9)
            lid.lineTo(20.6, 11.9)
            lid.lineTo(18.8, 14.2)
            lid.closeSubpath()
            shape(lid, "#f4dbb0", "#ddba87", "#b78958", "#9a744d")
            side = QPainterPath(QPointF(18.8, 14.2))
            side.lineTo(20.6, 11.9)
            side.lineTo(20.6, 19.2)
            side.lineTo(18.8, 21.2)
            side.closeSubpath()
            shape(side, "#d7b17d", "#ae8155", "#8c613e", "#8d6644")
            panel(11.4, 14, 1.7, 6.9, "#fff0c0", "#e7d299", "#c5ad76", 0.1, "#b7a06c")
            curve(12.3, 13.8, 13.2, 12.7, 14.1, 12.1, "#f6e8b7", 1.6)
            panel(14.1, 15.4, 3.5, 2.7, "#fffdf2", "#eee8d4", "#d5c7a7", 0.2)
            curve(15, 17.4, 15, 16.5, 15, 15.9, "#56736f", 0.4)
            curve(16.4, 17.4, 16.4, 16.5, 16.4, 15.9, "#56736f", 0.4)
            for x in (4.9, 17.9):
                soft_oval(x, 17.2, 3.1, 2.8, *coat, outline=coat[2] if organic else dark)
        elif state == "STOCK_OK":
            # A clear success gesture is also available as a manual celebration.
            lift = math.sin(frame * 0.4) * 0.5
            shield = QPainterPath(QPointF(18.8, 5.2 + lift))
            shield.quadTo(22.5, 6, 25.7, 5.2 + lift)
            shield.lineTo(25.1, 11 + lift)
            shield.quadTo(24.5, 13.5 + lift, 22.2, 15 + lift)
            shield.quadTo(19.4, 13.5 + lift, 18.8, 10.8 + lift)
            shield.closeSubpath()
            shape(shield, "#c7f1c4", "#67b793", "#328072", "#2d7268")
            mark = QPainterPath(QPointF(20.1, 9.5 + lift))
            mark.lineTo(21.6, 11.1 + lift)
            mark.lineTo(24.3, 7.8 + lift)
            stroke(mark, "#f4ffe2", 1.1)
            for i in range(4):
                angle = frame * 0.2 + i * math.pi / 2
                x, y = 22.2 + math.cos(angle) * 4.2, 3.3 + math.sin(angle) * 2.1
                oval(x - 0.35, y - 0.35, 0.7, 0.7, "#f4ce7c" if i % 2 else "#8acbd7")
        elif state == "FIND_SHELF":
            panel(4.1, 14.2, 15.7, 7.5, "#edf4e1", "#cdd9c6", "#9eafa0", 0.6, "#667f79")
            for x in (5.8, 10.5, 15.2):
                panel(x, 15.3, 2.8, 5, "#a3b8b4", "#7d9792", "#5c7973", 0.2, "#67847c")
                for y in (16.8, 18.5):
                    curve(x + 0.3, y, x + 1.2, y, x + 2.5, y, "#cad6c4", 0.3)
            # A marker travels down the aisle, then the paw points to its shelf.
            marker_y = 16.1 + (math.sin(frame * 0.35) + 1) * 1.65
            oval(13.5, marker_y, 1.1, 1.1, "#d87357")
            curve(18.9, 12.1, 21, 11.2, 21.6, 8.4, coat[1] if organic else limbs, 1.4)
            pin = QPainterPath(QPointF(20.1, 5.5))
            pin.cubicTo(20.1, 2.3, 25.2, 2.3, 25.2, 5.5)
            pin.quadTo(25.2, 7.5, 22.7, 9)
            pin.quadTo(20.1, 7.5, 20.1, 5.5)
            shape(pin, "#ffd7a4", "#e3a26a", "#b76e50", "#a76c50")
            oval(21.8, 4.2, 1.8, 1.8, "#fff0c5")
        elif state == "PAPER_PLANE":
            # The plane stays within the companion footprint throughout its loop.
            phase = frame * 0.35
            plane_x, plane_y = 21.3 + math.sin(phase) * 2, 3.3 + math.cos(phase) * 2.1
            trail = QPainterPath(QPointF(18.4, 7.7))
            trail.cubicTo(26, 6.6, 19.6, -0.9, plane_x, plane_y)
            stroke(trail, tint("#b5d7e9", 130), 0.4)
            p.save()
            p.translate(plane_x, plane_y)
            p.rotate(math.sin(phase) * 18)
            plane = QPainterPath(QPointF(-2.5, 0))
            plane.lineTo(2.8, -1.5)
            plane.lineTo(0.1, 2.4)
            plane.lineTo(-0.5, 0.9)
            plane.closeSubpath()
            shape(plane, "#f5ffff", "#c6e3ef", "#8cb7d0", "#729bb2")
            curve(-0.5, 0.9, 1.2, -0.4, 2.8, -1.5, "#96b6cb", 0.35)
            p.restore()
        elif state == "BLOW_BUBBLES":
            panel(3, 17, 3.6, 4.4, "#c3e6e3", "#75bcb8", "#4e8e98", 0.7)
            panel(3.2, 16.3, 3.2, 1.2, "#fff0bf", "#e5d192", "#baa66f", 0.3)
            curve(17.9, 15.8, 18.5, 13.7, 19.2, 11.6, "#edc69c", 0.65)
            p.setPen(QPen(QColor("#edc69c"), 0.5))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(18.4, 9.2, 2.6, 2.6))
            for i in range(3):
                travel = (frame * 0.035 + i / 3) % 1
                bubble_x = 20.9 + math.sin(travel * math.pi + i) * 1.1 + i * 0.8
                bubble_y = 9.6 - travel * 10.3
                radius = 0.8 + i * 0.3 + travel * 0.25
                opacity = round(180 * min(1, travel * 9, (1 - travel) * 9))
                p.setPen(QPen(tint("#c1e9ed", opacity), 0.35))
                p.setBrush(tint("#92cbe4", opacity // 5))
                p.drawEllipse(QRectF(bubble_x - radius, bubble_y - radius, radius * 2, radius * 2))
                curve(bubble_x - radius * 0.5, bubble_y - radius * 0.2,
                      bubble_x - radius * 0.5, bubble_y - radius * 0.7,
                      bubble_x + radius * 0.1, bubble_y - radius * 0.7,
                      tint("#fff2e5", opacity), 0.35)
        elif state == "TAI_CHI":
            # Slow, opposed arm arcs work with paws, wings and articulated hands.
            for side in (-1, 1):
                lift = math.sin(frame * 0.2 + (math.pi if side < 0 else 0)) * 2
                x = 12 + side * 8.4
                curve(x - side * 1.3, 9.5 + lift, x + side * 0.7, 11.1 + lift,
                      x + side * 0.2, 13.5 + lift, tint("#acd9cf", 145), 0.4)
            # A small breathing orb changes size smoothly with the movement.
            radius = 1.2 + math.sin(frame * 0.2) * 0.25
            glow(12, 18, radius * 1.9, "#a7e2ce", 95)
            soft_oval(12 - radius, 18 - radius, radius * 2, radius * 2,
                      "#e5ffe9", "#94d6ba", "#599e91")
        elif state == "STARGAZE":
            curve(21.2, 14.2, 19.6, 18.2, 18.4, 21.5, "#8d9fab", 0.7)
            curve(21.2, 14.2, 23, 18.5, 24.7, 21.5, "#8d9fab", 0.7)
            curve(21.2, 14.2, 21.4, 18, 21.4, 21.7, "#b6c3c7", 0.7)
            p.save()
            p.translate(21.2, 12.8)
            p.rotate(-22 + math.sin(frame * 0.25) * 7)
            panel(-3.9, -1.3, 8.7, 2.6, "#d6e6f1", "#84a6bf", "#4e718d", 0.7)
            panel(3.5, -1.8, 1.7, 3.6, "#f0d8a4", "#c6a56f", "#95744f", 0.5)
            oval(4.2, -1.1, 0.7, 2.1, "#bcf2f1")
            panel(-5.1, -0.7, 1.5, 1.4, "#a8becb", "#54778d", "#35556c", 0.3)
            p.restore()
            for i, (x, y) in enumerate(((2.6, 3.2), (21.7, 0), (25.3, 4.1))):
                reach = 0.45 + (math.sin(frame * 0.4 + i * 2) + 1) * 0.35
                curve(x - reach, y, x, y, x + reach, y, "#e7d59b", 0.45)
                curve(x, y - reach, x, y, x, y + reach, "#e7d59b", 0.45)
        elif state == "TAP_FOOT":
            # A wristwatch and an independently lifted foot make waiting legible.
            panel(18.2, 14.1, 2.3, 3.7, "#bacbd2", "#6f909f", "#486a7c", 0.5)
            soft_oval(17.8, 14.5, 3.1, 3.1, "#fff8e1", "#e2dbc3", "#a6a18d", "#71909d")
            angle = frame * 0.17
            curve(19.35, 16.05, 19.35 + math.sin(angle) * 0.35,
                  16.05 - math.cos(angle) * 0.35,
                  19.35 + math.sin(angle) * 0.85, 16.05 - math.cos(angle) * 0.85,
                  "#526f79", 0.3)
            curve(19.35, 16.05, 19, 16.05, 18.7, 16.05, "#526f79", 0.35)
            tap = max(0, -math.sin(frame * 0.9))
            curve(14.3 - tap * 0.4, 22.4, 15.6, 22.8 + tap * 0.3,
                  17.1 + tap * 0.4, 22.4, tint("#d6c89f", round(50 + tap * 130)), 0.45)
    if state == "CURL_UP" and not organic:
        # A softly pulsing night light distinguishes curl-up from ordinary sleep.
        glow(22.5, 10, 2.3, "#c8c2ed", 70 + round(math.sin(frame * 0.3) * 25))
        oval(21, 8.5, 2.6, 2.6, "#d6cbea")
        oval(22, 8.1, 2.1, 2.1, dark)
    p.restore()
    p.restore()
