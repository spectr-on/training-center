#!/usr/bin/env python3
"""Проверка загрузки тренировки с весом в Garmin Connect.

ВНИМАНИЕ: в отличие от двух других проверок, этот скрипт СОЗДАЁТ в Garmin Connect
одну новую тренировку «ТЦ тест веса». Ничего существующего он не меняет и не удаляет.
Созданную тестовую тренировку потом можно удалить руками.

Как работает: берёт образец (тренировка, которую ты создал руками и где вес показывается
правильно), копирует её структуру, меняет название и (по желанию) вес в килограммах
и загружает как новую тренировку.

Запуск:
  python3 test_workout_upload.py <папка_токена> --id <номер_ручной_тренировки> [--kg 62.5]
"""
import argparse, copy, inspect, json, sys
from pathlib import Path

STRIP_KEYS = {"workoutId", "ownerId", "createdDate", "updatedDate", "stepId"}


def strip_ids(node):
    if isinstance(node, dict):
        return {k: strip_ids(v) for k, v in node.items() if k not in STRIP_KEYS}
    if isinstance(node, list):
        return [strip_ids(v) for v in node]
    return node


def set_first_weight(node, kg):
    """Ставит вес (в кг, как есть) в первый шаг, где вес уже задан. Возвращает True, если нашёл."""
    if isinstance(node, dict):
        if node.get("weightValue") not in (None, 0):
            node["weightValue"] = float(kg)
            return True
        return any(set_first_weight(v, kg) for v in node.values())
    if isinstance(node, list):
        return any(set_first_weight(v, kg) for v in node)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("token_dir")
    ap.add_argument("--id", type=int, required=True)
    ap.add_argument("--kg", type=float, default=None)
    a = ap.parse_args()

    from garminconnect import Garmin
    api = Garmin()
    api.login(str(Path(a.token_dir).expanduser().resolve()))
    print("Вход выполнен.")

    src = api.get_workout_by_id(a.id)
    new = strip_ids(copy.deepcopy(src))
    new["workoutName"] = "ТЦ тест веса"
    if a.kg is not None:
        if not set_first_weight(new, a.kg):
            print("В образце нет шага с весом — менять нечего.")
        else:
            print(f"Вес первого шага с весом поставлен: {a.kg} кг (значение {a.kg}, без умножения).")

    print("Сигнатура upload_workout:", inspect.signature(api.upload_workout))
    try:
        res = api.upload_workout(new)
    except TypeError:
        res = api.upload_workout(json.dumps(new))
    print("Ответ Garmin:")
    print(json.dumps(res, ensure_ascii=False, indent=1)[:1500] if not isinstance(res, str) else res[:1500])
    Path(__file__).with_name("upload_result.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("\nГотово. Найди в Garmin Connect «ТЦ тест веса», отправь на часы и проверь вес на часах.")


if __name__ == "__main__":
    main()
