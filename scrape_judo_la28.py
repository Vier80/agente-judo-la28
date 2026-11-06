"""Simulazione dei qualificati di judo a LA28 (IJF)

Legge dal sito della federazione mondiale (ijf.org) il ranking olimpico individuale delle
14 categorie di peso (7 uomini, 7 donne) e applica il sistema di qualificazione del CIO
(JUD-LA28, versione del 12 maggio 2026) per simulare chi si qualificherebbe oggi.
Scrive le righe in un database Notion e produce il file judo.json per la pagina grafica.

Variabili: NOTION_TOKEN, NOTION_DATABASE_ID.
DRY_RUN=true: solo prova, non scrive su Notion (judo.json viene comunque creato).
MODALITA: "archivio" (le simulazioni vecchie restano) oppure "sostituzione" (vengono archiviate).
"""
import json
import os
import re
import sys
import time
from datetime import date, datetime

import requests

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()
_raw_id = os.getenv("NOTION_DATABASE_ID", "").strip().replace("-", "")
_found = re.search(r"[0-9a-fA-F]{32}", _raw_id)
DATABASE_ID = _found.group(0) if _found else _raw_id
DRY_RUN = os.getenv("DRY_RUN", "").strip().lower() == "true"
MODALITA = os.getenv("MODALITA", "archivio").strip().lower()
JSON_OUT = os.getenv("JSON_OUT", "judo.json")

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

API = "https://www.ijf.org/internal_api/wrl_olympic"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept": "application/json"})

HOST = "USA"
END_OF_PERIOD = date(2028, 6, 12)
DIRECT_PLACES = 17            # per categoria
RESERVES = 2                  # riserve per categoria
CONT_QUOTA = {"Africa": 12, "Europa": 13, "Asia": 12, "Oceania": 4, "Americhe": 11}  # per genere
CONT_ORDER = ["Europa", "Asia", "Africa", "Americhe", "Oceania"]
UNIVERSALITY_PLACES = 10
TEAM_INVITES = ["Africa", "Asia", "Europa", "Oceania", "Americhe", "Ranking squadre miste"]

WEIGHTS = {  # id_weight IJF -> (genere, nome)
    1: ("Uomini", "-60"), 2: ("Uomini", "-66"), 3: ("Uomini", "-73"), 4: ("Uomini", "-81"),
    5: ("Uomini", "-90"), 6: ("Uomini", "-100"), 7: ("Uomini", "+100"),
    8: ("Donne", "-48"), 9: ("Donne", "-52"), 10: ("Donne", "-57"), 11: ("Donne", "-63"),
    12: ("Donne", "-70"), 13: ("Donne", "-78"), 14: ("Donne", "+78"),
}
# codici CIO di riferimento per riconoscere i continenti IJF (id_continent) dai dati
ANCHORS = {
    "Africa": {"ALG", "EGY", "MAR", "TUN", "RSA", "SEN", "CMR", "CIV", "NGR", "MAD", "GAB", "ANG"},
    "Asia": {"JPN", "KOR", "MGL", "KAZ", "UZB", "CHN", "TPE", "IRI", "KGZ", "TJK", "IND", "UAE"},
    "Europa": {"FRA", "GER", "ITA", "ESP", "GBR", "NED", "POR", "GEO", "AUT", "BEL", "CRO", "HUN"},
    "Oceania": {"AUS", "NZL", "FIJ", "PNG", "SAM", "GUM", "TGA", "COK", "ASA", "VAN"},
    "Americhe": {"USA", "BRA", "CAN", "ARG", "CUB", "MEX", "COL", "CHI", "PER", "DOM", "VEN", "PUR"},
}


def die(msg):
    print(f"ERRORE: {msg}")
    sys.exit(1)


# ------------------------------------------------------------------ lettura dati
def fetch_json(params):
    last = None
    for attempt in range(4):
        try:
            r = SESSION.get(API, params=params, timeout=60)
            if r.status_code >= 500:
                raise RuntimeError(f"stato {r.status_code}")
            return r.json()
        except Exception as e:
            last = e
            time.sleep(2 + 2 * attempt)
    raise RuntimeError(f"ranking IJF non raggiungibile ({last})")


