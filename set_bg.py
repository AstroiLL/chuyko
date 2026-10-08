#!/usr/bin/env python3
"""Фоны страницы Chuyko: папка с картинками + ссылка на текущую.

    static/bg/images/        библиотека картинок (jpg/png/webp; рядом можно .txt с описанием)
    static/bg/current.jpg -> images/<имя>      символьная ссылка на показываемый фон

Поменять фон вручную:  cd <папка chuyko>/static/bg && ln -sfn images/<файл> current.jpg
Страница сама подхватит новый фон после ответа или при возврате на вкладку.

    set_bg.py gen "описание"     сгенерировать (OpenRouter), положить в images/ и показать
    set_bg.py add /путь/к/фото   ужать до 1080 px, положить в images/ и показать
    set_bg.py use <имя>          показать картинку из images/ (имя без расширения или с ним)
    set_bg.py list               список картинок, * — текущая

Генерация: модель BG_MODEL (по умолчанию google/gemini-3.1-flash-image), портрет 9:16;
ключ OPENROUTER_API_KEY берётся из окружения, .env рядом со скриптом или ~/.hermes/.env.
"""
from __future__ import annotations

import base64
import datetime as dt
import io
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

BASE = Path(__file__).parent
BG_DIR = BASE / "static" / "bg"
IMAGES = BG_DIR / "images"
CURRENT = BG_DIR / "current.jpg"
MODEL = os.environ.get("BG_MODEL", "google/gemini-3.1-flash-image")
MAX_SHORT = 1080
EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    for env in (BASE / ".env", Path.home() / ".hermes" / ".env"):
        if key or not env.exists():
            continue
        m = re.search(r"^OPENROUTER_API_KEY=(.*)$", env.read_text(encoding="utf-8"), re.M)
        key = m.group(1).strip().strip('"').strip("'") if m else ""
    if not key:
        sys.exit("OPENROUTER_API_KEY не найден")
    return key


def generate(prompt: str) -> bytes:
    body = {
        "model": MODEL,
        "modalities": ["image", "text"],
        "image_config": {"aspect_ratio": "9:16"},
        "messages": [{"role": "user", "content":
                      f"Generate a single image, vertical phone wallpaper, no text, no watermark. {prompt}"}],
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.load(r)
    msg = data["choices"][0]["message"]
    for img in msg.get("images") or []:
        url = img.get("image_url", {}).get("url", "")
        if url.startswith("data:"):
            return base64.b64decode(url.split(",", 1)[1])
        if url.startswith("http"):
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read()
    sys.exit(f"модель не вернула картинку: {str(msg.get('content'))[:300]}")


def slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^\w]+", "-", text.lower(), flags=re.U).strip("-")
    return s[:n].strip("-") or "bg"


def store(raw: bytes, name_hint: str, note: str) -> Path:
    """Ужать и сохранить в images/<дата>-<slug>.jpg, рядом .txt с описанием."""
    IMAGES.mkdir(parents=True, exist_ok=True)
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    scale = MAX_SHORT / min(im.size)
    if scale < 1:
        im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = IMAGES / f"{stamp}-{slug(name_hint)}.jpg"
    im.save(path, "JPEG", quality=85, optimize=True, progressive=True)
    path.with_suffix(".txt").write_text(note, encoding="utf-8")
    return path


def find(name: str) -> Path:
    cand = IMAGES / name
    if cand.is_file():
        return cand
    for ext in EXTS:
        if (IMAGES / f"{name}{ext}").is_file():
            return IMAGES / f"{name}{ext}"
    matches = [p for p in IMAGES.iterdir() if p.suffix.lower() in EXTS and name.lower() in p.name.lower()]
    if len(matches) == 1:
        return matches[0]
    sys.exit(f"не нашёл однозначно «{name}»: " + (", ".join(p.name for p in matches) or "совпадений нет"))


def activate(img: Path) -> None:
    """Переключить ссылку current.jpg атомарно (через временную ссылку + rename)."""
    tmp = BG_DIR / ".current.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(Path("images") / img.name)
    os.replace(tmp, CURRENT)
    print(f"фон: {img.name}")


def current_name() -> str | None:
    return os.readlink(CURRENT).split("/")[-1] if CURRENT.is_symlink() else None


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__); return 1
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "gen" and rest:
        prompt = " ".join(rest)
        activate(store(generate(prompt), prompt, f"gen[{MODEL}]: {prompt}"))
    elif cmd in ("add", "file") and rest:
        src = Path(rest[0]).expanduser()
        activate(store(src.read_bytes(), src.stem, f"file: {src}"))
    elif cmd == "use" and rest:
        activate(find(rest[0]))
    elif cmd == "list":
        cur = current_name()
        for p in sorted(IMAGES.iterdir()):
            if p.suffix.lower() not in EXTS:
                continue
            t = p.with_suffix(".txt")
            note = t.read_text(encoding="utf-8")[:90] if t.exists() else ""
            print(("* " if p.name == cur else "  ") + p.name + (f"  — {note}" if note else ""))
    else:
        print(__doc__); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
