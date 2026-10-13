#!/usr/bin/env python3
"""Captura de momios de la LMP desde Playdoit (API pública de Altenar) hacia Firebase.

Cada corrida toma una "foto" de los momios pre-partido de todos los juegos de LMP publicados
y la guarda en /lmp/momios/{fecha}/{gamePk}/snapshots/{ts}. El cierre de cada juego es la
última foto antes de su hora de inicio; se calcula después, no aquí.

Variables de entorno:
  FIREBASE_DB_URL   p. ej. https://tu-proyecto-default-rtdb.firebaseio.com
  FIREBASE_AUTH     credencial para ?auth= (la misma que usa V3)
  DRY_RUN=1         imprime las fotos en vez de escribir en Firebase
  FIXTURE=archivo   usa un JSON guardado en vez de llamar a la API (pruebas)

Solo usa la librería estándar de Python.
"""
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ALTENAR = "https://sb2frontend-altenar2.biahosted.com/api/widget"
BASE_PARAMS = {
    "culture": "es-ES", "timezoneOffset": "360", "integration": "playdoit2",
    "deviceType": "1", "numFormat": "en-GB", "countryCode": "MX",
}
SPORT_BASEBALL = 76
LMP_CHAMP_FALLBACK = 3526          # id de "Liga Mexicana del Pacífico" visto el 12 oct 2026
MLB_SCHEDULE = "https://statsapi.mlb.com/api/v1/schedule"
CDMX = timezone(timedelta(hours=-6))  # México no tiene horario de verano desde 2022

# Palabra clave normalizada -> team_id de la MLB Stats API (verificado el 12 oct 2026)
TEAM_KEYS = {
    "charros": 674, "jaguares": 6483, "yaquis": 680, "mayos": 676, "algodoneros": 5482,
    "caneros": 675, "tomateros": 678, "venados": 679, "naranjeros": 677, "aguilas": 673,
    # respaldo por ciudad, por si Playdoit usa formato "CIUDAD Apodo" abreviado
    "jalisco": 674, "nayarit": 6483, "obregon": 680, "navojoa": 676, "guasave": 5482,
    "mochis": 675, "culiacan": 678, "mazatlan": 679, "hermosillo": 677, "mexicali": 673,
}

# typeIds de Altenar observados en béisbol
MKT_MONEYLINE, MKT_RUNLINE, MKT_TOTAL = 251, 256, 258
ODD_OVER, ODD_UNDER = 12, 13


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", " ", s.lower())


def team_id(name):
    words = norm(name).split()
    for w in words:
        if w in TEAM_KEYS:
            return TEAM_KEYS[w]
    return None


def http_json(url, params=None, retries=3):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (lmp-captura)"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"Fallo al consultar {url}: {last}")


def lmp_champ_id():
    """Busca el id de la LMP en el menú de béisbol; si no aparece, usa el conocido."""
    try:
        menu = http_json(f"{ALTENAR}/GetSportMenu", {**BASE_PARAMS, "sportId": SPORT_BASEBALL, "period": 0})
        for c in menu.get("champs", []):
            if "pacifico" in norm(c.get("name")):
                return c["id"]
    except RuntimeError as e:
        print("Aviso:", e, file=sys.stderr)
    return LMP_CHAMP_FALLBACK


def fetch_events(champ_id):
    if os.environ.get("FIXTURE"):
        with open(os.environ["FIXTURE"], encoding="utf-8") as f:
            return json.load(f)
    return http_json(f"{ALTENAR}/GetEvents",
                     {**BASE_PARAMS, "eventCount": 0, "sportId": SPORT_BASEBALL, "champIds": champ_id})


def american(dec):
    if not dec or dec <= 1:
        return None
    return round((dec - 1) * 100) if dec >= 2 else round(-100 / (dec - 1))


