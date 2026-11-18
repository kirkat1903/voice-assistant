"""Погода для Айко: где ты сейчас, погода сейчас / завтра / на неделю, Яндекс Погода в браузере.

Данные — Open-Meteo (бесплатно, без ключа). Где ты находишься, выясняется по порядку:
  1. город, названный во фразе («погода в Москве»);
  2. город, который ты назвала сама («Айко, я в Москве») — пока не скажешь «определяй город сама»;
  3. местоположение Windows (если в параметрах разрешён доступ классическим приложениям);
  4. город по интернет-адресу — только если его часовой пояс совпадает с часами компьютера
     (с VPN адрес показывает другую страну, и такой ответ отбрасывается);
  5. город из config.json («city»).
"""
import datetime
import json
import logging
import math
import re
import subprocess
import time
from pathlib import Path

import requests

log = logging.getLogger("aiko")

STATE_PATH = Path(__file__).parent / "location.json"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
HEADERS = {"User-Agent": "Aiko-voice-assistant/1.0"}

WMO = {
    0: "ясно", 1: "в основном ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "туман с изморозью",
    51: "слабая морось", 53: "морось", 55: "сильная морось", 56: "ледяная морось", 57: "ледяная морось",
    61: "небольшой дождь", 63: "дождь", 65: "сильный дождь", 66: "ледяной дождь", 67: "ледяной дождь",
    71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежная крупа",
    80: "кратковременный дождь", 81: "ливни", 82: "сильные ливни", 85: "снегопад", 86: "сильный снегопад",
    95: "гроза", 96: "гроза с градом", 99: "гроза с сильным градом",
}
WEEKDAY_ACC = ["в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу", "в воскресенье"]


# ---------------------------------------------------------------------------------------------
# Как сказать вслух
# ---------------------------------------------------------------------------------------------

def deg(n):
    n = int(round(n))
    a = abs(n)
    word = "градусов" if 11 <= a % 100 <= 14 else {1: "градус", 2: "градуса", 3: "градуса", 4: "градуса"}.get(a % 10, "градусов")
    sign = "" if n == 0 else ("плюс " if n > 0 else "минус ")
    return f"{sign}{a} {word}"


def temp_short(n):
    n = int(round(n))
    return "0" if n == 0 else (f"плюс {n}" if n > 0 else f"минус {abs(n)}")


def range_text(lo, hi):
    lo, hi = int(round(lo)), int(round(hi))
    if lo == hi:
        return deg(lo)
    return f"от {temp_short(lo)} до {deg(hi)}"


def wind_text(ms):
    ms = int(round(ms))
    if ms < 4:
        return ""
    if ms >= 12:
        return f", сильный ветер {ms} метров в секунду"
    return f", ветер {ms} " + ("метр" if ms % 10 == 1 and ms % 100 != 11 else
                                "метра" if ms % 10 in (2, 3, 4) and not 12 <= ms % 100 <= 14 else "метров") + " в секунду"


# ---------------------------------------------------------------------------------------------
# Где ты
# ---------------------------------------------------------------------------------------------

def _load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state):
    try:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        log.warning("Не удалось сохранить location.json")


_ENDINGS = (("ске", "ск"), ("е", ""), ("е", "а"), ("е", "ь"), ("и", "ь"), ("и", "ы"), ("и", ""),
            ("ом", ""), ("у", ""), ("ах", "ы"), ("ах", "и"))


def _word_variants(word):
    out = [word]
    for end, repl in _ENDINGS:
        if word.endswith(end) and len(word) > len(end) + 2:
            out.append(word[: len(word) - len(end)] + repl)
    return out


def _city_variants(name):
    """«красноярске» -> красноярске, красноярск…; «нижнем новгороде» -> нижний новгород: падежи из речи."""
    words = name.strip().lower().split()
    if not words:
        return []
    if len(words) == 1:
        return _word_variants(words[0])
    head = [re.sub(r"(?:ем|ом)$", "ий", w) for w in words[:-1]]          # «нижнем» -> «нижний»
    out = [" ".join(words)] + [" ".join(head + [v]) for v in _word_variants(words[-1])]
    return list(dict.fromkeys(out))


ALIASES = {"питер": "санкт-петербург", "питере": "санкт-петербург", "спб": "санкт-петербург",
           "петербург": "санкт-петербург", "петербурге": "санкт-петербург", "мск": "москва", "екб": "екатеринбург",
           "екате": "екатеринбург", "новосиб": "новосибирск", "новосибе": "новосибирск", "нск": "новосибирск",
           "краснодаре": "краснодар", "железногорске": "железногорск"}


