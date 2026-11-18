"""Window / tab control for the voice assistant: find, close, minimize, maximize, switch tabs."""
import ctypes
import logging
import os
import subprocess
import time
from ctypes import wintypes
from difflib import SequenceMatcher
from pathlib import Path

import win32api
import win32com.client
import win32con
import win32gui
import win32process

log = logging.getLogger("aiko")

BROWSER_EXES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe",
    "brave.exe", "vivaldi.exe", "iexplore.exe", "yandex.exe", "browser.exe",
}

PROTECTED_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}
# Closing/killing these would take the whole desktop or every UWP window down with it.
NEVER_KILL = {
    "explorer.exe", "applicationframehost.exe", "dwm.exe", "csrss.exe", "winlogon.exe",
    "svchost.exe", "searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
    "systemsettings.exe", "textinputhost.exe",
}

SC_MINIMIZE, SC_MAXIMIZE, SC_RESTORE = 0xF020, 0xF030, 0xF120

# Spoken name -> executable names (title matching covers everything else).
SPOKEN_APPS = {
    "браузер": BROWSER_EXES, "browser": BROWSER_EXES, "интернет": BROWSER_EXES,
    "хром": {"chrome.exe"}, "chrome": {"chrome.exe"}, "гугл хром": {"chrome.exe"},
    "эдж": {"msedge.exe"}, "edge": {"msedge.exe"},
    "фаерфокс": {"firefox.exe"}, "firefox": {"firefox.exe"},
    "опера": {"opera.exe"}, "opera": {"opera.exe"},
    "яндекс браузер": {"browser.exe", "yandex.exe"},
    "блокнот": {"notepad.exe"}, "notepad": {"notepad.exe"},
    "паинт": {"mspaint.exe"}, "paint": {"mspaint.exe"},
    "ворд": {"winword.exe"}, "word": {"winword.exe"},
    "эксель": {"excel.exe"}, "excel": {"excel.exe"},
    "проводник": {"explorer.exe"}, "explorer": {"explorer.exe"},
    "диспетчер задач": {"taskmgr.exe"}, "task manager": {"taskmgr.exe"},
    "терминал": {"windowsterminal.exe"}, "terminal": {"windowsterminal.exe"},
    "командная строка": {"cmd.exe"}, "command prompt": {"cmd.exe"},
    "телеграм": {"telegram.exe"}, "telegram": {"telegram.exe"},
    "дискорд": {"discord.exe"}, "discord": {"discord.exe"},
    "стим": {"steam.exe", "steamwebhelper.exe"}, "steam": {"steam.exe"},
    "спотифай": {"spotify.exe"}, "spotify": {"spotify.exe"},
    "зум": {"zoom.exe"}, "zoom": {"zoom.exe"},
    "код": {"code.exe"}, "vs code": {"code.exe"}, "vscode": {"code.exe"},
    "чат gpt": {"chatgpt.exe"}, "чат джипити": {"chatgpt.exe"}, "chat gpt": {"chatgpt.exe"},
    "чат жпт": {"chatgpt.exe"}, "чат джи пи ти": {"chatgpt.exe"}, "chatgpt": {"chatgpt.exe"},
    "кап кат": {"capcut.exe"}, "капкат": {"capcut.exe"}, "capcut": {"capcut.exe"},
    "да винчи": {"resolve.exe"}, "давинчи": {"resolve.exe"}, "davinci resolve": {"resolve.exe"},
    "докер": {"docker desktop.exe"}, "docker desktop": {"docker desktop.exe"},
    "визуал студио код": {"code.exe"}, "вс код": {"code.exe"}, "visual studio code": {"code.exe"},
    "клоуд": {"claude.exe"}, "клод": {"claude.exe"}, "claude": {"claude.exe"},
    "пайчарм": {"pycharm64.exe"}, "pycharm": {"pycharm64.exe"},
}

# Foreground-app shortcuts for tabs, as virtual-key names (layout independent, see send_hotkey).
TAB_KEYS = {
    "browser": {
        "close": ("CTRL", "W"), "next": ("CTRL", "TAB"), "prev": ("CTRL", "SHIFT", "TAB"),
        "new": ("CTRL", "T"), "reopen": ("CTRL", "SHIFT", "T"),
    },
    "windowsterminal.exe": {
        "close": ("CTRL", "SHIFT", "W"), "next": ("CTRL", "TAB"), "prev": ("CTRL", "SHIFT", "TAB"),
        "new": ("CTRL", "SHIFT", "T"),
    },
    "explorer.exe": {
        "close": ("CTRL", "W"), "next": ("CTRL", "TAB"), "prev": ("CTRL", "SHIFT", "TAB"), "new": ("CTRL", "T"),
    },
    "notepad.exe": {
        "close": ("CTRL", "W"), "next": ("CTRL", "TAB"), "prev": ("CTRL", "SHIFT", "TAB"), "new": ("CTRL", "N"),
    },
    "code.exe": {
        "close": ("CTRL", "W"), "next": ("CTRL", "PGDN"), "prev": ("CTRL", "PGUP"), "new": ("CTRL", "N"),
    },
}