def fetch_category(idw, notes):
    """Tutte le righe del ranking olimpico di una categoria (paginato a 100)."""
    rows, page = [], 0
    while page < 40:
        params = {"limit": 100, "page": page}
        if idw != 1:
            params["category"] = idw
        data = fetch_json(params)
        feed = data.get("feed") if isinstance(data, dict) else None
        if not isinstance(feed, list) or not feed:
            break
        rows += feed
        if len(feed) < 100:
            break
        page += 1
    mine = [r for r in rows if str(r.get("id_weight")) == str(idw)]
    if len(mine) != len(rows):
        notes.append(f"categoria {idw}: scartate {len(rows) - len(mine)} righe di altre categorie")
    return mine


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def parse_rows(idw, feed):
    gender, wname = WEIGHTS[idw]
    out = []
    for i, r in enumerate(feed):
        noc = str(r.get("country_ioc_code") or "").upper()
        fam, giv = str(r.get("family_name") or "").strip(), str(r.get("given_name") or "").strip()
        if not noc or not (fam or giv):
            continue
        struck = str(r.get("strike_through") or "0") == "1"
        out.append({
            "pid": str(r.get("id_person")), "name": f"{fam} {giv}".strip(), "noc": noc,
            "country": str(r.get("country_name") or noc), "idc": str(r.get("id_continent") or ""),
            "pts": num(r.get("sum_points")), "place": int(num(r.get("place"), i + 1)) or i + 1,
            "order": i, "gender": gender, "wname": wname, "idw": idw, "struck": struck,
        })
    out.sort(key=lambda a: (a["place"], -a["pts"], a["order"]))
    return out


def continent_names(all_athletes, notes):
    """Dai codici dei Paesi riconosce quale id_continent corrisponde a quale continente."""
    votes = {}
    for a in all_athletes:
        for name, codes in ANCHORS.items():
            if a["noc"] in codes:
                votes.setdefault(a["idc"], {}).setdefault(name, set()).add(a["noc"])
    mapping = {}
    for idc, d in votes.items():
        best = max(d.items(), key=lambda kv: len(kv[1]))
        mapping[idc] = best[0]
    seen = sorted({a["idc"] for a in all_athletes})
    notes.append("continenti riconosciuti: " + ", ".join(f"id {k} = {v}" for k, v in sorted(mapping.items())))
    for idc in seen:
        if idc not in mapping:
            notes.append(f"ATTENZIONE: continente id {idc} non riconosciuto: i suoi atleti non possono "
                         f"prendere posti continentali")
    return mapping


def load_ranking(notes):
    data = {}
    for idw in WEIGHTS:
        feed = fetch_category(idw, notes)
        athletes = parse_rows(idw, feed)
        struck = [a for a in athletes if a["struck"]]
        data[idw] = [a for a in athletes if not a["struck"]]
        print(f"  {WEIGHTS[idw][0]} {WEIGHTS[idw][1]} kg: {len(athletes)} atleti in classifica"
              + (f" ({len(struck)} esclusi dalla classifica IJF)" if struck else ""))
    allat = [a for lst in data.values() for a in lst]
    cmap = continent_names(allat, notes)
    for a in allat:
        a["cont"] = cmap.get(a["idc"], "")
    return data


# ------------------------------------------------------------------ simulazione
def label(idw):
    g, w = WEIGHTS[idw]
    return f"{w} kg"


