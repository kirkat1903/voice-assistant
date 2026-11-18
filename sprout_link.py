"""Связь Айко с островком Sprout (лицо Айко и монитор сессий Claude Code).

Два направления:
  Sprout -> Айко   локальный HTTP на 127.0.0.1 (случайный порт + токен):
                   POST /chat    — текстовый чат из окна Sprout через бесплатного собеседника Айко
                   POST /notify  — «Claude ждёт разрешения» / «Claude закончил»: Айко говорит это вслух
  Айко -> Sprout   именованный канал Sprout: «Айко, что делает Claude», «разреши», «отклони», «вариант два»

Где искать друг друга: Айко пишет %APPDATA%\\Sprout\\aiko.json (порт, токен, путь к state.json, pid).
Sprout читает оттуда же state.json и рисует состояние Айко своим питомцем.
Всё только на этом компьютере: сервер слушает 127.0.0.1, запросы без токена отклоняются.
"""
import json
import logging
import os
import re
import secrets
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger("aiko")

BASE = Path(__file__).parent
SPROUT_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "Sprout"
LINK_PATH = SPROUT_DIR / "aiko.json"
SPROUT_MUTEX = "Local\\SproutClaudeIsland"
MAX_BODY = 64 * 1024

# ---------------------------------------------------------------------------------------------
# Голосовые команды про Claude Code (только когда Айко позвали по имени)
# ---------------------------------------------------------------------------------------------

CLAUDE_WORD = r"(?:клод\w*|клауд\w*|клоуд\w*|claude|cloud|клот\w*)"
AGENT_WORD = rf"(?:{CLAUDE_WORD}|кодекс\w*|codex)"
_STATUS_RE = re.compile(rf"(?:что|как|чем|статус).{{0,25}}{AGENT_WORD}|{AGENT_WORD}.{{0,20}}(?:делает|занят|статус|там)")
_TASKS_RE = re.compile(rf"(?:задач|прогресс|план)\w*.{{0,20}}{CLAUDE_WORD}|{CLAUDE_WORD}.{{0,25}}(?:задач|прогресс|план)"
                       r"|^(?:какие|покажи|прочитай|расскажи|что с|список)\s.{0,12}задач")
_TAIL = rf"(?:\s(?:ему|ей|это|эту|команду|запрос|{CLAUDE_WORD}))*"
_ALLOW_RE = re.compile(rf"^(?:да\s)?(?:разреш\w*|одобр\w*|подтвер\w*|позвол\w*){_TAIL}$")
_DENY_RE = re.compile(rf"^(?:нет\s)?(?:отклон\w*|запрет\w*|откаж\w*|нельзя|не разреш\w*){_TAIL}$")
_CHOOSE_RE = re.compile(r"^(?:вариант|ответ|выбери|выбираю)\s(?:номер\s)?(\S+)$")
_NUMBERS = {
    "1": 1, "один": 1, "первый": 1, "первая": 1, "первое": 1, "одна": 1,
    "2": 2, "два": 2, "второй": 2, "вторая": 2, "второе": 2, "две": 2,
    "3": 3, "три": 3, "третий": 3, "третья": 3, "третье": 3,
    "4": 4, "четыре": 4, "четвёртый": 4, "четвертый": 4, "четвёртая": 4, "четвертая": 4,
}


def spoken(text):
    """Так голос Светланы читает названия правильно: «Клод», а не «Клауде»."""
    for a, b in (("Claude Code", "Клод Код"), ("Claude", "Клод"), ("Sprout", "Спраут")):
        text = text.replace(a, b)
    return text


def voice_command(text):
    """Команда для Sprout по распознанной фразе (уже без имени «Айко»), или None."""
    t = text.lower().replace("ё", "е").strip()
    if _ALLOW_RE.match(t):
        return {"cmd": "allow"}
    if _DENY_RE.match(t):
        return {"cmd": "deny"}
    m = _CHOOSE_RE.match(t)
    if m and m.group(1) in _NUMBERS:
        return {"cmd": "choose", "n": _NUMBERS[m.group(1)]}
    if _TASKS_RE.search(t):
        return {"cmd": "tasks"}
    if _STATUS_RE.search(t):
        return {"cmd": "status"}
    return None


# ---------------------------------------------------------------------------------------------
# Айко -> Sprout: именованный канал
# ---------------------------------------------------------------------------------------------

def _pipe_name():
    import win32api

    user = win32api.GetUserName().replace("\\", "_").replace("/", "_")
    return "\\\\.\\pipe\\sprout-claude-" + user


