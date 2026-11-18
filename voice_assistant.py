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
from urllib.parse import quote, quote_plus

import pyaudio
import requests
import speech_recognition as sr
import win32api
import win32com.client
import win32con
import win32gui
import win32process
from PIL import ImageGrab

import claude_link
import codex_notifications
import sprout_link
import weather
import windows_ctl as wc
from companion import CRISIS_MESSAGE, CRISIS_RE, Companion, CompanionUnavailable, split_for_speech
from tts_engine import NeuralVoice, play_mp3

log = logging.getLogger("aiko")

FOREGROUND_WORDS = {
    "окно", "это окно", "текущее окно", "программу", "эту программу", "приложение", "это приложение",
    "это", "эту", "window", "this window", "current window", "the window", "app", "this app", "this", "it",
}
TAB_WORDS = {"вкладку", "эту вкладку", "текущую вкладку", "tab", "this tab", "the tab", "current tab"}
ALL_WORDS = {"все окна", "окна", "всё", "все", "все окна сразу", "all windows", "everything", "all"}
NOT_TAB_COMMAND_PREFIXES = ("найди", "поищи", "find", "search", "запиши", "напомни", "remind", "объясни", "расскажи")


def _tab_intent(text):
    """Understand any phrasing about tabs: «закрой текущую вкладку в браузере», «открой новую
    вкладку», «следующая вкладка», «верни закрытую вкладку», «close this tab»…"""
    if text.startswith(NOT_TAB_COMMAND_PREFIXES):
        return None
    if "вкладк" not in text and not re.search(r"\btabs?\b", text):
        return None
    if re.search(r"верни|восстанов|reopen|restore", text):
        return "reopen"
    if re.search(r"закр|убери|удали|close", text):
        return "close"
    if re.search(r"следующ|дальше|next", text):
        return "next"
    if re.search(r"предыдущ|прошл|назад|previous|prev", text):
        return "prev"
    if re.search(r"нов|откр|созда|добав|new|open", text):
        return "new"
    return None


CHAT_START_RE = re.compile(
    r"\b(?:поговор\w*|пообща\w*|выговорить\w*|побудь со мной|(?:хочу|давай|нужно|надо) (?:поговор\w*|выговорить\w*|пообща\w*)|"
    r"мне нужна (?:поддержка|помощь)|мне нужен (?:друг|собеседник)|как (?:твои )?дела|как ты(?: там)?)\b")
INVITE_RE = re.compile(
    r"\b(?:поговор\w*|пообща\w*|выговорить\w*|побудь со мной|мне нужн\w+ (?:друг|собеседник)|мне нужна поддержка)\b")
EMOTION_RE = re.compile(
    r"\bмне (?:очень |так |как-то |что-то )?(?:грустно|плохо|тревожно|страшно|одиноко|тяжело|обидно|больно|не по себе|"
    r"скучно|тоскливо)\b|"
    r"\bя (?:очень |так |ужасно )?(?:устала|расстроена|расстроилась|переживаю|волнуюсь|злюсь|плачу|боюсь|скучаю|"
    r"не справляюсь|выгорела|заболела)\b|"
    r"\bу меня (?:стресс|паника|тревога|депрессия|плохое настроение|ужасный день|тяжёлый день|тяжелый день)\b|"
    r"\b(?:накрыла|накрыл[оа]) (?:тревога|паника|грусть|тоска)\b|не могу уснуть|мне нужна поддержка|"
    r"\bвсё надоело|\bвсе надоело|\bнет сил\b|\bничего не хочу\b|\bпоссорилась\b|\bрасстались\b")
CHAT_END_RE = re.compile(
    r"^(?:хватит|достаточно|давай (?:закончим|на этом закончим)|закончим|закончи разговор|выйди из режима разговора|"
    r"мне уже лучше|спасибо,? (?:пока|на сегодня|достаточно|мне уже лучше|что выслушала)|(?:всё|все),? пока|"
    r"пока(?:,? айко)?|до свидания|я пойду|мне пора|спокойной ночи)\b")
RESET_RE = re.compile(r"\b(?:забудь|сотри|очисти|удали)\b.*\b(?:разговор\w*|историю|всё что я (?:говорила|рассказывала)|нашу беседу)\b")
BREATHING_RE = re.compile(
    r"\b(?:давай подышим|подыши со мной|подышим|дыхательн\w+ (?:практик\w*|упражнени\w*)|упражнени\w+ на дыхание|"
    r"успокой меня|помоги (?:мне )?успокоиться|хочу успокоиться)\b")

# «посмотри на экран», «что у меня на экране», «отправь скриншот Клоду» — скриншот уходит в Claude
SCREEN_ASK_RE = re.compile(
    r"\b(?:(?:посмотри|глянь|взгляни)\s+(?:на\s+)?(?:мой\s+|этот\s+)?экран\w*|"
    r"что\s+(?:у\s+меня\s+)?(?:сейчас\s+)?на\s+экране|"
    r"(?:отправь|покажи|скинь|пришли|отдай)\s+(?:мой\s+)?(?:экран|скриншот)\w*(?:\s+(?:клод\w*|клауд\w*|claude))?|"
    r"(?:спроси|покажи)\s+(?:у\s+)?(?:клод\w*|клауд\w*|claude))")

LATIN_NAME_RE = re.compile(r"^[a-z']{2,8}$")
# First words of genuine English commands. Any *other* short Latin word at the start is Google
# mis-hearing the name «Айко» («icon», «icom», «iko»…) and counts as calling her.
ENGLISH_COMMAND_WORDS = {
    "open", "launch", "start", "run", "find", "search", "go", "new", "next", "previous", "prev", "play", "pause",
    "mute", "volume", "close", "minimize", "maximize", "restore", "kill", "force", "what", "what's", "set",
    "remind", "change", "edit", "update", "show", "screenshot", "exit", "stop", "quit", "bye", "goodbye", "lock",
    "shutdown", "shut", "restart", "reboot", "sleep", "weather", "exchange", "brighter", "darker", "undo",
    "reopen", "this", "current", "the", "tab", "tabs", "take", "turn", "skip",
}