def _dist2(a, b):
    return (a["lat"] - b["lat"]) ** 2 + ((a["lon"] - b["lon"]) * 0.6) ** 2


def geocode(name, near=None):
    """Город по названию (с учётом падежей) -> {"name", "lat", "lon"} или None.
    Предпочтение: точное совпадение названия, Россия, ближе к `near` (где ты обычно), крупнее."""
    name = name.strip().lower()
    name = ALIASES.get(name, name)
    variants = _city_variants(name)
    norm = {v.replace("ё", "е") for v in variants}
    candidates = []
    for variant in variants:
        try:
            r = requests.get(GEOCODE_URL, params={"name": variant, "language": "ru", "count": 10}, timeout=8)
            results = r.json().get("results") or []
        except (requests.RequestException, ValueError):
            break
        for x in results:
            candidates.append((x.get("name", "").lower().replace("ё", "е") in norm, x))
        if any(e and x.get("country_code") == "RU" for e, x in candidates):
            break                                                         # нашли точный город в России
    if not candidates:
        return None
    pool = [x for e, x in candidates if e] or [x for _, x in candidates]

    def score(x):
        # крупный город важнее, но тёзка рядом с тобой (ваш Железногорск) побеждает далёкого
        km = (_dist2({"lat": x["latitude"], "lon": x["longitude"]}, near) ** 0.5) * 111 if near else 0
        return (x.get("country_code") == "RU", math.log10((x.get("population") or 0) + 10) - km / 1500)

    best = max(pool, key=score)
    return {"name": best["name"], "lat": best["latitude"], "lon": best["longitude"]}


def _windows_location():
    """Координаты из службы местоположения Windows, если доступ разрешён."""
    script = ("Add-Type -AssemblyName System.Device; $w=New-Object System.Device.Location.GeoCoordinateWatcher; "
              "$w.Start(); $t=0; while(($w.Status -ne 'Ready') -and $t -lt 40){Start-Sleep -Milliseconds 100;$t++}; "
              "$c=$w.Position.Location; if(-not $c.IsUnknown){'{0},{1}' -f $c.Latitude,$c.Longitude}; $w.Stop()")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                           timeout=10, creationflags=0x08000000)
        lat, lon = (float(x.replace(",", ".")) for x in r.stdout.strip().split(",")[:2]) if "," in r.stdout else (None, None)
    except (subprocess.SubprocessError, OSError, ValueError):
        return None
    if lat is None:
        return None
    return {"name": _reverse_name(lat, lon), "lat": lat, "lon": lon}


def _reverse_name(lat, lon):
    try:
        r = requests.get(REVERSE_URL, params={"lat": lat, "lon": lon, "format": "json", "zoom": 10,
                                              "accept-language": "ru"}, headers=HEADERS, timeout=6)
        a = r.json().get("address", {})
        return a.get("city") or a.get("town") or a.get("village") or a.get("state") or ""
    except (requests.RequestException, ValueError):
        return ""


def _ip_location():
    """Город по интернет-адресу — только если его часовой пояс совпадает с часами компьютера (нет VPN)."""
    try:
        d = requests.get("https://ipapi.co/json/", headers=HEADERS, timeout=6).json()
    except (requests.RequestException, ValueError):
        return None
    off = d.get("utc_offset") or ""                                   # «+0700»
    local = time.strftime("%z")                                       # «+0700»
    if not d.get("city") or not off or off != local:
        return None
    return {"name": d["city"], "lat": d["latitude"], "lon": d["longitude"]}


class Locator:
    def __init__(self, config):
        self.config = config
        self._auto = (0.0, None)
        self._win_denied_at = 0.0
        self._home = None

    def home(self):
        """Город из config.json — чтобы «Железногорск» был ваш, а не курский."""
        if self._home is None:
            self._home = geocode(self.config.get("city", "Красноярск")) or {"name": "Красноярск", "lat": 56.01, "lon": 92.87}
        return self._home

    def find(self, name):
        return geocode(name, near=self.current())

    def set_city(self, name):
        """«Айко, я в Москве». None — если такой город не нашёлся."""
        place = geocode(name, near=self.home())
        if place:
            _save_state({"manual": place})
        return place

    def clear(self):
        _save_state({})
        self._auto = (0.0, None)

    def manual(self):
        return _load_state().get("manual")

    def current(self):
        manual = self.manual()
        if manual:
            return manual
        if time.time() - self._auto[0] < 3600 and self._auto[1]:
            return self._auto[1]
        place = None
        if time.time() - self._win_denied_at > 6 * 3600:
            place = _windows_location()
            if place is None:
                self._win_denied_at = time.time()          # доступ закрыт — не спрашиваем Windows ещё 6 часов
        place = place or _ip_location()
        if not place or not place.get("name"):
            place = self.home()
        self._auto = (time.time(), place)
        return place


