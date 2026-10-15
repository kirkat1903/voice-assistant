import audioop
import json
import logging
import os
import random
import re
import subprocess
import sys
import threading
import time
import webbrowser
from difflib import get_close_matches
from pathlib import Path
from urllib.parse import quote_plus

import pyaudio
import requests
import speech_recognition as sr
import win32api
import win32com.client
import win32con
import win32gui
import win32process
from PIL import ImageGrab

import windows_ctl as wc
from tts_engine import NeuralVoice

log = logging.getLogger("aiko")

FOREGROUND_WORDS = {
    "окно", "это окно", "текущее окно", "программу", "эту программу", "приложение", "это приложение",
    "это", "эту", "window", "this window", "current window", "the window", "app", "this app", "this", "it",
}
TAB_WORDS = {"вкладку", "эту вкладку", "текущую вкладку", "tab", "this tab", "the tab", "current tab"}
ALL_WORDS = {"все окна", "окна", "всё", "все", "все окна сразу", "all windows", "everything", "all"}
TAB_PHRASES = {
    "new": {"новая вкладка", "открой новую вкладку", "открой вкладку", "открой ещё одну вкладку",
            "new tab", "open new tab", "open a new tab"},
    "next": {"следующая вкладка", "следующую вкладку", "переключи вкладку", "переключись на следующую вкладку",
             "next tab"},
    "prev": {"предыдущая вкладка", "предыдущую вкладку", "прошлая вкладка", "переключись на предыдущую вкладку",
             "previous tab", "prev tab"},
    "reopen": {"верни вкладку", "верни закрытую вкладку", "открой закрытую вкладку", "восстанови вкладку",
               "восстанови закрытую вкладку", "reopen tab", "reopen closed tab"},
}
RESTORE_PHRASES = {
    "верни окна", "верни все окна", "восстанови окна", "восстанови все окна", "покажи окна",
    "restore windows", "undo minimize",
}
DESKTOP_PHRASES = {"покажи рабочий стол", "show desktop"}

CONFIG_PATH = Path(__file__).parent / "config.json"
STATE_PATH = Path(__file__).parent / "state.json"
PENDING_SEARCH_PATH = Path(__file__).parent / "pending_search.txt"

GREETING = "Привет! Я тебе помогу, только позови меня — я Айко!"

EXIT_PHRASES = {
    "выход", "стоп", "заверши работу", "пока", "отключись",
    "exit", "stop", "quit", "bye", "goodbye",
}

SLEEP_PHRASES = {
    "ложись спать", "иди спать", "спокойной ночи",
}

EDIT_VERBS = (
    "измени", "поменяй", "смени", "исправь", "обнови", "поправь",
    "change", "edit", "update",
)
EDIT_NOUNS = ("запрос на ", "поиск на ", "search to ", "query to ")
EDIT_TRIGGERS = tuple(f"{v} {n}" for v in EDIT_VERBS for n in EDIT_NOUNS)

BROWSER_EXES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe",
    "brave.exe", "vivaldi.exe", "iexplore.exe",
}

PHRASES = {
    "greeting": [
        "Привет! Слушаю тебя.",
        "Я здесь, что нужно сделать?",
        "На связи! Говори, что нужно.",
        "Привет-привет! Чем помочь?",
        "Готов к работе, жду команду.",
    ],
    "not_understood": [
        "Хм, не расслышала. Повтори, пожалуйста?",
        "Кажется, я не поняла. Можешь сказать иначе?",
        "Упс, не разобрала. Ещё разок?",
        "Что-то пошло не так с распознаванием — повтори команду.",
        "Не совсем поняла, о чём речь. Уточнишь?",
    ],
    "error": [
        "Не получилось это сделать, извини.",
        "Тут что-то сломалось — попробуем ещё раз?",
        "Не вышло. Хочешь, попробую другим способом?",
        "Ой, ошибка. Дай мне секунду или повтори запрос.",
        "Не смогла справиться с этим сейчас.",
    ],
    "confirmation": [
        "Готово!",
        "Сделано, можешь проверить.",
        "Всё выполнила.",
        "Готово, что дальше?",
        "Есть! Задача закрыта.",
    ],
    "waiting": [
        "Секунду, думаю...",
        "Уже работаю над этим.",
        "Дай мне момент.",
        "Обрабатываю запрос...",
        "Почти готово, подожди чуть-чуть.",
    ],
    "clarifying": [
        "А что именно ты имеешь в виду?",
        "Уточни, пожалуйста, о чём речь?",
        "Какой вариант тебе нужен — этот или тот?",
        "Можешь пояснить чуть подробнее?",
        "Ты про это или про другое?",
    ],
    "farewell": [
        "Пока! Зови, если что.",
        "До встречи, я рядом.",
        "Заканчиваю работу, увидимся.",
        "Хорошего дня! Обращайся ещё.",
        "Ухожу в спящий режим, но я тут.",
    ],
    "filler": [
        "Так, дай подумать секунду...",
        "Копаюсь в настройках, не переключайся.",
        "Момент, собираю всё воедино.",
    ],
}


