#!/usr/bin/env python3
"""Liquidación AUTOMÁTICA de picks de Libra.
Lee las propuestas con Resultado = Pendiente en Notion, busca el resultado real (ESPN) de los partidos
terminados, evalúa la selección y escribe Ganó / Perdió / Nulo + el dato real, en Propuestas Claude y en Picks de Claude.
Solo liquida partidos TERMINADOS. Si el dato no es verificable (las partes no cuadran con el total) no liquida y lo deja anotado.
Env: NOTION_TOKEN. Opcionales: DRY=1 (no escribe), DAYS_BACK (por defecto 14)."""
import datetime as dt, difflib, json, os, re, sys, time, unicodedata, urllib.error, urllib.request

PROP_DS = "3355f707-917c-48ef-b5bc-0fe8c66fcd89"
LEDGER_DS = "35554701-4df3-4a2c-942b-f25654585ead"
TOKEN = os.environ["NOTION_TOKEN"]
DRY = os.environ.get("DRY") == "1"
DAYS_BACK = int(os.environ.get("DAYS_BACK", "14"))
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36", "Accept": "application/json"}
LOG = []


def log(*a):
    s = " ".join(str(x) for x in a)
    LOG.append(s)
    print(s, flush=True)


# ---------------------------------------------------------------- Notion
def notion(method, path, body=None):
    for i in range(4):
        req = urllib.request.Request("https://api.notion.com/v1/" + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": "Bearer " + TOKEN, "Notion-Version": "2025-09-03", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            msg = e.read().decode()[:300]
            if e.code in (429, 500, 502, 503, 504) and i < 3:
                time.sleep(2 * (i + 1)); continue
            raise RuntimeError(f"Notion HTTP {e.code} {method} {path}: {msg}")


def query_all(ds, flt):
    out, cur = [], None
    while True:
        body = {"filter": flt, "page_size": 100}
        if cur: body["start_cursor"] = cur
        r = notion("POST", f"data_sources/{ds}/query", body)
        out += r["results"]
        if not r.get("has_more"): return out
        cur = r["next_cursor"]


def txt(p):
    if not p: return ""
    t = p["type"]; x = p.get(t)
    if t in ("title", "rich_text"): return "".join(s.get("plain_text", "") for s in x)
    if t in ("select", "status"): return x["name"] if x else ""
    if t == "url": return x or ""
    if t == "date": return (x or {}).get("start") or ""
    if t == "number": return x
    return ""


def rt(s):
    return [{"type": "text", "text": {"content": s[:1900]}}]


# ---------------------------------------------------------------- utilidades de nombres
_TR = str.maketrans({"ø": "o", "æ": "ae", "ð": "d", "þ": "th", "ł": "l", "đ": "d", "ß": "ss", "œ": "oe"})
ALIAS = {"copenhagen": "kobenhavn", "internazionale": "inter", "munchen": "munich", "koln": "cologne", "psg": "paris saint germain",
         "man": "manchester", "utd": "united", "sporting cp": "sporting", "malmo ff": "malmo", "gothenburg": "goteborg",
         "spurs": "tottenham", "wolves": "wolverhampton", "sheffield utd": "sheffield united"}


def norm(s):
    s = (s or "").lower().translate(_TR)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9. ]+", " ", s)
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", s)
    s = " " + re.sub(r"\s+", " ", s).strip() + " "
    for k, v in ALIAS.items():
        s = s.replace(" " + k + " ", " " + v + " ")
    return s.strip()


STOP = {"fc", "cf", "ac", "as", "sc", "cd", "ca", "us", "ud", "rc", "sv", "fk", "bk", "if", "afc", "club", "de", "the", "real", "sporting",
        "atletico", "athletic", "united", "city", "sp", "go", "ce", "al", "mg", "la", "el", "los", "del", "y", "vs", "sk", "ff", "cp", "sad", "ik", "fodbold",
        "stockholm", "town", "rovers", "wanderers", "county", "borussia", "deportivo", "atletica", "calcio", "futbol", "football", "kv", "krc", "rsc", "ssc"}
RES = re.compile(r"\b(ii|iii|b|u17|u18|u19|u20|u21|u23|jong|reserves?|youth|women|w|res)\b")