# ---------------------------------------------------------------------------------------------
# Прогноз
# ---------------------------------------------------------------------------------------------

def forecast(place, days=7):
    r = requests.get(FORECAST_URL, params={
        "latitude": place["lat"], "longitude": place["lon"], "timezone": "auto", "forecast_days": days,
        "wind_speed_unit": "ms",
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    }, timeout=8)
    r.raise_for_status()
    return r.json()


def _day_name(i, date_str):
    if i == 0:
        return "сегодня"
    if i == 1:
        return "завтра"
    return WEEKDAY_ACC[datetime.date.fromisoformat(date_str).weekday()]


def _rain(p):
    return f", осадки {int(p)}%" if p is not None and p >= 40 else ""


def say_now(place, data):
    c, d = data["current"], data["daily"]
    feels = c.get("apparent_temperature")
    feels_txt = f", ощущается как {temp_short(feels)}" if feels is not None and abs(feels - c["temperature_2m"]) >= 3 else ""
    return (f"{place['name']}: сейчас {WMO.get(c['weather_code'], 'без осадков')}, {deg(c['temperature_2m'])}{feels_txt}"
            f"{wind_text(c.get('wind_speed_10m', 0))}. Сегодня {range_text(d['temperature_2m_min'][0], d['temperature_2m_max'][0])}"
            f"{_rain(d['precipitation_probability_max'][0])}.")


def say_day(place, data, i):
    d = data["daily"]
    return (f"{place['name']}, {_day_name(i, d['time'][i])}: {WMO.get(d['weather_code'][i], '')}, "
            f"{range_text(d['temperature_2m_min'][i], d['temperature_2m_max'][i])}{_rain(d['precipitation_probability_max'][i])}.")


def say_week(place, data):
    d = data["daily"]
    parts = []
    for i in range(min(7, len(d["time"]))):
        parts.append(f"{_day_name(i, d['time'][i])} {WMO.get(d['weather_code'][i], '')}, "
                     f"{range_text(d['temperature_2m_min'][i], d['temperature_2m_max'][i])}")
    text = "; ".join(parts)
    return f"Погода на неделю, {place['name']}. " + text[:1].upper() + text[1:] + "."


def yandex_url(place):
    return f"https://yandex.ru/pogoda/?lat={place['lat']:.4f}&lon={place['lon']:.4f}"


# ---------------------------------------------------------------------------------------------
# Разбор фразы
# ---------------------------------------------------------------------------------------------

WEATHER_RE = re.compile(r"погод|прогноз|weather|сколько градусов|какая температура|холодно ли|тепло ли|нужен ли зонт|будет ли дождь")
OPEN_RE = re.compile(r"\b(?:открой|покажи|открыть|показать|запусти|в яндексе|яндекс)\b")
WEEK_RE = re.compile(r"недел|7 дней|семь дней|ближайшие дни|выходны")
TOMORROW_RE = re.compile(r"\bзавтра\b")
AFTER_RE = re.compile(r"\bпослезавтра\b")
SET_CITY_RE = re.compile(r"^(?:я (?:сейчас |теперь |уже )?(?:нахожусь |живу )?(?:в|во)|мой город|я переехала в|я приехала в|"
                         r"запомни (?:что )?(?:я (?:в|во)|мой город))\s+(.+)$")
AUTO_CITY_RE = re.compile(r"^(?:определи(?:,)? где я|определяй город сама|где я(?: сейчас| нахожусь)?|забудь (?:мой )?город)$")
NOT_CITY = {"порядке", "норме", "шоке", "восторге", "душе", "дороге", "пути", "магазине", "гостях", "кровати",
            "постели", "ванной", "туалете", "отпуске", "универе", "универститете", "университете", "офисе", "сети",
            "чате", "интернете", "игре", "очереди", "метро", "автобусе", "такси"}


def city_in_phrase(text):
    m = re.search(r"\b(?:в|во|для|in)\s+([а-яёa-z][а-яёa-z\- ]*?)(?:\s+(?:сейчас|сегодня|завтра|послезавтра|на неделю|"
                  r"на выходные|на 7 дней|на семь дней|в яндексе|пожалуйста))*$", text)
    if not m:
        return ""
    city = m.group(1).strip()
    if city in ("яндексе", "яндекс", "браузере", "интернете") or city.startswith("яндекс"):
        return ""
    return city
