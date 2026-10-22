"""Aiko desktop widget: a native Win32 layered window (per-pixel alpha), no Tk.

Why not Tk: with the Nahimic audio software installed, Tk windows in this setup kept dying with a
corrupted Tcl heap ("alloc: invalid block", crash in tcl86t.dll). A plain Win32 window gives real
antialiased edges and a soft shadow as well.

A dark rounded card: the pet on the left, the status text (14 pt) on the right.
Drag to move, click the pet to pat it, x closes (exit code 10, so the tray does not restart it).
"""
import ctypes
import json
import re
import sys
import time
import traceback
from ctypes import wintypes
from pathlib import Path

# Must happen before the first window: otherwise Windows bitmap-stretches the widget at 125%+.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from pet import PetRenderer

BASE = Path(__file__).parent
STATE_PATH = BASE / "state.json"
POS_PATH = BASE / "widget_pos.json"
LOG_PATH = BASE / "widget.log"
EXIT_USER_CLOSED = 10

# ---- layout (logical px; multiplied by the DPI factor) ---------------------------------------------
MARGIN = 20                        # transparent room around the card for the shadow
CARD_W, CARD_H = 470, 176
PET_BOX = (14, 13, 164, 163)       # relative to the card
TEXT_X = 176
FONT_TITLE_PT, FONT_STATUS_PT, FONT_CAPTION_PT = 17, 14, 10
MAX_STATUS_LINES = 4

COL_TEXT = (236, 233, 251, 255)
COL_DIM = (157, 153, 189, 255)

# ---- Win32 plumbing ------------------------------------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WS_POPUP = 0x80000000
WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x8, 0x80, 0x80000, 0x08000000
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
HIDE_DELAY = 3.0                   # seconds the pet is shown asleep before the widget disappears
ULW_ALPHA = 2
SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x4, 0x10
WM_DESTROY, WM_TIMER, WM_SETCURSOR, WM_MOUSEACTIVATE = 0x2, 0x113, 0x20, 0x21
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x200, 0x201, 0x202
MA_NOACTIVATE = 3
IDC_ARROW, IDC_HAND = 32512, 32649
SPI_GETWORKAREA = 0x30
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
TIMER_ID, TICK_MS = 1, 80


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                   wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, wintypes.LPVOID]
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT), ctypes.POINTER(SIZE),
                                       wintypes.HDC, ctypes.POINTER(POINT), wintypes.COLORREF,
                                       ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
user32.UpdateLayeredWindow.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wintypes.UINT]
user32.SetCapture.argtypes = [wintypes.HWND]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.SetCursor.argtypes = [wintypes.HANDLE]
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                   wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def log_note(text):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
        if LOG_PATH.stat().st_size > 200_000:
            LOG_PATH.write_text("", encoding="utf-8")
    except OSError:
        pass


def log_exception(where):
    log_note(f"{where}\n{traceback.format_exc()}")


def process_alive(pid):
    SYNCHRONIZE, WAIT_TIMEOUT = 0x00100000, 0x102
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


class StateReader:
    """state.json is replaced ~14x/s by the assistant, so a read can hit the swap and fail.
    Keep the last good state instead of blanking the widget."""

    def __init__(self):
        self.last = {}

    def read(self):
        for _ in range(3):
            try:
                self.last = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                break
            except (OSError, json.JSONDecodeError):
                time.sleep(0.004)
        return self.last


def load_font(names, px):
    for name in names:
        try:
            return ImageFont.truetype(str(Path(r"C:\Windows\Fonts") / name), px)
        except OSError:
            continue
    return ImageFont.load_default()


