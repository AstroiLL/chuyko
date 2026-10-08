# Установка Чуйко (инструкция для ИИ-агента)

Цель: по HTTPS-адресу открывается страница Чуйко. Пользователь говорит с телефона,
ты (агент) отвечаешь, ответ звучит голосом Silero.

Делай шаги по порядку и проверяй результат каждого. Не печатай токены и ключи в чат пользователю,
если он сам не попросил. Токен `VOICE_TOKEN` передай один раз, в конце, приватно.

## 0. Что нужно

| Что | Проверка |
|---|---|
| Linux с systemd, ~1.5 ГБ диска, 2+ ГБ RAM | `df -h ~`, `free -h` |
| Python 3.10+ и модуль venv **или** [uv](https://docs.astral.sh/uv/) | `python3 -m venv --help` / `uv --version` |
| ffmpeg | `ffmpeg -version` |
| git | `git --version` |
| Агент с OpenAI-совместимым `POST /v1/chat/completions` (stream) | см. шаг 1 |
| Домен (или поддомен) на этот сервер + reverse proxy с HTTPS | см. шаг 5 |

GPU не нужен: Silero работает на CPU, около 0.2 с на фразу.

## 1. Подключить агента

**Hermes Agent.** Нужен включённый API Server. Если в `~/.hermes/.env` нет `API_SERVER_ENABLED=true`,
добавь его и `API_SERVER_KEY=<случайная строка>`, затем перезапусти gateway Hermes.
Если меняешь собственный gateway, предупреди пользователя: разговор прервётся.
Документация: https://hermes-agent.nousresearch.com/docs (раздел API Server).

Проверка (по умолчанию порт 8642):
```sh
curl -s http://127.0.0.1:8642/v1/models -H "Authorization: Bearer $API_SERVER_KEY"
```

**Другой агент или LLM.** Подойдёт любой OpenAI-совместимый endpoint со стримингом.
Понадобятся URL (без `/v1`), ключ и имя модели.

Чуйко передаёт заголовок `X-Hermes-Session-Id: voice-…`. Hermes по нему ведёт одну сессию на телефон,
другие серверы этот заголовок просто игнорируют.

## 2. Скачать и установить

```sh
cd ~ && git clone <URL этого репозитория> chuyko && cd chuyko
./install.sh            # venv, torch CPU, зависимости, .env с новым VOICE_TOKEN, модель Silero (~140 МБ)
```
Если системный Python старше 3.10: `PYTHON=python3.11 ./install.sh`.

## 3. Настроить `.env`

Открой `.env` (права 600) и проверь:

- `AGENT_URL`: адрес агента, по умолчанию `http://127.0.0.1:8642` (Hermes).
- `AGENT_KEY`: ключ агента. Для Hermes можно оставить пустым, ключ возьмётся из `~/.hermes/.env`.
- `AGENT_MODEL`: для Hermes `hermes-agent`, для другого сервера имя его модели.
- `VOICE_HOST=127.0.0.1`, если перед сервисом стоит reverse proxy (рекомендуется).
  `0.0.0.0` нужен, только если пользователь хочет доступ напрямую из LAN.
- `SILERO_VOICE`: `eugene` (мужской, по умолчанию), `aidar`, `xenia`, `kseniya` или `baya`.
- `VOICE_JOURNAL_DIR`: папка для дневника разговоров (например в Obsidian vault). Пусто — дневник выключен.
- `OPENROUTER_API_KEY`: нужен только для генерации фонов, необязательно.

Имя, характер и правила ответа голосом задаются в `voice_prompt.md`. Файл читается при каждом запросе,
перезапуск не нужен.

## 4. Запустить как сервис

```sh
mkdir -p ~/.config/systemd/user
sed "s|@DIR@|$PWD|g" deploy/chuyko.service > ~/.config/systemd/user/chuyko.service
systemctl --user daemon-reload && systemctl --user enable --now chuyko
loginctl enable-linger "$USER"     # чтобы сервис жил без входа пользователя (может понадобиться sudo)
systemctl --user status chuyko --no-pager
```

Проверка (подставь токен из `.env`):
```sh
T=$(grep ^VOICE_TOKEN= .env | cut -d= -f2-)
curl -s http://127.0.0.1:7460/api/ping -H "Authorization: Bearer $T"           # {"ok":true,...}
curl -s -o /tmp/chuyko.ogg -w '%{http_code}\n' http://127.0.0.1:7460/api/tts \
     -H "Authorization: Bearer $T" -H 'Content-Type: application/json' -d '{"text":"Привет, я Чуйко"}'   # 200
curl -sN http://127.0.0.1:7460/api/chat -H "Authorization: Bearer $T" \
     -H 'Content-Type: application/json' -d '{"text":"Ты кто?","session_id":"voice-install-test"}'
```
Последняя команда должна вернуть строки `data: {"delta": ...}`, а в конце `{"done": true}`.
Если пришёл `{"error": ...}`, проблема в шаге 1: адрес или ключ агента.

## 5. HTTPS (обязательно)

Без HTTPS браузер не даст доступ к микрофону. Нужен домен, который указывает на сервер,
и reverse proxy. Если уже стоит Caddy, nginx или Traefik, используй его. Если нет, проще всего Caddy:
пример в `deploy/Caddyfile.example`.

Важно: прокси **не должен буферизовать** ответ `/api/chat`, это SSE-стрим.
В Caddy для этого `flush_interval -1`, в nginx `proxy_buffering off;`.

Проверка: `curl -sI https://<домен>/` отвечает `200`.
Конфиг прокси, DNS и проброс портов на роутере затрагивают чужую инфраструктуру,
поэтому согласуй их с пользователем.

## 6. Отдать пользователю

Сообщи пользователю:
- адрес `https://<домен>/`;
- токен приватно. Можно дать ссылку `https://<домен>/?t=<VOICE_TOKEN>`: страница запомнит токен и уберёт его из адреса;
- на телефоне открывать в **Chrome** (распознавание речи работает там), меню → «Добавить на главный экран».

## Необязательно

**Фон страницы.** Картинки лежат в `static/bg/images/`, показывается та, на которую ссылается `static/bg/current.jpg`.
```sh
.venv/bin/python set_bg.py gen "calm alpine lake, snowy mountains, sunny, photorealistic"   # нужен OPENROUTER_API_KEY
.venv/bin/python set_bg.py add ~/photo.jpg      # своя картинка (ужимается до 1080 px)
.venv/bin/python set_bg.py list            # список; переключить: set_bg.py use <имя>
```
Сервер сам подсказывает агенту эти команды в голосовом промпте, поэтому пользователь может попросить голосом.

**Словарь ударений.** Файл `tts/lexicon.json`, знак `+` ставится перед ударной гласной:
```sh
.venv/bin/python tts/lexicon.py add "Чуйко" "ч+уйко"
.venv/bin/python tts/lexicon.py list
.venv/bin/python tts/lexicon.py test "Я Чуйко"     # что реально уйдёт в синтез
.venv/bin/python tts/lexicon.py remove "слово"
```
Применяется без перезапуска.

**Тот же голос в Telegram и других мессенджерах Hermes.** `tts/say.sh` подходит как command-провайдер TTS:
```sh
hermes config set tts.providers.silero.type command --force
hermes config set tts.providers.silero.command "$PWD/tts/say.sh {text_path} {output_path} {voice}" --force
hermes config set tts.providers.silero.voice eugene --force
hermes config set tts.providers.silero.voice_compatible true --force
hermes config set tts.provider silero
```
Затем перезапусти gateway Hermes.

## Если не работает

| Симптом | Причина / решение |
|---|---|
| Сервис не стартует, `AGENT_KEY не задан` | заполни `AGENT_KEY` в `.env` |
| `/api/chat` → `агент ответил 401` | неверный ключ агента |
| `/api/chat` → `нет связи с агентом` | агент не запущен или неверный `AGENT_URL` |
| Ответ приходит целиком в конце, а не по словам | прокси буферизует SSE, см. шаг 5 |
| Кнопка микрофона не реагирует | страница открыта не по HTTPS или не в Chrome |
| Английские слова не озвучиваются | добавь их в словарь (`tts/lexicon.py add`) |
| Логи | `journalctl --user -u chuyko -n 50 --no-pager` |
