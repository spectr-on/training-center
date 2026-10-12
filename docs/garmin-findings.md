# Проверенные факты о Garmin Connect (2026-10-12)

## Вес в тренировке
- `weightValue` передаётся в КИЛОГРАММАХ как есть: 62.5 кг -> `62.5`.
- Единица: `{"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}` — Garmin сам делает пересчёт.
- Старый перенос умножал на 1000 (60000.0) -> Connect показывал «60 000,00 кг», часы вес не показывали.
- Проверено: загрузка через `upload_workout`, вес 62.5 виден в Connect и на Instinct 3.
- В ручной тренировке шаги без веса: `weightValue = None`, `weightUnit = None`.

## Журнал образа жизни (`get_lifestyle_logging_data(date)`)
- Ответ: `dailyLogsReport[]`; отмечено, если `logStatus == "YES"`.
- Встроенные пункты: `name` на английском (`Alcohol`, ...), `details[]` (`subTypeName`, `amount`).
- Свои пункты: `category == "CUSTOM"`, `name` как создан («Кальян»).
- Дни без отметок: пункты есть, `logStatus` нет.

## Методы библиотеки (0.3.17)
upload_workout(dict|list|str), update_workout, get_workouts, get_workout_by_id, schedule_workout,
unschedule_workout, push_workout_to_device, delete_workout.
