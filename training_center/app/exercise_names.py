"""Русские названия упражнений Garmin (коды Garmin -> по-русски).
Названия составлены вручную, не скопированы из Garmin Connect. Неизвестный код показывается
читаемо по-английски со значком ⁇, чтобы его можно было добавить сюда."""

NAMES = {
    "BARBELL_BACK_SQUAT": "Приседания со штангой на спине",
    "BARBELL_FRONT_SQUAT": "Приседания со штангой на груди",
    "SQUAT": "Приседания", "GOBLET_SQUAT": "Гоблет-приседания", "BARBELL_SQUAT": "Приседания со штангой",
    "BARBELL_BENCH_PRESS": "Жим штанги лёжа",
    "DUMBBELL_BENCH_PRESS": "Жим гантелей лёжа",
    "INCLINE_DUMBBELL_BENCH_PRESS": "Жим гантелей на наклонной скамье",
    "INCLINE_BARBELL_BENCH_PRESS": "Жим штанги на наклонной скамье",
    "SEATED_DUMBBELL_SHOULDER_PRESS": "Жим гантелей сидя",
    "DUMBBELL_SHOULDER_PRESS": "Жим гантелей стоя",
    "BARBELL_SHOULDER_PRESS": "Жим штанги стоя", "OVERHEAD_PRESS": "Жим над головой",
    "NEUTRAL_GRIP_PULL_UP": "Подтягивания нейтральным хватом",
    "PULL_UP": "Подтягивания", "CHIN_UP": "Подтягивания обратным хватом",
    "LAT_PULLDOWN": "Тяга верхнего блока", "WIDE_GRIP_LAT_PULLDOWN": "Тяга верхнего блока широким хватом",
    "BENT_OVER_ROW_WITH_BARBELL": "Тяга штанги в наклоне",
    "BENT_OVER_ROW_WITH_DUMBBELL": "Тяга гантели в наклоне",
    "ONE_ARM_BENT_OVER_ROW": "Тяга гантели одной рукой", "SEATED_CABLE_ROW": "Тяга нижнего блока сидя",
    "DUMBBELL_LATERAL_RAISE": "Разведение гантелей в стороны",
    "DUMBBELL_FRONT_RAISE": "Подъём гантелей перед собой",
    "DUMBBELL_BICEPS_CURL": "Сгибания рук с гантелями на бицепс",
    "BARBELL_BICEPS_CURL": "Сгибания рук со штангой на бицепс",
    "HAMMER_CURL": "Молотковые сгибания", "DUMBBELL_HAMMER_CURL": "Молотковые сгибания",
    "DUMBBELL_LYING_TRICEPS_EXTENSION": "Французский жим гантелей лёжа",
    "BARBELL_LYING_TRICEPS_EXTENSION": "Французский жим штанги лёжа",
    "TRICEPS_PRESSDOWN": "Разгибания рук на блоке", "CLOSE_GRIP_BARBELL_BENCH_PRESS": "Жим штанги узким хватом",
    "BARBELL_DEADLIFT": "Становая тяга", "DEADLIFT": "Становая тяга",
    "ROMANIAN_DEADLIFT": "Румынская тяга", "BARBELL_ROMANIAN_DEADLIFT": "Румынская тяга со штангой",
    "DUMBBELL_ROMANIAN_DEADLIFT": "Румынская тяга с гантелями",
    "BARBELL_HIP_THRUST": "Ягодичный мостик со штангой", "HIP_THRUST": "Ягодичный мостик",
    "LUNGE": "Выпады", "DUMBBELL_LUNGE": "Выпады с гантелями", "WALKING_LUNGE": "Выпады в ходьбе",
    "BULGARIAN_SPLIT_SQUAT": "Болгарские сплит-приседания",
    "LEG_PRESS": "Жим ногами", "LEG_EXTENSION": "Разгибания ног", "LEG_CURL": "Сгибания ног",
    "STANDING_CALF_RAISE": "Подъём на носки стоя", "SEATED_CALF_RAISE": "Подъём на носки сидя",
    "PUSH_UP": "Отжимания", "DIP": "Отжимания на брусьях", "TRICEPS_DIP": "Отжимания на брусьях",
    "PLANK": "Планка", "CRUNCH": "Скручивания", "SIT_UP": "Подъём корпуса",
    "HANGING_LEG_RAISE": "Подъём ног в висе", "FACE_PULL": "Тяга к лицу",
    "SHRUG": "Шраги", "DUMBBELL_SHRUG": "Шраги с гантелями", "BARBELL_SHRUG": "Шраги со штангой",
    "CABLE_CROSSOVER": "Сведение рук на блоках", "DUMBBELL_FLYE": "Разведение гантелей лёжа",
    "WIDE_GRIP_PULL_UP": "Подтягивания широким хватом",
    "LYING_DUMBBELL_PULLOVER_TO_EXTENSION": "Французский жим гантелей (лёжа)",
    "SINGLE_ARM_DUMBBELL_OVERHEAD_TRICEPS_EXTENSION": "Французский жим гантели одной рукой над головой",
    "OVERHEAD_DUMBBELL_PRESS": "Жим гантелей над головой",
    "REVERSE_FLYE": "Разведение в наклоне на заднюю дельту",
}


import json
from pathlib import Path

OVERRIDES_FILE = Path("/config/exercise_names.json")  # свои названия: {"КОД_GARMIN": "Название"}


def _load():
    """Возвращает (словарь, статус-текст)."""
    f = OVERRIDES_FILE
    if not f.exists():
        return {}, f"файл не найден: {f}"
    try:
        data = json.loads(f.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            return {}, "в файле должен быть объект {\"КОД\": \"Название\"}"
        return {str(k).strip().upper().replace(" ", "_"): v for k, v in data.items()}, f"прочитан, записей: {len(data)}"
    except Exception as e:
        return {}, f"ошибка чтения: {e}"


def _overrides():
    return _load()[0]


def overrides_status():
    return _load()[1]


def ru(code):
    if not code:
        return "—"
    code = code.strip().upper().replace(" ", "_")
    own = _overrides()
    if code in own:
        return own[code]
    if code in NAMES:
        return NAMES[code]
    return code.replace("_", " ").capitalize() + " ⁇"