VK_NAMES = {
    "CTRL": 0x11, "SHIFT": 0x10, "ALT": 0x12, "TAB": 0x09, "ENTER": 0x0D,
    "PGUP": 0x21, "PGDN": 0x22, "F4": 0x73, "ESC": 0x1B,
}

_kernel32 = ctypes.windll.kernel32
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
]
_dwmapi = ctypes.windll.dwmapi


def exe_of_pid(pid):
    handle = _kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name.lower()
        return ""
    finally:
        _kernel32.CloseHandle(handle)


def _describe(hwnd):
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    return {
        "hwnd": hwnd,
        "title": win32gui.GetWindowText(hwnd),
        "cls": win32gui.GetClassName(hwnd),
        "pid": pid,
        "exe": exe_of_pid(pid),
    }


def _is_own(win):
    if win["pid"] == os.getpid():
        return True
    return win["exe"] in ("python.exe", "pythonw.exe") and win["cls"] == "TkTopLevel"


def _is_cloaked(hwnd):
    cloaked = ctypes.c_int(0)
    try:
        _dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), 14, ctypes.byref(cloaked), 4)  # DWMWA_CLOAKED
    except Exception:
        return False
    return bool(cloaked.value)


def list_windows():
    """Visible top-level application windows (what you would see in Alt+Tab)."""
    found = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd) or not win32gui.GetWindowText(hwnd):
            return True
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if ex_style & win32con.WS_EX_TOOLWINDOW and not ex_style & win32con.WS_EX_APPWINDOW:
            return True
        if win32gui.GetWindow(hwnd, win32con.GW_OWNER):
            return True
        if _is_cloaked(hwnd):
            return True
        win = _describe(hwnd)
        if win["cls"] in PROTECTED_CLASSES or _is_own(win):
            return True
        found.append(win)
        return True

    win32gui.EnumWindows(callback, None)
    return found


def foreground_window():
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd or not win32gui.IsWindow(hwnd):
        return None
    win = _describe(hwnd)
    if win["cls"] in PROTECTED_CLASSES or _is_own(win):
        return None
    return win


def find_windows(query, config_aliases=None):
    q = query.lower().strip()
    if not q:
        return []
    exes = set(SPOKEN_APPS.get(q, ()))
    if not exes:
        closest = max(SPOKEN_APPS, key=lambda name: SequenceMatcher(None, q, name).ratio())
        if SequenceMatcher(None, q, closest).ratio() >= 0.72:
            exes.update(SPOKEN_APPS[closest])
    target = (config_aliases or {}).get(q)
    if target and target.lower().endswith(".exe"):
        exes.add(Path(target).name.lower())

    matches = []
    for win in list_windows():
        stem = Path(win["exe"]).stem
        title = win["title"].lower()
        if (
            win["exe"] in exes
            or (len(q) >= 3 and q in title)
            or (len(q) >= 4 and stem and q in stem)
            or (len(q) >= 4 and stem and SequenceMatcher(None, q, stem).ratio() >= 0.85)
        ):
            matches.append(win)
    return matches


def exists(hwnd):
    return bool(win32gui.IsWindow(hwnd))


def close_windows(wins, wait=1.5):
    """Politely ask windows to close (WM_CLOSE keeps 'save changes?' prompts). Returns windows still open."""
    for win in wins:
        try:
            win32gui.PostMessage(win["hwnd"], win32con.WM_CLOSE, 0, 0)
        except Exception:
            log.exception("WM_CLOSE не отправлен: %s", win["title"])
    end = time.time() + wait
    while time.time() < end and any(exists(w["hwnd"]) for w in wins):
        time.sleep(0.1)
    return [w for w in wins if exists(w["hwnd"])]


def force_kill(wins):
    """Terminate the processes owning these windows. Returns the number of processes killed."""
    pids = {w["pid"] for w in wins if w["exe"] not in NEVER_KILL}
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    return len(pids)


def _syscommand(hwnd, command):
    win32gui.PostMessage(hwnd, win32con.WM_SYSCOMMAND, command, 0)


def minimize(wins):
    for win in wins:
        _syscommand(win["hwnd"], SC_MINIMIZE)


