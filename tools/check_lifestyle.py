#!/usr/bin/env python3
"""Проверка: отдаёт ли библиотека журнал образа жизни Garmin Connect.

Только читает. Ничего не пишет и не меняет в Garmin.

Запуск (в терминале HA):
    python3 check_lifestyle.py /homeassistant/garmin_token
    python3 check_lifestyle.py /homeassistant/garmin_token --days 30

Что делает: берёт записи журнала образа жизни за последние дни, показывает, какие
пункты нашлись (например, «Алкоголь», «Кальян», «Болезнь»), и сохраняет полный ответ
в файл lifestyle_dump.json рядом со скриптом.
"""
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

args = [a for a in sys.argv[1:]]
days = 14
if "--days" in args:
    i = args.index("--days")
    days = int(args[i + 1])
    del args[i:i + 2]
token_dir = Path(args[0]) if args else Path.home() / ".garminconnect"
token_dir = token_dir.resolve()  # библиотека не принимает пути через симлинк

try:
    from garminconnect import Garmin
except ImportError:
    print("Библиотека не установлена: pip install garminconnect")
    sys.exit(1)

print(f"Папка токена: {token_dir}")
api = Garmin()
try:
    api.login(str(token_dir))
except Exception as e:
    print(f"Вход не удался: {type(e).__name__}: {str(e)[:200]}")
    sys.exit(2)
print("Вход выполнен.")

if not hasattr(api, "get_lifestyle_logging_data"):
    names = [n for n in dir(api) if "lifestyle" in n.lower() or "journal" in n.lower()]
    print("В этой версии библиотеки нет метода get_lifestyle_logging_data.")
    print("Похожие методы:", names or "нет")
    sys.exit(3)


def walk(obj, path=""):
    """Возвращает пары (путь, значение) для всех листьев."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, obj


dump, errors = {}, 0
today = date.today()
for n in range(days):
    d = (today - timedelta(days=n)).isoformat()
    try:
        dump[d] = api.get_lifestyle_logging_data(d)
    except Exception as e:
        errors += 1
        dump[d] = {"_error": f"{type(e).__name__}: {str(e)[:200]}"}
        print(f"  {d}: ОШИБКА {type(e).__name__}: {str(e)[:150]}")
    time.sleep(0.6)

out = Path(__file__).resolve().parent / "lifestyle_dump.json"
out.write_text(json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")

print(f"\nЗапрошено дней: {days}, ошибок: {errors}")
first = next((v for v in dump.values() if isinstance(v, dict) and "_error" not in v), None)
if first is not None:
    print("\nКлючи верхнего уровня ответа:", list(first.keys()))

print("\nПо дням (что отмечено в журнале):")
found_any = False
for d, data in sorted(dump.items()):
    if not isinstance(data, dict) or "_error" in data:
        continue
    leaves = [(p, v) for p, v in walk(data) if v not in (None, "", [], {}, 0, False)]
    names = sorted({str(v) for p, v in leaves
                    if p.lower().endswith(("name", "behaviorname", "label", "displayname"))})
    if names:
        found_any = True
        print(f"  {d}: {', '.join(names)}")
    else:
        print(f"  {d}: пусто")

if first is not None:
    print("\nСырой ответ за первый день (для разбора формата, обрезано до 2500 знаков):")
    print(json.dumps(first, ensure_ascii=False, indent=1)[:2500])

print(f"\nПолный ответ сохранён: {out}")
print("=" * 50)
if errors == days:
    print("ИТОГ: все запросы упали. Метод есть, но Garmin его не отдаёт. Пришли текст ошибок.")
elif found_any:
    print("ИТОГ: журнал образа жизни читается. Пришли вывод целиком.")
else:
    print("ИТОГ: метод работает, но в этих днях отметок нет (или формат ответа другой).")
    print("Отметь в Garmin Connect за сегодня «Кальян» или «Алкоголь», подожди минуту и запусти снова.")