def simulate(data, today=None):
    today = today or date.today()
    final = today >= END_OF_PERIOD
    stato_ok = "Qualificato" if final else "Provvisorio"
    notes = []
    quota = {}      # (idw) -> list di assegnazioni
    holders = {idw: set() for idw in WEIGHTS}   # NOC con un posto nella categoria
    qualified = {idw: set() for idw in WEIGHTS}  # id atleta
    assign = []

    def give(a, via, tipo, status=None, detail=""):
        qualified[a["idw"]].add(a["pid"])
        holders[a["idw"]].add(a["noc"])
        rec = {"a": a, "via": via, "tipo": tipo, "status": status or stato_ok, "detail": detail}
        assign.append(rec)
        return rec

    # 1. qualificazione diretta: i primi 17 per categoria, uno per NOC
    for idw, lst in data.items():
        taken = 0
        for a in lst:
            if a["noc"] in holders[idw]:
                continue
            give(a, "Qualificazione diretta (ranking olimpico)", "D",
                 detail=f"{a['place']}º nel ranking olimpico")
            taken += 1
            if taken == DIRECT_PLACES:
                break
        if taken < DIRECT_PLACES:
            notes.append(f"{WEIGHTS[idw][0]} {label(idw)}: solo {taken} atleti disponibili per la qualificazione diretta")

    # 2. paese ospitante: un posto per categoria
    for idw, lst in data.items():
        if HOST in holders[idw]:
            nxt = next((a for a in lst if a["noc"] not in holders[idw]), None)
            if nxt:
                give(nxt, "Qualificazione diretta (posto ospitante riassegnato)", "D",
                     detail=f"{nxt['place']}º nel ranking olimpico; il posto {HOST} è stato riassegnato "
                            f"perché {HOST} è già qualificato in diretta")
            else:
                notes.append(f"{WEIGHTS[idw][0]} {label(idw)}: posto ospitante non riassegnabile")
            continue
        usa = next((a for a in lst if a["noc"] == HOST), None)
        if usa:
            give(usa, f"Paese ospitante ({HOST})", "H", detail=f"{usa['place']}º nel ranking olimpico")
        else:
            notes.append(f"{WEIGHTS[idw][0]} {label(idw)}: nessun atleta {HOST} in classifica: posto ospitante da assegnare")
            assign.append({"a": None, "idw": idw, "via": f"Paese ospitante ({HOST})", "tipo": "H",
                           "status": "In attesa", "detail": f"nessun atleta {HOST} in classifica"})

    # 3. posti continentali (un atleta per NOC in tutte le categorie e in entrambi i generi)
    cont_noc = set()
    unused = {"Uomini": 0, "Donne": 0}
    by_cont = {}
    for idw, lst in data.items():
        for a in lst:
            if a["cont"]:
                by_cont.setdefault((a["cont"], a["gender"]), []).append(a)
    for key in by_cont:
        by_cont[key].sort(key=lambda a: (-a["pts"], a["place"], a["order"]))

    def eligible(a):
        return (a["noc"] not in cont_noc and a["noc"] not in holders[a["idw"]]
                and a["pid"] not in qualified[a["idw"]])

    for cont in CONT_ORDER:
        ptr = {"Uomini": 0, "Donne": 0}
        count = {"Uomini": 0, "Donne": 0}
        done = {"Uomini": False, "Donne": False}
        quota_c = CONT_QUOTA[cont]
        while not all(done[g] or count[g] >= quota_c for g in done):
            for g in ("Uomini", "Donne"):
                if done[g] or count[g] >= quota_c:
                    continue
                lst = by_cont.get((cont, g), [])
                while ptr[g] < len(lst) and not eligible(lst[ptr[g]]):
                    ptr[g] += 1
                if ptr[g] >= len(lst):
                    done[g] = True
                    continue
                a = lst[ptr[g]]
                ptr[g] += 1
                cont_noc.add(a["noc"])
                count[g] += 1
                give(a, f"Quota continentale ({cont})", "C",
                     detail=f"{count[g]}º posto continentale {cont} ({g.lower()}); "
                            f"{a['pts']:g} punti, {a['place']}º nel ranking olimpico del peso")
        for g in ("Uomini", "Donne"):
            if count[g] < quota_c:
                unused[g] += quota_c - count[g]
                notes.append(f"{cont} {g.lower()}: assegnati {count[g]} posti continentali su {quota_c}; "
                             f"{quota_c - count[g]} riassegnati al miglior atleta non qualificato")

    # 3b. posti continentali non usati: miglior atleta non qualificato dello stesso genere
    for g in ("Uomini", "Donne"):
        pool = sorted((a for idw, lst in data.items() if WEIGHTS[idw][0] == g for a in lst),
                      key=lambda a: (-a["pts"], a["place"], a["order"]))
        for _ in range(unused[g]):
            a = next((x for x in pool if eligible(x)), None)
            if not a:
                notes.append(f"{g}: posto continentale non assegnabile")
                continue
            cont_noc.add(a["noc"])
            give(a, "Quota continentale (riassegnata)", "C",
                 detail=f"posto continentale non usato dal continente, assegnato al miglior atleta non "
                        f"qualificato; {a['pts']:g} punti")

    # 4. riserve: i migliori non qualificati della categoria (una per NOC)
    reserves = []
    for idw, lst in data.items():
        n, used = 0, set(holders[idw])
        for a in lst:
            if a["pid"] in qualified[idw] or a["noc"] in used:
                continue
            reserves.append({"a": a, "via": "Riserva (ranking olimpico)", "tipo": "R", "status": "Riserva",
                             "detail": f"{a['place']}º nel ranking olimpico, prima riserva" if n == 0 else
                                       f"{a['place']}º nel ranking olimpico, riserva n.{n + 1}"})
            used.add(a["noc"])
            n += 1
            if n == RESERVES:
                break
    assign += reserves

    # 5. posti non ancora assegnabili dal ranking
    for i in range(UNIVERSALITY_PLACES):
        assign.append({"a": None, "idw": None, "via": "Universality", "tipo": "U", "status": "In attesa",
                       "detail": "assegnato dalla Commissione Tripartita (richieste dal 1 ottobre 2027)",
                       "n": i + 1})
    for i, c in enumerate(TEAM_INVITES):
        assign.append({"a": None, "idw": None, "via": "Invito squadra mista", "tipo": "M", "status": "In attesa",
                       "detail": f"invito squadra mista: {c}", "n": i + 1, "cont": c})
    return assign, notes


