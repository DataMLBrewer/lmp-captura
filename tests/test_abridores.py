"""Pruebas de capture_abridores con la respuesta real de api.lmp.mx del 16 oct 2026."""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import capture_abridores as ca  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "lmpapi_scoreboard_2026-10-16.json")
NOW = datetime(2026, 10, 16, 22, 40, tzinfo=timezone.utc)
# Respuesta de /people verificada el 16 oct para dos IDs; los demás se simulan válidos
PEOPLE = {"672598": {"fullName": "Alemao Hernandez", "pitchHand": "L"},
          "687195": {"fullName": "Jake Garland", "pitchHand": "R"},
          "666627": {"fullName": "Faustino Carrera", "pitchHand": "L"}}


def data():
    return json.load(open(FIX, encoding="utf-8"))


def test_reads_today_and_tomorrow():
    recs, _ = ca.build_records(data(), PEOPLE, NOW)
    assert len(recs) == 10
    assert {r["gameDate"] for r in recs} == {"2026-10-16", "2026-10-17"}


def test_validated_pitchers_get_id_and_hand():
    recs, _ = ca.build_records(data(), PEOPLE, NOW)
    r = next(r for r in recs if r["gamePk"] == 850549)
    assert (r["away"], r["home"]) == ("Charros", "Jaguares")
    assert (r["away_id"], r["home_id"]) == (674, 6483)
    assert r["sp_away"] == "Antonio Alemao Hernández" and r["sp_away_id"] == 672598 and r["sp_away_hand"] == "L"
    assert r["sp_home_id"] == 666627 and r["estado"] == "OK"
    assert r["start_utc"] == "2026-10-17T02:30:00+00:00"
    assert r["caliente_ref"] == {"away": 1.769, "home": 1.9}


def test_non_mlb_id_is_flagged():
    recs, problems = ca.build_records(data(), PEOPLE, NOW)
    r = next(r for r in recs if r["gamePk"] == 850562)
    assert r["sp_away"] == "Finn Del Bonta-Smith" and r["sp_away_id"] is None and r["sp_away_lmp_id"] == "90162"
    assert r["estado"] == "REVISAR"
    assert any("90162" in p for p in problems)


def test_wrong_person_for_id_is_flagged():
    people = dict(PEOPLE, **{"687195": {"fullName": "Otro Lanzador", "pitchHand": "R"}})
    recs, _ = ca.build_records(data(), people, NOW)
    r = next(r for r in recs if r["gamePk"] == 850562)
    assert r["sp_home_id"] is None


def test_future_games_without_gamepk_are_reported():
    recs, problems = ca.build_records(data(), PEOPLE, NOW)
    assert sum(1 for r in recs if r["gamePk"] is None) == 5
    assert any(p.startswith("Sin gamePk") for p in problems)


def test_name_matching():
    assert ca.same_person("BRENNEN Oxford", "Brennen Oxford")
    assert ca.same_person("Antonio Alemao Hernández", "Alemao Hernandez")
    assert not ca.same_person("Jake Garland", "Luis Cessa")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