class Widget:
    def __init__(self):
        dpi = user32.GetDpiForSystem() if hasattr(user32, "GetDpiForSystem") else 96
        self.k = max(1.0, dpi / 96.0)
        S = self.S
        self.W, self.H = S(CARD_W + 2 * MARGIN), S(CARD_H + 2 * MARGIN)
        self.card_x, self.card_y = S(MARGIN), S(MARGIN)

        px = lambda pt: max(8, round(pt * dpi / 72))
        self.f_title = load_font(("seguisb.ttf", "segoeuib.ttf"), px(FONT_TITLE_PT))
        self.f_status = load_font(("segoeui.ttf",), px(FONT_STATUS_PT))
        self.f_caption = load_font(("segoeui.ttf",), px(FONT_CAPTION_PT))

        pb = PET_BOX
        self.pet_rect = (self.card_x + S(pb[0]), self.card_y + S(pb[1]), self.card_x + S(pb[2]), self.card_y + S(pb[3]))
        self.close_c = (self.card_x + S(CARD_W - 24), self.card_y + S(24))
        self.text_left = self.card_x + S(TEXT_X)
        self.text_right = self.card_x + S(CARD_W - 22)

        self.card = self._build_card()
        self.pet = PetRenderer(self.pet_rect[2] - self.pet_rect[0])
        self.reader = StateReader()
        self.state = {}

        self.pat_ts = -99.0
        self.t0 = time.time()
        self.tick_n = 0
        self.smooth_vol = 0.0
        self.parent_pid = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0
        self.ui_key = None
        self.ui_layer = None
        self.press = None
        self.dragged = False
        self.hover_close = False
        self.close_armed = False
        self.closed_by_user = False
        self.visible = True
        self.sleep_since = None
        self.x, self.y = self._start_pos()

        self._make_window()
        self._make_dib()

    # ---- geometry helpers ---------------------------------------------------------------------

    def S(self, v):
        return int(round(v * self.k))

    @staticmethod
    def _inside(rect, x, y):
        return rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]

    def _start_pos(self):
        vx, vy = user32.GetSystemMetrics(SM_XVIRTUALSCREEN), user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw, vh = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        try:
            pos = json.loads(POS_PATH.read_text(encoding="utf-8"))
            x, y = int(pos["x"]), int(pos["y"])
            if vx - self.W // 2 < x < vx + vw - self.W // 2 and vy <= y < vy + vh - self.S(40):
                return x, y
        except Exception:
            pass
        wa = wintypes.RECT()
        user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(wa), 0)
        return wa.right - self.W - self.S(4), wa.bottom - self.H - self.S(4)

    # ---- window / bitmap ----------------------------------------------------------------------

    def _make_window(self):
        self._wndproc = WNDPROC(self._on_message)       # keep a reference or it gets garbage-collected
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.hCursor = user32.LoadCursorW(None, IDC_ARROW)
        wc.lpszClassName = "AikoWidgetWindow"
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, wc.lpszClassName, "Айко", WS_POPUP,
            self.x, self.y, self.W, self.H, None, None, hinst, None)
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

    def _make_dib(self):
        self.hdc_screen = user32.GetDC(None)
        self.hdc_mem = gdi32.CreateCompatibleDC(self.hdc_screen)
        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth, bmi.biHeight = self.W, -self.H          # top-down
        bmi.biPlanes, bmi.biBitCount = 1, 32
        bits = ctypes.c_void_p()
        self.hbmp = gdi32.CreateDIBSection(self.hdc_screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        self.bits = bits
        gdi32.SelectObject(self.hdc_mem, self.hbmp)

    def present(self, img):
        """Push an RGBA PIL image (window size) to the layered window with per-pixel alpha."""
        data = img.convert("RGBa").tobytes("raw", "BGRa")
        ctypes.memmove(self.bits, data, len(data))
        pos, size, src = POINT(self.x, self.y), SIZE(self.W, self.H), POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, 255, 1)                  # AC_SRC_OVER, AC_SRC_ALPHA
        user32.UpdateLayeredWindow(self.hwnd, self.hdc_screen, ctypes.byref(pos), ctypes.byref(size),
                                   self.hdc_mem, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)

    # ---- drawing ------------------------------------------------------------------------------

    def _build_card(self):
        S, W, H, ss = self.S, self.W, self.H, 2
        size = (W * ss, H * ss)
        box = [self.card_x * ss, self.card_y * ss, (self.card_x + S(CARD_W)) * ss, (self.card_y + S(CARD_H)) * ss]
        radius = S(26) * ss

        shadow = Image.new("RGBA", size, (0, 0, 0, 0))
        sbox = [box[0], box[1] + S(7) * ss, box[2], box[3] + S(7) * ss]
        ImageDraw.Draw(shadow).rounded_rectangle(sbox, radius=radius, fill=(0, 0, 0, 170))
        shadow = shadow.filter(ImageFilter.GaussianBlur(S(11) * ss))

        grad = Image.linear_gradient("L").resize(size)
        body = Image.composite(Image.new("RGBA", size, (16, 15, 25, 246)), Image.new("RGBA", size, (31, 29, 46, 246)), grad)
        mask = Image.new("L", size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
        card = Image.new("RGBA", size, (0, 0, 0, 0))
        card.paste(body, mask=mask)
        ImageDraw.Draw(card).rounded_rectangle(box, radius=radius, outline=(255, 255, 255, 30), width=ss)

        out = Image.alpha_composite(shadow, card)
        return out.convert("RGBa").resize((W, H), Image.LANCZOS).convert("RGBA")   # premultiplied resize

    def _wrap(self, text, font, max_w, max_lines):
        lines, cur = [], ""
        for word in text.split():
            trial = f"{cur} {word}".strip()
            if font.getlength(trial) <= max_w:
                cur = trial
                continue
            if cur:
                lines.append(cur)
            cur = word
            while font.getlength(cur) > max_w and len(cur) > 1:      # a single very long word
                cut = len(cur)
                while cut > 1 and font.getlength(cur[:cut]) > max_w:
                    cut -= 1
                lines.append(cur[:cut])
                cur = cur[cut:]
        if cur:
            lines.append(cur)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            last = lines[-1]
            while last and font.getlength(last + "…") > max_w:
                last = last[:-1]
            lines[-1] = last.rstrip() + "…"
        return lines

    @staticmethod
    def _caption(sleeping, activity, listening):
        if sleeping:
            return "сплю"
        if activity == "thinking":
            return "думаю…"
        if activity == "speaking":
            return "говорю"
        return "слушаю" if listening else "на связи"

    def _render_ui(self, status, caption):
        S = self.S
        # Segoe UI has no emoji/pictograph glyphs: drop them instead of drawing empty boxes.
        status = re.sub("[←-⯿︎️‍\U0001f000-\U0001ffff]", "", status)
        status = re.sub(r"\s{2,}", " ", status).strip()
        layer = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.text((self.text_left, self.card_y + S(14)), "Айко", font=self.f_title, fill=COL_TEXT)
        d.text((self.card_x + S(CARD_W - 48), self.card_y + S(24)), caption, font=self.f_caption, fill=COL_DIM, anchor="rm")

        line_h = round(self.f_status.size * 1.32)
        y = self.card_y + S(54)
        for line in self._wrap(status, self.f_status, self.text_right - self.text_left, MAX_STATUS_LINES):
            d.text((self.text_left, y), line, font=self.f_status, fill=COL_TEXT)
            y += line_h

        cx, cy = self.close_c
        r = S(5)
        col = (255, 255, 255, 230) if self.hover_close else (119, 115, 154, 255)
        d.line([cx - r, cy - r, cx + r, cy + r], fill=col, width=max(2, S(2)))
        d.line([cx - r, cy + r, cx + r, cy - r], fill=col, width=max(2, S(2)))
        return layer

    def _frame(self, now):
        s = self.state
        sleeping = bool(s.get("paused") or s.get("sleeping"))
        activity = s.get("activity", "idle")
        mood = s.get("mood")
        mood_age = now - float(s.get("mood_ts", 0)) if mood else 99.0
        self.smooth_vol += (float(s.get("volume", 0.0)) - self.smooth_vol) * 0.5
        listening = self.smooth_vol > 0.12 and not sleeping

        status = s.get("status", "")
        caption = self._caption(sleeping, activity, listening)
        ui_key = (status, caption, self.hover_close)
        if ui_key != self.ui_key:
            self.ui_key = ui_key
            self.base = self.card.copy()                  # card + text, rebuilt only when the text changes
            self.base.alpha_composite(self._render_ui(status, caption))

        pet = self.pet.render(now - self.t0, vol=self.smooth_vol, sleeping=sleeping, activity=activity, mood=mood,
                              mood_age=mood_age, pat_age=now - self.pat_ts)
        frame = self.base.copy()
        frame.alpha_composite(pet, (self.pet_rect[0], self.pet_rect[1]))
        busy = activity != "idle" or listening or mood_age < 2.6 or now - self.pat_ts < 1.8
        return frame, sleeping, busy

    def tick(self):
        now = time.time()
        self.tick_n += 1
        if not self.visible:
            if self.tick_n % 4:                               # hidden: just poll for the wake-up, cheaply
                return
            self.state = self.reader.read()
        elif self.tick_n % 2 == 1 or not self.state:
            self.state = self.reader.read()
        if self.tick_n % 40 == 0 and self.parent_pid and not process_alive(self.parent_pid):
            log_note(f"parent process {self.parent_pid} is gone -> closing")
            user32.DestroyWindow(self.hwnd)
            return

        s = self.state
        # «Айко, ложись спать» -> the pet falls asleep, then the widget disappears until she is called.
        just_shown = False
        if s.get("sleeping"):
            if self.sleep_since is None:
                self.sleep_since = now
            if self.visible and now - self.sleep_since > HIDE_DELAY:
                user32.ShowWindow(self.hwnd, SW_HIDE)
                self.visible = False
        else:
            self.sleep_since = None
            if not self.visible:
                user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
                self.visible = just_shown = True
        if not self.visible:
            return

        # Frame rate follows what the pet is doing: full speed when busy, slower when just breathing.
        sleeping = bool(s.get("paused") or s.get("sleeping"))
        busy = (s.get("activity", "idle") != "idle" or self.smooth_vol > 0.12
                or now - float(s.get("mood_ts", 0)) < 2.6 or now - self.pat_ts < 1.8)
        step = 4 if sleeping and not busy else (1 if busy else 2)
        if self.tick_n % step and not just_shown:
            return
        frame, _, _ = self._frame(now)
        self.present(frame)

    # ---- window messages ----------------------------------------------------------------------

    def _hit(self, x, y):
        cx, cy = self.close_c
        if (x - cx) ** 2 + (y - cy) ** 2 <= self.S(15) ** 2:
            return "close"
        if self._inside(self.pet_rect, x, y):
            return "pet"
        if self._inside((self.card_x, self.card_y, self.card_x + self.S(CARD_W), self.card_y + self.S(CARD_H)), x, y):
            return "card"
        return None

    def _on_message(self, hwnd, msg, wparam, lparam):
        try:
            return self._dispatch(hwnd, msg, wparam, lparam)
        except Exception:
            log_exception(f"wndproc msg=0x{msg:x}")
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _dispatch(self, hwnd, msg, wparam, lparam):
        if msg == WM_TIMER:
            self.tick()
            return 0
        if msg in (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE):
            x, y = ctypes.c_short(lparam & 0xFFFF).value, ctypes.c_short((lparam >> 16) & 0xFFFF).value
            hit = self._hit(x, y)
            if msg == WM_MOUSEMOVE:
                self.hover_close = hit == "close"
                if self.press and (wparam & 1):
                    cur = POINT()
                    user32.GetCursorPos(ctypes.byref(cur))
                    dx, dy = cur.x - self.press[0], cur.y - self.press[1]
                    if abs(dx) + abs(dy) > self.S(4):
                        self.dragged = True
                    if self.dragged:
                        self.x, self.y = self.press[2] + dx, self.press[3] + dy
                        user32.SetWindowPos(hwnd, None, self.x, self.y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
                return 0
            if msg == WM_LBUTTONDOWN:
                self.close_armed = hit == "close"           # closing needs press AND release on the cross
                if hit in ("close", None):
                    self.press = None
                    return 0
                cur = POINT()
                user32.GetCursorPos(ctypes.byref(cur))
                self.press, self.dragged = (cur.x, cur.y, self.x, self.y, hit), False
                user32.SetCapture(hwnd)
                return 0
            user32.ReleaseCapture()                           # WM_LBUTTONUP
            press, self.press = self.press, None
            armed, self.close_armed = self.close_armed, False
            if press is None and hit == "close" and armed:
                self.closed_by_user = True
                user32.DestroyWindow(hwnd)
            elif press and self.dragged:
                try:
                    POS_PATH.write_text(json.dumps({"x": self.x, "y": self.y}), encoding="utf-8")
                except OSError:
                    pass
            elif press and press[4] == "pet":
                self.pat_ts = time.time()
            return 0
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        if msg == WM_SETCURSOR:
            cur = POINT()
            user32.GetCursorPos(ctypes.byref(cur))
            user32.ScreenToClient(hwnd, ctypes.byref(cur))
            shape = IDC_HAND if self._hit(cur.x, cur.y) in ("close", "pet") else IDC_ARROW
            user32.SetCursor(user32.LoadCursorW(None, shape))
            return 1
        if msg in (0x10, 0x11, 0x16):                 # WM_CLOSE, WM_QUERYENDSESSION, WM_ENDSESSION
            log_note(f"window message 0x{msg:x} wparam={wparam} lparam={lparam}")
        if msg == WM_DESTROY:
            log_note("WM_DESTROY -> quitting" + (" (closed by user)" if self.closed_by_user else ""))
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def run(self):
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        self.state = self.reader.read()
        frame, _, _ = self._frame(time.time())
        self.present(frame)
        user32.SetTimer(self.hwnd, TIMER_ID, TICK_MS, None)
        msg = wintypes.MSG()
        while True:
            got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if got <= 0:
                log_note(f"message loop ended (GetMessage returned {got}, quit code {msg.wParam})")
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        return EXIT_USER_CLOSED if self.closed_by_user else 0


if __name__ == "__main__":
    try:
        code = Widget().run()
    except Exception:
        log_exception("fatal")
        raise
    sys.exit(code)