def is_res(n):
    return bool(RES.search(norm(n)))


def words(n):
    return [w for w in RES.sub(" ", norm(n)).split() if w]


def toks(name):
    return [w for w in words(name) if len(w) >= 3 and w not in STOP]


def sim(a, b):
    if is_res(a) != is_res(b): return 0.0
    ta, tb = set(toks(a)), set(toks(b))
    s1 = 0.0
    if ta and tb:
        com = {x for x in ta if any(x == y or (len(x) >= 5 and len(y) >= 5 and (x in y or y in x)) for y in tb)}
        s1 = len(com) / min(len(ta), len(tb))
    wa, wb = words(a), words(b)
    s3 = 0.0
    if wa and wb:
        sh, lo = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
        if all(lo[i].startswith(sh[i]) for i in range(len(sh))): s3 = 0.9
    s2 = difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()
    return max(s1, s3, s2 if s2 >= 0.78 else 0)


def http(url):
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            err = e
            time.sleep(1.5 * (i + 1))
    log("  ! no se pudo leer", url[:110], str(err)[:80])
    return None


# ---------------------------------------------------------------- selección -> regla
def parse(sel, home, away):
    n = norm(sel); r = {}
    if re.search(r"ambos anotan|btts|ambos equipos", n): r["stat"] = "btts"
    elif re.search(r"corner", n): r["stat"] = "corners"
    elif re.search(r"tarjeta", n): r["stat"] = "cards"
    elif re.search(r"offside|fuera de juego|fueras de juego", n): r["stat"] = "offsides"
    elif re.search(r"falta", n): r["stat"] = "fouls"
    elif re.search(r"a puerta|al arco|on target", n): r["stat"] = "sot"
    elif re.search(r"remate|tiro", n): r["stat"] = "shots"
    elif re.search(r"gol|anota", n): r["stat"] = "goals"
    else: r["stat"] = "?"
    if re.search(r"primer tiempo|1 er|1er|1t|primera mitad|1st half|1 tiempo|primer t", n): r["per"] = "FH"
    elif re.search(r"segundo tiempo|2 o tiempo|2o tiempo|2t|segunda mitad|2 do|2do|2 tiempo", n): r["per"] = "SH"
    else: r["per"] = "All"
    m = re.search(r"(mas|menos)\s*(?:de\s*)?(\d+\.\d)", n)
    if m:
        r["dir"] = m.group(1); r["line"] = float(m.group(2))
    else:
        m2 = re.search(r"(\d+\.\d)", n)
        if m2:
            r["line"] = float(m2.group(1))
            if re.search(r"mas", n): r["dir"] = "mas"
            elif re.search(r"menos", n): r["dir"] = "menos"
    if "anota" in n and "line" not in r:
        r["dir"] = "mas"; r["line"] = 0.5
    ht, at = toks(home), toks(away)
    hs = any(w in n for w in ht); as_ = any(w in n for w in at)
    if re.search(r"totales|total", n) and not (hs or as_): r["scope"] = "tot"
    elif hs and not as_: r["scope"] = "home"
    elif as_ and not hs: r["scope"] = "away"
    else: r["scope"] = "tot"
    return r


def verdict(v, r):
    if v == r["line"]: return "Nulo"
    if r["dir"] == "mas": return "Ganó" if v > r["line"] else "Perdió"
    return "Ganó" if v < r["line"] else "Perdió"


# ---------------------------------------------------------------- fútbol (ESPN)
_sb = {}


def scoreboard(d):
    k = d.strftime("%Y%m%d")
    if k not in _sb:
        j = http(f"https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard?dates={k}&limit=1000")
        _sb[k] = (j or {}).get("events", [])
    return _sb[k]


def ev_names(e):
    return [c["team"].get("displayName", "") for c in e["competitions"][0]["competitors"]]


