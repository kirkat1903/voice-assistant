import applog

applog.setup()

import ctypes

# Without this Windows bitmap-stretches everything at 125%/150% scaling (blurry, jagged icon).
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pystray
import win32api
import win32event
from PIL import Image, ImageEnhance, ImageOps
from pystray._util import serialized_image
from pystray._util import win32 as pwin32
from winerror import ERROR_ALREADY_EXISTS

from voice_assistant import VoiceAssistant, load_config

log = logging.getLogger("aiko")

ICO_PATH = Path(__file__).parent / "icon.ico"
WIDGET_SCRIPT = Path(__file__).parent / "widget.py"
MUTEX_NAME = "Global\\VoiceAssistant_Айко_SingleInstance"
SM_CXSMICON = 49


def tray_icon_size():
    return ctypes.windll.user32.GetSystemMetrics(SM_CXSMICON) or 16


def load_ico_frame(size):
    """Pick the frame of icon.ico that matches the tray size exactly (no rescaling)."""
    with Image.open(ICO_PATH) as ico:
        sizes = sorted(w for w, _ in ico.info.get("sizes", [(256, 256)]))
        best = next((s for s in sizes if s >= size), sizes[-1])
        ico.size = (best, best)
        ico.load()
        frame = ico.convert("RGBA")
    if frame.width != size:
        frame = frame.resize((size, size), Image.LANCZOS)
    return frame


def make_paused_variant(img):
    rgb = img.convert("RGB")
    gray = ImageOps.grayscale(rgb).convert("RGB")
    dimmed = ImageEnhance.Brightness(gray).enhance(0.85)
    dimmed.putalpha(img.getchannel("A"))
    return dimmed


class SharpIcon(pystray.Icon):
    """pystray loads icons at the 'large icon' size and lets Windows shrink them (jagged).
    Load the frame at exactly the notification-area size instead."""

    def _assert_icon_handle(self):
        if self._icon_handle:
            return
        size = tray_icon_size()
        with serialized_image(self.icon, "ICO") as icon_path:
            self._icon_handle = pwin32.LoadImage(
                None, icon_path, pwin32.IMAGE_ICON, size, size, pwin32.LR_LOADFROMFILE
            )


ICON_ACTIVE = load_ico_frame(tray_icon_size())
ICON_PAUSED = make_paused_variant(ICON_ACTIVE)


class TrayApp:
    def __init__(self):
        self.config = load_config()
        self.assistant = VoiceAssistant(self.config)
        self.thread = None
        self.widget_process = None
        self.icon = SharpIcon(
            "voice_assistant",
            ICON_ACTIVE,
            "Айко",
            menu=pystray.Menu(
                pystray.MenuItem(
                    "На паузе", self.toggle_pause, checked=lambda item: self.assistant.paused
                ),
                pystray.MenuItem(
                    "Виджет", self.toggle_widget, checked=lambda item: self._widget_running()
                ),
                pystray.MenuItem("Перезапустить", self.restart),
                pystray.MenuItem("Выход", self.quit_app),
            ),
        )

    def start_thread(self):
        self.thread = threading.Thread(target=self.assistant.loop, daemon=True, name="assistant")
        self.thread.start()

    def _widget_running(self):
        return self.widget_process is not None and self.widget_process.poll() is None

    def start_widget(self):
        if self._widget_running():
            return
        self.widget_process = subprocess.Popen(
            [sys.executable, str(WIDGET_SCRIPT), str(os.getpid())],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def toggle_widget(self, icon, item):
        if self._widget_running():
            self.widget_process.terminate()
            self.widget_process = None
        else:
            self.start_widget()

    def toggle_pause(self, icon, item):
        if self.assistant.paused:
            self.assistant.resume()
        else:
            self.assistant.pause()
        self._sync_icon()

    def _sync_icon(self):
        dim = self.assistant.paused or self.assistant.sleeping
        target = ICON_PAUSED if dim else ICON_ACTIVE
        if self.icon.icon is not target:
            self.icon.icon = target

    def _icon_sync_loop(self):
        while not self.assistant.stopped:
            try:
                self._sync_icon()
            except Exception:
                log.exception("Не удалось обновить значок трея")
            time.sleep(1)

    def restart(self, icon, item):
        self.assistant.stop()
        if self.thread:
            self.thread.join(timeout=15)
            if self.thread.is_alive():
                log.warning("Перезапуск: старый поток не завершился за 15 с")
                return
        self.assistant.sleeping = False
        self.start_thread()
        threading.Thread(target=self._icon_sync_loop, daemon=True, name="icon-sync").start()

    def quit_app(self, icon, item):
        self.assistant.stop()
        if self._widget_running():
            self.widget_process.terminate()
        icon.stop()

    def run(self):
        self.start_thread()
        threading.Thread(target=self._icon_sync_loop, daemon=True, name="icon-sync").start()
        if self.config.get("widget_enabled", True):
            self.start_widget()
        self.icon.run()


if __name__ == "__main__":
    mutex = win32event.CreateMutex(None, False, MUTEX_NAME)
    if win32api.GetLastError() == ERROR_ALREADY_EXISTS:
        log.warning("Уже запущена другая копия — выхожу")
        sys.exit(0)
    log.info("Запуск (python %s, pid %d)", sys.version.split()[0], os.getpid())
    try:
        TrayApp().run()
    except Exception:
        log.exception("Фатальная ошибка в трее")
        raise
    log.info("Завершение")
