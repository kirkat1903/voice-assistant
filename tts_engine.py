import asyncio
import ctypes
import hashlib
import logging
import os
import threading
from pathlib import Path

import win32api

log = logging.getLogger("aiko")

CACHE_DIR = Path(__file__).parent / "tts_cache"
MAX_CACHE_FILES = 300
MAX_CACHED_CHARS = 120
SYNTH_TIMEOUT = 10

_winmm = ctypes.windll.winmm
_alias_counter = 0
_alias_lock = threading.Lock()


def _mci(command):
    buf = ctypes.create_unicode_buffer(256)
    err = _winmm.mciSendStringW(command, buf, 255, 0)
    if err:
        msg = ctypes.create_unicode_buffer(256)
        _winmm.mciGetErrorStringW(err, msg, 255)
        raise OSError(f"MCI '{command}': {msg.value}")
    return buf.value


def play_mp3(path):
    global _alias_counter
    with _alias_lock:
        _alias_counter += 1
        alias = f"aiko_{_alias_counter}"
    short = win32api.GetShortPathName(str(path))
    _mci(f'open "{short}" type mpegvideo alias {alias}')
    try:
        _mci(f"play {alias} wait")
    finally:
        _mci(f"close {alias}")


class NeuralVoice:
    """Microsoft Edge neural voices (online) with an on-disk phrase cache."""

    def __init__(self, voice="ru-RU-SvetlanaNeural", rate="+0%", pitch="+0Hz"):
        self.voice = voice
        self.rate = rate
        self.pitch = pitch
        self._synth_lock = threading.Lock()
        CACHE_DIR.mkdir(exist_ok=True)
        for leftover in CACHE_DIR.glob("*.part"):
            try:
                leftover.unlink()
            except OSError:
                pass

    def _cache_path(self, text):
        key = f"{self.voice}|{self.rate}|{self.pitch}|{text}"
        return CACHE_DIR / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".mp3")

    async def _synth(self, text, target):
        import edge_tts

        tmp = target.with_suffix(".part")
        communicate = edge_tts.Communicate(text, self.voice, rate=self.rate, pitch=self.pitch)
        await asyncio.wait_for(communicate.save(str(tmp)), timeout=SYNTH_TIMEOUT)
        if not tmp.exists() or tmp.stat().st_size == 0:
            raise RuntimeError("пустой ответ синтезатора")
        os.replace(tmp, target)

    def prepare(self, text):
        """Return path to an mp3 for `text`, synthesizing it if it is not cached."""
        cacheable = len(text) <= MAX_CACHED_CHARS
        target = self._cache_path(text) if cacheable else CACHE_DIR / "_dynamic.mp3"
        if cacheable and target.exists():
            return target
        with self._synth_lock:
            if cacheable and target.exists():
                return target
            asyncio.run(self._synth(text, target))
        if cacheable:
            self._trim_cache()
        return target

    def say(self, text):
        play_mp3(self.prepare(text))

    def _trim_cache(self):
        try:
            files = sorted(CACHE_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
            for old in files[:-MAX_CACHE_FILES]:
                old.unlink()
        except OSError:
            pass