# ------------------------------------------------------------------ righe e file
def to_rows(assign, rankdate):
    rows = []
    for r in assign:
        a = r["a"]
        if a is None and r["tipo"] == "H":
            g, w = WEIGHTS[r["idw"]]
            rows.append({"name": f"(da assegnare) Ospitante {HOST}", "noc": HOST, "country": "United States",
                         "cont": "Americhe", "gender": g, "cat": f"{w} kg", "via": r["via"], "status": r["status"],
                         "rank": None, "points": None, "detail": r["detail"], "tipo": "H",
                         "id": f"H|{r['idw']}", "rankdate": rankdate})
        elif a is None and r["tipo"] == "U":
            rows.append({"name": f"(da assegnare) Universality n.{r['n']}", "noc": "", "country": "",
                         "cont": "", "gender": "Misto", "cat": "Universality", "via": r["via"], "status": r["status"],
                         "rank": None, "points": None, "detail": r["detail"], "tipo": "U",
                         "id": f"U|{r['n']}", "rankdate": rankdate})
        elif a is None:
            rows.append({"name": f"(da assegnare) {r['detail']}", "noc": "", "country": "", "cont": r.get("cont", ""),
                         "gender": "Misto", "cat": "Squadra mista", "via": r["via"], "status": r["status"],
                         "rank": None, "points": None, "detail": r["detail"], "tipo": "M",
                         "id": f"M|{r['n']}", "rankdate": rankdate})
        else:
            rows.append({"name": a["name"], "noc": a["noc"], "country": a["country"], "cont": a["cont"],
                         "gender": a["gender"], "cat": f"{a['wname']} kg", "via": r["via"], "status": r["status"],
                         "rank": a["place"], "points": a["pts"], "detail": r["detail"], "tipo": r["tipo"],
                         "id": f"{a['pid']}|{a['idw']}|{r['tipo']}", "rankdate": rankdate})
    order = {"D": 0, "H": 1, "C": 2, "R": 3, "M": 4, "U": 5}
    gord = {"Uomini": 0, "Donne": 1, "Misto": 2}
    rows.sort(key=lambda x: (gord[x["gender"]], WEIGHT_ORDER.get(x["cat"], 99), order[x["tipo"]],
                             x["rank"] if x["rank"] is not None else 9999))
    return rows


WEIGHT_ORDER = {f"{w} kg": i for i, (_, w) in WEIGHTS.items()}


def print_rows(rows):
    cur = None
    for r in rows:
        key = (r["gender"], r["cat"])
        if key != cur:
            cur = key
            print(f"\n=== {r['gender']} {r['cat']} ===")
        rk = r["rank"] if r["rank"] is not None else "-"
        print(f"{r['tipo']} | {rk:>3} | {r['name'][:34]:<34} | {r['noc']:<3} | {r['status']:<11} | {r['via']}")


