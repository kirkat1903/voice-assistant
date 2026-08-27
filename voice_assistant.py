import json
import os
import subprocess
import sys
import time
import webbrowser
from difflib import get_close_matches
from pathlib import Path

import pyttsx3
import speech_recognition as sr

CONFIG_PATH = Path(__file__).parent / "config.json"

EXIT_PHRASES = {"выход", "стоп", "заверши работу", "пока", "отключись"}

START_MENU_DIRS = [
    Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
    Path.home() / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs",
]


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class VoiceAssistant:
    def __init__(self, config):
        self.config = config
        self.recognizer = sr.Recognizer()
        self.microphone = sr.Microphone()
        self.tts = pyttsx3.init()
        self._select_russian_voice()
        self._shortcuts_cache = None
        self.paused = False
        self.stopped = False

    def _select_russian_voice(self):
        for voice in self.tts.getProperty("voices"):
            name = (voice.name or "").lower()
            vid = (voice.id or "").lower()
            if "ru" in vid or "russian" in name:
                self.tts.setProperty("voice", voice.id)
                break

    def speak(self, text):
        print(f"Ассистент: {text}")
        if self.config.get("voice_feedback", True):
            self.tts.say(text)
            self.tts.runAndWait()

    def listen(self):
        with self.microphone as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=0.4)
            print("Слушаю...")
            try:
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=10)
            except sr.WaitTimeoutError:
                return ""
        try:
            text = self.recognizer.recognize_google(
                audio, language=self.config.get("recognition_language", "ru-RU")
            )
            print(f"Вы сказали: {text}")
            return text.lower().strip()
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as e:
            self.speak("Проблема с распознаванием речи, проверьте интернет")
            print(f"Ошибка распознавания: {e}")
            return ""

    def index_start_menu_shortcuts(self):
        shortcuts = {}
        for base in START_MENU_DIRS:
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
        matches = get_close_matches(query, names, n=1, cutoff=0.5)
        if not matches:
            matches = [n for n in names if query in n]
        if matches:
            return self._shortcuts_cache[matches[0]]
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

        shortcut = self.find_shortcut(query)
        if shortcut:
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
        webbrowser.open(f"https://www.google.com/search?q={query}")

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

    def handle_command(self, text):
        wake_word = self.config.get("wake_word", "").strip().lower()
        if wake_word:
            if wake_word not in text:
                return
            text = text.replace(wake_word, "", 1).strip()

        if not text:
            return

        if text in EXIT_PHRASES:
            self.speak("До встречи")
            return "exit"

        for phrase, action in self.config.get("system_commands", {}).items():
            if phrase in text:
                self.speak("Выполняю")
                self.run_system_command(action)
                return

        for trigger in ("открой сайт ", "зайди на сайт ", "перейди на сайт "):
            if text.startswith(trigger):
                query = text[len(trigger):].strip()
                self.speak(f"Открываю сайт {query}")
                self.open_website(query)
                return

        if text.startswith("найди ") or text.startswith("поищи "):
            query = text.split(" ", 1)[1].strip()
            self.speak(f"Ищу {query}")
            self.search_google(query)
            return

        for trigger in ("открой ", "запусти "):
            if text.startswith(trigger):
                query = text[len(trigger):].strip()
                if query in self.config.get("site_aliases", {}):
                    self.speak(f"Открываю {query}")
                    self.open_website(query)
                    return
                self.speak(f"Открываю {query}")
                if not self.open_app(query):
                    self.speak(f"Не могу найти {query}")
                return

        self.speak("Не поняла команду")

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def stop(self):
        self.stopped = True

    def loop(self):
        self.stopped = False
        self.speak("Голосовой помощник запущен")
        while not self.stopped:
            if self.paused:
                time.sleep(0.3)
                continue
            text = self.listen()
            if not text:
                continue
            if self.handle_command(text) == "exit":
                break


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
