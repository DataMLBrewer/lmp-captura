#!/usr/bin/env python3
"""Captura de abridores probables de la LMP desde la API oficial de la liga (api.lmp.mx).

Cada corrida lee los juegos de hoy y de mañana (hora CDMX), valida el ID de cada abridor contra
la MLB Stats API y guarda en Firebase:

  /lmp/abridores/{gameDate}/{gamePk}/latest            estado actual del juego
  /lmp/abridores/{gameDate}/{gamePk}/cambios/{ts}      cada vez que cambia un abridor anunciado

Si el juego todavía no tiene gamePk en la API de la liga, se busca en el calendario de MLB por
equipos y fecha. Variables de entorno: las mismas que capture_playdoit.py (FIREBASE_DB_URL,
FIREBASE_SERVICE_ACCOUNT, DRY_RUN, y FIXTURE para pruebas).
"""
import json
import os
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import capture_playdoit as cp

LMP_API = "https://api.lmp.mx/3.0.0/scoreboard"
MLB_PEOPLE = "https://statsapi.mlb.com/api/v1/people"


def norm_name(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join(s.replace("-", " ").split())


def same_person(lmp_name, mlb_name):
    """Coincide si comparten el apellido principal (la LMP a veces usa dos apellidos o mayúsculas)."""
    a, b = norm_name(lmp_name).split(), norm_name(mlb_name).split()
    return bool(a and b) and (a[-1] in b or b[-1] in a)


def fetch_scoreboard(day):
    if os.environ.get("FIXTURE"):
        with open(os.environ["FIXTURE"], encoding="utf-8") as f:
            return json.load(f)
    return cp.http_json(LMP_API, {"date": day, "days_next": 1, "days_prev": 0})


def fetch_people(ids):
    """Devuelve {id: {fullName, pitchHand}} para los IDs que existen en MLB."""
    if os.environ.get("FIXTURE"):
        return json.loads(os.environ.get("FIXTURE_PEOPLE", "{}"))
    if not ids:
        return {}
    data = cp.http_json(MLB_PEOPLE, {"personIds": ",".join(str(i) for i in sorted(ids))})
    return {str(p["id"]): {"fullName": p.get("fullName"), "pitchHand": (p.get("pitchHand") or {}).get("code")}
            for p in data.get("people", [])}


def resolve_gamepks(games):
    """Completa gamePk de juegos futuros desde el calendario de MLB (equipos + fecha)."""
    missing = [g for g in games if not g.get("boxScore")]
    if not missing or os.environ.get("FIXTURE"):
        return
    days = sorted({g["gameDate"] for g in missing})
    sched = cp.http_json(cp.MLB_SCHEDULE, {"sportId": 17, "leagueId": 132, "startDate": days[0], "endDate": days[-1]})
    idx = {}
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            key = (g.get("officialDate"), g["teams"]["home"]["team"]["id"], g["teams"]["away"]["team"]["id"])
            idx.setdefault(key, []).append(g["gamePk"])
    for g in missing:
        pks = idx.get((g["gameDate"], g["homeTeamId"], g["awayTeamId"]), [])
        if len(pks) == 1:  # con doble cartelera no se adivina: queda sin gamePk y se reporta
            g["boxScore"] = pks[0]


def build_records(data, people, now):
    resp = data.get("response", {})
    games = (resp.get("games") or []) + (resp.get("nextGames") or [])
    out, problems = [], []
    for g in games:
        rec = {
            "source": "api.lmp.mx", "lmp_game_id": g.get("id"), "gamePk": g.get("boxScore"),
            "gameDate": g.get("gameDate"),
            "start_utc": (g.get("utcFullDate") or "").replace(" ", "T") + "+00:00" if g.get("utcFullDate") else None,
            "away": g.get("awayTeamShort"), "away_id": g.get("awayTeamId"),
            "home": g.get("homeTeamShort"), "home_id": g.get("homeTeamId"),
            "stadium": g.get("stadium"), "ts_utc": now.isoformat(),
        }
        estado = "OK"
        for side in ("away", "home"):
            name = (g.get(f"{side}ProbablePitcher") or "").strip()
            pid = g.get(f"{side}ProbablePitcherMlbid")
            pid = str(pid) if pid else None
            person = people.get(pid) if pid else None
            rec[f"sp_{side}"] = name or None
            rec[f"sp_{side}_lmp_id"] = pid
            if not name:
                rec[f"sp_{side}_id"], rec[f"sp_{side}_hand"] = None, None
                estado = "SIN_DATO"
                problems.append(f"Sin abridor anunciado: {rec['away']} @ {rec['home']} ({rec['gameDate']}, {side})")
            elif person and same_person(name, person["fullName"]):
                rec[f"sp_{side}_id"], rec[f"sp_{side}_hand"] = int(pid), person["pitchHand"]
            else:
                rec[f"sp_{side}_id"], rec[f"sp_{side}_hand"] = None, None
                estado = "REVISAR" if estado == "OK" else estado
                problems.append(f"ID sin validar en MLB: {name} (#{pid}) — {rec['away']} @ {rec['home']} ({rec['gameDate']})")
        rec["estado"] = estado
        bet = next((b for b in g.get("bets") or [] if b.get("type") == 3), None)
        if bet:  # línea de Caliente que muestra la liga: referencia secundaria, sin hora de captura propia
            try:
                rec["caliente_ref"] = {"away": float(bet["away"]["dec_prc"]), "home": float(bet["home"]["dec_prc"])}
            except (KeyError, TypeError, ValueError):
                pass
        if not rec["gamePk"]:
            problems.append(f"Sin gamePk: {rec['away']} @ {rec['home']} ({rec['gameDate']})")
        out.append(rec)
    return out, problems


def firebase_get(path):
    url = cp.firebase_url(path)
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Firebase respondió HTTP {e.code} al leer {path}") from None


def main():
    try:
        run()
    except Exception as e:  # noqa: BLE001
        cp.gh_error(f"Abridores · {type(e).__name__}: {e}")
        raise


def run():
    now = datetime.now(timezone.utc)
    dry = bool(os.environ.get("DRY_RUN"))
    if not dry and not os.environ.get("FIREBASE_DB_URL"):
        raise RuntimeError("Falta el secret FIREBASE_DB_URL (o está vacío) en Settings → Secrets → Actions")
    today = now.astimezone(cp.CDMX).strftime("%Y-%m-%d")
    data = fetch_scoreboard(today)
    games = (data.get("response", {}).get("games") or []) + (data.get("response", {}).get("nextGames") or [])
    resolve_gamepks(games)
    ids = {str(g[f"{s}ProbablePitcherMlbid"]) for g in games for s in ("away", "home") if g.get(f"{s}ProbablePitcherMlbid")}
    people = fetch_people(ids)
    records, problems = build_records(data, people, now)
    ts = now.strftime("%Y%m%dT%H%M%SZ")

    for rec in records:
        key = rec["gamePk"] or f"lmp_{rec['lmp_game_id']}"
        base = f"lmp/abridores/{rec['gameDate']}/{key}"
        if dry:
            print(json.dumps(rec, ensure_ascii=False))
            continue
        prev = firebase_get(f"{base}/latest")
        if prev and (prev.get("sp_away"), prev.get("sp_home")) != (rec["sp_away"], rec["sp_home"]):
            cp.firebase_put(f"{base}/cambios/{ts}", {
                "antes": {"away": prev.get("sp_away"), "home": prev.get("sp_home")},
                "ahora": {"away": rec["sp_away"], "home": rec["sp_home"]}, "ts_utc": rec["ts_utc"]})
            cp.gh_error(f"Cambio de abridor: {rec['away']} @ {rec['home']} — {prev.get('sp_away')}/{prev.get('sp_home')} → {rec['sp_away']}/{rec['sp_home']}")
        cp.firebase_put(f"{base}/latest", rec)

    print(f"{now:%Y-%m-%d %H:%M} UTC | abridores: {len(records)} juegos")
    summary = ["", f"**Abridores** · {len(records)} juegos", "",
               "| Fecha | Visita @ Local | Abridor visita | Abridor local | Estado |", "|---|---|---|---|---|"]
    summary += [f"| {r['gameDate']} | {r['away']} @ {r['home']} | {r['sp_away']} ({r['sp_away_hand'] or '?'}) | "
                f"{r['sp_home']} ({r['sp_home_hand'] or '?'}) | {r['estado']} |" for r in records]
    cp.gh_summary(summary)
    # Un ID sin validar no es error de la corrida: es dato para revisar. Solo se anota.
    for p in problems:
        print("AVISO:", p)
        print(f"::warning::{p}")


if __name__ == "__main__":
    main()
