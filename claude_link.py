"""«Посмотри на экран»: скриншот отправляется модели с поддержкой картинок и ответ зачитывается вслух.

По умолчанию — бесплатно через Groq (тот же ключ, что и для разговора). Запасной вариант, если ключа Groq
нет, — консольный `claude` (нужен вход в аккаунт Claude).

Отправить сообщение в уже открытый чат Claude Code снаружи нельзя, поэтому каждый скриншот ещё и кладётся
в screenshots/latest.png: достаточно написать в чате «посмотри screenshots/latest.png», и Claude его откроет.
"""
import base64
import io
import logging
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

import requests
from PIL import Image

from companion import clean_for_speech, secret

log = logging.getLogger("aiko")

SHOTS_DIR = Path(__file__).parent / "screenshots"
LATEST = SHOTS_DIR / "latest.png"
TIMEOUT = 150

PROMPT = (
    "Это скриншот экрана пользователя: открой файл {name} (инструмент Read) и посмотри на него. "
    "{task} Отвечай по-русски, 1-3 коротких предложения, без markdown, списков и эмодзи — "
    "ответ будет зачитан голосом. Ничего не изменяй и не запускай, только смотри."
)
DEFAULT_TASK = "Коротко скажи, что на экране, и если видишь ошибку или проблему — что с ней делать."


class ClaudeLinkError(Exception):
    pass


def find_claude():
    found = shutil.which("claude")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "claude.exe"
    return str(local) if local.exists() else None


def proxy_env():
    """The CLI (Node) ignores the Windows system proxy (VPN client), so hand it over explicitly."""
    env = dict(os.environ)
    proxy = urllib.request.getproxies().get("https") or urllib.request.getproxies().get("http")
    if proxy and not (env.get("HTTPS_PROXY") or env.get("https_proxy")):
        env["HTTPS_PROXY"] = env["HTTP_PROXY"] = proxy
    return env


def publish_latest(path):
    """Копия последнего скриншота по постоянному пути (чтобы на него можно было сослаться в чате)."""
    try:
        shutil.copyfile(path, LATEST)
    except OSError as e:
        log.warning("Не удалось обновить latest.png: %s", e)


VISION_MODELS = ("qwen/qwen3.8-27b", "openai/gpt-oss-120b")   # first one that accepts images
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
VISION_PROMPT = (
    "Это скриншот экрана пользователя. {task} Отвечай по-русски, 1-3 коротких предложения, без markdown, "
    "списков и эмодзи: ответ будет зачитан голосом.")


def _jpeg_b64(path, max_side=1600):
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode()


def ask_groq(path, question=""):
    """Free path: Groq's vision-capable model (the key Aiko already uses for conversation)."""
    key = secret("groq_api_key")
    if not key:
        raise ClaudeLinkError("no_key")
    task = f"Вопрос пользователя: {question}." if question else DEFAULT_TASK
    content = [{"type": "text", "text": VISION_PROMPT.format(task=task)},
               {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + _jpeg_b64(path)}}]
    last = "failed"
    for model in VISION_MODELS:
        body = {"model": model, "max_tokens": 1024, "temperature": 0.4,
                "messages": [{"role": "user", "content": content}]}
        try:
            r = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=60)
        except requests.RequestException as e:
            log.warning("Groq (экран): %s", e)
            raise ClaudeLinkError("network")
        if r.status_code in (401, 403):
            raise ClaudeLinkError("bad_key")
        if r.status_code == 429:
            raise ClaudeLinkError("rate")
        if r.status_code != 200:
            log.warning("Groq (экран) %s %s: %s", model, r.status_code, r.text[:200])
            continue
        try:
            text = clean_for_speech(r.json()["choices"][0]["message"].get("content") or "")
        except (KeyError, IndexError, ValueError):
            continue
        if text:
            return text
    raise ClaudeLinkError(last)


def ask_about_screenshot(path, question=""):
    """Groq first (free); the logged-in `claude` CLI only as a fallback when there is no Groq key."""
    try:
        return ask_groq(path, question)
    except ClaudeLinkError as e:
        if str(e) != "no_key":
            raise
    return ask_claude_cli(path, question)


def ask_claude_cli(path, question=""):
    exe = find_claude()
    if not exe:
        raise ClaudeLinkError("not_installed")
    task = f"Вопрос пользователя: {question}." if question else DEFAULT_TASK
    cmd = [exe, "-p", PROMPT.format(name=Path(path).name, task=task),
           "--allowedTools", "Read", "--strict-mcp-config", "--disable-slash-commands",
           "--no-session-persistence", "--output-format", "text"]
    try:
        r = subprocess.run(cmd, cwd=str(Path(path).parent), capture_output=True, text=True, env=proxy_env(),
                           encoding="utf-8", errors="replace", timeout=TIMEOUT, stdin=subprocess.DEVNULL,
                           creationflags=0x08000000)       # CREATE_NO_WINDOW
    except subprocess.TimeoutExpired:
        raise ClaudeLinkError("timeout")
    except OSError as e:
        raise ClaudeLinkError(f"start: {e}")
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        log.warning("claude -p код %s: %s", r.returncode, (r.stderr or out)[:300])
        low = (r.stderr + out).lower()
        raise ClaudeLinkError("not_logged_in" if "log in" in low or "login" in low or "auth" in low else "failed")
    return out
