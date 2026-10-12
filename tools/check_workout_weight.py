#!/usr/bin/env python3
"""Проверка: как Garmin Connect хранит вес в шагах тренировки (единица измерения).

Только читает. Ничего не создаёт и не меняет в Garmin.

Шаг 1. Список тренировок (найди нужные номера):
    python3 check_workout_weight.py /homeassistant/garmin_token

Шаг 2. Разбор двух тренировок: той, что ты создал руками в Garmin Connect с весом
(там 60 кг показывается правильно), и той, что создал наш прежний перенос
(там 60 000,00 кг):
    python3 check_workout_weight.py /homeassistant/garmin_token --id 111111 --id 222222

Для каждой тренировки скрипт покажет все поля, где есть слово weight, и сохранит
полный ответ в файл workout_<номер>.json рядом со скриптом.
"""
import json
import sys
import time
from pathlib import Path

args = sys.argv[1:]
ids = []
while "--id" in args:
    i = args.index("--id")
    ids.append(args[i + 1])
    del args[i:i + 2]
token_dir = Path(args[0]) if args else Path.home() / ".garminconnect"
token_dir = token_dir.resolve()  # библиотека не принимает пути через симлинк

try:
    from garminconnect import Garmin
except ImportError:
    print("Библиотека не установлена: pip install garminconnect")
    sys.exit(1)

api = Garmin()
try:
    api.login(str(token_dir))
except Exception as e:
    print(f"Вход не удался: {type(e).__name__}: {str(e)[:200]}")
    sys.exit(2)
print("Вход выполнен.\n")

methods = [n for n in dir(api) if "workout" in n.lower() and not n.startswith("_")]
print("Методы библиотеки про тренировки:", ", ".join(methods) or "нет")
get_list = getattr(api, "get_workouts", None)
get_one = getattr(api, "get_workout_by_id", None)
if get_list is None or get_one is None:
    print("Нет нужных методов (get_workouts / get_workout_by_id). Пришли строку выше целиком.")
    sys.exit(3)


def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, obj


if not ids:
    try:
        items = get_list(0, 30)
    except Exception as e:
        print(f"Список не получен: {type(e).__name__}: {str(e)[:200]}")
        sys.exit(4)
    if isinstance(items, dict):
        items = items.get("workouts") or items.get("items") or []
    print(f"\nТренировок в списке: {len(items)}")
    for w in items:
        print(f"  {w.get('workoutId')}  {w.get('workoutName')}  "
              f"(создана {str(w.get('createdDate') or w.get('updateDate') or '')[:10]})")
    print("\nДальше запусти с --id <номер> для двух тренировок (см. описание в начале файла).")
    sys.exit(0)

for wid in ids:
    print("=" * 60)
    try:
        data = get_one(wid)
    except Exception as e:
        print(f"Тренировка {wid}: ОШИБКА {type(e).__name__}: {str(e)[:200]}")
        continue
    out = Path(__file__).resolve().parent / f"workout_{wid}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Тренировка {wid}: {data.get('workoutName')}  (полный ответ: {out.name})")
    hits = [(p, v) for p, v in walk(data) if "weight" in p.lower()]
    if not hits:
        print("  Полей со словом weight нет.")
    for p, v in hits:
        print(f"  {p} = {v!r}")
    time.sleep(0.6)

print("\nПришли весь этот вывод. Сравню, как записан вес в двух тренировках.")
