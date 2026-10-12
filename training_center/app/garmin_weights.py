"""Вес в тренировках Garmin: всегда килограммы, без умножения."""

KG_UNIT = {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}


def weight_fields(kg):
    """Поля шага тренировки для веса в кг (None -> шаг без веса)."""
    if kg is None:
        return {"weightValue": None, "weightUnit": None}
    return {"weightValue": float(kg), "weightUnit": dict(KG_UNIT)}


def read_weight_kg(step):
    """Вес шага в кг из ответа Garmin (или None)."""
    v = step.get("weightValue")
    return None if v is None else float(v)
