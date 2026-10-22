"""Aiko the pet: a gradient spirit-orb (same palette as the logo) drawn procedurally.

PetRenderer.render() returns one animation frame. It is pure PIL (no Tk), so it can be tested
headless. Everything is drawn at 2x and downsampled for smooth edges.
"""
import math
import random

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

import make_icon

INK = (34, 24, 78, 255)
EYE_WHITE = (250, 250, 255, 255)
MOUTH = (74, 24, 84, 255)
TONGUE = (255, 125, 165, 255)
CYAN = (45, 212, 245)
PINK = (244, 100, 190)
FONT_PATHS = (r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf")


def _lerp(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def _star(cx, cy, r, k=3.0, n=64):
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        c, s = math.cos(t), math.sin(t)
        pts.append((cx + r * math.copysign(abs(c) ** k, c), cy + r * math.copysign(abs(s) ** k, s)))
    return pts


def _heart(cx, cy, r, n=36):
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        x = 16 * math.sin(t) ** 3
        y = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((cx + x * r / 16, cy + y * r / 16))
    return pts


def _font(px):
    for path in FONT_PATHS:
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            continue
    return ImageFont.load_default()


class PetRenderer:
    SS = 2

    def __init__(self, size, background=None):
        """size: output square in pixels; background: RGBA image of the same size to composite onto."""
        self.size = size
        self.W = size * self.SS
        self.background = background
        body_px = int(self.W * 0.70)
        self.body = make_icon.render(body_px, detailed=True, sparkles=False)
        self.body_sleep = self._dim(self.body)
        self._aura = {}
        self._font = _font(max(8, int(self.W * 0.12)))
        self._font_big = _font(max(8, int(self.W * 0.17)))
        self._next_blink = 0.0
        self._blink_until = 0.0

    @staticmethod
    def _dim(img):
        rgb = img.convert("RGB")
        mixed = Image.blend(rgb, ImageOps.grayscale(rgb).convert("RGB"), 0.5)
        mixed = ImageEnhance.Brightness(mixed).enhance(0.66).convert("RGBA")
        mixed.putalpha(img.getchannel("A"))
        return mixed

    def _aura_layer(self, level, sleeping):
        key = (level, sleeping)
        if key not in self._aura:
            W = self.W
            layer = Image.new("RGBA", (W, W), (0, 0, 0, 0))
            if sleeping:
                color, alpha, r = (120, 100, 210), 38, 0.352 * W
            else:
                color, alpha, r = _lerp(CYAN, PINK, level / 8), 64 + level * 15, (0.352 + 0.006 * level) * W
            cy = 0.56 * W
            ImageDraw.Draw(layer).ellipse([W / 2 - r, cy - r, W / 2 + r, cy + r], fill=color + (alpha,))
            self._aura[key] = layer.filter(ImageFilter.GaussianBlur(W * 0.035))
        return self._aura[key]

    def render(self, t, vol=0.0, sleeping=False, activity="idle", mood=None, mood_age=99.0, pat_age=99.0):
        W = self.W
        listening = vol > 0.12 and not sleeping
        expr = None
        if not sleeping:
            if pat_age < 1.6:
                expr = "happy"
            elif mood and mood_age < 2.4:
                expr = mood
        age = pat_age if pat_age < 1.6 else mood_age

        # --- blinking
        if t >= self._next_blink:
            self._blink_until = t + 0.15
            self._next_blink = t + random.uniform(2.5, 5.0)
        blink = abs(2 * (self._blink_until - t) / 0.15 - 1) if t < self._blink_until else 1.0

        # --- body motion: breathing, bounce while talking, hops when happy / when it hears its name
        period = 4.2 if sleeping else 2.6
        breath = math.sin(2 * math.pi * t / period)
        sy, sx = 1 + 0.028 * breath, 1 - 0.018 * breath
        lift = -0.012 * W * breath
        if activity == "speaking":
            lift -= 0.022 * W * abs(math.sin(t * 9))
        if expr == "happy":
            lift -= 0.07 * W * abs(math.sin(t * 10)) * max(0.0, 1 - age / 2.4)
        if expr == "heard":
            lift -= 0.05 * W * abs(math.sin(age * 10)) * max(0.0, 1 - age / 0.7)

        bcx, bcy = W / 2, 0.56 * W + lift

        canvas = Image.new("RGBA", (W, W), (0, 0, 0, 0))
        level = 0 if sleeping else min(8, int(vol * 10))
        canvas.alpha_composite(self._aura_layer(level, sleeping))

        body = self.body_sleep if sleeping else self.body
        bw, bh = int(body.width * sx), int(body.height * sy)
        canvas.alpha_composite(body.resize((bw, bh), Image.BILINEAR), (int(bcx - bw / 2), int(bcy - bh / 2)))

        # One overlay layer for cheeks, face and effects (cheapest: a single composite per frame).
        face = Image.new("RGBA", (W, W), (0, 0, 0, 0))
        d = ImageDraw.Draw(face)
        lw = max(2, int(W * 0.02))

        # --- cheeks (two stacked ellipses fake a soft blush without a per-frame blur)
        for sign in (-1, 1):
            cx_ = bcx + sign * 0.215 * W
            for grow, alpha in ((1.35, 45), (1.0, 100 if expr != "happy" else 150)):
                d.ellipse([cx_ - 0.045 * W * grow, bcy + 0.065 * W - 0.02 * W * grow,
                           cx_ + 0.045 * W * grow, bcy + 0.065 * W + 0.02 * W * grow], fill=(255, 120, 170, alpha))

        # --- eyes
        eye = 0.0 if sleeping else blink
        if listening:
            eye = min(1.25, eye * 1.2)
        if expr == "heard":
            eye = min(1.3, eye * 1.3)
        lx, ly = 0.012 * W * math.sin(t * 0.7), 0.008 * W * math.sin(t * 0.9 + 1)
        if activity == "thinking":
            lx, ly = 0.022 * W, -0.026 * W
        ey = bcy - 0.035 * W
        rx = 0.062 * W
        for sign in (-1, 1):
            ex = bcx + sign * 0.135 * W
            if expr == "happy":
                d.arc([ex - rx, ey - rx * 0.7, ex + rx, ey + rx * 0.9], 200, 340, fill=INK, width=lw + 1)
            elif sleeping or eye * 0.078 * W < 0.012 * W:
                d.arc([ex - rx * 0.9, ey - rx * 0.5, ex + rx * 0.9, ey + rx * 0.5], 15, 165, fill=INK, width=lw)
            else:
                ry = 0.078 * W * eye * (0.82 if expr == "sad" else 1.0)
                d.ellipse([ex - rx, ey - ry, ex + rx, ey + ry], fill=EYE_WHITE)
                pr = rx * 0.64
                py = min(pr, ry * 0.95)
                px_, py_ = ex + lx, ey + ly * 0.6
                d.ellipse([px_ - pr, py_ - py, px_ + pr, py_ + py], fill=INK)
                hr = pr * 0.3
                d.ellipse([px_ - pr * 0.45 - hr, py_ - py * 0.5 - hr, px_ - pr * 0.45 + hr, py_ - py * 0.5 + hr],
                          fill=EYE_WHITE)
            if expr == "confused":
                y0 = ey - 0.115 * W + (0.02 * W if sign > 0 else 0)
                d.line([ex - rx * 0.8, y0 + sign * 0.012 * W, ex + rx * 0.8, y0 - sign * 0.012 * W], fill=INK, width=lw)
            elif expr == "sad":
                # inner ends higher than the outer ends = worried
                d.line([ex - sign * rx * 0.9, ey - 0.125 * W, ex + sign * rx * 0.7, ey - 0.085 * W], fill=INK, width=lw)

        # --- mouth
        mx, my = bcx, bcy + 0.115 * W
        if activity == "speaking":
            m = abs(math.sin(t * 11)) * (0.65 + 0.35 * math.sin(t * 3.1 + 1))
        elif listening:
            m = min(1.0, vol * 1.6)
        else:
            m = 0.0
        if sleeping:
            d.ellipse([mx - 0.012 * W, my - 0.006 * W, mx + 0.012 * W, my + 0.012 * W + 0.006 * W * breath], fill=MOUTH)
        elif expr == "happy":
            d.pieslice([mx - 0.07 * W, my - 0.045 * W, mx + 0.07 * W, my + 0.07 * W], 0, 180, fill=MOUTH)
            d.pieslice([mx - 0.035 * W, my + 0.012 * W, mx + 0.035 * W, my + 0.07 * W], 0, 180, fill=TONGUE)
        elif expr == "sad":
            d.arc([mx - 0.05 * W, my, mx + 0.05 * W, my + 0.07 * W], 200, 340, fill=INK, width=lw)
        elif expr == "confused":
            pts = [(mx - 0.05 * W + i * 0.01 * W, my + 0.012 * W * math.sin(i * 1.6 + t * 4)) for i in range(11)]
            d.line(pts, fill=INK, width=lw)
        elif m > 0.06:
            mrx, mry = 0.034 * W * (0.8 + 0.4 * m), 0.012 * W + 0.05 * W * m
            d.ellipse([mx - mrx, my - mry * 0.4, mx + mrx, my + mry], fill=MOUTH)
            d.pieslice([mx - mrx * 0.6, my + mry * 0.25, mx + mrx * 0.6, my + mry * 1.05], 0, 180, fill=TONGUE)
        else:
            d.arc([mx - 0.045 * W, my - 0.035 * W, mx + 0.045 * W, my + 0.035 * W], 20, 160, fill=INK, width=lw)
        # --- effects: antenna spark, hearts, zzz, thinking dots, question mark (same layer)
        fd = d
        if not sleeping:
            r = 0.052 * W * (1 + 0.25 * math.sin(t * 3))
            if listening:
                r *= 1.35
            if expr == "heard":
                r *= 1 + 0.9 * max(0.0, 1 - age / 0.8)
            sx_, sy_ = bcx, bcy - 0.35 * W
            fd.polygon(_star(sx_, sy_, r * 1.6), fill=(255, 255, 255, 70))
            fd.polygon(_star(sx_, sy_, r), fill=(255, 255, 255, 255))
        else:
            for i in range(3):
                ph = (t * 0.45 + i / 3) % 1.0
                x = bcx + 0.2 * W + ph * 0.16 * W
                y = bcy - 0.2 * W - ph * 0.2 * W
                a = int(235 * math.sin(math.pi * ph))
                fd.text((x, y), "z", font=self._font if i < 2 else self._font_big, fill=(205, 200, 255, a), anchor="mm")
        if expr == "happy":
            for i in range(3):
                ph = min(1.0, (age / 1.6) + i * 0.0)
                hx = bcx + (i - 1) * 0.17 * W + 0.02 * W * math.sin(t * 5 + i)
                hy = bcy - 0.28 * W - ph * 0.12 * W - i * 0.03 * W
                a = int(255 * max(0.0, 1 - ph) )
                fd.polygon(_heart(hx, hy, 0.045 * W), fill=(255, 110, 175, a))
        if activity == "thinking" and not sleeping:
            for i in range(3):
                k = 0.5 + 0.5 * math.sin(t * 6 - i * 1.1)
                rr = 0.012 * W + 0.012 * W * k
                fd.ellipse([bcx + 0.22 * W + i * 0.045 * W - rr, bcy - 0.30 * W - rr,
                            bcx + 0.22 * W + i * 0.045 * W + rr, bcy - 0.30 * W + rr], fill=(255, 255, 255, 230))
        if expr == "confused":
            fd.text((bcx + 0.27 * W, bcy - 0.26 * W), "?", font=self._font_big, fill=(255, 255, 255, 235), anchor="mm")
        canvas.alpha_composite(face)

        small = canvas.reduce(self.SS)
        if self.background is not None:
            out = self.background.copy()
            out.alpha_composite(small)
            return out.convert("RGB")
        return small
