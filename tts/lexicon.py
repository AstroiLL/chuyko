#!/usr/bin/env python3
"""Управление словарём произношения Silero TTS (lexicon.json) — часть Chuyko.

    lexicon.py add "GitHub" "гитх+аб"      добавить/заменить правило
    lexicon.py list [фильтр]               показать словарь
    lexicon.py remove "GitHub"             удалить правило
    lexicon.py test "текст с GitHub"       показать, что реально уйдёт в синтез
    lexicon.py say "текст"                 синтез в файл для прослушивания

Свои слова пишутся в lexicon.local.json (не попадает в git), общий словарь —
lexicon.json; `--base` заставляет add/remove править общий. Свой главнее общего.
Знак `+` ставится ПЕРЕД ударной гласной: `сомкн+уты`, `г+ермес`.
Ключи сравниваются без учёта регистра; правило применяется по границам слова,
поэтому «Git» не сломает «GitHub» (длинные ключи проверяются первыми).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent
BASE_PATH = BASE / "lexicon.json"                       # общий словарь (в репозитории)
LOCAL_PATH = Path(__import__("os").environ.get("SILERO_LEXICON", BASE / "lexicon.local.json"))
PATH = LOCAL_PATH                                        # add/remove пишут сюда; --base — в общий


def load() -> dict:
    if not PATH.exists():
        return {"words": {}}
    data = json.loads(PATH.read_text(encoding="utf-8"))
    data.setdefault("words", {})
    return data


def save(data: dict) -> None:
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cmd_add(word: str, pron: str) -> int:
    data = load()
    old = data["words"].get(word)
    data["words"][word] = pron
    save(data)
    print(f"{'изменено' if old else 'добавлено'}: {word} -> {pron}"
          + (f"  (было: {old})" if old else ""))
    return 0


def cmd_remove(word: str) -> int:
    data = load()
    for key in list(data["words"]):
        if key.lower() == word.lower():
            removed = data["words"].pop(key)
            save(data)
            print(f"удалено: {key} -> {removed}")
            return 0
    print(f"не найдено: {word}", file=sys.stderr)
    return 1


def cmd_list(pattern: str | None) -> int:
    sys.path.insert(0, str(BASE))
    from silero_tts import load_lexicon
    words = load_lexicon()
    items = sorted(words.items(), key=lambda kv: kv[0].lower())
    if pattern:
        low = pattern.lower()
        items = [(k, v) for k, v in items if low in k.lower() or low in v.lower()]
    if not items:
        print("пусто")
        return 0
    width = max(len(k) for k, _ in items)
    for key, value in items:
        print(f"{key.ljust(width)}  ->  {value}")
    print(f"\nвсего: {len(items)}   (общий {BASE_PATH.name} + свой {LOCAL_PATH.name})")
    return 0


def _processed(text: str) -> str:
    sys.path.insert(0, str(BASE))
    from silero_tts import apply_lexicon, load_lexicon
    from translit import convert_text
    return convert_text(apply_lexicon(text, load_lexicon()))


def cmd_test(text: str) -> int:
    print("исходный :", text)
    print("в синтез :", _processed(text))
    return 0


def cmd_say(text: str, out: str | None) -> int:
    out = out or "/tmp/lexicon_say.ogg"
    print("в синтез :", _processed(text))
    subprocess.run([str(BASE / "say.sh"), "-", out], input=text.encode(), check=True)
    print("файл     :", out)
    return 0


def main() -> int:
    global PATH
    args = sys.argv[1:]
    if "--base" in args:
        args.remove("--base")
        PATH = BASE_PATH
    if not args:
        print(__doc__)
        return 1
    cmd, rest = args[0], args[1:]
    if cmd == "add" and len(rest) == 2:
        return cmd_add(rest[0], rest[1])
    if cmd == "remove" and len(rest) == 1:
        return cmd_remove(rest[0])
    if cmd == "list":
        return cmd_list(rest[0] if rest else None)
    if cmd == "test" and rest:
        return cmd_test(" ".join(rest))
    if cmd == "say" and rest:
        return cmd_say(rest[0], rest[1] if len(rest) > 1 else None)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