def maximize(wins):
    for win in wins:
        _syscommand(win["hwnd"], SC_MAXIMIZE)


def bring_to_front(hwnd):
    """Give `hwnd` the keyboard focus. Windows often refuses this to a background process, so try
    progressively more forceful ways. Returns True once the window really is in the foreground."""
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    def direct():
        win32gui.SetForegroundWindow(hwnd)

    def attach_input():
        # Share the input queue with the current foreground thread: then the focus change is allowed.
        fg = win32gui.GetForegroundWindow()
        fg_tid = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
        cur_tid = win32api.GetCurrentThreadId()
        attached = bool(fg_tid) and fg_tid != cur_tid and win32process.AttachThreadInput(cur_tid, fg_tid, True)
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        finally:
            if attached:
                win32process.AttachThreadInput(cur_tid, fg_tid, False)

    def switch_to_this():
        ctypes.windll.user32.SwitchToThisWindow(hwnd, True)

    def minimize_restore():
        # Restoring a window is an activation Windows allows even while a fullscreen game owns the
        # foreground (the only method that worked in that case).
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        time.sleep(0.25)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.15)

    def alt_tap():
        # Last resort: a synthetic key press makes this process the last input owner. It is sent to
        # whatever has focus right now, so it goes last.
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        win32gui.SetForegroundWindow(hwnd)

    for strategy in (direct, minimize_restore, switch_to_this, attach_input, alt_tap):
        try:
            strategy()
        except Exception:
            pass
        time.sleep(0.08)
        if win32gui.GetForegroundWindow() == hwnd:
            return True
    log.warning("Не удалось вывести окно на передний план: %s", win32gui.GetWindowText(hwnd))
    return False


def minimize_all():
    win32com.client.Dispatch("Shell.Application").MinimizeAll()


def restore_all():
    win32com.client.Dispatch("Shell.Application").UndoMinimizeAll()


def send_hotkey(*keys):
    """Press a key combination by virtual-key code.

    Unlike WScript's SendKeys (which types *characters* for the active layout) this works with the
    Russian layout too: Ctrl+W is the physical W key whatever letter the layout puts on it.
    """
    codes = [VK_NAMES[k] if k in VK_NAMES else ord(k.upper()) for k in keys]
    pressed = []
    try:
        for code in codes:
            win32api.keybd_event(code, win32api.MapVirtualKey(code, 0), 0, 0)
            pressed.append(code)
        time.sleep(0.03)
    finally:
        # never leave Ctrl/Shift stuck down, even if something above raised
        for code in reversed(pressed):
            win32api.keybd_event(code, win32api.MapVirtualKey(code, 0), win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.03)


def paste_text(text):
    """Type arbitrary text into the focused field via the clipboard (layout independent)."""
    import win32clipboard

    def open_clipboard():
        for _ in range(10):
            try:
                win32clipboard.OpenClipboard()
                return True
            except Exception:
                time.sleep(0.05)
        return False

    previous = None
    if not open_clipboard():
        raise RuntimeError("буфер обмена занят")
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
            previous = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()

    send_hotkey("CTRL", "V")
    time.sleep(0.25)
    if previous is not None and open_clipboard():
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, previous)
        finally:
            win32clipboard.CloseClipboard()


def tab_keys_for(win):
    if win is None:
        return None
    if win["exe"] in BROWSER_EXES:
        return TAB_KEYS["browser"]
    return TAB_KEYS.get(win["exe"])


def tab_target():
    """The window tab commands should act on: the foreground tabbed app, otherwise the browser
    window used most recently (EnumWindows walks the z-order from the top)."""
    win = foreground_window()
    if tab_keys_for(win):
        return win
    for candidate in list_windows():
        if candidate["exe"] in BROWSER_EXES:
            return candidate
    return None


def _wait_foreground(hwnd, timeout=1.2):
    end = time.time() + timeout
    while time.time() < end:
        fg = foreground_window()
        if fg and fg["hwnd"] == hwnd:
            return True
        time.sleep(0.05)
    return False


def tab_action(action):
    """Run a tab shortcut (close/new/next/prev/reopen) in the right window.

    Returns True on success, False if the shortcut is unsupported or the window refused focus,
    None if there is no tabbed app (no browser open) at all."""
    win = tab_target()
    if win is None:
        return None
    combo = tab_keys_for(win).get(action)
    if combo is None:
        return False
    fg = foreground_window()
    if not fg or fg["hwnd"] != win["hwnd"]:
        if not bring_to_front(win["hwnd"]) and not _wait_foreground(win["hwnd"]):
            log.warning("Вкладки: окно %s не получило фокус", win["exe"])
            return False
        time.sleep(0.15)
    send_hotkey(*combo)
    return True
