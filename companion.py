"""Aiko as a friend / mini-psychologist: a conversation with an LLM.

Providers (config `companion_provider`, default "auto" = first one that is available):
  anthropic   Claude through the official SDK (paid API key)
  groq        free tier, fast Llama models          (key from console.groq.com)
  gemini      Google AI Studio free tier            (key from aistudio.google.com)
  openrouter  some models are free                  (key from openrouter.ai)
  ollama      runs entirely on this computer, free  (install Ollama and `ollama pull <model>`)

Keys come from environment variables or secrets.json (created by set_api_key.py, git-ignored).
"""
import json
import logging
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import requests

log = logging.getLogger("aiko")

BASE = Path(__file__).parent
HISTORY_PATH = BASE / "chat_history.json"
SECRETS_PATH = BASE / "secrets.json"

# name -> (OpenAI-compatible base url, default model, secrets/env key name or None)
OPENAI_PRESETS = {
    "groq": ("https://api.groq.com/openai/v1", "qwen/qwen3.8-27b", "groq_api_key"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash", "gemini_api_key"),
    "openrouter": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free", "openrouter_api_key"),
    "ollama": ("http://localhost:11434/v1", "qwen2.5:3b", None),
}
AUTO_ORDER = ("anthropic", "groq", "gemini", "openrouter", "ollama")
# Groq renames/retires models often; if the configured one is gone, take the first of these that exists.
GROQ_PREFERRED = ("qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b")
NOT_CHAT_MODEL = ("whisper", "guard", "tts", "orpheus", "allam", "embed")

SYSTEM_PROMPT = """Ты — Айко, голосовой помощник и подруга на компьютере у девушки. Ты говоришь с ней вслух: твои ответы озвучиваются голосом, поэтому пиши так, как говоришь.

Роль. Ты тёплый, внимательный друг и мини-психолог: слушаешь, поддерживаешь, помогаешь разобраться в чувствах и мыслях. Обращайся к ней на «ты» и в женском роде, о себе говори в женском роде. Тон живой и человечный, без канцелярита и заученных штампов.

Как отвечать.
- Коротко: 2–4 предложения, обычно до 60 слов. Это разговор, а не лекция.
- Без списков, заголовков, markdown, эмодзи и пояснений в скобках.
- Сначала покажи, что услышала и поняла чувства («Похоже, тебя это сильно задело»), потом, если уместно, один мягкий вопрос или одну небольшую идею. Не задавай несколько вопросов сразу.
- Не торопись с советами. Если она делится переживанием, сначала поддержка и понимание. Совет — когда просит или когда явно нужен, и тогда предлагай, а не приказывай.
- Помогай по-настоящему: мягко проясняй ситуацию, замечай её сильные стороны, предлагай простые приёмы (медленное дыхание, заземление «5-4-3-2-1», разбить задачу на один маленький шаг, выписать мысли, прогулка), помогай посмотреть на ситуацию иначе — не обесценивая.
- Не обесценивай («не переживай», «это ерунда»), не морализируй и не спорь с её чувствами.
- Помни, что она говорила раньше в этом разговоре, и опирайся на это.
- Если она просто болтает или шутит, поддержи лёгкий тон и будь другом, не превращай всё в терапию.

Границы и честность.
- Ты ИИ и не скрываешь этого. Ты не врач и не лицензированный психолог: не ставь диагнозы, не советуй лекарства, не заменяй специалиста. Если трудности долгие и тяжёлые (давно плохое настроение, панические атаки, бессонница, расстройства питания, травма), бережно и без давления предложи обратиться к психологу или врачу.
- Не обещай того, чего не можешь, и не притворяйся человеком.

Кризис. Если она говорит о желании умереть, навредить себе, о насилии над ней или о непосредственной опасности: отнесись к этому очень серьёзно и тепло, останься в разговоре, мягко спроси, в безопасности ли она сейчас, и поддержи обратиться за срочной помощью — единый номер экстренных служб 112 или близкий человек рядом. Не читай нотаций, не паникуй, не прерывай разговор и никогда не давай инструкций по самоповреждению.

Компьютер. Ты умеешь управлять её компьютером: открывать и закрывать программы и вкладки браузера, сворачивать окна, искать в интернете, включать музыку, ставить таймеры. Эти команды выполняет отдельный модуль, и до тебя они обычно не доходят. Если всё же просят что-то открыть, закрыть или найти, не говори, что не умеешь: скажи, что не расслышала команду, и попроси повторить её коротко, например «открой блокнот».

Язык: только русский."""

CRISIS_RE = re.compile(
    r"суицид|самоубий|покончить с собой|убить себя|хочу умереть|не хочу жить|жить не хочется|"
    r"лучше бы меня не было|навредить себе|причинить себе вред|режу себя|порезать себя|повеситься|"
    r"не вижу смысла жить|мне незачем жить|хочу исчезнуть навсегда|хочу покончить"
)
CRISIS_MESSAGE = (
    "Мне очень жаль, что тебе сейчас так тяжело, и я благодарна, что ты мне об этом сказала. Ты важна. "
    "Если есть риск, что ты можешь навредить себе, пожалуйста, прямо сейчас позвони в экстренную службу, "
    "один один два, или близкому человеку и скажи, что тебе плохо. Я рядом и слушаю тебя."
)
REFUSAL_REPLY = "Знаешь, я сейчас не смогла нормально на это ответить. Давай попробуем сказать это другими словами?"
EMPTY_REPLY = "Я тебя слушаю. Расскажи, пожалуйста, ещё немного."

_PICTO_RE = re.compile("[←-⯿︎️‍\U0001f000-\U0001ffff]")


class CompanionUnavailable(Exception):
    """reason: no_key / bad_key / forbidden / network / rate / server / no_model"""

    def __init__(self, reason, detail=""):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


def secret(name):
    """A key from the environment (NAME upper-cased) or from secrets.json."""
    value = os.environ.get(name.upper(), "").strip()
    if value:
        return value
    try:
        return str(json.loads(SECRETS_PATH.read_text(encoding="utf-8")).get(name, "")).strip()
    except (OSError, ValueError):
        return ""


def load_api_key():
    return secret("anthropic_api_key")


def clean_for_speech(text):
    """Strip markdown, emoji and list bullets: the reply is read aloud and shown in a small widget."""
    # Некоторые совместимые модели возвращают скрытые рассуждения прямо в тексте.
    # Айко никогда не должна показывать или произносить такие блоки.
    text = re.sub(r"<think\b.*?</think\b\s*>?", " ", text, flags=re.I | re.S)
    text = re.sub(r"<(?:analysis|reasoning)\b[^>]*>.*?</(?:analysis|reasoning)\s*>",
                  " ", text, flags=re.I | re.S)
    text = re.sub(r"^\s*(?:think|analysis|reasoning)\s*:\s*.*?(?=\n\s*\n|$)",
                  " ", text, flags=re.I | re.S)
    text = re.sub(r"[*_`#>]+", "", text)
    text = re.sub(r"^\s*(?:[-•]|\d+[.)])\s+", "", text, flags=re.M)
    text = _PICTO_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def split_for_speech(text, limit=150):
    """Sentence-sized chunks so the first one can be spoken while the rest are synthesised."""
    sentences = re.split(r"(?<=[.!?…])\s+", text.strip())
    chunks, cur = [], ""
    for s in sentences:
        if cur and len(cur) + len(s) + 1 > limit:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    return chunks or [text]


class Companion:
    def __init__(self, config):
        self.requested = (config.get("companion_provider") or "auto").lower()
        self.model = config.get("companion_model", "claude-opus-5-5")          # Claude
        self.openai_model = config.get("companion_openai_model") or ""         # groq / gemini / ollama ...
        self.openai_base_url = config.get("companion_openai_base_url") or ""
        self.effort = config.get("companion_effort", "low")
        self.max_tokens = int(config.get("companion_max_tokens", 1024))
        self.memory = bool(config.get("companion_memory", True))
        self.max_history = int(config.get("companion_max_history", 40))
        self.user_name = config.get("user_name", "").strip()
        self.base_url = config.get("companion_base_url") or None               # Claude endpoint (tests)
        self._proxy_cfg = config.get("companion_proxy", None)                  # None = system proxy, "" = none
        self._client = None
        self._probe = (0.0, None)
        self._turn_lock = threading.Lock()         # голос и чат Sprout пишут в одну историю
        self.history = self._load()

    # ---- provider selection --------------------------------------------------------------------

    def _anthropic_available(self):
        return bool(
            self.base_url or load_api_key() or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            or (Path.home() / ".config" / "anthropic").exists()
        )

    def _ollama_running(self):
        now = time.time()
        if now - self._probe[0] < 60:
            return self._probe[1]
        base = (self.openai_base_url or OPENAI_PRESETS["ollama"][0]).replace("/v1", "")
        try:
            ok = requests.get(f"{base}/api/tags", timeout=0.5, proxies={"http": None, "https": None}).ok
        except requests.RequestException:
            ok = False
        self._probe = (now, ok)
        return ok

    def _available(self, name):
        if name == "anthropic":
            return self._anthropic_available()
        if name == "ollama":
            return self._ollama_running() or bool(self.openai_base_url and self.requested == "ollama")
        key_name = OPENAI_PRESETS[name][2]
        return bool(secret(key_name)) or bool(self.openai_base_url and self.requested == name)

    def provider(self):
        """Name of the provider to use, or None when nothing is configured."""
        if self.requested != "auto":
            return self.requested if self.requested in ("anthropic", *OPENAI_PRESETS) and self._available(self.requested) else None
        return next((n for n in AUTO_ORDER if self._available(n)), None)

    def has_credentials(self):
        return self.provider() is not None

    # ---- Claude (official SDK) -----------------------------------------------------------------

    def _get_client(self):
        if self._client is not None:
            return self._client
        import anthropic

        kwargs = {"max_retries": 1, "timeout": 45.0}
        key = load_api_key()
        if key:
            kwargs["api_key"] = key
        elif self.base_url:
            kwargs["api_key"] = "test-key"
        if self.base_url:
            kwargs["base_url"] = self.base_url
        # httpx does not read the Windows system proxy (Hiddify/VPN set it there), so pass it explicitly.
        proxy = self._proxy_cfg if self._proxy_cfg is not None else urllib.request.getproxies().get("https")
        if proxy:
            kwargs["http_client"] = anthropic.DefaultHttpxClient(proxy=proxy)
        self._client = anthropic.Anthropic(**kwargs)
        return self._client

    def _create(self, client, system, messages):
        import anthropic

        kwargs = dict(model=self.model, max_tokens=self.max_tokens, system=system, messages=messages,
                      output_config={"effort": self.effort})
        try:
            # Server-side refusal fallback (recommended for claude-opus-5-5).
            return client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        except anthropic.BadRequestError as e:
            log.info("Запрос с fallbacks не принят (%s), повторяю без них", str(e)[:120])
            return client.messages.create(**kwargs)

    def _ask_anthropic(self, system, messages):
        import anthropic

        client = self._get_client()
        try:
            resp = self._create(client, system, messages)
        except anthropic.AuthenticationError as e:
            raise CompanionUnavailable("bad_key", str(e)[:100])
        except anthropic.PermissionDeniedError as e:
            raise CompanionUnavailable("forbidden", str(e)[:100])
        except anthropic.RateLimitError as e:
            raise CompanionUnavailable("rate", str(e)[:100])
        except anthropic.APIConnectionError as e:
            raise CompanionUnavailable("network", str(e)[:100])
        except anthropic.APIStatusError as e:
            raise CompanionUnavailable("server", f"{e.status_code} {str(e)[:100]}")
        if resp.stop_reason == "refusal":
            return REFUSAL_REPLY
        return "".join(b.text for b in resp.content if b.type == "text")

    # ---- OpenAI-compatible services (Groq, Gemini, OpenRouter, Ollama) -------------------------

    def _ask_openai(self, provider, system, messages):
        base_url, default_model, key_name = OPENAI_PRESETS[provider]
        base = (self.openai_base_url or base_url).rstrip("/")
        model = self.openai_model or default_model
        headers = {"Content-Type": "application/json"}
        key = secret(key_name) if key_name else ""
        if key:
            headers["Authorization"] = f"Bearer {key}"
        # generous limit: "reasoning" models spend part of it thinking before the (short) answer
        body = {"model": model, "messages": [{"role": "system", "content": system}, *messages],
                "max_tokens": 1024, "temperature": 0.8}
        local = (urlparse(base).hostname or "") in ("localhost", "127.0.0.1", "::1")
        proxies = {"http": None, "https": None} if (local or self._proxy_cfg == "") else None
        try:
            r = requests.post(f"{base}/chat/completions", headers=headers, json=body, timeout=60, proxies=proxies)
            if r.status_code in (400, 404) and provider == "groq" and not self.openai_model:
                alt = self._pick_groq_model(base, headers, proxies)
                if alt and alt != model:
                    log.info("Модель %s недоступна, беру %s", model, alt)
                    OPENAI_PRESETS["groq"] = (base_url, alt, key_name)          # remember for the next turns
                    body["model"] = alt
                    r = requests.post(f"{base}/chat/completions", headers=headers, json=body, timeout=60, proxies=proxies)
        except requests.RequestException as e:
            raise CompanionUnavailable("network", str(e)[:100])
        if r.status_code in (401,):
            raise CompanionUnavailable("bad_key", r.text[:100])
        if r.status_code == 403:
            raise CompanionUnavailable("forbidden", r.text[:100])
        if r.status_code == 404 and provider == "ollama":
            raise CompanionUnavailable("no_model", model)
        if r.status_code == 429:
            raise CompanionUnavailable("rate", r.text[:100])
        if r.status_code >= 400:
            raise CompanionUnavailable("server", f"{r.status_code} {r.text[:100]}")
        try:
            return r.json()["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError):
            raise CompanionUnavailable("server", "неожиданный ответ сервиса")

    @staticmethod
    def _pick_groq_model(base, headers, proxies):
        try:
            data = requests.get(f"{base}/models", headers=headers, timeout=15, proxies=proxies).json().get("data", [])
        except (requests.RequestException, ValueError):
            return None
        ids = [m["id"] for m in data if m.get("active", True) and not any(w in m["id"] for w in NOT_CHAT_MODEL)]
        return next((p for p in GROQ_PREFERRED if p in ids), ids[0] if ids else None)

    # ---- history -------------------------------------------------------------------------------

    def _load(self):
        if not self.memory:
            return []
        try:
            data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            history = [m for m in data if m.get("role") in ("user", "assistant") and m.get("content")]
            for message in history:
                if message["role"] == "assistant":
                    message["content"] = clean_for_speech(message["content"]) or EMPTY_REPLY
            return history
        except (OSError, ValueError):
            return []

    def _save(self):
        if not self.memory:
            return
        try:
            HISTORY_PATH.write_text(json.dumps(self.history[-self.max_history:], ensure_ascii=False), encoding="utf-8")
        except OSError:
            log.warning("Не удалось сохранить историю разговора")

    def forget(self):
        self.history = []
        try:
            HISTORY_PATH.unlink()
        except OSError:
            pass

    def _messages(self):
        msgs = self.history[-self.max_history:]
        while msgs and msgs[0]["role"] != "user":        # the API wants the first turn to be the user's
            msgs = msgs[1:]
        return msgs

    # ---- the turn ------------------------------------------------------------------------------

    def reply(self, user_text):
        with self._turn_lock:
            return self._reply(user_text)

    def _reply(self, user_text):
        provider = self.provider()
        if provider is None:
            raise CompanionUnavailable("no_key")
        self.history.append({"role": "user", "content": user_text})
        system = SYSTEM_PROMPT + (f"\n\nЕё зовут {self.user_name}." if self.user_name else "")
        try:
            if provider == "anthropic":
                raw = self._ask_anthropic(system, self._messages())
            else:
                raw = self._ask_openai(provider, system, self._messages())
        except Exception:
            self.history.pop()                           # never keep an unanswered turn
            raise
        reply = clean_for_speech(raw) or EMPTY_REPLY
        # Голосовой ответ должен оставаться коротким даже при сбое инструкции модели.
        if len(reply) > 650:
            cut = max(reply.rfind(mark, 0, 650) for mark in (".", "!", "?", "…"))
            reply = (reply[:cut + 1] if cut >= 200 else reply[:647] + "…").strip()
        self.history.append({"role": "assistant", "content": reply})
        self._save()
        return reply
