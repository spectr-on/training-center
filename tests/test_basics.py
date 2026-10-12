import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "training_center"))
from app.garmin_weights import weight_fields, read_weight_kg
from app.lifestyle import parse_day


def test_weight_kg_not_multiplied():
    f = weight_fields(62.5)
    assert f["weightValue"] == 62.5 and f["weightUnit"]["unitKey"] == "kilogram"
    assert read_weight_kg(f) == 62.5


def test_no_weight():
    assert weight_fields(None) == {"weightValue": None, "weightUnit": None}


def test_lifestyle():
    resp = {"dailyLogsReport": [
        {"name": "Alcohol", "logStatus": "YES", "details": [{"subTypeName": "BEER", "amount": 4}]},
        {"name": "Кальян", "logStatus": "YES"},
        {"name": "Heavy Meals"}]}
    assert parse_day(resp) == {"alcohol": {"amount": 4}, "hookah": {"amount": None}}
    assert parse_day({"dailyLogsReport": [{"name": "Alcohol"}]}) == {}