def parse_events(data, champ_id, now):
    """Convierte la respuesta de Altenar en una lista de fotos, una por juego pre-partido."""
    odds = {o["id"]: o for o in data.get("odds", [])}
    markets = {m["id"]: m for m in data.get("markets", [])}
    comps = {c["id"]: c["name"] for c in data.get("competitors", [])}
    out, problems = [], []

    for ev in data.get("events", []):
        if ev.get("champId") != champ_id or ev.get("status", 0) != 0:
            continue
        start = datetime.fromisoformat(ev["startDate"].replace("Z", "+00:00"))
        if start <= now:  # ya empezó: esos momios son en vivo, no sirven
            continue
        name = ev.get("name", "")
        if " @ " not in name:
            problems.append(f"Nombre sin ' @ ': {name}")
            continue
        away_name, home_name = [x.strip() for x in name.split(" @ ", 1)]
        cid_by_name = {v: k for k, v in comps.items()}
        home_cid, away_cid = cid_by_name.get(home_name), cid_by_name.get(away_name)
        home_id, away_id = team_id(home_name), team_id(away_name)
        if home_id is None or away_id is None:
            problems.append(f"Equipo sin mapear: {name}")

        snap = {
            "source": "playdoit", "event_id": ev["id"], "name": name,
            "start_utc": start.isoformat(), "mins_to_start": round((start - now).total_seconds() / 60),
            "home": home_name, "away": away_name, "home_id": home_id, "away_id": away_id,
        }
        for p in ev.get("pitchers", []) or []:
            side = "home" if p.get("competitorId") == home_cid else "away" if p.get("competitorId") == away_cid else None
            if side:
                snap[f"sp_{side}"] = p.get("name")
                snap[f"sp_{side}_hand"] = p.get("hand")

        for mid in ev.get("marketIds", []):
            m = markets.get(mid)
            if not m:
                continue
            sel = [odds[i] for i in m.get("oddIds", []) if i in odds]
            if m["typeId"] == MKT_MONEYLINE:
                for o in sel:
                    side = "home" if o.get("competitorId") == home_cid else "away" if o.get("competitorId") == away_cid else None
                    if not side:
                        continue
                    snap[f"ml_{side}"] = o["price"]
                    snap[f"ml_{side}_us"] = american(o["price"])
                    snap[f"ml_{side}_active"] = o.get("oddStatus", 0) == 0
                    pa = [x.get("parameter") for x in o.get("offers", []) or [] if x.get("type") == 0]
                    snap[f"pago_anticipado_{side}"] = pa[0] if pa else None
            elif m["typeId"] == MKT_RUNLINE:
                for o in sel:
                    side = "home" if o.get("competitorId") == home_cid else "away" if o.get("competitorId") == away_cid else None
                    if not side:
                        continue
                    line = re.search(r"\(([+-]?\d+(?:\.\d+)?)\)", o.get("name", ""))
                    snap[f"rl_{side}"] = o["price"]
                    if line:
                        snap[f"rl_{side}_line"] = float(line.group(1))
            elif m["typeId"] == MKT_TOTAL:
                snap["total_line"] = float(m.get("sv")) if m.get("sv") else None
                for o in sel:
                    if o["typeId"] == ODD_OVER:
                        snap["over"] = o["price"]
                    elif o["typeId"] == ODD_UNDER:
                        snap["under"] = o["price"]

        if snap.get("ml_home") and snap.get("ml_away"):
            ih, ia = 1 / snap["ml_home"], 1 / snap["ml_away"]
            snap["vig"] = round(ih + ia - 1, 4)
            snap["p_home_novig"] = round(ih / (ih + ia), 4)
        else:
            problems.append(f"Sin moneyline completo: {name}")
        out.append(snap)
    return out, problems


def attach_gamepk(snaps):
    """Empata cada juego con su gamePk de la MLB Stats API (mismos equipos, inicio a ±4 h)."""
    if not snaps or os.environ.get("FIXTURE"):
        return
    starts = [datetime.fromisoformat(s["start_utc"]).astimezone(CDMX).date() for s in snaps]
    sched = http_json(MLB_SCHEDULE, {"sportId": 17, "leagueId": 132,
                                     "startDate": min(starts) - timedelta(days=1),
                                     "endDate": max(starts) + timedelta(days=1)})
    games = [g for d in sched.get("dates", []) for g in d.get("games", [])]
    for s in snaps:
        st = datetime.fromisoformat(s["start_utc"])
        for g in games:
            gt = datetime.fromisoformat(g["gameDate"].replace("Z", "+00:00"))
            if (g["teams"]["home"]["team"]["id"] == s["home_id"]
                    and g["teams"]["away"]["team"]["id"] == s["away_id"]
                    and abs((gt - st).total_seconds()) <= 4 * 3600):
                s["gamePk"] = g["gamePk"]
                s["officialDate"] = g.get("officialDate")
                break


def firebase_put(path, payload):
    db, auth = os.environ["FIREBASE_DB_URL"].rstrip("/"), os.environ.get("FIREBASE_AUTH", "")
    url = f"{db}/{path}.json" + (f"?auth={urllib.parse.quote(auth)}" if auth else "")
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="PUT",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


def main():
    now = datetime.now(timezone.utc)
    champ = int(os.environ.get("LMP_CHAMP_ID") or (LMP_CHAMP_FALLBACK if os.environ.get("FIXTURE") else lmp_champ_id()))
    data = fetch_events(champ)
    snaps, problems = parse_events(data, champ, now)
    attach_gamepk(snaps)
    ts = now.strftime("%Y%m%dT%H%M%SZ")

    for s in snaps:
        s["ts_utc"] = now.isoformat()
        day = s.get("officialDate") or datetime.fromisoformat(s["start_utc"]).astimezone(CDMX).strftime("%Y-%m-%d")
        key = s.get("gamePk") or f"pd_{s['event_id']}"
        if not s.get("gamePk"):
            problems.append(f"Sin gamePk: {s['name']}")
        if os.environ.get("DRY_RUN"):
            print(json.dumps(s, ensure_ascii=False))
        else:
            firebase_put(f"lmp/momios/{day}/{key}/snapshots/{ts}", s)

    print(f"{now:%Y-%m-%d %H:%M} UTC | champ {champ} | juegos capturados: {len(snaps)}")
    for p in problems:
        print("PROBLEMA:", p)
    # Falla la corrida solo si hubo juegos y alguno no se pudo interpretar o empatar
    if snaps and problems:
        sys.exit(2)


if __name__ == "__main__":
    main()
