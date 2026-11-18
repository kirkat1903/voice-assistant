"""Read Codex completion toasts from the local Windows notification database."""
import logging
import os
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from contextlib import closing
from pathlib import Path

log = logging.getLogger("aiko")
DB = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Notifications/wpndatabase.db"
WINDOWS_EPOCH = 116444736000000000


def _event(payload):
    try:
        root = ET.fromstring(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, ET.ParseError):
        return None
    texts = [" ".join(node.itertext()).strip() for node in root.iter("text")]
    title = texts[0] if texts else ""
    body = " ".join(texts[1:])
    words = (title + " " + body).lower()
    if any(word in words for word in ("заверш", "готов", "выполн", "completed", "finished", "done")):
        return "codex_chat_done", title
    if any(word in words for word in ("ждёт", "ждет", "разрешен", "ответ", "approval", "permission", "input")):
        return "codex_chat_wait", title
    return None


def watch(assistant):
    seen = set()
    first = True
    query = ("SELECT n.Id, n.ArrivalTime, n.Payload FROM Notification n "
             "JOIN NotificationHandler h ON h.RecordId = n.HandlerId "
             "WHERE n.Type = 'toast' AND h.PrimaryId LIKE '%OpenAI.Codex%'")
    while not assistant.stopped:
        try:
            with closing(sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
                rows = db.execute(query).fetchall()
            for row_id, arrival, payload in rows:
                if row_id in seen:
                    continue
                seen.add(row_id)
                if first or (time.time() - (arrival - WINDOWS_EPOCH) / 10_000_000) > 120:
                    continue
                event = _event(payload or b"")
                if event:
                    assistant.claude_notice(*event)
            first = False
            seen.intersection_update(row[0] for row in rows)
        except (OSError, sqlite3.Error):
            log.debug("Codex notifications unavailable", exc_info=True)
        time.sleep(2)


def start(assistant):
    threading.Thread(target=watch, args=(assistant,), daemon=True, name="codex-notifications").start()
