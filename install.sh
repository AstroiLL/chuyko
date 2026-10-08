#!/bin/sh
# Chuyko — установка: venv + зависимости + .env с токеном + модель Silero.
# Запуск из папки репозитория:  ./install.sh
set -eu
cd "$(dirname "$0")"

command -v ffmpeg >/dev/null || { echo "Нужен ffmpeg (apt install ffmpeg)"; exit 1; }
PY=${PYTHON:-python3}
"$PY" -c 'import sys; assert sys.version_info >= (3,10), sys.version' \
  || { echo "Нужен Python 3.10+ (задай PYTHON=python3.11)"; exit 1; }

[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q torch --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  TOKEN=$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(24))')
  sed -i "s|^VOICE_TOKEN=.*|VOICE_TOKEN=$TOKEN|" .env
  chmod 600 .env
  echo "Создан .env с новым VOICE_TOKEN — проверь AGENT_URL / AGENT_KEY."
fi

# Скачать модель Silero заранее (~140 МБ), чтобы первый запуск был быстрым.
.venv/bin/python -c 'import sys; sys.path.insert(0,"tts"); import silero_tts as s; print("модель:", s.ensure_model(s.DEFAULT_MODEL))'
echo "Готово. Проверка: .venv/bin/python server.py  (порт из .env, по умолчанию 7460)"