def ask_sprout(command, timeout_ms=3000):
    """Отправить команду в Sprout. dict с ответом ({"ok", "say"}) или None, если Sprout не запущен."""
    import pywintypes
    import win32file
    import win32pipe

    name = _pipe_name()
    try:
        win32pipe.WaitNamedPipe(name, timeout_ms)
        h = win32file.CreateFile(name, win32file.GENERIC_READ | win32file.GENERIC_WRITE, 0, None,
                                 win32file.OPEN_EXISTING, 0, None)
    except pywintypes.error:
        return None
    try:
        win32pipe.SetNamedPipeHandleState(h, win32pipe.PIPE_READMODE_MESSAGE, None, None)
        win32file.WriteFile(h, json.dumps({"v": 1, **command}, ensure_ascii=False).encode("utf-8"))
        chunks = []
        while True:
            hr, data = win32file.ReadFile(h, 65536)
            chunks.append(data)
            if hr != 234:                                   # ERROR_MORE_DATA
                break
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (pywintypes.error, ValueError) as e:
        log.warning("Sprout: нет ответа по каналу: %s", e)
        return None
    finally:
        h.Close()


def sprout_running():
    import pywintypes
    import win32event

    try:
        win32event.OpenMutex(0x00100000, False, SPROUT_MUTEX).Close()     # SYNCHRONIZE
        return True
    except pywintypes.error:
        return False


def find_sprout_exe(config):
    configured = os.path.expandvars((config.get("sprout_exe") or "").strip())
    candidates = [Path(configured)] if configured else []
    candidates += [BASE / "sprout" / "Sprout.exe", BASE / "Sprout.exe"]
    for c in candidates:
        c = c if c.is_absolute() else BASE / c
        if c.exists():
            return c
    return None


def launch_sprout(config):
    """Запустить Sprout.exe, если он ещё не работает. True — если Sprout работает или запущен."""
    if sprout_running():
        return True
    exe = find_sprout_exe(config)
    if exe is None:
        log.warning("Sprout.exe не найден (положи его в папку sprout рядом с Айко или укажи sprout_exe в config.json)")
        return False
    try:
        subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=0x00000008)   # DETACHED_PROCESS
        return True
    except OSError as e:
        log.warning("Не удалось запустить Sprout: %s", e)
        return False


# ---------------------------------------------------------------------------------------------
# Sprout -> Айко: локальный HTTP
# ---------------------------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    server_version = "AikoBridge/1"
    assistant = None
    token = ""

    def log_message(self, fmt, *args):            # без мусора в консоли
        pass

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        return secrets.compare_digest(self.headers.get("X-Aiko-Token", ""), self.token)

    def do_GET(self):
        if not self._authorized():
            return self._send(403, {"ok": False})
        if self.path == "/ping":
            return self._send(200, {"ok": True, "name": "Айко"})
        self._send(404, {"ok": False})

    def do_POST(self):
        if not self._authorized():
            return self._send(403, {"ok": False})
        try:
            length = min(int(self.headers.get("Content-Length", "0")), MAX_BODY)
            data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return self._send(400, {"ok": False, "error": "bad json"})
        a = self.assistant
        if self.path == "/chat":
            text = str(data.get("text", "")).strip()
            if not text:
                return self._send(400, {"ok": False, "error": "empty"})
            ok, reply = a.text_chat(text)
            return self._send(200, {"ok": ok, "text": reply})
        if self.path == "/notify":
            a.claude_notice(str(data.get("kind", "")), str(data.get("project", "")), str(data.get("detail", "")))
            return self._send(200, {"ok": True})
        if self.path == "/control":
            action = str(data.get("action", ""))
            if action == "pause":
                a.pause()
            elif action == "resume":
                a.resume()
            else:
                return self._send(400, {"ok": False, "error": "bad action"})
            return self._send(200, {"ok": True, "paused": a.paused})
        self._send(404, {"ok": False})


class Bridge:
    def __init__(self, assistant, state_path):
        self.assistant = assistant
        self.state_path = Path(state_path)
        self.server = None

    def start(self):
        token = secrets.token_urlsafe(24)
        handler = type("AikoHandler", (_Handler,), {"assistant": self.assistant, "token": token})
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        except OSError as e:
            log.warning("Мост для Sprout не запустился: %s", e)
            return False
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True, name="sprout-bridge").start()
        info = {"name": "Айко", "port": self.server.server_address[1], "token": token,
                "state": str(self.state_path), "pid": os.getpid()}
        try:
            SPROUT_DIR.mkdir(parents=True, exist_ok=True)
            tmp = LINK_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
            tmp.replace(LINK_PATH)
        except OSError as e:
            log.warning("Не удалось записать %s: %s", LINK_PATH, e)
        log.info("Мост для Sprout: 127.0.0.1:%d", info["port"])
        return True

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        try:
            if json.loads(LINK_PATH.read_text(encoding="utf-8")).get("pid") == os.getpid():
                LINK_PATH.unlink()
        except (OSError, ValueError):
            pass
