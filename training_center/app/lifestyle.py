"""Разбор журнала образа жизни Garmin."""

# какие отметки Garmin считаем факторами (имя в Garmin -> наш код)
FACTORS = {"Alcohol": "alcohol", "Алкоголь": "alcohol", "Кальян": "hookah", "Болезнь": "illness"}


def parse_day(resp):
    """Ответ get_lifestyle_logging_data -> {код: {'amount':..} } только для отмеченных (YES)."""
    out = {}
    for item in (resp or {}).get("dailyLogsReport", []):
        if item.get("logStatus") != "YES":
            continue
        code = FACTORS.get(item.get("name"))
        if not code:
            continue
        amount = sum(d.get("amount") or 0 for d in item.get("details") or [])
        out[code] = {"amount": amount or None}
    return out
