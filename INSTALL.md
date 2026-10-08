# Установка Чуйко (инструкция для ИИ-агента)

Цель: по HTTPS-адресу открывается страница Чуйко. Пользователь говорит с телефона,
ты (агент) отвечаешь, ответ звучит голосом.

Делай шаги по порядку и проверяй результат каждого. Не печатай токены и ключи в чат пользователю,
если он сам не попросил. Токен `VOICE_TOKEN` передай один раз, в конце, приватно.

## 0. Что нужно

| Что | Проверка |
|---|---|
| Linux с systemd, ~150 МБ диска, ~100 МБ RAM | `df -h ~`, `free -h` |
| Python 3.10+ и модуль venv **или** [uv](https://docs.astral.sh/uv/) | `python3 -m venv --help` / `uv --version` |
| git | `git --version` |
| Агент с OpenAI-совместимым `POST /v1/chat/completions` (stream) | см. шаг 1 |
| Синтез речи: сервис с OpenAI-совместимым `/v1/audio/speech` или голос браузера | см. шаг 4 |
| Домен (или поддомен) на этот сервер + reverse proxy с HTTPS | см. шаг 6 |

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

Если репозиторий публичный:
```sh
cd ~ && git clone https://github.com/AstroiLL/chuyko.git chuyko && cd chuyko
```

Если репозиторий **приватный**, пользователь даст тебе файл ключа только для чтения (deploy key):
```sh
install -m 600 <полученный файл ключа> ~/.ssh/chuyko_deploy
cd ~ && GIT_SSH_COMMAND='ssh -i ~/.ssh/chuyko_deploy -o IdentitiesOnly=yes' \
  git clone git@github.com:AstroiLL/chuyko.git chuyko && cd chuyko
git config core.sshCommand 'ssh -i ~/.ssh/chuyko_deploy -o IdentitiesOnly=yes'   # чтобы работал git pull
```
Ключ даёт только чтение этого репозитория. Не коммить его и никуда не копируй.

Затем:
```sh
./install.sh            # venv, зависимости (~100 МБ), .env с новым VOICE_TOKEN
```
Если системный Python старше 3.10: `PYTHON=python3.11 ./install.sh`.

## 3. Настроить `.env`

Открой `.env` (права 600) и проверь:

- `AGENT_URL`: адрес агента, по умолчанию `http://127.0.0.1:8642` (Hermes).
- `AGENT_KEY`: ключ агента. Для Hermes можно оставить пустым, ключ возьмётся из `~/.hermes/.env`.
- `AGENT_MODEL`: для Hermes `hermes-agent`, для другого сервера имя его модели.
- `VOICE_HOST=127.0.0.1`, если перед сервисом стоит reverse proxy (рекомендуется).
  `0.0.0.0` нужен, только если пользователь хочет доступ напрямую из LAN.
- `VOICE_JOURNAL_DIR`: папка для дневника разговоров (например в Obsidian vault). Пусто — дневник выключен.
- `OPENROUTER_API_KEY`: нужен только для генерации фонов, необязательно.

Имя, характер и правила ответа голосом задаются в `voice_prompt.md`. Файл читается при каждом запросе,
перезапуск не нужен.

## 4. Голос: выбрать синтез речи

Сам Чуйко речь не синтезирует. Он отправляет фразы сервису с OpenAI-совместимым API
(`POST <TTS_URL>/audio/speech`), а если такого нет, ответы читает голос браузера телефона.
Сначала выясни, что уже есть, потом **спроси пользователя**. Не выбирай молча, особенно если у агента
уже настроен платный TTS.

### 4.1. Что уже есть

```sh
curl -s http://127.0.0.1:7470/health        # silero-tts-service на этом сервере
grep -A4 '^tts:' ~/.hermes/config.yaml       # чем озвучивает агент (Hermes)
```
- Ответ `{"ok":true,"service":"silero-tts-service",...}` значит, что общий сервис Silero уже стоит.
  Его мог поставить и другой пользователь этого сервера.
- Спроси пользователя, нет ли [silero-tts-service](https://github.com/AstroiLL/silero-tts-service)
  на другой машине в сети, например на мощном сервере: `curl -s http://<адрес>:7470/health`.
- `tts.provider` у Hermes:
  - `openai`: если `tts.openai.base_url` указывает на `:7470`, это silero-tts-service, иначе платный OpenAI;
  - `elevenlabs`, `minimax`, `gemini`, `xai`, `mistral`: платные облачные;
  - `edge`: бесплатный облачный Microsoft;
  - `piper`, `neutts`, `kittentts`: локальные внутри агента;
  - имя из `tts.providers.*` с `type: command`: своя команда, посмотри, что она вызывает.

### 4.2. Спросить пользователя

Предложи подходящие варианты:

| Вариант | Когда | Цена | Что сделать |
|---|---|---|---|
| **A. Существующий silero-tts-service** | нашёлся в 4.1 | бесплатно | получить токен: на машине с сервисом `tokens.py add chuyko-<пользователь>` (делает его владелец) |
| **B. Поставить silero-tts-service** | сервиса нет; ~1.2 ГБ диска и ~0.8 ГБ RAM один раз на сервер или на сеть | бесплатно | по [его INSTALL.md](https://github.com/AstroiLL/silero-tts-service/blob/main/INSTALL.md). Если на сервере несколько пользователей, ставь в режиме «системный сервис» |
| **C. Платный TTS агента**, если он OpenAI-совместимый | пользователь хочет тот же голос, что у агента | по тарифу провайдера (OpenAI `tts-1`: около $15 за 1 млн символов) | взять ключ и модель из настроек агента |
| **D. Голос браузера** | без установки; качество зависит от телефона | бесплатно | оставить `TTS_URL` пустым |

ElevenLabs, Gemini, MiniMax и другие провайдеры без OpenAI-совместимого API напрямую не подходят.
Для них предложи A, B или D.

Если выбран A или B, а агент озвучивает в мессенджерах чем-то другим, предложи подключить к тому же
сервису и агента (шаг 6 INSTALL.md silero-tts-service): голос будет один и тот же. Если у агента
платный TTS, менять его можно только с согласия пользователя.

### 4.3. Записать в `.env`

```sh
# A, B: silero-tts-service (токен — свой для Чуйко, не общий с агентом)
TTS_URL=http://127.0.0.1:7470/v1        # или http://<хост в сети>:7470/v1
TTS_KEY=<токен stts_…>
TTS_VOICE=eugene                         # eugene, aidar, xenia, kseniya, baya

# C: OpenAI. Ключ Hermes хранит в ~/.hermes/.env (VOICE_TOOLS_OPENAI_KEY или OPENAI_API_KEY)
#    или в tts.openai.api_key в config.yaml
TTS_URL=https://api.openai.com/v1
TTS_KEY=<ключ>
TTS_MODEL=gpt-4o-mini-tts
TTS_VOICE=alloy

# D: голос браузера
TTS_URL=
```

Если сервис синтеза временно недоступен (например, мощный сервер выключен), страница читает фразы
голосом браузера, и разговор не прерывается.

Свои ударения для Чуйко можно положить в `lexicon.local.json` рядом с `server.py`
(`{"words": {"Чуйко": "ч+уйко"}}`). Они передаются с каждой фразой, но только в silero-tts-service.

## 5. Запустить как сервис

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
     -H "Authorization: Bearer $T" -H 'Content-Type: application/json' -d '{"text":"Привет, я Чуйко"}'   # 200 (голос браузера: 204)
curl -sN http://127.0.0.1:7460/api/chat -H "Authorization: Bearer $T" \
     -H 'Content-Type: application/json' -d '{"text":"Ты кто?","session_id":"voice-install-test"}'
```
Последняя команда должна вернуть строки `data: {"delta": ...}`, а в конце `{"done": true}`.
Если пришёл `{"error": ...}`, проблема в шаге 1: адрес или ключ агента.
`/api/tts` → `503`: сервис синтеза недоступен или не принял токен (шаг 4, журнал Чуйко).

## 6. HTTPS (обязательно)

Без HTTPS браузер не даст доступ к микрофону. Нужен домен, который указывает на сервер,
и reverse proxy. Если уже стоит Caddy, nginx или Traefik, используй его. Если нет, проще всего Caddy:
пример в `deploy/Caddyfile.example`.

Важно: прокси **не должен буферизовать** ответ `/api/chat`, это SSE-стрим.
В Caddy для этого `flush_interval -1`, в nginx `proxy_buffering off;`.

Проверка: `curl -sI https://<домен>/` отвечает `200`.
Конфиг прокси, DNS и проброс портов на роутере затрагивают чужую инфраструктуру,
поэтому согласуй их с пользователем.

## 7. Отдать пользователю

Сообщи пользователю:
- адрес `https://<домен>/`;
- токен приватно. Можно дать ссылку `https://<домен>/?t=<VOICE_TOKEN>`: страница запомнит токен и уберёт его из адреса;
- на телефоне открывать в **Chrome** (распознавание речи работает там), меню → «Добавить на главный экран».

## Обновление

```sh
cd ~/chuyko && git pull && ./install.sh && systemctl --user restart chuyko
```
`install.sh` можно запускать повторно: `.env` он не трогает.

**Обновление со старой версии, где Silero был внутри Чуйко** (папка `tts/`): после `git pull` синтеза
в Чуйко больше нет. Пройди шаг 4 (обычно B: поставить silero-tts-service и перенести свои слова из
старого `tts/lexicon.local.json` в его `lexicon.local.json`) и впиши `TTS_URL`/`TTS_KEY`. Переменные
`SILERO_VOICE`/`SILERO_SPEED` ещё читаются как запасные, но лучше заменить их на `TTS_VOICE`/`TTS_SPEED`.
Если Hermes озвучивал Telegram через старый `tts/say.sh`, переключи его на сервис (шаг 6 его INSTALL.md)
**до** обновления Чуйко, иначе голосовые в мессенджере пропадут.

Свои настройки держи в файлах, которые не попадают в git. Тогда `git pull` их не затрёт:
- `.env`: всё про этот сервер;
- `voice_prompt.local.md`: свой промпт (если есть, используется вместо `voice_prompt.md`);
- `lexicon.local.json`: свои ударения для Чуйко (только с silero-tts-service).

## Необязательно

**Фон страницы.** Картинки лежат в `static/bg/images/`, показывается та, на которую ссылается `static/bg/current.jpg`.
```sh
.venv/bin/python set_bg.py gen "calm alpine lake, snowy mountains, sunny, photorealistic"   # нужен OPENROUTER_API_KEY
.venv/bin/python set_bg.py add ~/photo.jpg      # своя картинка (ужимается до 1080 px)
.venv/bin/python set_bg.py list            # список; переключить: set_bg.py use <имя>
```
Сервер сам подсказывает агенту эти команды в голосовом промпте, поэтому пользователь может попросить голосом.

**Словарь ударений и голос агента в Telegram** настраиваются в
[silero-tts-service](https://github.com/AstroiLL/silero-tts-service) (`lexicon.py`, INSTALL.md шаг 6).

## Если не работает

| Симптом | Причина / решение |
|---|---|
| Сервис не стартует, `AGENT_KEY не задан` | заполни `AGENT_KEY` в `.env` |
| `/api/chat` → `агент ответил 401` | неверный ключ агента |
| `/api/chat` → `нет связи с агентом` | агент не запущен или неверный `AGENT_URL` |
| Ответ приходит целиком в конце, а не по словам | прокси буферизует SSE, см. шаг 6 |
| Кнопка микрофона не реагирует | страница открыта не по HTTPS или не в Chrome |
| Говорит голос браузера, а не Silero | `TTS_URL` пуст или сервис синтеза недоступен: `journalctl --user -u chuyko`, строки `TTS` |
| Английские слова звучат криво | словарь silero-tts-service (`lexicon.py add`) или свой `lexicon.local.json` |
| Логи | `journalctl --user -u chuyko -n 50 --no-pager` |