def find_event(home, away, d):
    evs = []
    for off in (0, 1, -1):
        evs += scoreboard(d + dt.timedelta(days=off))
    best, anchor = None, []
    for e in evs:
        names = ev_names(e)
        if len(names) != 2: continue
        s_direct = min(sim(home, names[0]), sim(away, names[1]))
        s_swap = min(sim(home, names[1]), sim(away, names[0]))
        s = max(s_direct, s_swap)
        if s >= 0.5 and (best is None or s > best[0]):
            best = (s, e, s_swap > s_direct)
        else:
            h = max(sim(home, names[0]), sim(home, names[1])); a = max(sim(away, names[0]), sim(away, names[1]))
            if max(h, a) >= 0.85 and (is_res(home) == is_res(away)):
                anchor.append((e, sim(home, names[1]) > sim(home, names[0]) if h >= a else sim(away, names[0]) > sim(away, names[1])))
    if best: return best
    if len(anchor) == 1:
        log("  (ancla) usada")
        return (0.85, anchor[0][0], anchor[0][1])
    return None


_det = {}


def detail(eid):
    if eid not in _det:
        _det[eid] = http(f"https://site.web.api.espn.com/apis/site/v2/sports/soccer/all/summary?event={eid}")
    return _det[eid]


def stat_of(team_stats, name):
    for s in team_stats:
        if s.get("name") == name:
            try: return int(float(s.get("displayValue") or 0))
            except Exception: return 0
    return 0


def minute_of(c):
    m = re.match(r"(\d+)", (c.get("time") or {}).get("displayValue", "") or "")
    return int(m.group(1)) if m else None


def build_match(ev, summ, home, away):
    """Devuelve dict por lado (0 = local del pick, 1 = visitante del pick) para All/FH/SH, con verificación."""
    hdr = summ["header"]["competitions"][0]["competitors"]
    # índice ESPN de cada lado del pick
    names = [c["team"].get("displayName", "") for c in hdr]
    idx = [1, 0] if min(sim(home, names[1]), sim(away, names[0])) > min(sim(home, names[0]), sim(away, names[1])) else [0, 1]
    box = {}
    for t in summ["boxscore"]["teams"]:
        box[str(t["team"]["id"])] = t["statistics"]
    # los competitors del header traen 'id' del equipo
    teams = []
    for k in range(2):
        e = hdr[idx[k]]
        tid = str(e.get("id") or e["team"]["id"])
        st = box.get(tid) or box.get(str(e["team"]["id"])) or []
        teams.append({"name": names[idx[k]], "tid": tid, "st": st, "score": int(float(e.get("score") or 0)),
                      "ls": [int(float(x.get("displayValue") or 0)) for x in (e.get("linescores") or [])]})
    m = {"fin": ev["competitions"][0]["status"]["type"].get("completed", False), "teams": teams, "ok": {}}
    # totales del box
    key = {"corners": "wonCorners", "shots": "totalShots", "sot": "shotsOnTarget", "offsides": "offsides", "fouls": "foulsCommitted",
           "yellow": "yellowCards", "red": "redCards"}
    tot = {s: [stat_of(teams[k]["st"], key[s]) for k in range(2)] for s in key}
    per = {s: {"FH": [0, 0], "SH": [0, 0]} for s in key}
    # comentario minuto a minuto -> mitades
    half = 1
    for c in sorted(summ.get("commentary") or [], key=lambda c: c.get("sequence", 0)):
        t = c.get("text") or ""
        pn = ((c.get("play") or {}).get("period") or {}).get("number")
        if re.match(r"Second Half begins", t): half = 2
        elif re.match(r"First Half ends|Match ends|Half Time", t) and half == 1 and pn == 2: half = 2
        h = pn if pn in (1, 2) else half
        h = "FH" if h == 1 else "SH"
        def side(name):
            if not name: return None
            best = [sim(name, teams[0]["name"]), sim(name, teams[1]["name"])]
            if max(best) < 0.5: return None
            return 0 if best[0] >= best[1] else 1
        mm = re.match(r"Corner, ([^.]+)\.", t)
        if mm:
            s = side(mm.group(1)); per["corners"][h][s] += 1 if s is not None else 0; continue
        mm = re.match(r"Offside, ([^.]+)\.", t)
        if mm:
            s = side(mm.group(1)); per["offsides"][h][s] += 1 if s is not None else 0; continue
        pm = re.search(r"\(([^)]+)\)", t)
        s = side(pm.group(1)) if pm else None
        if s is None: continue
        if re.match(r"Foul by", t): per["fouls"][h][s] += 1
        elif "is shown the yellow card" in t: per["yellow"][h][s] += 1
        elif "is shown the red card" in t: per["red"][h][s] += 1
        elif re.match(r"Attempt (saved|missed|blocked)", t) or re.match(r"Goal!", t) or re.match(r"Penalty - Scored", t) or re.match(r"Attempt", t):
            per["shots"][h][s] += 1
            if t.startswith("Attempt saved") or t.startswith("Goal!") or t.startswith("Penalty - Scored"): per["sot"][h][s] += 1
    m["tot"], m["per"] = tot, per
    # verificación: mitades suman el total
    for s in key:
        okk = all(per[s]["FH"][k] + per[s]["SH"][k] == tot[s][k] for k in range(2))
        m["ok"][s] = okk
    # goles por mitad (linescores) verificados contra marcador
    g_ok = all(len(teams[k]["ls"]) >= 2 and sum(teams[k]["ls"][:2]) == teams[k]["score"] for k in range(2))
    m["ok"]["goals"] = g_ok
    return m