def summary(rows):
    n = {}
    for r in rows:
        n[r["tipo"]] = n.get(r["tipo"], 0) + 1
    print("\nRiepilogo righe:",
          ", ".join(f"{k}={v}" for k, v in sorted(n.items())),
          "(D diretta, H ospitante, C continentale, R riserva, M squadra mista, U universality)")
    quals = [r for r in rows if r["tipo"] in ("D", "H", "C") and r["name"][:1] != "("]
    print(f"Atleti qualificati in simulazione: {len(quals)} su {len({r['noc'] for r in quals})} NOC")
    for g in ("Uomini", "Donne"):
        print(f"  {g}: {sum(1 for r in quals if r['gender'] == g)} "
              f"(diretti {sum(1 for r in quals if r['gender'] == g and r['tipo'] == 'D')}, "
              f"ospitante {sum(1 for r in quals if r['gender'] == g and r['tipo'] == 'H')}, "
              f"continentali {sum(1 for r in quals if r['gender'] == g and r['tipo'] == 'C')})")


def build_json(rows, rankdate, notes):
    cats = {}
    for idw, (g, w) in WEIGHTS.items():
        key = f"{'M' if g == 'Uomini' else 'F'} {w}"
        cats[key] = {"genere": "M" if g == "Uomini" else "F", "kg": w, "nome": f"{w} kg", "atleti": [], "riserve": [],
                     "da_assegnare": 0}
    for r in rows:
        if r["tipo"] in ("M", "U"):
            continue
        key = f"{'M' if r['gender'] == 'Uomini' else 'F'} {r['cat'].replace(' kg', '')}"
        if r["name"].startswith("("):
            cats[key]["da_assegnare"] += 1
            continue
        item = {"nome": r["name"], "noc": r["noc"], "paese": r["country"], "continente": r["cont"],
                "rank": r["rank"], "punti": r["points"], "tipo": r["tipo"], "via": r["via"],
                "stato": r["status"], "nota": r["detail"]}
        cats[key]["riserve" if r["tipo"] == "R" else "atleti"].append(item)
    totals = {"diretti": 0, "ospitanti": 0, "continentali": 0}
    for r in rows:
        if r["name"].startswith("("):
            continue
        if r["tipo"] == "D":
            totals["diretti"] += 1
        elif r["tipo"] == "H":
            totals["ospitanti"] += 1
        elif r["tipo"] == "C":
            totals["continentali"] += 1
    return {
        "aggiornato": rankdate, "fine_periodo": END_OF_PERIOD.isoformat(),
        "totali": totals,
        "universality": UNIVERSALITY_PLACES, "squadra_mista": len(TEAM_INVITES),
        "quote_continentali": CONT_QUOTA,
        "categorie": cats, "note": notes,
    }


# --------------------------------------------------------------------- Notion
COLUMNS = {
    "gender": ["Genere"], "cat": ["Categoria"], "via": ["Via di qualificazione", "Via"], "status": ["Stato"],
    "noc": ["NOC"], "country": ["Nazione", "Paese"], "cont": ["Continente"], "rank": ["Ranking"],
    "points": ["Punti"], "detail": ["Dettagli"], "rankdate": ["Data classifica"], "sim": ["Simulazione"],
}
OPTIONAL = {"country", "cont", "points", "detail", "rankdate", "sim"}


def notion(method, path, **kwargs):
    r = None
    for attempt in range(4):
        r = requests.request(method, "https://api.notion.com/v1" + path, headers=NOTION_HEADERS,
                             timeout=30, **kwargs)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(float(r.headers.get("Retry-After", 2)) + attempt)
            continue
        break
    if not r.ok:
        raise RuntimeError(f"Notion ha risposto {r.status_code}: {r.text[:400]}")
    return r.json()


def read_schema():
    props = notion("GET", f"/databases/{DATABASE_ID}")["properties"]
    by_name = {n.strip().lower(): (n, p["type"]) for n, p in props.items()}
    mapping, missing = {}, []
    for key, options in COLUMNS.items():
        hit = next((by_name[o.lower()] for o in options if o.lower() in by_name), None)
        if hit:
            mapping[key] = hit
        elif key in OPTIONAL:
            print(f"Nota: nel database non c'e' la colonna '{options[0]}': quel dato non verra' scritto.")
        else:
            missing.append(options[0])
    titles = [(n, p["type"]) for n, p in props.items() if p["type"] == "title"]
    mapping["name"] = titles[0]
    if missing:
        die("nel database non trovo queste colonne: " + ", ".join(missing)
            + ". Colonne presenti: " + ", ".join(props.keys()))
    for k in ("gender", "cat", "via", "status"):
        if mapping[k][0] == titles[0][0]:
            die(f"la colonna '{mapping[k][0]}' e' la colonna titolo: il titolo deve contenere il nome dell'atleta.")
    return mapping


