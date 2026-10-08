#!/usr/bin/env python3
"""Silero TTS (ru) — локальный синтез речи (часть Chuyko).

Модели и голоса — проект Silero Models: https://github.com/snakers4/silero-models
(© Silero Team, лицензия моделей — см. их репозиторий).

    silero_tts.py --text-file IN.txt --out OUT.wav [--voice eugene] [--speed 1.5]
    silero_tts.py --text "привет" --out OUT.wav

Голоса v5_5_ru: aidar, baya, kseniya, xenia, eugene (мужские — aidar, eugene).
Модель кэшируется в MODEL_DIR; интернет нужен только при первом запуске.

Перед синтезом текст прогоняется через словарь произношения lexicon.json
(ударения вида `сомкн+уты`, транслит английских терминов). Управление словарём —
`lexicon.py`.

Темп НЕ задаётся через SSML: парсер SSML в Silero падает на латинице
(ValueError: Failed to parse SSML). Ускорение делает ffmpeg atempo —
он сохраняет высоту тона и принимает любой множитель.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
MODEL_DIR = Path(os.environ.get("SILERO_MODEL_DIR", BASE_DIR / "models"))
LEXICON_PATH = Path(os.environ.get("SILERO_LEXICON", BASE_DIR / "lexicon.json"))
DEFAULT_MODEL = os.environ.get("SILERO_MODEL", "v5_5_ru")
DEFAULT_VOICE = os.environ.get("SILERO_VOICE", "eugene")
DEFAULT_SPEED = float(os.environ.get("SILERO_SPEED", "1.0"))
SAMPLE_RATE = int(os.environ.get("SILERO_SAMPLE_RATE", "48000"))
VOICES = {"aidar", "baya", "kseniya", "xenia", "eugene"}


# --- словарь произношения -------------------------------------------------

def load_lexicon() -> dict:
    if not LEXICON_PATH.exists():
        return {}
    import json
    try:
        data = json.loads(LEXICON_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print(f"[silero] lexicon ignored ({exc})", file=sys.stderr)
        return {}
    return data.get("words", data) if isinstance(data, dict) else {}


def apply_lexicon(text: str, lexicon: dict) -> str:
    """Заменить словарные слова. Длинные ключи первыми — чтобы фразы били слова.

    Границы слова учитывают кириллицу и латиницу; сравнение регистронезависимое.
    """
    if not lexicon:
        return text
    for src in sorted(lexicon, key=len, reverse=True):
        dst = lexicon[src]
        pattern = re.compile(
            r"(?<![0-9A-Za-zА-Яа-яЁё]){}(?![0-9A-Za-zА-Яа-яЁё])".format(re.escape(src)),
            re.IGNORECASE,
        )
        text = pattern.sub(lambda _m, d=dst: d, text)
    return text


# --- синтез ---------------------------------------------------------------

def ensure_model(name: str) -> Path:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    path = MODEL_DIR / f"{name}.pt"
    if not path.exists():
        import torch
        url = f"https://models.silero.ai/models/tts/ru/{name}.pt"
        print(f"[silero] downloading {url} -> {path}", file=sys.stderr)
        torch.hub.download_url_to_file(url, str(path), progress=False)
    return path


def atempo_chain(speed: float) -> str:
    """ffmpeg atempo принимает 0.5..2.0 за фильтр — большее собираем цепочкой."""
    filters = []
    remaining = speed
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.4f}")
    return ",".join(filters)


def synth(text: str, out_path: str, voice: str, speed: float, model_name: str) -> None:
    import torch
    torch.set_num_threads(max(1, (os.cpu_count() or 4) // 2))
    model = torch.package.PackageImporter(str(ensure_model(model_name))).load_pickle(
        "tts_models", "model")
    model.to(torch.device("cpu"))

    audio = model.apply_tts(text=text, speaker=voice, sample_rate=SAMPLE_RATE)

    import soundfile as sf
    if abs(speed - 1.0) < 0.01:
        sf.write(out_path, audio.numpy(), SAMPLE_RATE)
        return

    raw = str(Path(out_path).with_suffix(".raw.wav"))
    sf.write(raw, audio.numpy(), SAMPLE_RATE)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", raw,
             "-filter:a", atempo_chain(speed), out_path],
            check=True,
        )
    finally:
        Path(raw).unlink(missing_ok=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--text")
    p.add_argument("--text-file")
    p.add_argument("--out", required=True)
    p.add_argument("--voice", default=DEFAULT_VOICE)
    p.add_argument("--speed", type=float, default=DEFAULT_SPEED)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--no-lexicon", action="store_true")
    a = p.parse_args()

    if a.text_file:
        text = Path(a.text_file).read_text(encoding="utf-8")
    elif a.text:
        text = a.text
    else:
        text = sys.stdin.read()

    text = text.strip()
    if not text:
        print("[silero] empty text", file=sys.stderr)
        return 1

    if not a.no_lexicon:
        text = apply_lexicon(text, load_lexicon())

    # Всё, что осталось латиницей, модель бы молча выбросила — переписываем.
    from translit import convert_text
    text = convert_text(text)

    voice = a.voice if a.voice in VOICES else DEFAULT_VOICE
    synth(text, a.out, voice, a.speed, a.model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
