#!/usr/bin/env python3
"""Chuyko — голосовой веб-интерфейс к ИИ-агенту (Hermes или любой OpenAI-совместимый API).

Браузер телефона сам распознаёт речь (Web Speech API), сервер:
  * POST /api/chat  — передаёт текст агенту (/v1/chat/completions), стримит ответ (SSE);
  * POST /api/tts   — озвучивает фразу через внешний TTS с OpenAI-совместимым API
                      (silero-tts-service, OpenAI и т.п.); не настроен — читает браузер;
  * GET  /          — страница.

Все /api/* требуют заголовок `Authorization: Bearer <VOICE_TOKEN>`.
Настройки — в .env рядом с server.py (см. .env.example).
Author: Ilya Byven aka @AstroiLL (astroill.info). License: MIT.
"""
from __future__ import annotations

import datetime as dt
import hmac
import json
import os
import re
import sys
from contextlib import asynccontextmanager
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
# чтобы модули ниже (voicelog) видели свои настройки.
for _k, _v in _read_env(BASE / ".env").items():
    os.environ.setdefault(_k, _v)
HERMES_ENV = _read_env(Path.home() / ".hermes" / ".env")   # запасной источник ключа для Hermes

# voicelog читает VOICE_JOURNAL_DIR при импорте — поэтому после загрузки .env.
sys.path.insert(0, str(BASE))
from voicelog import append_turn  # noqa: E402


def env(name: str, default: str = "") -> str:
    return os.environ.get(name) or default


VOICE_TOKEN = env("VOICE_TOKEN")
AGENT_URL = env("AGENT_URL", "http://127.0.0.1:8642").rstrip("/")
AGENT_KEY = env("AGENT_KEY") or HERMES_ENV.get("API_SERVER_KEY", "")
AGENT_MODEL = env("AGENT_MODEL", "hermes-agent")
PORT = int(env("VOICE_PORT", "7460"))
HOST = env("VOICE_HOST", "127.0.0.1")
# Синтез речи: любой сервис с OpenAI-совместимым POST {TTS_URL}/audio/speech.
#   TTS_URL задан            — он (и TTS_KEY);
#   TTS_URL пуст             — ищем клиентский файл silero-tts-service
#                              (~/.config/silero-tts/client.env: SILERO_TTS_URL, SILERO_TTS_KEY);
#   TTS_URL=off (или browser) — сервер не озвучивает, страница читает голосом браузера.
SILERO_CLIENT_CONF = Path(env("SILERO_TTS_CONF") or
                          Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
                          / "silero-tts" / "client.env")
TTS_URL = env("TTS_URL").strip().rstrip("/")
TTS_KEY = env("TTS_KEY")
TTS_SOURCE = ".env"
if TTS_URL.lower() in {"off", "browser", "none"}:
    TTS_URL, TTS_SOURCE = "", "off"
elif not TTS_URL and SILERO_CLIENT_CONF.exists():
    _client = _read_env(SILERO_CLIENT_CONF)
    if _client.get("SILERO_TTS_URL"):
        TTS_URL = re.sub(r"(/v1)?/*$", "", _client["SILERO_TTS_URL"]) + "/v1"
        TTS_KEY = TTS_KEY or _client.get("SILERO_TTS_KEY", "")
        TTS_SOURCE = str(SILERO_CLIENT_CONF)
TTS_MODEL = env("TTS_MODEL", "silero")
TTS_VOICE = env("TTS_VOICE") or env("SILERO_VOICE", "eugene")
TTS_SPEED = float(env("TTS_SPEED") or env("SILERO_SPEED", "1.0"))
LEXICON_FILE = BASE / "lexicon.local.json"   # свои ударения Чуйко (только для silero-tts-service)

if not VOICE_TOKEN:
    sys.exit("VOICE_TOKEN не задан (см. .env.example)")
if not AGENT_KEY:
    sys.exit("AGENT_KEY не задан (ключ API агента; для Hermes — API_SERVER_KEY из ~/.hermes/.env)")

# Голосовой системный промпт — файл voice_prompt.md (правится без перезапуска кода).
# К нему автоматически дописывается, как агенту менять фон страницы.
# Свой промпт (вне git) — voice_prompt.local.md, если он есть.
_LOCAL_PROMPT = BASE / "voice_prompt.local.md"
PROMPT_FILE = Path(env("VOICE_PROMPT_FILE", str(_LOCAL_PROMPT if _LOCAL_PROMPT.exists() else BASE / "voice_prompt.md")))
BG_HINT = f"""
Фон голосовой страницы — картинки в {BASE / "static" / "bg" / "images"}; показывается та,
на которую ссылается static/bg/current.jpg. Менять командой
`{sys.executable} {BASE / "set_bg.py"}` с подкомандами: `gen "<подробное описание на английском>"`
(сгенерировать), `add <путь>` (своя картинка), `list`, `use <имя>`. Страница подхватит фон сама.

Подпись в шапке страницы после «Чуйко» задаётся в файле {BASE / ".env"}:
`AGENT_NAME=` — имя агента (главнее всего); `PUBLIC_URL=` — внешний адрес этой страницы
(https://домен, если настроен); оба пусты — показывается внутренний адрес агента.
Если пользователь просит подписать агента по имени — впиши `AGENT_NAME`. Если просит показать
внешний адрес — выясни домен этой страницы (конфиг reverse proxy, проверь `curl -sI https://<домен>/`)
и впиши `PUBLIC_URL`. Перезапуск не нужен: шапка обновится, когда пользователь перезагрузит страницу.
"""


