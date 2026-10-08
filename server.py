#!/usr/bin/env python3
"""Chuyko — голосовой веб-интерфейс к ИИ-агенту (Hermes или любой OpenAI-совместимый API).

Браузер телефона сам распознаёт речь (Web Speech API), сервер:
  * POST /api/chat  — передаёт текст агенту (/v1/chat/completions), стримит ответ (SSE);
  * POST /api/tts   — озвучивает фразу Silero (модель держится в памяти);
  * GET  /          — страница.

Все /api/* требуют заголовок `Authorization: Bearer <VOICE_TOKEN>`.
Настройки — в .env рядом с server.py (см. .env.example).
Автор: Илья Брызгалов aka @AstroiLL (astroill.info). Лицензия MIT.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hmac
import io
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).parent


def _read_env(path: Path) -> dict:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


# .env рядом с server.py; переменные окружения главнее. Значения попадают и в os.environ,
# чтобы модули tts/ видели SILERO_* настройки.
for _k, _v in _read_env(BASE / ".env").items():
    os.environ.setdefault(_k, _v)
HERMES_ENV = _read_env(Path.home() / ".hermes" / ".env")   # запасной источник ключа для Hermes

# Модули ниже читают SILERO_* / VOICE_JOURNAL_DIR при импорте — поэтому после загрузки .env.
sys.path.insert(0, str(BASE / "tts"))
sys.path.insert(0, str(BASE))
from silero_tts import apply_lexicon, atempo_chain, ensure_model, load_lexicon  # noqa: E402
from translit import convert_text  # noqa: E402
from voicelog import append_turn  # noqa: E402


def env(name: str, default: str = "") -> str:
    return os.environ.get(name) or default


VOICE_TOKEN = env("VOICE_TOKEN")
AGENT_URL = env("AGENT_URL", "http://127.0.0.1:8642").rstrip("/")
AGENT_KEY = env("AGENT_KEY") or HERMES_ENV.get("API_SERVER_KEY", "")
AGENT_MODEL = env("AGENT_MODEL", "hermes-agent")
PORT = int(env("VOICE_PORT", "7460"))
HOST = env("VOICE_HOST", "127.0.0.1")
VOICE = env("SILERO_VOICE", "eugene")
SPEED = float(env("SILERO_SPEED", "1.0"))
MODEL_NAME = env("SILERO_MODEL", "v5_5_ru")
SAMPLE_RATE = 48000

if not VOICE_TOKEN:
    sys.exit("VOICE_TOKEN не задан (см. .env.example)")
if not AGENT_KEY:
    sys.exit("AGENT_KEY не задан (ключ API агента; для Hermes — API_SERVER_KEY из ~/.hermes/.env)")

# Голосовой системный промпт — файл voice_prompt.md (правится без перезапуска кода).
# К нему автоматически дописывается, как агенту менять фон страницы.
PROMPT_FILE = Path(env("VOICE_PROMPT_FILE", str(BASE / "voice_prompt.md")))
BG_HINT = f"""
Фон голосовой страницы — картинки в {BASE / "static" / "bg" / "images"}; показывается та,
на которую ссылается static/bg/current.jpg. Менять командой
`{sys.executable} {BASE / "set_bg.py"}` с подкомандами: `gen "<подробное описание на английском>"`
(сгенерировать), `add <путь>` (своя картинка), `list`, `use <имя>`. Страница подхватит фон сама.
"""


def voice_prompt() -> str:
    try:
        base = PROMPT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        base = "Тебе говорят голосом, ответ будет зачитан синтезатором: отвечай коротко, без markdown."
    return base + "\n" + BG_HINT

# --- Silero в памяти ---------------------------------------------------------

_model = None
_model_lock = threading.Lock()


def get_model():
    global _model
    if _model is None:
        import torch
        torch.set_num_threads(max(1, (os.cpu_count() or 4) // 2))
        m = torch.package.PackageImporter(str(ensure_model(MODEL_NAME))).load_pickle(
            "tts_models", "model")
        m.to(torch.device("cpu"))
        _model = m
    return _model


_MD = [
    (re.compile(r"```.*?```", re.S), " "),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), " "),
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),
    (re.compile(r"https?://\S+"), " ссылка "),
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.M), ""),
    (re.compile(r"^\s*[-*•]\s+", re.M), ""),
    (re.compile(r"[*_~|>#]+"), " "),
    (re.compile(r"[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F]"), " "),
    (re.compile(r"\s+"), " "),
]


def clean_for_speech(text: str) -> str:
    for pat, rep in _MD:
        text = pat.sub(rep, text)
    return text.strip()


def synth_ogg(text: str) -> bytes:
    text = convert_text(apply_lexicon(clean_for_speech(text), load_lexicon()))
    if not re.search(r"[А-Яа-яЁё0-9]", text):
        return b""
    with _model_lock:
        audio = get_model().apply_tts(text=text, speaker=VOICE, sample_rate=SAMPLE_RATE)
    pcm = (audio.clamp(-1, 1).numpy() * 32767).astype("int16").tobytes()
    cmd = ["ffmpeg", "-loglevel", "error", "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1",
           "-i", "pipe:0"]
    if abs(SPEED - 1.0) > 0.01:
        cmd += ["-filter:a", atempo_chain(SPEED)]
    cmd += ["-c:a", "libopus", "-b:a", "64k", "-f", "ogg", "pipe:1"]
    return subprocess.run(cmd, input=pcm, capture_output=True, check=True).stdout


# --- HTTP ----------------------------------------------------------------------

app = FastAPI(title="chuyko", docs_url=None, redoc_url=None)


def check_auth(request: Request) -> None:
    got = request.headers.get("authorization", "")
    if not hmac.compare_digest(got.encode(), f"Bearer {VOICE_TOKEN}".encode()):
        raise HTTPException(401, "bad token")


@app.on_event("startup")
async def warm() -> None:
    await asyncio.to_thread(get_model)
    print(f"[voice] silero {MODEL_NAME}/{VOICE} загружен; http://{HOST}:{PORT}", flush=True)


@app.get("/")
async def index():
    return FileResponse(BASE / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(BASE / "static" / "icons" / "favicon-32.png", media_type="image/png")


BG_LINK = BASE / "static" / "bg" / "current.jpg"


def _bg_target() -> Path | None:
    """Файл, на который указывает current.jpg (ссылка или обычный файл)."""
    try:
        target = BG_LINK.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    bg_root = (BASE / "static" / "bg").resolve()
    return target if target.is_file() and bg_root in target.parents else None


@app.get("/api/bg")
async def bg_version():
    """Версия текущего фона = имя цели ссылки + mtime. Без авторизации — картинка не секрет."""
    t = _bg_target()
    version = f"{t.name}:{int(t.stat().st_mtime)}" if t else None
    return Response(json.dumps({"version": version}), media_type="application/json",
                    headers={"Cache-Control": "no-store"})


@app.get("/bg/current")
async def bg_current():
    t = _bg_target()
    if not t:
        raise HTTPException(404, "фон не задан")
    return FileResponse(t, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/api/ping")
async def ping(request: Request):
    check_auth(request)
    return {"ok": True, "voice": VOICE}


@app.post("/api/tts")
async def tts(request: Request):
    check_auth(request)
    body = await request.json()
    text = str(body.get("text", ""))[:2000]
    data = await asyncio.to_thread(synth_ogg, text)
    if not data:
        return Response(status_code=204)
    return Response(data, media_type="audio/ogg")


@app.post("/api/chat")
async def chat(request: Request):
    check_auth(request)
    body = await request.json()
    text = str(body.get("text", "")).strip()
    session_id = str(body.get("session_id", "")).strip()
    if not text:
        raise HTTPException(400, "empty text")
    if not re.fullmatch(r"voice-[A-Za-z0-9_-]{6,64}", session_id):
        raise HTTPException(400, "bad session_id")

    payload = {
        "model": AGENT_MODEL,
        "stream": True,
        "messages": [
            {"role": "system", "content": voice_prompt()},
            {"role": "user", "content": text},
        ],
    }
    headers = {
        "Authorization": f"Bearer {AGENT_KEY}",
        "X-Hermes-Session-Id": session_id,
        "Content-Type": "application/json",
    }

    async def gen():
        started = dt.datetime.now()
        reply, error = [], None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=10)) as client:
                async with client.stream("POST", f"{AGENT_URL}/v1/chat/completions",
                                         json=payload, headers=headers) as r:
                    if r.status_code != 200:
                        err = (await r.aread()).decode(errors="replace")[:300]
                        error = f"агент ответил {r.status_code}: {err}"
                        yield _sse({"error": error})
                        return
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except ValueError:
                            continue
                        for ch in chunk.get("choices") or []:
                            delta = (ch.get("delta") or {}).get("content")
                            if delta:
                                reply.append(delta)
                                yield _sse({"delta": delta})
        except httpx.HTTPError as exc:
            error = f"нет связи с агентом: {exc}"
            yield _sse({"error": error})
        finally:
            # Дневник (если включён) пишется даже при обрыве связи с телефоном.
            try:
                if not session_id.startswith("voice-selftest"):
                    append_turn(session_id, text, "".join(reply) or (error or ""), when=started,
                                error=bool(error and not reply))
            except Exception as exc:  # дневник не должен ронять разговор
                print(f"[voice] voicelog: {exc}", file=sys.stderr, flush=True)
        yield _sse({"done": True})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