APP_NAME_ALIASES = {
    "чат gpt": "chatgpt", "чат жпт": "chatgpt", "чат джипити": "chatgpt",
    "чат джи пи ти": "chatgpt", "чад gpt": "chatgpt",
    "кап кат": "capcut", "капкат": "capcut",
    "да винчи": "davinci resolve", "давинчи": "davinci resolve",
    "дискорд": "discord", "докер": "docker desktop",
    "пайтчарм": "pycharm", "пайчарм": "pycharm",
    "телеграм": "telegram", "клоуд": "claude", "клод": "claude",
    "визуал студио код": "visual studio code", "вс код": "visual studio code",
    "гугл хром": "google chrome", "хром": "google chrome",
    "ворд": "word", "эксель": "excel", "пауэр поинт": "powerpoint",
    "пауэрпоинт": "powerpoint", "ван ноут": "onenote",
    "стим": "steam", "эпик геймс": "epic games launcher",
    "анаконда": "anaconda navigator", "джупитер": "jupyter notebook",
}

APP_IDS = {
    "chatgpt": r"shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App",
}


def _app_name(text):
    query = text.strip().lower()
    if query in APP_NAME_ALIASES:
        return APP_NAME_ALIASES[query]
    close = get_close_matches(query, APP_NAME_ALIASES, n=1, cutoff=0.72)
    return APP_NAME_ALIASES[close[0]] if close else query

MUSIC_VERB_RE = re.compile(
    r"^(?:включи|включай|включите|поставь|поставьте|сыграй|играй|врубай|play)\s+(?:(?:мне|нам|пожалуйста)\s+)*(.*)$")
MUSIC_NOUN_RE = re.compile(
    r"\b(?:музык\w*|музон\w*|песн\w*|трек\w*|клип\w*|плейлист\w*|радио|альбом\w*|композици\w*|мелоди\w*|саундтрек\w*|music|songs?|tracks?|playlists?)\b")
MUSIC_FILLER_RE = re.compile(
    r"\b(?:какую|какое|какие|любую|любое|что|чего|нибудь|группы|группу|исполнителя|артиста|певца|певицу|от|мне|нам|пожалуйста)\b")
MUSIC_MOODS = (
    (r"\bспокойн|\bрасслаб|\bрелакс|\bтих", "спокойная расслабляющая музыка"),
    (r"\bвес[её]л|\bбодр|\bэнергичн|\bзаряжа|\bтанцева", "бодрая энергичная музыка"),
    (r"\bгрустн", "грустная музыка"),
    (r"\bромантич", "романтическая музыка"),
    (r"для работы|для учёбы|для учебы|концентр|\bфонов", "музыка для концентрации"),
    (r"для сна|на ночь|засып", "музыка для сна"),
    (r"\blo-?fi|\bлоу-?фай|\bлофи", "lofi hip hop radio"),
    (r"\bрок\w*", "рок музыка хиты"),
    (r"\bджаз\w*", "джаз музыка"),
    (r"\bклассик\w*|\bклассическ\w*", "классическая музыка"),
    (r"\bрэп\w*|\bреп\b|\bхип-?хоп\w*", "рэп хиты"),
    (r"\bпоп\b|\bпоп-?музык\w*", "поп музыка хиты"),
    (r"\bэлектрон\w*|\bтехно\b|\bхаус\b", "электронная музыка"),
    (r"\bсаундтрек\w*|\bиз фильм\w*", "саундтреки из фильмов"),
    (r"\bигров\w*|\bгейминг\w*", "музыка для игр"),
)
YT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Cookie": "CONSENT=YES+1",
}


def _music_query(text, known_names):
    """None: not a music request. "": «just play something». Otherwise a search query."""
    m = MUSIC_VERB_RE.match(text)
    if not m:
        return None
    verb, rest = text.split()[0], m.group(1).strip()
    if not rest or rest in known_names:
        return None
    mood = next((q for pattern, q in MUSIC_MOODS if re.search(pattern, rest)), None)
    has_noun = bool(MUSIC_NOUN_RE.search(rest))
    generic = rest.startswith(("что-нибудь", "что нибудь", "чего-нибудь"))
    if not (has_noun or mood or generic or verb in ("сыграй", "играй")):
        return None
    if mood:
        return mood
    cleaned = MUSIC_FILLER_RE.sub(" ", MUSIC_NOUN_RE.sub(" ", rest)).replace("-", " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def _deg_word(n):
    n = abs(n)
    if 11 <= n % 100 <= 14:
        return "градусов"
    return {1: "градус", 2: "градуса", 3: "градуса", 4: "градуса"}.get(n % 10, "градусов")


def _humanize_weather(raw):
    """«Красноярск: Пасмурно, +4°C» -> «Красноярск: пасмурно, плюс 4 градуса» (reads well aloud)."""
    def repl(m):
        n = int(m.group(2))
        sign = "" if n == 0 else {"+": "плюс ", "-": "минус ", "−": "минус "}.get(m.group(1), "")
        return f"{sign}{n} {_deg_word(n)}"

    text = re.sub(r"([+\-−]?)(\d+)\s*°\s*C", repl, raw)
    text = re.sub(r"\s{2,}", " ", text).strip()
    head, sep, tail = text.partition(": ")
    return head + sep + tail[:1].lower() + tail[1:] if sep else text


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
    "помолчи", "замолчи", "отдохни", "спи", "засыпай",
}
STOP_LISTEN_PHRASES = {
    "не слушай", "не слушай меня", "не подслушивай", "перестань слушать",
    "отключи микрофон", "выключи микрофон", "поставь микрофон на паузу",
}
# В разговоре эти короткие слова скорее про беседу («продолжай»), чем про музыку
CHAT_AMBIGUOUS = {"продолжай", "продолжи", "дальше", "давай дальше", "next", "стоп", "останови", "пауза"}

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
    "weather": [
        "Сейчас посмотрю погоду…",
        "Секунду, узнаю прогноз.",
        "Смотрю, что там за окном…",
    ],
    "filler": [
        "Так, дай подумать секунду...",
        "Копаюсь в настройках, не переключайся.",
        "Момент, собираю всё воедино.",
    ],
    "chat_start": [
        "Конечно, я рядом. Как ты себя чувствуешь?",
        "Я тебя слушаю. Что у тебя на душе?",
        "Давай поговорим. Расскажи, как прошёл твой день?",
    ],
    "chat_end": [
        "Я рада, что мы поговорили. Береги себя, а если захочется — просто позови меня.",
        "Хорошо. Спасибо, что поделилась со мной. Я всегда готова поговорить.",
        "Договорились. Отдыхай и будь к себе добра. Я рядом, если что.",
    ],
}