def football_value(m, r):
    """(valor, texto, verificado)"""
    st, per, sc = r["stat"], r["per"], r["scope"]
    T = m["teams"]
    def pick(a):
        return a[0] + a[1] if sc == "tot" else a[0] if sc == "home" else a[1]
    if st == "goals":
        if per == "All": v = pick([T[0]["score"], T[1]["score"]]); return v, f"goles {T[0]['score']}-{T[1]['score']}", True
        i = 0 if per == "FH" else 1
        g = [T[0]["ls"][i] if len(T[0]["ls"]) > i else 0, T[1]["ls"][i] if len(T[1]["ls"]) > i else 0]
        return pick(g), f"goles {'1T' if i == 0 else '2T'} {g[0]}-{g[1]}", m["ok"]["goals"]
    if st == "cards":
        def c(k, p):
            if p == "All": return m["tot"]["yellow"][k] + 2 * m["tot"]["red"][k]
            return m["per"]["yellow"][p][k] + 2 * m["per"]["red"][p][k]
        a = [c(0, per), c(1, per)]
        okk = True if per == "All" else (m["ok"]["yellow"] and m["ok"]["red"])
        return pick(a), f"tarjetas {a[0]}-{a[1]} (amarillas+2×rojas)", okk
    key = {"corners": "corners", "shots": "shots", "sot": "sot", "offsides": "offsides", "fouls": "fouls"}.get(st)
    if not key: return None, "", False
    a = m["tot"][key] if per == "All" else m["per"][key][per]
    okk = True if per == "All" else m["ok"][key]
    label = {"corners": "córners", "shots": "remates", "sot": "remates a puerta", "offsides": "fueras de juego", "fouls": "faltas"}[key]
    pl = "" if per == "All" else (" 1T" if per == "FH" else " 2T")
    return pick(a), f"{label}{pl} {a[0]}-{a[1]}", okk


def eval_football(p):
    d = dt.date.fromisoformat(p["fecha"][:10])
    if " vs " not in p["partido"]: return None, "partido sin formato"
    home, away = p["partido"].split(" vs ", 1)
    f = find_event(home, away, d)
    if not f: return None, "partido no encontrado en ESPN"
    _, ev, swapped = f
    if not ev["competitions"][0]["status"]["type"].get("completed"): return None, "no terminado"
    summ = detail(ev["id"])
    if not summ: return None, "sin detalle ESPN"
    m = build_match(ev, summ, home, away)
    r = parse(p["seleccion"], home, away)
    n = norm(p["seleccion"])
    T = m["teams"]
    if r["stat"] == "btts":
        if "tarjeta" in n or p["mercado"] == "Tarjetas":
            ch = m["tot"]["yellow"][0] + m["tot"]["red"][0]; ca = m["tot"]["yellow"][1] + m["tot"]["red"][1]
            cond = (not (ch >= 2 and ca >= 2)) if re.search(r"no reciben", n) else (ch >= 1 and ca >= 1)
            return ("Ganó" if cond else "Perdió"), f"tarjetas {ch}-{ca}"
        cond = T[0]["score"] >= 1 and T[1]["score"] >= 1
        if re.search(r"\bno\b", n) and "sí" not in p["seleccion"].lower(): cond = not cond
        return ("Ganó" if cond else "Perdió"), f"marcador {T[0]['score']}-{T[1]['score']}"
    if "line" not in r or "dir" not in r or r["stat"] == "?": return None, "selección no interpretada"
    v, txt_, ok = football_value(m, r)
    if v is None: return None, "selección no interpretada"
    if not ok: return None, "dato por mitad no verificable en ESPN"
    return verdict(v, r), f"{txt_} → {v} vs {r['line']}"