def agent_label() -> str:
    """Подпись в шапке, чтобы при нескольких агентах было видно, с кем разговор.
    AGENT_NAME → PUBLIC_URL (внешний адрес страницы) → AGENT_URL (внутренний адрес агента).
    .env читается при каждом запросе: правки применяются без перезапуска."""
    cfg = _read_env(BASE / ".env")
    return (cfg.get("AGENT_NAME", "").strip()[:60] or cfg.get("PUBLIC_URL", "").strip().rstrip("/")[:100]
            or AGENT_URL)


def voice_prompt() -> str:
    try:
        base = PROMPT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        base = "Тебе говорят голосом, ответ будет зачитан синтезатором: отвечай коротко, без markdown."
    return base + "\n" + BG_HINT

# --- TTS ---------------------------------------------------------------------

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


def _local_lexicon() -> dict:
    try:
        data = json.loads(LEXICON_FILE.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.get("words", data).items() if not k.startswith("_")}
    except (OSError, ValueError, AttributeError):
        return {}


async def _detect_silero() -> bool:
    """Это silero-tts-service? Тогда ему можно слать свои ударения (поле lexicon)."""
    root = re.sub(r"/v1$", "", TTS_URL)
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{root}/health")
        return r.status_code == 200 and r.json().get("service") == "silero-tts-service"
    except (httpx.HTTPError, ValueError):
        return False


TTS_IS_SILERO = False
_http: httpx.AsyncClient | None = None


async def synth_ogg(text: str) -> bytes | None:
    """Фраза → Ogg Opus. b"" — произносить нечего, None — сервис синтеза недоступен."""
    text = clean_for_speech(text)
    if not re.search(r"[0-9A-Za-zА-Яа-яЁё]", text):
        return b""
    body = {"model": TTS_MODEL, "input": text, "voice": TTS_VOICE, "response_format": "opus"}
    if abs(TTS_SPEED - 1.0) > 0.01:
        body["speed"] = TTS_SPEED
    if TTS_IS_SILERO and (lex := _local_lexicon()):
        body["lexicon"] = lex
    headers = {"Authorization": f"Bearer {TTS_KEY}"} if TTS_KEY else {}
    try:
        r = await _http.post(f"{TTS_URL}/audio/speech", json=body, headers=headers)
    except httpx.HTTPError as exc:
        print(f"[voice] TTS недоступен: {exc!r}", file=sys.stderr, flush=True)
        return None
    if r.status_code != 200:
        print(f"[voice] TTS ответил {r.status_code}: {r.text[:200]}", file=sys.stderr, flush=True)
        return None
    return r.content


# --- HTTP ----------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app):
    global _http, TTS_IS_SILERO
    _http = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=5))
    if TTS_URL:
        TTS_IS_SILERO = await _detect_silero()
        kind = "silero-tts-service" if TTS_IS_SILERO else "OpenAI-совместимый"
        print(f"[voice] TTS: {TTS_URL} ({kind}, из {TTS_SOURCE}), голос {TTS_VOICE}", flush=True)
    else:
        why = "TTS_URL=off" if TTS_SOURCE == "off" else "TTS_URL не задан и нет client.env"
        print(f"[voice] {why} — озвучивает браузер", flush=True)
    print(f"[voice] http://{HOST}:{PORT}", flush=True)
    yield
    await _http.aclose()


app = FastAPI(title="chuyko", docs_url=None, redoc_url=None, lifespan=lifespan)


def check_auth(request: Request) -> None:
    got = request.headers.get("authorization", "")
    if not hmac.compare_digest(got.encode(), f"Bearer {VOICE_TOKEN}".encode()):
        raise HTTPException(401, "bad token")


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
    return {"ok": True, "tts": "server" if TTS_URL else "browser", "agent": agent_label()}


@app.post("/api/tts")
async def tts(request: Request):
    check_auth(request)
    body = await request.json()
    text = str(body.get("text", ""))[:2000]
    if not TTS_URL:
        return Response(status_code=204)
    data = await synth_ogg(text)
    if data is None:                     # страница прочитает фразу голосом браузера
        return Response(status_code=503)
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
