#!/bin/sh
# Обёртка Silero TTS для агента (например Hermes: tts.providers.<имя>, type: command).
#   $1 — файл с текстом ("-" = stdin), $2 — выходной файл, $3 — голос (опц.)
# Формат вывода определяется по расширению $2; синтез всегда идёт в wav,
# затем ffmpeg приводит к нужному контейнеру.
# Темп задаётся переменной SILERO_SPEED (по умолчанию см. silero_tts.py).
set -eu

DIR=$(cd "$(dirname "$0")" && pwd)
PY=${CHUYKO_PYTHON:-$DIR/../.venv/bin/python}
TEXT_FILE=$1
OUT=$2
VOICE=${3:-}
[ -n "$VOICE" ] || VOICE=${SILERO_VOICE:-eugene}

TMP=$(mktemp "${TMPDIR:-/tmp}/silero_XXXXXX.wav")
STDIN_FILE=""
trap 'rm -f "$TMP" ${STDIN_FILE:+"$STDIN_FILE"}' EXIT

if [ "$TEXT_FILE" = "-" ]; then
    STDIN_FILE=$(mktemp "${TMPDIR:-/tmp}/silero_XXXXXX.txt")
    cat > "$STDIN_FILE"
    TEXT_FILE=$STDIN_FILE
fi

"$PY" "$DIR/silero_tts.py" \
    --model "${SILERO_MODEL:-v5_5_ru}" \
    --text-file "$TEXT_FILE" \
    --out "$TMP" \
    --voice "$VOICE" >&2

case "$OUT" in
    *.wav) cp "$TMP" "$OUT" ;;
    *.ogg|*.opus) ffmpeg -y -loglevel error -i "$TMP" -c:a libopus -b:a 96k "$OUT" ;;
    *.mp3) ffmpeg -y -loglevel error -i "$TMP" -c:a libmp3lame -b:a 128k "$OUT" ;;
    *) ffmpeg -y -loglevel error -i "$TMP" "$OUT" ;;
esac
