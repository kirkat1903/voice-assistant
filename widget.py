import ctypes
import json
import sys
import tkinter as tk
from pathlib import Path

# Must happen before the first Tk window: otherwise Windows bitmap-stretches the widget at 125%+.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

from PIL import Image, ImageTk

STATE_PATH = Path(__file__).parent / "state.json"
PENDING_SEARCH_PATH = Path(__file__).parent / "pending_search.txt"
ICON_PATH = next(
    (p for p in (Path(__file__).parent / "icon_512.png", Path(__file__).parent / "icon.png") if p.exists()),
    Path(__file__).parent / "icon.png",
)

BG_KEY = "#010101"
CARD_COLOR = "#15141f"
CARD_BORDER = "#2b2842"
ENTRY_COLOR = "#0d0c14"
RING_QUIET = (45, 212, 245)    # cyan  (same pools as the logo)
RING_LOUD = (244, 100, 190)    # pink
RING_IDLE = "#6b6880"


def lerp_color(a, b, t):
    t = max(0.0, min(1.0, t))
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def process_alive(pid):
    SYNCHRONIZE = 0x00100000
    WAIT_TIMEOUT = 0x102
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def read_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def round_rect(canvas, x1, y1, x2, y2, r, **kwargs):
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class Widget:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG_KEY)
        self.root.attributes("-transparentcolor", BG_KEY)

        self.k = max(1.0, self.root.winfo_fpixels("1i") / 96.0)   # 1.25 at 125% scaling
        S = self.S

        self.w, self.h = S(236), S(94)
        x = self.root.winfo_screenwidth() - self.w - S(24)
        y = self.root.winfo_screenheight() - self.h - S(90)
        self.root.geometry(f"{self.w}x{self.h}+{x}+{y}")

        self.canvas = tk.Canvas(self.root, width=self.w, height=self.h, bg=BG_KEY, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        round_rect(self.canvas, 1, 1, self.w - 1, self.h - 1, S(18), fill=CARD_COLOR, outline=CARD_BORDER, width=1)

        self.cx, self.cy = S(32), S(32)
        self.icon_size = S(42)
        icon_img = Image.open(ICON_PATH).convert("RGBA").resize((self.icon_size, self.icon_size), Image.LANCZOS)
        self.icon_photo = ImageTk.PhotoImage(icon_img)
        self.canvas.create_image(self.cx, self.cy, image=self.icon_photo)

        r = self.icon_size // 2 + S(3)
        self.ring_item = self.canvas.create_oval(
            self.cx - r, self.cy - r, self.cx + r, self.cy + r, outline=lerp_color(RING_QUIET, RING_LOUD, 0), width=max(2, S(2))
        )

        self.canvas.create_text(
            S(70), S(15), anchor="w", text="Айко", fill="#f4f2ff", font=("Segoe UI Semibold", 11)
        )
        self.text_item = self.canvas.create_text(
            S(70), S(28), anchor="nw", text="", fill="#a9a5c4", font=("Segoe UI", 9), width=S(146)
        )
        self.close_item = self.canvas.create_text(
            self.w - S(14), S(14), text="✕", fill="#77739a", font=("Segoe UI", 9)
        )
        self.canvas.tag_bind(self.close_item, "<Button-1>", lambda e: self.root.destroy())

        self.entry_var = tk.StringVar()
        self.entry = tk.Entry(
            self.root, textvariable=self.entry_var, font=("Segoe UI", 10),
            bg=ENTRY_COLOR, fg="#f4f2ff", insertbackground="#f4f2ff", relief="flat",
            highlightthickness=1, highlightbackground=CARD_BORDER, highlightcolor=lerp_color(RING_QUIET, RING_LOUD, 0.3),
        )
        self.entry.place(x=S(12), y=S(64), width=self.w - S(24), height=S(22))
        self.entry.bind("<Return>", self._submit_query)

        self._drag = {"x": 0, "y": 0}
        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._do_drag)

        self._last_seen_query = ""
        self._tick = 0
        self._smooth = 0.0
        self.parent_pid = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0
        self._poll()

    def S(self, v):
        return int(round(v * self.k))

    def _start_drag(self, event):
        self._drag["x"] = event.x
        self._drag["y"] = event.y

    def _do_drag(self, event):
        x = self.root.winfo_x() + event.x - self._drag["x"]
        y = self.root.winfo_y() + event.y - self._drag["y"]
        self.root.geometry(f"+{x}+{y}")

    def _submit_query(self, event):
        query = self.entry_var.get().strip()
        if query:
            try:
                PENDING_SEARCH_PATH.write_text(query, encoding="utf-8")
            except OSError:
                pass

    def _poll(self):
        state = read_state()
        volume = float(state.get("volume", 0.0))
        status = state.get("status", "")
        query = state.get("last_query", "")
        idle = state.get("paused", False) or state.get("sleeping", False)

        self._tick += 1
        if self._tick % 25 == 0 and self.parent_pid and not process_alive(self.parent_pid):
            self.root.destroy()
            return

        # Smooth the ring so it breathes instead of jittering.
        self._smooth += (volume - self._smooth) * 0.45
        r = self.icon_size // 2 + self.S(3) + self._smooth * self.S(12)
        self.canvas.coords(self.ring_item, self.cx - r, self.cy - r, self.cx + r, self.cy + r)
        self.canvas.itemconfig(
            self.ring_item, outline=RING_IDLE if idle else lerp_color(RING_QUIET, RING_LOUD, self._smooth * 1.6)
        )

        shown = status if len(status) <= 42 else status[:41].rstrip() + "…"
        self.canvas.itemconfig(self.text_item, text=shown)

        if query and query != self._last_seen_query:
            self._last_seen_query = query
            if self.root.focus_get() != self.entry:
                self.entry_var.set(query)

        self.root.after(60, self._poll)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    Widget().run()
