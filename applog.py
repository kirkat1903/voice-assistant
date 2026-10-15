import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_PATH = Path(__file__).parent / "assistant.log"

log = logging.getLogger("aiko")


class _StreamToLog:
    """Replacement for sys.stdout/sys.stderr under pythonw, where they are None."""

    def __init__(self, level):
        self.level = level
        self._buf = ""

    def write(self, s):
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                log.log(self.level, line.rstrip())
        return len(s)

    def flush(self):
        pass

    def isatty(self):
        return False


def setup():
    if log.handlers:
        return
    log.setLevel(logging.INFO)
    handler = RotatingFileHandler(LOG_PATH, maxBytes=512_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)

    if sys.stdout is None:
        sys.stdout = _StreamToLog(logging.INFO)
    if sys.stderr is None:
        sys.stderr = _StreamToLog(logging.ERROR)

    def _excepthook(exc_type, exc, tb):
        log.error("Необработанное исключение", exc_info=(exc_type, exc, tb))

    def _thread_excepthook(args):
        log.error(
            "Необработанное исключение в потоке %s",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