def _pick(category):
    return random.choice(PHRASES[category])


VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3

DURATION_UNITS = (
    ("час", 3600), ("мин", 60), ("сек", 1),
)

CURRENCY_MAP = {
    "доллар": ("cbr", "USD", "Доллар"), "доллара": ("cbr", "USD", "Доллар"),
    "долларов": ("cbr", "USD", "Доллар"), "usd": ("cbr", "USD", "Доллар"),
    "евро": ("cbr", "EUR", "Евро"), "eur": ("cbr", "EUR", "Евро"),
    "юань": ("cbr", "CNY", "Юань"), "юаня": ("cbr", "CNY", "Юань"),
    "биткоин": ("crypto", "bitcoin", "Биткоин"), "биткоина": ("crypto", "bitcoin", "Биткоин"),
    "bitcoin": ("crypto", "bitcoin", "Биткоин"), "btc": ("crypto", "bitcoin", "Биткоин"),
    "эфириум": ("crypto", "ethereum", "Эфириум"), "ethereum": ("crypto", "ethereum", "Эфириум"),
    "eth": ("crypto", "ethereum", "Эфириум"),
}

SHORTCUT_DIRS = [
    Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
    Path.home() / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs",
    Path.home() / "Desktop",
    Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop",
]


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class VoiceAssistant:
    def __init__(self, config):
        self.config = config
        self.recognizer = sr.Recognizer()
        self.microphone = None
        self._tts_fallback = None
        self._speak_lock = threading.Lock()
        self.neural_voice = None
        if config.get("neural_voice", True):
            self.neural_voice = NeuralVoice(
                voice=config.get("tts_voice", "ru-RU-SvetlanaNeural"),
                rate=config.get("tts_rate", "+5%"),
                pitch=config.get("tts_pitch", "+0Hz"),
            )
        self._shortcuts_cache = None
        self.paused = False
        self.sleeping = False
        self.stopped = False
        self.last_search_query = ""
        self._current_addressed = False
        self._last_text = ""
        self._volume = 0.0
        self._level_thread = None

    def _speak_system(self, text):
        """Offline fallback: the built-in Windows SAPI voice."""
        try:
            if self._tts_fallback is None:
                import pyttsx3

                engine = pyttsx3.init()
                for voice in engine.getProperty("voices"):
                    if "ru" in (voice.id or "").lower() or "russian" in (voice.name or "").lower():
                        engine.setProperty("voice", voice.id)
                        break
                self._tts_fallback = engine
            self._tts_fallback.say(text)
            self._tts_fallback.runAndWait()
        except Exception:
            log.exception("Системный голос не сработал")
            self._tts_fallback = None

    def _speak_audio(self, text):
        with self._speak_lock:
            if self.neural_voice is not None:
                try:
                    self.neural_voice.say(text)
                    return
                except Exception as e:
                    log.warning("Нейроголос недоступен (%s: %s), говорю системным", type(e).__name__, e)
            self._speak_system(text)

    def prewarm_voice(self):
        """Synthesize the fixed phrases once so they play instantly and work offline."""
        if self.neural_voice is None:
            return
        phrases = [GREETING, "Спокойной ночи! Разбуди меня, когда понадоблюсь."]
        for group in PHRASES.values():
            phrases.extend(group)
        for phrase in phrases:
            if self.stopped:
                return
            try:
                self.neural_voice.prepare(phrase)
            except Exception as e:
                log.info("Прогрев голоса остановлен: %s: %s", type(e).__name__, e)
                return

    def speak(self, text):
        print(f"Ассистент: {text}")
        self._last_text = text
        self._write_state()
        if self.config.get("voice_feedback", True):
            self._speak_audio(text)

    def _say(self, text):
        self._last_text = text
        self._write_state()
        if self._current_addressed:
            self.speak(text)
        else:
            print(f"(тихо, не по имени) {text}")

    def _write_state(self):
        state = {
            "status": self._last_text,
            "volume": round(self._volume, 3),
            "addressed": self._current_addressed,
            "last_query": self.last_search_query,
            "paused": self.paused,
            "sleeping": self.sleeping,
            "ts": time.time(),
        }
        try:
            tmp = STATE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            tmp.replace(STATE_PATH)
        except OSError:
            pass

    def _level_meter_loop(self):
        """Feeds the widget's volume ring; reopens the stream after sleep/boot glitches."""
        while not self.stopped:
            p = stream = None
            try:
                p = pyaudio.PyAudio()
                stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000,
                                 input=True, frames_per_buffer=512)
                last_write = 0.0
                while not self.stopped:
                    data = stream.read(512, exception_on_overflow=False)
                    self._volume = min(1.0, audioop.rms(data, 2) / 4000)
                    now = time.time()
                    if now - last_write > 0.07:
                        self._write_state()
                        last_write = now
            except Exception as e:
                log.warning("Индикатор громкости: %s: %s — повтор через 5 с", type(e).__name__, e)
                self._volume = 0.0
                time.sleep(5)
            finally:
                try:
                    if stream is not None:
                        stream.stop_stream()
                        stream.close()
                    if p is not None:
                        p.terminate()
                except Exception:
                    pass

    def listen(self):
        if self.microphone is None:
            self.microphone = sr.Microphone()
        with self.microphone as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=0.4)
            try:
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=10)
            except sr.WaitTimeoutError:
                return ""

        languages = self.config.get("recognition_languages") or [
            self.config.get("recognition_language", "ru-RU")
        ]
        for language in languages:
            try:
                text = self.recognizer.recognize_google(audio, language=language)
                print(f"Вы сказали ({language}): {text}")
                return text.lower().strip()
            except sr.UnknownValueError:
                continue
            except sr.RequestError as e:
                self.speak(f"{_pick('error')} Проблема с распознаванием речи, проверь интернет.")
                print(f"Ошибка распознавания: {e}")
                return ""
        return ""

    def index_start_menu_shortcuts(self):
        shortcuts = {}
        for base in SHORTCUT_DIRS:
            if not base.exists():
                continue
            for path in base.rglob("*.lnk"):
                name = path.stem.lower()
                shortcuts[name] = path
        self._shortcuts_cache = shortcuts
        return shortcuts

    def find_shortcut(self, query):
        if self._shortcuts_cache is None:
            self.index_start_menu_shortcuts()
        names = list(self._shortcuts_cache.keys())
        matches = get_close_matches(query, names, n=1, cutoff=0.75)
        if not matches:
            matches = [n for n in names if len(query) >= 4 and query in n]
        if matches:
            return matches[0], self._shortcuts_cache[matches[0]]
        return None

    def open_app(self, query):
        query = query.strip().lower()
        aliases = self.config.get("app_aliases", {})
        if query in aliases:
            target = aliases[query]
            try:
                if target.startswith("ms-settings:"):
                    os.startfile(target)
                else:
                    subprocess.Popen(target)
                return True
            except OSError:
                pass

        found = self.find_shortcut(query)
        if found:
            name, shortcut = found
            if name != query:
                self._say(f"Нашла похожее: {name}")
            os.startfile(str(shortcut))
            return True

        try:
            subprocess.Popen(query)
            return True
        except OSError:
            return False

    def open_website(self, query):
        query = query.strip().lower()
        sites = self.config.get("site_aliases", {})
        if query in sites:
            url = sites[query]
        elif "." in query:
            url = query
        else:
            url = f"{query}.ru"
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        webbrowser.open(url)
        return True

    def search_google(self, query):
        webbrowser.open(f"https://www.google.com/search?q={quote_plus(query)}")

    def update_search_google(self, query):
        url = f"https://www.google.com/search?q={quote_plus(query)}"
        if not self._update_search_in_browser(url):
            webbrowser.open(url)

    def _foreground_browser_exe(self):
        try:
            hwnd = win32gui.GetForegroundWindow()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid
            )
            try:
                exe_path = win32process.GetModuleFileNameEx(handle, 0)
            finally:
                win32api.CloseHandle(handle)
            return Path(exe_path).name.lower()
        except Exception:
            return None

    def _update_search_in_browser(self, url):
        if self._foreground_browser_exe() not in BROWSER_EXES:
            return False
        try:
            wc.send_hotkey("CTRL", "L")
            time.sleep(0.2)
            wc.paste_text(url)
            wc.send_hotkey("ENTER")
            return True
        except Exception:
            log.exception("Не удалось обновить поиск в текущей вкладке")
            return False

    def run_system_command(self, action):
        if action == "lock":
            subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"])
        elif action == "shutdown":
            subprocess.run(["shutdown", "/s", "/t", "0"])
        elif action == "restart":
            subprocess.run(["shutdown", "/r", "/t", "0"])
        elif action == "sleep":
            subprocess.run(
                ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]
            )

    def _key_tap(self, vk, times=1):
        for _ in range(times):
            win32api.keybd_event(vk, 0, 0, 0)
            win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.03)

    def get_brightness(self):
        try:
            wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\wmi")
            for item in wmi.ExecQuery("SELECT * FROM WmiMonitorBrightness"):
                return item.CurrentBrightness
        except Exception:
            return None
        return None

    def set_brightness(self, level):
        level = max(0, min(100, level))
        try:
            wmi = win32com.client.GetObject("winmgmts:\\\\.\\root\\wmi")
            for method in wmi.ExecQuery("SELECT * FROM WmiMonitorBrightnessMethods"):
                method.WmiSetBrightness(1, level)
            return True
        except Exception:
            return False

    def minimize_all(self):
        try:
            wc.minimize_all()
            return True
        except Exception:
            log.exception("Не удалось свернуть все окна")
            return False

    # ---- windows, tabs -------------------------------------------------------------------

    def _do_tab(self, action):
        try:
            ok = wc.tab_action(action)
        except Exception:
            log.exception("Вкладки: %s", action)
            ok = False
        if ok is None:
            self._say("Здесь нет вкладок — открой браузер и повтори.")
        elif ok:
            self._say(_pick("confirmation"))
        else:
            self._say("Для этой программы такая команда с вкладками не поддерживается.")

    def _close_wins(self, wins, label):
        left = wc.close_windows(wins)
        if left:
            self._say(f"«{label}» не закрылось — возможно, спрашивает про сохранение. "
                      f"Если нужно жёстко, скажи «Айко, принудительно закрой {label}».")
        else:
            self._say(f"Закрыла: {label}" if label not in ("окно",) else _pick("confirmation"))

    def _close_command(self, rest, addressed):
        if not addressed:
            self._say("Чтобы я что-то закрыла, сначала позови меня: «Айко, закрой …»")
            return
        if rest in TAB_WORDS:
            self._do_tab("close")
        elif rest in FOREGROUND_WORDS:
            win = wc.foreground_window()
            if win is None:
                self._say("Не вижу окна, которое можно закрыть.")
            else:
                self._close_wins([win], "окно")
        elif rest in ALL_WORDS:
            self._say("Все окна разом закрывать не буду — назови, что именно закрыть.")
        else:
            wins = wc.find_windows(rest, self.config.get("app_aliases", {}))
            if not wins:
                self._say(f"{_pick('error')} Не нашла открытое окно «{rest}».")
            else:
                self._close_wins(wins, rest)

    def _force_close_command(self, rest, addressed):
        if not addressed:
            self._say("Принудительное закрытие — только после «Айко».")
            return
        if rest in FOREGROUND_WORDS:
            win = wc.foreground_window()
            wins = [win] if win else []
        else:
            wins = wc.find_windows(rest, self.config.get("app_aliases", {}))
        if not wins:
            self._say(f"{_pick('error')} Не нашла открытое окно «{rest}».")
            return
        killed = wc.force_kill(wins)
        self._say(_pick("confirmation") if killed else "Это системное окно, его я не трогаю.")

    def _minimize_command(self, rest):
        if rest in ALL_WORDS:
            self._say(_pick("confirmation") if self.minimize_all() else _pick("error"))
            return
        if rest == "" or rest in FOREGROUND_WORDS:
            win = wc.foreground_window()
            wins = [win] if win else []
        else:
            wins = wc.find_windows(rest, self.config.get("app_aliases", {}))
        if not wins:
            self._say(f"{_pick('error')} Не нашла окно «{rest or 'текущее'}».")
            return
        wc.minimize(wins)
        self._say(_pick("confirmation"))

    def _maximize_command(self, rest):
        if rest in ALL_WORDS:
            try:
                wc.restore_all()
                self._say(_pick("confirmation"))
            except Exception:
                log.exception("Не удалось вернуть окна")
                self._say(_pick("error"))
            return
        fullscreen = rest.endswith("на весь экран")
        if fullscreen:
            rest = rest[: -len("на весь экран")].strip()
        if rest == "" or rest in FOREGROUND_WORDS:
            win = wc.foreground_window()
            wins = [win] if win else []
            if wins:
                wc.maximize(wins)
        else:
            wins = wc.find_windows(rest, self.config.get("app_aliases", {}))
            if wins:
                wc.bring_to_front(wins[0]["hwnd"])
                if fullscreen:
                    wc.maximize(wins[:1])
        if not wins:
            self._say(f"{_pick('error')} Не нашла окно «{rest or 'текущее'}».")
        else:
            self._say(_pick("confirmation"))

    def _handle_window_command(self, text, addressed):
        """Returns True if `text` was a window/tab command (handled or politely refused)."""
        for action, phrases in TAB_PHRASES.items():
            if text in phrases:
                self._do_tab(action)
                return True
        if text in RESTORE_PHRASES:
            self._maximize_command("все окна")
            return True
        if text in DESKTOP_PHRASES:
            self._minimize_command("все окна")
            return True

        clean = lambda s: re.sub(r"^(?:программу|программа|приложение|окно|app|application|window)\s+", "", s.strip())

        m = re.match(r"^(?:принудительно закрой|принудительно закрыть|убей|заверши процесс|kill|force close)\s+(.+)$", text)
        if m:
            self._force_close_command(clean(m.group(1)), addressed)
            return True
        m = re.match(r"^(?:закрой|закрыть|close)\s+(.+)$", text)
        if m:
            self._close_command(clean(m.group(1)) if m.group(1).strip() not in FOREGROUND_WORDS else m.group(1).strip(), addressed)
            return True
        m = re.match(r"^(?:сверни|свернуть|minimize)(?:\s+(.*))?$", text)
        if m:
            rest = (m.group(1) or "").strip()
            self._minimize_command(rest if rest in FOREGROUND_WORDS | ALL_WORDS else clean(rest))
            return True
        m = re.match(r"^(?:разверни|развернуть|maximize|restore)(?:\s+(.*))?$", text)
        if m:
            rest = (m.group(1) or "").strip()
            self._maximize_command(rest if rest in FOREGROUND_WORDS | ALL_WORDS else clean(rest))
            return True
        return False

    def take_screenshot(self):
        shots_dir = Path(__file__).parent / "screenshots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        filename = shots_dir / f"screenshot_{time.strftime('%Y%m%d_%H%M%S')}.png"
        img = ImageGrab.grab()
        img.save(filename)
        return filename

    def get_weather(self, city):
        try:
            resp = requests.get(
                f"https://wttr.in/{quote_plus(city)}",
                params={"format": "3", "lang": "ru", "m": ""},
                headers={"User-Agent": "curl"},
                timeout=6,
            )
            resp.raise_for_status()
            return resp.text.strip()
        except requests.RequestException:
            return None

    def get_currency_rate(self, name):
        info = CURRENCY_MAP.get(name.strip().lower())
        if not info:
            return None
        kind, code, label = info
        try:
            if kind == "cbr":
                resp = requests.get("https://www.cbr-xml-daily.ru/daily_json.js", timeout=6)
                resp.raise_for_status()
                value = resp.json()["Valute"][code]["Value"]
                return f"{label}: {value:.2f} руб."
            resp = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": code, "vs_currencies": "rub"},
                timeout=6,
            )
            resp.raise_for_status()
            price = resp.json()[code]["rub"]
            return f"{label}: {price:,.0f} руб.".replace(",", " ")
        except (requests.RequestException, KeyError, ValueError):
            return None

    def _parse_duration(self, text):
        match = re.search(r"(\d+)\s*(час\w*|мин\w*|сек\w*)", text)
        if not match:
            return None
        n = int(match.group(1))
        unit_word = match.group(2)
        for prefix, mult in DURATION_UNITS:
            if unit_word.startswith(prefix):
                return n * mult
        return None

    def _format_duration(self, seconds):
        if seconds % 3600 == 0:
            return f"{seconds // 3600} ч"
        if seconds % 60 == 0:
            return f"{seconds // 60} мин"
        return f"{seconds} сек"

    def _strip_duration_prefix(self, text):
        text = re.sub(r"через\s*\d+\s*(час\w*|мин\w*|сек\w*)\s*", "", text, count=1).strip()
        text = re.sub(r"^(о|про|что|чтобы)\s+", "", text)
        return text.strip()

    def _start_timer(self, seconds, message):
        def fire():
            self._current_addressed = True
            self.speak(message)

        timer = threading.Timer(seconds, fire)
        timer.daemon = True
        timer.start()

    def _wake_words(self):
        wake_word = self.config.get("wake_word", "")
        if isinstance(wake_word, list):
            return [w.strip().lower() for w in wake_word if w.strip()]
        wake_word = wake_word.strip().lower()
        return [wake_word] if wake_word else []

    def _match_edit_trigger(self, text):
        for trigger in EDIT_TRIGGERS:
            if text.startswith(trigger):
                return trigger
        return None

    def _check_pending_search(self):
        if not PENDING_SEARCH_PATH.exists():
            return
        try:
            query = PENDING_SEARCH_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            query = ""
        try:
            PENDING_SEARCH_PATH.unlink()
        except OSError:
            pass
        if query:
            self.last_search_query = query
            self._current_addressed = True
            self._say(f"Меняю запрос на {query}")
            self.update_search_google(query)

    def handle_command(self, text):
        text = re.sub(r"[,.!?;:]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        wake_words = self._wake_words()
        addressed = False
        if wake_words:
            words = text.split()
            if words:
                first = words[0]
                if first in wake_words or get_close_matches(first, wake_words, n=1, cutoff=0.6):
                    addressed = True
                    text = " ".join(words[1:]).strip()

        self._current_addressed = addressed

        if self.sleeping:
            if not addressed:
                return
            self.sleeping = False
            self._write_state()
            if not text:
                self.speak(_pick("greeting"))
                return

        if not text:
            if addressed:
                self.speak(_pick("greeting"))
            return

        if addressed and text in SLEEP_PHRASES:
            self.sleeping = True
            self.speak("Спокойной ночи! Разбуди меня, когда понадоблюсь.")
            return

        if addressed and text in EXIT_PHRASES:
            self.sleeping = True
            self.speak(_pick("farewell"))
            return

        if addressed:
            for phrase, action in self.config.get("system_commands", {}).items():
                if phrase in text:
                    self._say(_pick("confirmation"))
                    self.run_system_command(action)
                    return

        if self._handle_window_command(text, addressed):
            return

        if text in ("который час", "сколько времени", "what time is it"):
            self._say(f"Сейчас {time.strftime('%H:%M')}")
            return

        if text in ("какое сегодня число", "какая сегодня дата", "what's the date", "what is the date"):
            self._say(f"Сегодня {time.strftime('%d.%m.%Y')}")
            return

        timer_triggers = ("поставь таймер на ", "запусти таймер на ", "set timer for ")
        for trigger in timer_triggers:
            if text.startswith(trigger):
                seconds = self._parse_duration(text)
                if seconds:
                    self._say(f"Таймер на {self._format_duration(seconds)} запущен")
                    self._start_timer(seconds, f"Таймер на {self._format_duration(seconds)} завершён!")
                else:
                    self._say("Не поняла, на сколько ставить таймер")
                return

        reminder_triggers = ("напомни мне ", "напомни ", "remind me ")
        for trigger in reminder_triggers:
            if text.startswith(trigger):
                rest = text[len(trigger):]
                seconds = self._parse_duration(rest)
                if not seconds:
                    self._say("Через сколько напомнить?")
                    return
                msg = self._strip_duration_prefix(rest) or "напоминание"
                self._say(f"Напомню через {self._format_duration(seconds)}: {msg}")
                self._start_timer(seconds, f"Напоминаю: {msg}")
                return

        weather_prefixes = (
            "какая погода в ", "погода в ", "погода на ", "weather in ",
            "какая погода", "погода", "weather",
        )
        for trigger in weather_prefixes:
            if text.startswith(trigger):
                city = text[len(trigger):].strip() or self.config.get("city", "Москва")
                self._say(_pick("filler"))
                result = self.get_weather(city)
                self._say(result if result else _pick("error"))
                return

        currency_triggers = ("какой курс ", "курс ", "exchange rate ")
        for trigger in currency_triggers:
            if text.startswith(trigger):
                name = text[len(trigger):].replace("валюты", "").strip()
                self._say(_pick("filler"))
                result = self.get_currency_rate(name)
                self._say(result if result else f"Не знаю курс для «{name}», извини")
                return

        if text in ("громче", "сделай громче", "увеличь громкость", "volume up"):
            self._key_tap(VK_VOLUME_UP, times=3)
            self._say(_pick("confirmation"))
            return
        if text in ("тише", "сделай тише", "уменьши громкость", "volume down"):
            self._key_tap(VK_VOLUME_DOWN, times=3)
            self._say(_pick("confirmation"))
            return
        if text in ("выключи звук", "без звука", "заглуши", "mute"):
            self._key_tap(VK_VOLUME_MUTE)
            self._say(_pick("confirmation"))
            return
        if text in ("пауза", "останови музыку", "поставь на паузу", "pause"):
            self._key_tap(VK_MEDIA_PLAY_PAUSE)
            self._say(_pick("confirmation"))
            return
        if text in ("play", "продолжи", "включи музыку", "воспроизведи"):
            self._key_tap(VK_MEDIA_PLAY_PAUSE)
            self._say(_pick("confirmation"))
            return
        if text in ("следующий трек", "дальше трек", "next track", "next"):
            self._key_tap(VK_MEDIA_NEXT_TRACK)
            self._say(_pick("confirmation"))
            return
        if text in ("предыдущий трек", "прошлый трек", "previous track", "previous"):
            self._key_tap(VK_MEDIA_PREV_TRACK)
            self._say(_pick("confirmation"))
            return

        if text in ("ярче", "сделай ярче", "увеличь яркость", "brighter"):
            current = self.get_brightness()
            ok = self.set_brightness((current or 50) + 15)
            self._say(_pick("confirmation") if ok else _pick("error"))
            return
        if text in ("темнее", "сделай темнее", "уменьши яркость", "darker"):
            current = self.get_brightness()
            ok = self.set_brightness((current or 50) - 15)
            self._say(_pick("confirmation") if ok else _pick("error"))
            return
        for trigger in ("яркость на ", "поставь яркость ", "установи яркость "):
            if text.startswith(trigger):
                match = re.search(r"\d+", text)
                if match:
                    ok = self.set_brightness(int(match.group()))
                    self._say(_pick("confirmation") if ok else _pick("error"))
                else:
                    self._say(_pick("clarifying"))
                return

        if text in ("сделай скриншот", "скриншот", "screenshot", "сними экран"):
            try:
                path = self.take_screenshot()
                self._say(f"Скриншот сохранён: {path.name}")
            except Exception:
                self._say(_pick("error"))
            return

        edit_trigger = self._match_edit_trigger(text)
        if edit_trigger is not None:
            query = text[len(edit_trigger):].strip()
            if query:
                self.last_search_query = query
                self._say(f"Меняю запрос на {query}")
                self.update_search_google(query)
            else:
                self._say(_pick("clarifying"))
            return

        shortcut_triggers = ("открой ярлык ", "запусти ярлык ", "open shortcut ")
        for trigger in shortcut_triggers:
            if text.startswith(trigger):
                query = text[len(trigger):].strip()
                found = self.find_shortcut(query)
                if found:
                    name, path = found
                    self._say(f"Открываю ярлык {name}")
                    os.startfile(str(path))
                else:
                    self._say(f"{_pick('error')} Не нашла ярлык «{query}».")
                return

        site_triggers = (
            "открой сайт ", "зайди на сайт ", "перейди на сайт ",
            "open site ", "go to site ", "open website ",
        )
        for trigger in site_triggers:
            if text.startswith(trigger):
                query = text[len(trigger):].strip()
                self._say(f"Открываю сайт {query}")
                self.open_website(query)
                return

        search_triggers = ("найди ", "поищи ", "find ", "search for ", "search ")
        for trigger in search_triggers:
            if text.startswith(trigger):
                query = text[len(trigger):].strip()
                self.last_search_query = query
                self._say(f"Ищу {query}")
                self.search_google(query)
                return

        open_triggers = ("открой ", "запусти ", "open ", "launch ", "start ", "run ")
        for trigger in open_triggers:
            if text.startswith(trigger):
                query = text[len(trigger):].strip()
                if query in self.config.get("site_aliases", {}):
                    self._say(f"Открываю {query}")
                    self.open_website(query)
                    return
                self._say(f"Открываю {query}")
                if not self.open_app(query):
                    self._say(f"{_pick('error')} Не нашла «{query}».")
                return

        self._say(_pick("not_understood"))

    def pause(self):
        self.paused = True
        self._write_state()

    def resume(self):
        self.paused = False
        self._write_state()

    def stop(self):
        self.stopped = True
        self._write_state()

    def loop(self):
        self.stopped = False
        if self.config.get("widget_enabled", True):
            self._level_thread = threading.Thread(target=self._level_meter_loop, daemon=True)
            self._level_thread.start()
        threading.Thread(target=self.prewarm_voice, daemon=True).start()
        try:
            self.speak(GREETING)
        except Exception:
            log.exception("Не удалось произнести приветствие")

        failures = 0
        while not self.stopped:
            if self.paused:
                time.sleep(0.3)
                continue
            try:
                self._check_pending_search()
                text = self.listen()
                failures = 0
                if text:
                    self.handle_command(text)
            except Exception as e:
                # Microphone not ready after boot, device reset after sleep, network hiccup...
                failures += 1
                self.microphone = None
                delay = min(30, 2 * failures)
                if failures <= 3 or failures % 10 == 0:
                    log.exception("Сбой в цикле прослушивания (подряд: %d), повтор через %d с", failures, delay)
                else:
                    log.warning("Сбой в цикле прослушивания (подряд: %d): %s", failures, e)
                end = time.time() + delay
                while time.time() < end and not self.stopped:
                    time.sleep(0.25)
        self._write_state()


def main():
    config = load_config()
    assistant = VoiceAssistant(config)
    try:
        assistant.loop()
    except KeyboardInterrupt:
        print("\nОстановлено пользователем")
        sys.exit(0)


if __name__ == "__main__":
    main()
