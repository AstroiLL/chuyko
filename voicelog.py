"""Дневник голосовых разговоров в markdown (удобно класть в Obsidian или любую папку заметок).

Включается переменной VOICE_JOURNAL_DIR (пусто — дневник выключен).
Файл на день: <VOICE_JOURNAL_DIR>/YYYY-MM-DD.md, внутри — блоки по сессиям
(маркер `<!-- sid:voice-… -->`), реплики с временем. Пишется только текст разговора,
без вызовов инструментов агента.
"""
from __future__ import annotations

import datetime as dt
import os
import threading
from pathlib import Path

_DIR = os.environ.get("VOICE_JOURNAL_DIR", "").strip()
LOG_DIR = Path(_DIR).expanduser() if _DIR else None
ASSISTANT = os.environ.get("VOICE_ASSISTANT_NAME", "Чуйко")
_lock = threading.Lock()


def _day_file(day: dt.date) -> Path:
    return LOG_DIR / f"{day.isoformat()}.md"


def _quote(text: str) -> str:
    """Текст в blockquote, чтобы markdown ответа не ломал структуру дневника."""
    return "\n".join("> " + line if line.strip() else ">" for line in text.strip().splitlines())


def append_turn(session_id: str, user_text: str, reply: str, when: dt.datetime | None = None,
                title: str | None = None, error: bool = False) -> Path | None:
    if LOG_DIR is None:
        return None
    when = when or dt.datetime.now()
    path = _day_file(when.date())
    marker = f"<!-- sid:{session_id} -->"
    with _lock:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        parts = []
        if not existing:
            parts.append(f"---\ntype: voice-log\ndate: {when.date().isoformat()}\ntags: [voice]\n---\n"
                         f"# Голосовые разговоры {when.date().isoformat()}\n")
        # Новый блок сессии — если её ещё не было в этом файле или последней писалась другая.
        last_marker = existing.rfind("<!-- sid:")
        last_sid = existing[last_marker + 9:].split("-->")[0].strip() if last_marker >= 0 else ""
        if session_id != last_sid:
            head = f"\n## {when:%H:%M} · {title or 'голосовая сессия'}\n{marker}\n"
            parts.append(head)
        who = f"⚠️ {ASSISTANT} (ошибка)" if error else f"🤖 {ASSISTANT}"
        parts.append(f"\n**🗣 Я** · {when:%H:%M}\n{_quote(user_text)}\n\n**{who}**\n{_quote(reply or '—')}\n")
        with path.open("a", encoding="utf-8") as f:
            f.write("".join(parts))
    return path
