# Проверенные факты о Garmin Connect (2026-10-12)

## Вес в тренировке
- `weightValue` передаётся в КИЛОГРАММАХ как есть: 62.5 кг -> `62.5`.
- Единица: `{"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}` — Garmin сам делает пересчёт.
- Старый перенос умножал на 1000 (60000.0) -> Connect показывал «60 000,00 кг», часы вес не показывали.
- Проверено: загрузка через `upload_workout`, вес 62.5 виден в Connect и на Instinct 3.
- В ручной тренировке шаги без веса: `weightValue = None`, `weightUnit = None`.

## Журнал образа жизни (`get_lifestyle_logging_data(date)`) — проверено 25.10.2026
- Ответ: `calendarDate`, `dailyLogsReport[]`, `completionStats[]` (по дням: `totalTracking`, `completedTracking`).
- Пункт: `behaviourId`, `name`, `category` (`LIFESTYLE` — встроенные, `CUSTOM` — свои), `measurementType` (`QUANTITY` — с числом, `NONE` — да/нет), `logStatus` (`YES` / `NO` / нет ключа = не отмечено), `details[]`.
- Кальян (свой, id 868349): `QUANTITY`, число в `details[0].amount`, без `subTypeName`. До октября был да/нет без числа.
- Alcohol (встроенный, id 1): `QUANTITY`, `details[]` с `subTypeName` BEER / WINE / SPIRIT / OTHER и `amount`; у неотмеченного дня `details` без `amount`.
- Healthy Meals (id 4) и Болезнь (свой, id 855576): `NONE`, только да/нет.
- Дни без отметок: пункты есть, `logStatus` нет.
- Пример (24.10): Кальян 3, Alcohol BEER 1, Healthy Meals YES, Болезнь NO.
- Быстрая проверка из терминала HA: см. «Как быстро проверить данные Garmin API» в `docs/HANDOFF.md`.

## Методы библиотеки (0.3.17)
upload_workout(dict|list|str), update_workout, get_workouts, get_workout_by_id, schedule_workout,
unschedule_workout, push_workout_to_device, delete_workout.
