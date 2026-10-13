"""Pruebas del parser con una respuesta real de Altenar (MLB, 12 oct 2026) adaptada a LMP."""
import copy
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import capture_playdoit as cp  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "playdoit_mlb_2026-10-12.json")
NOW = datetime(2026, 10, 14, 20, 0, tzinfo=timezone.utc)  # antes del inicio (2026-10-15 00:00Z)


def as_lmp(away, home, offers=True):
    d = json.load(open(FIX, encoding="utf-8"))
    d = copy.deepcopy(d)
    ren = {"MIL Brewers": away, "LA Dodgers": home}
    for c in d["competitors"]:
        c["name"] = ren[c["name"]]
    for o in d["odds"]:
        for k, v in ren.items():
            o["name"] = o["name"].replace(k, v)
        if not offers:
            o.pop("offers", None)
    ev = d["events"][0]
    ev["champId"] = cp.LMP_CHAMP_FALLBACK
    ev["name"] = f"{away} @ {home}"
    return d


def test_mlb_raw_is_ignored_for_lmp_champ():
    d = json.load(open(FIX, encoding="utf-8"))
    snaps, _ = cp.parse_events(d, cp.LMP_CHAMP_FALLBACK, NOW)
    assert snaps == []


def test_full_names():
    snaps, problems = cp.parse_events(as_lmp("Jaguares de Nayarit", "Charros de Jalisco"), cp.LMP_CHAMP_FALLBACK, NOW)
    assert problems == [], problems
    s = snaps[0]
    assert (s["home_id"], s["away_id"]) == (674, 6483)
    assert s["ml_home"] == 1.5883 and s["ml_away"] == 2.45
    assert s["ml_home_us"] == -170 and s["ml_away_us"] == 145
    assert s["rl_home_line"] == -1.5 and s["rl_home"] == 2.25 and s["rl_away_line"] == 1.5
    assert s["total_line"] == 7.5 and s["over"] == 1.9091 and s["under"] == 1.9091
    assert s["sp_home"] == "Snell, Blake" and s["sp_away_hand"] == "L"
    assert s["pago_anticipado_home"] == 5 and s["pago_anticipado_away"] == 5
    assert 0.60 < s["p_home_novig"] < 0.62 and 0.03 < s["vig"] < 0.04
    assert s["mins_to_start"] == 240


def test_abbreviated_and_accented_names():
    snaps, problems = cp.parse_events(as_lmp("NAY Jaguares", "Cañeros de Los Mochis", offers=False), cp.LMP_CHAMP_FALLBACK, NOW)
    assert problems == [], problems
    s = snaps[0]
    assert (s["home_id"], s["away_id"]) == (675, 6483)
    assert s["pago_anticipado_home"] is None


def test_started_games_are_skipped():
    later = datetime(2026, 10, 15, 0, 5, tzinfo=timezone.utc)
    snaps, _ = cp.parse_events(as_lmp("Jaguares de Nayarit", "Charros de Jalisco"), cp.LMP_CHAMP_FALLBACK, later)
    assert snaps == []


def test_unknown_team_is_reported():
    _, problems = cp.parse_events(as_lmp("Equipo Raro", "Charros de Jalisco"), cp.LMP_CHAMP_FALLBACK, NOW)
    assert any("sin mapear" in p for p in problems)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