# ---------------------------------------------------------------- tenis (ESPN)
_tn = {}


def tennis_matches(d):
    out = []
    for off in (0, 1, -1):
        k = (d + dt.timedelta(days=off)).strftime("%Y%m%d")
        for tour in ("atp", "wta"):
            if (tour, k) not in _tn:
                j = http(f"https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard?dates={k}")
                _tn[(tour, k)] = (j or {}).get("events", [])
            for ev in _tn[(tour, k)]:
                for g in ev.get("groupings", []):
                    if "Singles" not in (g.get("grouping", {}).get("displayName", "")): continue
                    out += g.get("competitions", [])
    return out


def games(ls):
    return sum(int(re.match(r"\d+", str(x.get("value", x.get("displayValue", "0")))).group(0)) if re.match(r"\d+", str(x.get("value", x.get("displayValue", "0")))) else 0 for x in ls)


def eval_tennis(p):
    d = dt.date.fromisoformat(p["fecha"][:10])
    if " vs " not in p["partido"]: return None, "partido sin formato"
    a, b = p["partido"].split(" vs ", 1)
    for c in tennis_matches(d):
        cs = c.get("competitors", [])
        if len(cs) != 2 or not all(x.get("athlete") for x in cs): continue
        nm = [x["athlete"].get("displayName", "") for x in cs]
        s = max(min(sim(a, nm[0]), sim(b, nm[1])), min(sim(a, nm[1]), sim(b, nm[0])))
        if s < 0.6: continue
        stt = c["status"]["type"]["name"]
        if stt not in ("STATUS_FINAL", "STATUS_RETIRED", "STATUS_WALKOVER"): return None, "no terminado"
        sets = [[int(re.match(r"\d+", str(l.get("value", 0))).group(0)) for l in x.get("linescores", [])] for x in cs]
        tot_games = sum(sum(s_) for s_ in sets)
        nsets = max(len(sets[0]), len(sets[1]))
        w = [bool(x.get("winner")) for x in cs]
        retired = stt != "STATUS_FINAL"
        n = norm(p["seleccion"])
        mk = norm(p["mercado"])
        who = None
        for i in range(2):
            if sim(nm[i], p["seleccion"]) >= 0.5 or any(t in n for t in toks(nm[i])): who = i if who is None else who
        info = f"{nm[0]} {'-'.join(map(str, sets[0]))} / {nm[1]} {'-'.join(map(str, sets[1]))}" + (" (retiro)" if retired else "")
        if "games" in mk or "juegos" in mk:
            r = parse(p["seleccion"], a, b)
            if "line" not in r: return None, "selección no interpretada"
            if retired: return "Nulo", info + " — partido incompleto"
            return verdict(tot_games, r), f"{info} → {tot_games} games vs {r['line']}"
        if "set" in mk and who is not None:
            if retired: return "Nulo", info + " — partido incompleto"
            won = sum(1 for s1, s2 in zip(sets[who], sets[1 - who]) if s1 > s2)
            return ("Ganó" if won >= 1 else "Perdió"), f"{info} → sets ganados {won}"
        if "ganador" in mk and who is not None:
            if retired: return None, "retiro: revisar regla de la casa"
            return ("Ganó" if w[who] else "Perdió"), info
        return None, "mercado de tenis no interpretado"
    return None, "partido de tenis no encontrado en ESPN"