NO_AI_MESSAGE = ("Чтобы по-настоящему разговаривать с тобой, мне нужен доступ к ИИ-модели. Нужно один раз добавить "
                 "ключ, инструкция в файле README. А пока могу помочь подышать: скажи «Айко, давай подышим».")
COMPANION_ERRORS = {
    "bad_key": "Ключ доступа не подошёл. Проверь его и добавь заново командой set_api_key.",
    "forbidden": "Сервис не пускает меня с этого адреса. Попробуй включить VPN и повтори.",
    "network": "Не получается связаться с ИИ. Проверь интернет или VPN.",
    "rate": "Сейчас слишком много запросов, давай чуть позже.",
    "server": "У сервиса сейчас сбой, давай поговорим чуть позже.",
    "no_model": "Модель на этом компьютере ещё не скачана. Команда для скачивания есть в файле README.",
}
# (what Aiko says, seconds to wait afterwards): a slow 4-4-6 breathing exercise
BREATHING_INTRO = "Давай подышим вместе. Сядь поудобнее и расслабь плечи. Дыши в моём ритме."
BREATHING_CYCLE = (
    ("Вдох носом. Раз, два, три, четыре.", 0.5),
    ("Задержи дыхание. Два, три, четыре.", 0.5),
    ("И медленный выдох. Два, три, четыре, пять, шесть.", 1.5),
)
BREATHING_OUTRO = "Вот и хорошо. Ты молодец. Как ты себя чувствуешь?"


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
        self._addressed_until = 0.0
        self._last_text = ""
        self._volume = 0.0
        self._level_thread = None
        self._activity = "idle"      # idle / thinking / speaking: drives the pet in the widget
        self._mood = None            # transient reaction: heard / happy / confused / sad
        self._mood_ts = 0.0
        self.chat_mode = False       # a friendly conversation is going on: no need to say «Айко» each time
        self.chat_last = 0.0
        self._companion = None
        self._weather_cache = {}     # city -> (time, text): weather is re-fetched at most every 10 minutes
        self._yt_cache = {}          # search query -> YouTube video id
        self._bridge = None          # связь с островком Sprout (лицо Айко + сессии Claude Code)
        self._last_claude_notice = {}
        self.notifier = None         # (title, text) -> уведомление Windows; задаёт трей

    def _set_mood(self, mood):
        self._mood = mood
        self._mood_ts = time.time()
        self._write_state()

    def _react(self, text):
        """Let the pet react to what is being said, even when the answer is muted."""
        if text in PHRASES["confirmation"] or text in PHRASES["greeting"]:
            self._set_mood("happy")
        elif text in PHRASES["not_understood"] or text in PHRASES["clarifying"]:
            self._set_mood("confused")
        elif any(text.startswith(p) for p in PHRASES["error"]):
            self._set_mood("sad")

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
            try:
                if self.neural_voice is not None:
                    try:
                        self._activity = "thinking"       # synthesizing (can take 1-3 s)
                        self._write_state()
                        path = self.neural_voice.prepare(text)
                        self._activity = "speaking"
                        self._write_state()
                        play_mp3(path)
                        return
                    except Exception as e:
                        log.warning("Нейроголос недоступен (%s: %s); системный голос отключён",
                                    type(e).__name__, e)
                        if not self.config.get("system_voice_fallback", False):
                            return
                self._activity = "speaking"
                self._write_state()
                self._speak_system(text)
            finally:
                self._activity = "idle"
                self._write_state()

    def prewarm_voice(self):
        """Synthesize the fixed phrases once so they play instantly and work offline."""
        if self.neural_voice is None:
            return
        phrases = [GREETING, "Спокойной ночи! Разбуди меня, когда понадоблюсь.", CRISIS_MESSAGE, NO_AI_MESSAGE,
                   BREATHING_INTRO, BREATHING_OUTRO, *COMPANION_ERRORS.values(), *(p for p, _ in BREATHING_CYCLE)]
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

    # ---- friend / mini-psychologist ------------------------------------------------------------

    def _companion_obj(self):
        if not self.config.get("companion_enabled", True):
            return None
        if self._companion is None:
            self._companion = Companion(self.config)
        return self._companion

    def companion_ready(self):
        comp = self._companion_obj()
        return bool(comp and comp.has_credentials())

    def _speak_long(self, text, limit=150):
        """Speak a long reply in sentence-sized pieces; the next pieces are synthesised while one plays."""
        self._last_text = text
        self._write_state()
        if not self.config.get("voice_feedback", True):
            print(f"Ассистент: {text}")
            return
        chunks = split_for_speech(text, limit=limit)
        if self.neural_voice is not None and len(chunks) > 1:
            def prefetch():
                for chunk in chunks[1:]:
                    try:
                        self.neural_voice.prepare(chunk)
                    except Exception:
                        return
            threading.Thread(target=prefetch, daemon=True).start()
        print(f"Ассистент: {text}")
        for chunk in chunks:
            self._speak_audio(chunk)

    def _end_chat(self):
        self.chat_mode = False
        self.speak(_pick("chat_end"))

    def _chat_turn(self, text, start=False):
        """One turn of the friendly conversation. Replies are always spoken: she is talking to Aiko."""
        self.chat_last = time.time()
        if self.chat_mode and CHAT_END_RE.search(text):
            self._end_chat()
            return
        self.chat_mode = True
        # an invitation without content («давай поговорим») just opens the conversation
        if start and INVITE_RE.search(text) and len(text.split()) <= 5 and not EMOTION_RE.search(text):
            self.speak(_pick("chat_start"))
            return
        if CRISIS_RE.search(text):
            self.speak(CRISIS_MESSAGE)                      # immediately, without waiting for the model
        comp = self._companion_obj()
        if comp is None or not comp.has_credentials():
            self.chat_mode = False
            self.speak(NO_AI_MESSAGE)
            return
        self._activity = "thinking"
        self._write_state()
        try:
            reply = comp.reply(text)
        except CompanionUnavailable as e:
            log.warning("Собеседник недоступен: %s", e)
            self._activity = "idle"
            self.speak(COMPANION_ERRORS.get(e.reason, _pick("error")))
            return
        except Exception:
            log.exception("Ошибка в разговоре")
            self._activity = "idle"
            self.speak(_pick("error"))
            return
        finally:
            self._activity = "idle"
            self._write_state()
        self.chat_last = time.time()
        self._speak_long(reply)

    # ---- Sprout: текстовый чат и сессии Claude Code ---------------------------------------------

    def text_chat(self, text):
        """Сообщение из окна чата Sprout: тот же собеседник и та же история, что и в голосе."""
        comp = self._companion_obj()
        if comp is None or not comp.has_credentials():
            return False, NO_AI_MESSAGE
        prefix = CRISIS_MESSAGE + " " if CRISIS_RE.search(text) else ""
        self._activity = "thinking"
        self._write_state()
        try:
            reply = comp.reply(text)
        except CompanionUnavailable as e:
            log.warning("Собеседник недоступен (чат Sprout): %s", e)
            return False, COMPANION_ERRORS.get(e.reason, NO_AI_MESSAGE if e.reason == "no_key" else _pick("error"))
        except Exception:
            log.exception("Ошибка в чате Sprout")
            return False, _pick("error")
        finally:
            self._activity = "idle"
            self._write_state()
        if self.config.get("sprout_chat_speak", True) and not (self.sleeping or self.paused):
            threading.Thread(target=self._speak_long, args=(reply,), daemon=True).start()
        return True, prefix + reply

    def claude_notice(self, kind, project, detail=""):
        """Sprout сообщает о чате или сессии Claude: уведомление Windows от Айко (всегда, даже во сне)
        и голос (только если Айко не спит и не на паузе)."""
        where = f" в проекте {project}" if project else ""
        chat = f" в чате «{project}»" if project else ""
        # (вслух — «Клод», чтобы голос читал правильно; в уведомлении — как пишется)
        phrases = {
            "ask": (f"Клод ждёт твоего разрешения{where}.", f"Claude ждёт твоего разрешения{where}."),
            "question": (f"У Клода вопрос{where}.", f"У Claude вопрос{where}."),
            "waiting": (f"Клод ждёт твоего ответа{where}.", f"Claude ждёт твоего ответа{where}."),
            "done": (f"Клод закончил{where}.", f"Claude закончил задачу{where}."),
            "chat_wait": (f"Клод ждёт твоего ответа{chat}.", f"Claude ждёт твоего ответа{chat}."),
            "chat_done": (f"Клод закончил задачу{chat}.", f"Claude закончил задачу{chat}."),
            "codex_chat_wait": (f"Кодекс ждёт твоего ответа{chat}.", f"Codex ждёт твоего ответа{chat}."),
            "codex_chat_done": (f"Кодекс закончил задачу{chat}.", f"Codex закончил задачу{chat}."),
        }
        if kind not in phrases or self.stopped:
            return
        spoken, written = phrases[kind]
        now = time.time()
        if now - self._last_claude_notice.get((kind, project), 0) < 20:      # не повторяться
            return
        self._last_claude_notice[(kind, project)] = now
        if kind in ("done", "chat_done", "codex_chat_done"):
            self._set_mood("happy")
        if self.config.get("claude_toast_notify", True) and self.notifier is not None:
            try:
                self.notifier("Айко", written)
            except Exception:
                log.exception("Не удалось показать уведомление")
        if self.config.get("claude_voice_notify", True) and not (self.sleeping or self.paused):
            threading.Thread(target=self.speak, args=(spoken,), daemon=True).start()

    def _claude_command(self, text):
        """«Айко, что делает Claude / разреши / отклони / вариант два». True — если фраза была про Claude."""
        command = sprout_link.voice_command(text)
        if command is None:
            return False
        answer = sprout_link.ask_sprout(command)
        if answer is None:
            self._say("Островок Спраут не запущен, мне не видно Клода.")
        else:
            self._say(sprout_link.spoken(answer.get("say") or _pick("confirmation")))
        return True

    def warm_weather(self):
        """Заранее выяснить, где ты: первый вопрос о погоде отвечается без паузы."""
        try:
            self._locator().current()
        except Exception:
            log.exception("Погода: не удалось определить город")

    def start_bridge(self):
        if self._bridge is None and self.config.get("sprout_link", True):
            self._bridge = sprout_link.Bridge(self, STATE_PATH)
            if not self._bridge.start():
                self._bridge = None

    def _breathing(self):
        """A slow guided breathing exercise (works without any AI access)."""
        self.chat_last = time.time()
        self.speak(BREATHING_INTRO)
        for _ in range(4):
            for phrase, pause in BREATHING_CYCLE:
                if self.stopped or self.paused:
                    return
                if self.config.get("voice_feedback", True):
                    self._speak_audio(phrase)
                self._last_text = phrase
                self._write_state()
                time.sleep(pause)
        self.speak(BREATHING_OUTRO)
        self.chat_mode = self.companion_ready()               # her answer continues the conversation

    def speak(self, text):
        print(f"Ассистент: {text}")
        self._last_text = text
        self._react(text)
        self._write_state()
        if self.config.get("voice_feedback", True):
            self._speak_audio(text)

    def _say(self, text):
        self._last_text = text
        self._react(text)
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
            "activity": self._activity,
            "mood": self._mood,
            "mood_ts": self._mood_ts,
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
                # in a conversation she talks longer and pauses to think
                self.recognizer.pause_threshold = 1.2 if self.chat_mode else 0.8
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=30 if self.chat_mode else 10)
            except sr.WaitTimeoutError:
                return ""

        languages = self.config.get("recognition_languages") or [
            self.config.get("recognition_language", "ru-RU")
        ]
        for language in languages:
            for attempt in range(3):
                try:
                    text = self.recognizer.recognize_google(audio, language=language)
                    print(f"Вы сказали ({language}): {text}")
                    return text.lower().strip()
                except sr.UnknownValueError:
                    break
                except sr.RequestError as e:
                    print(f"Ошибка распознавания, попытка {attempt + 1}: {e}")
                    if attempt < 2:
                        time.sleep(0.6)
                        continue
                    self.speak(f"{_pick('error')} Проблема с распознаванием речи, проверь интернет.")
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
        query = _app_name(query)
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
        query = _app_name(query)
        aliases = self.config.get("app_aliases", {})
        wins = wc.find_windows(query, aliases)
        if wins:
            return wc.bring_to_front(wins[0]["hwnd"])
        if query in APP_IDS:
            try:
                os.startfile(APP_IDS[query])
                return True
            except OSError:
                pass
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
            if action == "new":
                webbrowser.open_new_tab("https://www.google.com")   # no browser open yet: start one
                self._say(_pick("confirmation"))
            else:
                self._say("Не вижу открытого браузера — открой его и повтори.")
        elif ok:
            self._say(_pick("confirmation"))
        else:
            self._say("Не получилось переключиться на браузер — щёлкни по его окну и повтори.")

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
        intent = _tab_intent(text)
        if intent:
            if intent == "close" and not addressed:
                self._say("Чтобы я закрыла вкладку, сначала позови меня: «Айко, закрой вкладку»")
            else:
                self._do_tab(intent)
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
        m = re.match(
            r"^(?:сверни(?:те)?|свернуть|стерни(?:те)?|минимизируй(?:те)?|убери(?:те)?|minimize)"
            r"(?:\s+(.*?))?(?:\s*,?\s*пожалуйста)?$",
            text,
        )
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
        claude_link.publish_latest(filename)
        return filename

    def _ask_claude_about_screen(self, text, match):
        """Screenshot → vision model (free Groq; `claude -p` as fallback) → answer aloud."""
        question = re.sub(r"^[\s,.]*(?:и\s+)?(?:скажи|расскажи|объясни)?[\s,.]*", "", text[match.end():]).strip()
        if len(question) < 4:
            question = ""
        try:
            path = self.take_screenshot()
        except Exception:
            log.exception("Не удалось сделать скриншот")
            self._say(_pick("error"))
            return
        self._say("Смотрю на экран.")
        box = {}

        def work():
            try:
                box["answer"] = claude_link.ask_about_screenshot(path, question)
            except claude_link.ClaudeLinkError as e:
                box["error"] = str(e)

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        worker.join(12)
        if worker.is_alive():
            self._say(_pick("filler"))
            worker.join(70)
        if box.get("answer"):
            log.info("Про экран: %s", box["answer"][:300])
            self._speak_long(box["answer"])
            return
        reason = box.get("error", "failed")
        self._say({
            "no_key": "Чтобы я смотрела на экран, нужен ключ Грок. Скриншот сохранён.",
            "not_installed": "Чтобы я смотрела на экран, нужен ключ Грок. Скриншот сохранён.",
            "bad_key": "Ключ Грок не подошёл. Скриншот сохранён.",
            "rate": "Грок просит подождать, слишком много запросов. Попробуй через минуту.",
            "network": "Не достучалась до интернета. Скриншот сохранён.",
            "timeout": "Ответ слишком долго идёт. Скриншот сохранён.",
        }.get(reason, "Не получилось разобрать, что на экране. Скриншот сохранён."))

    def get_weather(self, city):
        try:
            resp = requests.get(
                f"https://wttr.in/{quote_plus(city)}",
                params={"format": "%l: %C, %t", "lang": "ru", "m": ""},   # text only: no emoji
                headers={"User-Agent": "curl"},
                timeout=6,
            )
            resp.raise_for_status()
            return resp.text.strip()
        except requests.RequestException:
            return None

    def _fetch_with_filler(self, fn, *args, filler_after=1.2, filler_kind="filler"):
        """Run a slow lookup in a worker. A spoken filler is only used if it takes longer than
        `filler_after` seconds (before, the filler was always spoken first and delayed every answer)."""
        box = {}
        worker = threading.Thread(target=lambda: box.update(v=fn(*args)), daemon=True)
        previous_activity = self._activity
        self._activity = "thinking"
        self._write_state()
        try:
            worker.start()
            worker.join(filler_after)
            if worker.is_alive():
                self._say(_pick(filler_kind))
                self._activity = "thinking"
                self._write_state()
                worker.join(15)
            return box.get("v")
        finally:
            if self._activity == "thinking":
                self._activity = previous_activity
                self._write_state()

    def weather_text(self, city):
        key = city.lower()
        hit = self._weather_cache.get(key)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        raw = self.get_weather(city)
        text = _humanize_weather(raw) if raw else None
        if text:
            self._weather_cache[key] = (time.time(), text)
        return text

    # ---- погода: где ты, сейчас / завтра / неделя, Яндекс Погода -----------------------------

    def _locator(self):
        if getattr(self, "_loc", None) is None:
            self._loc = weather.Locator(self.config)
        return self._loc

    def _forecast(self, place):
        key = (round(place["lat"], 2), round(place["lon"], 2))
        hit = self._weather_cache.get(key)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        try:
            data = weather.forecast(place)
        except Exception as e:
            log.warning("Погода (Open-Meteo): %s", e)
            return None
        self._weather_cache[key] = (time.time(), data)
        return data

    def _weather_command(self, text, addressed, in_chat):
        """Погода и «я в Москве». True — если фраза была про это."""
        named = addressed or in_chat
        if named and weather.AUTO_CITY_RE.match(text):
            self._locator().clear()
            place = self._fetch_with_filler(self._locator().current, filler_kind="weather")
            self._say(f"Хорошо, буду определять город сама. Сейчас ты, похоже, в городе {place['name']}."
                      if place else "Хорошо, буду определять город сама.")
            return True
        m = weather.SET_CITY_RE.match(text)
        if named and m:
            city = m.group(1).strip()
            if city in weather.NOT_CITY:
                return False
            place = self._fetch_with_filler(self._locator().set_city, city, filler_kind="weather")
            if place:
                self._say(f"Запомнила: ты в городе {place['name']}. Погоду буду говорить для него.")
                return True
            if addressed:
                self._say(f"Не нашла город «{city}». Скажи ещё раз, например: «Айко, я в Новосибирске».")
                return True
            return False                                     # в разговоре: это была просто фраза
        if not weather.WEATHER_RE.search(text) or len(text.split()) > 12 or text.startswith(NOT_TAB_COMMAND_PREFIXES):
            return False
        self._current_addressed = True

        def work():
            city = weather.city_in_phrase(text)
            place = self._locator().find(city) if city else self._locator().current()
            if city and not place:
                return ("nocity", city)
            return ("ok", place)

        status, place = self._fetch_with_filler(work, filler_kind="weather") or ("error", None)
        if status == "nocity":
            self._say(f"Не нашла город «{place}».")
            return True
        if status != "ok" or not place:
            self._say("Не получилось узнать погоду. Проверь интернет.")
            return True

        if weather.OPEN_RE.search(text):
            webbrowser.open(weather.yandex_url(place))
            self._say(f"Открываю Яндекс Погоду: {place['name']}.")
            return True

        data = self._fetch_with_filler(self._forecast, place, filler_kind="weather")
        if not data:
            self._say("Сервис погоды не отвечает. Открою Яндекс Погоду.")
            webbrowser.open(weather.yandex_url(place))
            return True
        if weather.WEEK_RE.search(text):
            answer = weather.say_week(place, data)
        elif weather.AFTER_RE.search(text):
            answer = weather.say_day(place, data, 2)
        elif weather.TOMORROW_RE.search(text):
            answer = weather.say_day(place, data, 1)
        else:
            answer = weather.say_now(place, data)
        if self._current_addressed and len(answer) > 80:
            self._react(answer)
            self._speak_long(answer, limit=80)
        else:
            self._say(answer)
        return True

    def _weather_city(self, text):
        m = re.search(r"\b(?:в|во|на|для|in)\s+([а-яёa-z][а-яёa-z\- ]*)$", text)
        city = m.group(1) if m else ""
        city = re.sub(r"\b(?:сейчас|сегодня|завтра|пожалуйста|вообще|там|у нас|today|now|tomorrow)\b", " ", city)
        city = re.sub(r"\s+", " ", city).strip()
        return city or self.config.get("city", "Красноярск")

    def _youtube_first_video(self, query):
        if query in self._yt_cache:
            return self._yt_cache[query]
        try:
            r = requests.get("https://www.youtube.com/results", params={"search_query": query},
                             headers=YT_HEADERS, timeout=10)
            ids = re.findall(r'"videoRenderer":\{"videoId":"([\w-]{11})"', r.text)
            if ids:
                self._yt_cache[query] = ids[0]
                return ids[0]
        except requests.RequestException as e:
            log.warning("Поиск на YouTube не удался: %s", e)
        return None

    def play_music(self, query):
        """Open music for `query` in the browser; with YouTube the first hit is opened as a Mix so
        «следующий трек» keeps working. Returns the query that was used."""
        service = self.config.get("music_service", "youtube")
        q = query or self.config.get("music_default_query", "популярная музыка подборка")
        if service == "yandex":
            webbrowser.open(f"https://music.yandex.ru/search?text={quote_plus(q)}")
        elif service == "spotify":
            os.startfile(f"spotify:search:{quote(q)}")
        else:
            vid = self._youtube_first_video(q)
            url = (f"https://www.youtube.com/watch?v={vid}&list=RD{vid}" if vid
                   else f"https://www.youtube.com/results?search_query={quote_plus(q)}")
            webbrowser.open(url)
        return q

    def _handle_music(self, text):
        known = set(self.config.get("site_aliases", {})) | set(self.config.get("app_aliases", {}))
        query = _music_query(text, known)
        if query is None:
            m = MUSIC_VERB_RE.match(text)
            rest = m.group(1).strip() if m else ""
            if rest and rest in known:                          # «включи ютуб» is a site, not music
                self._say(f"Открываю {rest}")
                if rest in self.config.get("site_aliases", {}):
                    self.open_website(rest)
                elif not self.open_app(rest):
                    self._say(f"{_pick('error')} Не нашла «{rest}».")
                return True
            return False
        worker = threading.Thread(target=self.play_music, args=(query,), daemon=True)
        worker.start()                                          # search + open the browser while she answers
        self._say(f"Включаю {query}" if query else "Включаю музыку")
        worker.join(12)
        return True

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
                strip = 0
                # порог 0.74: «айкор», «икон» — это имя, а «закрой», «какой», «такой» — нет
                if first in wake_words or get_close_matches(first, wake_words, n=1, cutoff=0.74):
                    strip = 1
                elif len(words) >= 2 and (first + words[1]) in wake_words:
                    strip = 2                                   # «ай ко»
                elif len(words) >= 2 and LATIN_NAME_RE.match(first) and first not in ENGLISH_COMMAND_WORDS:
                    strip = 1                                   # Google heard «Айко» as «icon», «icom»…
                if strip:
                    addressed = True
                    text = " ".join(words[strip:]).strip()

        if not addressed and time.time() < self._addressed_until:
            addressed = True
        self._addressed_until = 0.0
        self._current_addressed = addressed
        if addressed:
            self._set_mood("heard")

        if self.sleeping:
            if not addressed:
                return
            self.sleeping = False
            self._write_state()
            if not text:
                self._addressed_until = time.time() + 20
                self.speak(_pick("greeting"))
                return

        if not text:
            if addressed:
                self._addressed_until = time.time() + 20
                self.speak(_pick("greeting"))
            return

        if addressed and text in STOP_LISTEN_PHRASES:
            self.chat_mode = False
            self.speak("Хорошо, отключаю микрофон. Включить меня снова можно через меню Ростка.")
            self.pause()
            return

        if (addressed or self.chat_mode) and text in SLEEP_PHRASES:
            self.sleeping = True
            self.chat_mode = False
            self.speak("Спокойной ночи! Разбуди меня, когда понадоблюсь.")
            return

        if addressed and text in EXIT_PHRASES:
            self.sleeping = True
            self.chat_mode = False
            self.speak(_pick("farewell"))
            return

        # --- Claude Code через островок Sprout (только по имени: «Айко, разреши»)
        if addressed and self.config.get("sprout_link", True) and self._claude_command(text):
            return

        # --- a friend to talk to: во время разговора команды по-прежнему работают
        if self.chat_mode and not addressed:
            if not CHAT_END_RE.search(text) and text not in CHAT_AMBIGUOUS:
                self._current_addressed = True              # с ней уже разговаривают: отвечать вслух
                if self._run_command(text, addressed=False, in_chat=True):
                    self.chat_last = time.time()
                    return
            self._chat_turn(text)
            return
        if addressed and RESET_RE.search(text):
            comp = self._companion_obj()
            if comp:
                comp.forget()
            self._say("Хорошо, я забыла наш разговор.")
            return
        if (addressed or self.chat_mode) and BREATHING_RE.search(text):
            self._breathing()
            return
        if addressed and (CHAT_START_RE.search(text) or EMOTION_RE.search(text)):
            self._chat_turn(text, start=True)
            return

        # Если фраза оказалась компьютерной командой, подтверждение всегда произносится
        # вслух. Имя «Айко» остаётся обязательным только для опасных команд.
        previous_addressed = self._current_addressed
        self._current_addressed = True
        if self._run_command(text, addressed):
            return
        self._current_addressed = previous_addressed

        if addressed and self.companion_ready():
            self._chat_turn(text, start=True)          # not a command: talk to her like a friend would
            return
        self._say(_pick("not_understood"))

    def _run_command(self, text, addressed, in_chat=False):
        """Команды компьютера: окна, вкладки, приложения, поиск, музыка, таймеры…
        True — фраза была командой (выполнена или вежливо отклонена). Выключение/перезагрузка — только по имени."""
        if addressed or in_chat:
            screen = SCREEN_ASK_RE.search(text)
            if screen:
                self._ask_claude_about_screen(text, screen)
                return True

        for phrase, action in self.config.get("system_commands", {}).items():
            if phrase in text:
                if not addressed:
                    if in_chat:
                        self._say(f"Это только по имени: скажи «Айко, {phrase}».")
                        return True
                    break
                self._say(_pick("confirmation"))
                self.run_system_command(action)
                return True

        if self._handle_window_command(text, addressed or in_chat):
            return True

        if text in ("который час", "сколько времени", "what time is it"):
            self._say(f"Сейчас {time.strftime('%H:%M')}")
            return True

        if text in ("какое сегодня число", "какая сегодня дата", "what's the date", "what is the date"):
            self._say(f"Сегодня {time.strftime('%d.%m.%Y')}")
            return True

        timer_triggers = ("поставь таймер на ", "запусти таймер на ", "set timer for ")
        for trigger in timer_triggers:
            if text.startswith(trigger):
                seconds = self._parse_duration(text)
                if seconds:
                    self._say(f"Таймер на {self._format_duration(seconds)} запущен")
                    self._start_timer(seconds, f"Таймер на {self._format_duration(seconds)} завершён!")
                else:
                    self._say("Не поняла, на сколько ставить таймер")
                return True

        reminder_triggers = ("напомни мне ", "напомни ", "remind me ")
        for trigger in reminder_triggers:
            if text.startswith(trigger):
                rest = text[len(trigger):]
                seconds = self._parse_duration(rest)
                if not seconds:
                    self._say("Через сколько напомнить?")
                    return True
                msg = self._strip_duration_prefix(rest) or "напоминание"
                self._say(f"Напомню через {self._format_duration(seconds)}: {msg}")
                self._start_timer(seconds, f"Напоминаю: {msg}")
                return True

        if self._weather_command(text, addressed, in_chat):
            return True

        currency_triggers = ("какой курс ", "курс ", "exchange rate ")
        for trigger in currency_triggers:
            if text.startswith(trigger):
                name = text[len(trigger):].replace("валюты", "").strip()
                result = self._fetch_with_filler(self.get_currency_rate, name)
                self._say(result if result else f"Не знаю курс для «{name}», извини")
                return True

        if self._handle_music(text):
            return True

        if text in ("громче", "сделай громче", "увеличь громкость", "volume up"):
            self._key_tap(VK_VOLUME_UP, times=3)
            self._say(_pick("confirmation"))
            return True
        if text in ("тише", "сделай тише", "уменьши громкость", "volume down"):
            self._key_tap(VK_VOLUME_DOWN, times=3)
            self._say(_pick("confirmation"))
            return True
        if text in ("выключи звук", "без звука", "заглуши", "mute"):
            self._key_tap(VK_VOLUME_MUTE)
            self._say(_pick("confirmation"))
            return True
        if text in ("пауза", "останови музыку", "поставь на паузу", "pause", "выключи музыку", "хватит музыки",
                    "поставь паузу", "на паузу", "останови"):
            self._key_tap(VK_MEDIA_PLAY_PAUSE)
            self._say(_pick("confirmation"))
            return True
        if text in ("play", "продолжи", "продолжи музыку", "воспроизведи", "продолжай"):
            self._key_tap(VK_MEDIA_PLAY_PAUSE)
            self._say(_pick("confirmation"))
            return True
        if text in ("следующий трек", "дальше трек", "next track", "next", "следующая песня", "следующую песню",
                    "следующий", "другую песню", "переключи песню", "переключи трек", "дальше", "скип", "skip"):
            self._key_tap(VK_MEDIA_NEXT_TRACK)
            self._say(_pick("confirmation"))
            return True
        if text in ("предыдущий трек", "прошлый трек", "previous track", "previous", "предыдущая песня",
                    "предыдущую песню", "прошлая песня", "прошлую песню", "верни песню"):
            self._key_tap(VK_MEDIA_PREV_TRACK)
            self._say(_pick("confirmation"))
            return True

        if text in ("ярче", "сделай ярче", "увеличь яркость", "brighter"):
            current = self.get_brightness()
            ok = self.set_brightness((current or 50) + 15)
            self._say(_pick("confirmation") if ok else _pick("error"))
            return True
        if text in ("темнее", "сделай темнее", "уменьши яркость", "darker"):
            current = self.get_brightness()
            ok = self.set_brightness((current or 50) - 15)
            self._say(_pick("confirmation") if ok else _pick("error"))
            return True
        for trigger in ("яркость на ", "поставь яркость ", "установи яркость "):
            if text.startswith(trigger):
                match = re.search(r"\d+", text)
                if match:
                    ok = self.set_brightness(int(match.group()))
                    self._say(_pick("confirmation") if ok else _pick("error"))
                else:
                    self._say(_pick("clarifying"))
                return True

        if text in ("сделай скриншот", "скриншот", "screenshot", "сними экран"):
            try:
                path = self.take_screenshot()
                self._say(f"Скриншот сохранён: {path.name}")
            except Exception:
                self._say(_pick("error"))
            return True

        edit_trigger = self._match_edit_trigger(text)
        if edit_trigger is not None:
            query = text[len(edit_trigger):].strip()
            if query:
                self.last_search_query = query
                self._say(f"Меняю запрос на {query}")
                self.update_search_google(query)
            else:
                self._say(_pick("clarifying"))
            return True

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
                return True

        site_triggers = (
            "открой сайт ", "зайди на сайт ", "перейди на сайт ",
            "open site ", "go to site ", "open website ",
        )
        for trigger in site_triggers:
            if text.startswith(trigger):
                query = text[len(trigger):].strip()
                self._say(f"Открываю сайт {query}")
                self.open_website(query)
                return True

        search_triggers = ("найди ", "поищи ", "find ", "search for ", "search ")
        for trigger in search_triggers:
            if text.startswith(trigger):
                query = text[len(trigger):].strip()
                self.last_search_query = query
                self._say(f"Ищу {query}")
                self.search_google(query)
                return True

        open_triggers = ("открой ", "запусти ", "open ", "launch ", "start ", "run ")
        for trigger in open_triggers:
            if text.startswith(trigger):
                query = text[len(trigger):].strip()
                if query in self.config.get("site_aliases", {}):
                    self._say(f"Открываю {query}")
                    self.open_website(query)
                    return True
                self._say(f"Открываю {query}")
                if not self.open_app(query):
                    self._say(f"{_pick('error')} Не нашла «{query}».")
                return True

        return False

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
        self.start_bridge()
        codex_notifications.start(self)
        threading.Thread(target=self.warm_weather, daemon=True).start()
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
                if self.chat_mode and time.time() - self.chat_last > 300:
                    self.chat_mode = False                  # silence for 5 minutes: the talk is over
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