def notion_value(ptype, value):
    if ptype == "number":
        return {"number": None if value in (None, "") else float(value)}
    value = "" if value is None else str(value)
    if ptype == "title":
        return {"title": [{"text": {"content": value[:2000]}}]}
    if ptype == "rich_text":
        return {"rich_text": [{"text": {"content": value[:2000]}}]}
    if ptype == "select":
        return {"select": {"name": value.replace(",", " ")[:100]} if value else None}
    if ptype == "date":
        return {"date": {"start": value} if value else None}
    raise RuntimeError(f"Tipo di colonna non gestito: {ptype}. Usa testo, numero, selezione o data.")


def archive_all_rows():
    cursor, n = None, 0
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        data = notion("POST", f"/databases/{DATABASE_ID}/query", json=body)
        for page in data["results"]:
            notion("PATCH", f"/pages/{page['id']}", json={"archived": True})
            n += 1
            time.sleep(0.3)
        if not data.get("has_more"):
            return n
        cursor = data["next_cursor"]


def write_rows(mapping, rows, sim_label):
    done = errors = 0
    for r in rows:
        values = dict(r)
        values["sim"] = sim_label
        props = {name: notion_value(ptype, values.get(key, "")) for key, (name, ptype) in mapping.items()}
        try:
            notion("POST", "/pages", json={"parent": {"database_id": DATABASE_ID}, "properties": props})
            done += 1
            if done % 50 == 0:
                print(f"  ... scritte {done} righe", flush=True)
            time.sleep(0.35)
        except RuntimeError as e:
            errors += 1
            print(f"  Errore su {r['name']}: {e}")
    return done, errors


# ----------------------------------------------------------------------- main
def main():
    if not DRY_RUN and (not NOTION_TOKEN or not DATABASE_ID):
        die("mancano NOTION_TOKEN o NOTION_DATABASE_ID (controlla i Secrets su GitHub).")
    print("Leggo il ranking olimpico IJF (ijf.org/wrl_olympic) ...")
    notes = []
    data = load_ranking(notes)
    if not any(data.values()):
        die("non sono riuscito a leggere nessuna classifica olimpica.")
    first = next(a for lst in data.values() for a in lst)
    print("\nEsempio di riga letta:", {k: first[k] for k in ("name", "noc", "pts", "place", "cont", "gender", "wname")})
    assign, more = simulate(data)
    notes += more
    notes.append("Regola di assegnazione dei posti continentali (il regolamento non la precisa): si procede per "
                 "continente, alternando uomini e donne, dal punteggio più alto; un atleta per NOC in tutte le "
                 "categorie e in entrambi i generi, e solo in una categoria dove la NOC non ha già un posto.")
    notes.append("Gli atleti di qualunque NOC presente in classifica sono considerati, salvo quelli esclusi dalla "
                 "classifica IJF.")
    rankdate = date.today().isoformat()
    rows = to_rows(assign, rankdate)
    print_rows(rows)
    summary(rows)
    print()
    for n in notes:
        print("NOTA:", n)
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(build_json(rows, rankdate, notes), f, ensure_ascii=False, indent=1)
    print(f"\nFile per la pagina grafica scritto: {JSON_OUT}")
    if DRY_RUN:
        print(f"\nProva terminata: {len(rows)} righe preparate, non e' stato scritto nulla su Notion.")
        return
    mapping = read_schema()
    if MODALITA.startswith("sostit"):
        print(f"Modalita' sostituzione: archiviate {archive_all_rows()} righe della simulazione precedente.")
    sim_label = datetime.now().strftime("%Y-%m-%d %H:%M")
    done, errors = write_rows(mapping, rows, sim_label)
    print(f"\nFatto. Righe scritte: {done}. Errori: {errors}. Simulazione: {sim_label}.")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    code = 0
    try:
        main()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    except Exception:
        import traceback
        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