# ---------------------------------------------------------------- principal
def main():
    today = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)).date()
    lo = (today - dt.timedelta(days=DAYS_BACK)).isoformat()
    flt = {"and": [{"property": "Resultado", "select": {"equals": "Pendiente"}},
                   {"property": "Decisión", "select": {"does_not_equal": "Descartada"}},
                   {"property": "Fecha", "date": {"on_or_before": today.isoformat()}},
                   {"property": "Fecha", "date": {"on_or_after": lo}}]}
    pages = query_all(PROP_DS, flt)
    log(f"Pendientes con fecha <= hoy: {len(pages)}")
    settled, left, dato = 0, {}, {}
    blocked, queue = False, []
    for pg in pages:
        pr = pg["properties"]
        p = {"id": pg["id"], "partido": txt(pr.get("Partido")), "seleccion": txt(pr.get("Selección")), "mercado": txt(pr.get("Mercado")),
             "deporte": txt(pr.get("Deporte")), "fecha": txt(pr.get("Fecha"))}
        try:
            if p["deporte"] == "Fútbol": res, info = eval_football(p)
            elif p["deporte"] == "Tenis": res, info = eval_tennis(p)
            else: res, info = None, "deporte sin fuente automática (lo cubre la tarea diaria con búsqueda web)"
        except Exception as e:
            res, info = None, f"error: {e}"
        if res is None:
            left[pg["id"]] = info
            if info != "no terminado": log(f"  · pendiente: ...{pg['id'][-6:]} → {info}")
            continue
        dato[pg["id"]] = (res, info)
        log(f"  {res}: ...{pg['id'][-6:]} | {p['deporte']}")
        applied = False
        if not DRY and not blocked:
            props = {"Resultado": {"select": {"name": res}}, "Dato real": {"rich_text": rt(info)}}
            try:
                notion("PATCH", f"pages/{pg['id']}", {"properties": props})
                applied = True
            except RuntimeError as e:
                if "403" not in str(e): raise
                blocked = True
                log("AVISO: la integración de Notion no tiene permiso de edición (activar 'Update content' en la integración)")
        queue.append({"id": pg["id"], "url": "https://app.notion.com/p/" + pg["id"].replace("-", ""), "partido": p["partido"], "fecha": p["fecha"][:10],
                      "seleccion": p["seleccion"], "resultado": res, "dato_real": info, "aplicado": applied})
        settled += 1
    log(f"Propuestas liquidadas: {settled}; sin liquidar: {len(left)}")

    # espejo en Picks de Claude (ledger)
    try:
        led = query_all(LEDGER_DS, {"property": "Resultado", "select": {"equals": "Pendiente"}})
    except RuntimeError as e:
        log("AVISO: Picks de Claude no es accesible para la integración (conectarla en Notion: ··· > Conexiones). " + str(e)[:120])
        led = []
    mir = 0
    for pg in led:
        pr = pg["properties"]
        org = txt(pr.get("Propuesta origen"))
        mt = re.search(r"([0-9a-f]{32})", org.replace("-", "")) if org else None
        if not mt: continue
        pid = mt.group(1); pid = f"{pid[:8]}-{pid[8:12]}-{pid[12:16]}-{pid[16:20]}-{pid[20:]}"
        if pid in dato:
            res, info = dato[pid]
        else:
            src = notion("GET", f"pages/{pid}")
            rs = txt(src["properties"].get("Resultado"))
            if rs not in ("Ganó", "Perdió", "Nulo", "½ Ganó", "½ Perdió"): continue
            res, info = rs, txt(src["properties"].get("Dato real"))
        nota = txt(pr.get("Nota"))
        if "Dato real:" not in nota and info:
            nota = (nota + " | " if nota else "") + "Dato real: " + info
        if not DRY:
            notion("PATCH", f"pages/{pg['id']}", {"properties": {"Resultado": {"select": {"name": res}}, "Nota": {"rich_text": rt(nota)}}})
        mir += 1
    log(f"Picks de Claude (ledger) espejados: {mir} de {len(led)} pendientes")
    # resumen para el log de Actions
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("```\n" + "\n".join(LOG[-80:]) + "\n```\n")


if __name__ == "__main__":
    main()
