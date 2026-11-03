#!/usr/bin/env python3
"""Anti-duplicados de Propuestas Claude (Manual §24.5).
Marca Descartada las filas repetidas (misma fecha, mismo partido y misma seleccion) de los proximos dias.
Se queda con la que ya esta enlazada en Picks de Claude (Propuesta origen) o, si no hay, con la mas antigua.
Nunca toca filas ya liquidadas ni filas ya descartadas. Env: NOTION_TOKEN. DRY=1 no escribe."""
import datetime as dt, json, os, re, time, unicodedata, urllib.error, urllib.request

PROP_DS = "3355f707-917c-48ef-b5bc-0fe8c66fcd89"
LEDGER_DS = "35554701-4df3-4a2c-942b-f25654585ead"
TOKEN = os.environ["NOTION_TOKEN"]
DRY = os.environ.get("DRY") == "1"


def notion(method, path, body=None):
    for i in range(4):
        req = urllib.request.Request("https://api.notion.com/v1/" + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": "Bearer " + TOKEN, "Notion-Version": "2025-09-03",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            msg = e.read().decode()[:200]
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
    return ""


def norm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9. ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def pid(u):
    m = re.search(r"([0-9a-f]{32})", (u or "").replace("-", ""))
    return m.group(1) if m else ""


def main():
    hoy = dt.date.today()
    d0, d1 = (hoy - dt.timedelta(days=1)).isoformat(), (hoy + dt.timedelta(days=3)).isoformat()
    rows = query_all(PROP_DS, {"and": [
        {"property": "Fecha", "date": {"on_or_after": d0}},
        {"property": "Fecha", "date": {"on_or_before": d1}},
        {"property": "Decisión", "select": {"does_not_equal": "Descartada"}}]})
    linked = set()
    try:
        for r in query_all(LEDGER_DS, {"property": "Propuesta origen", "url": {"is_not_empty": True}}):
            linked.add(pid(txt(r["properties"].get("Propuesta origen"))))
    except Exception as e:
        print("Aviso: no se pudo leer Picks de Claude (", str(e)[:100], "); se conserva la fila mas antigua")
    grupos = {}
    for r in rows:
        p = r["properties"]
        sel = norm(txt(p.get("Selección")))
        if not sel: continue
        k = (txt(p.get("Fecha"))[:10], norm(txt(p.get("Partido"))), sel)
        grupos.setdefault(k, []).append(r)
    n = 0
    for k, g in grupos.items():
        if len(g) < 2: continue
        g.sort(key=lambda r: (pid(r["id"]) not in linked, r["created_time"]))
        for r in g[1:]:
            if txt(r["properties"].get("Resultado")) not in ("", "Pendiente"):
                continue
            n += 1
            print(f"Duplicado descartado: {k[0]} | {k[1]} | {k[2]} ({r['id'][:8]})")
            if not DRY:
                notion("PATCH", f"pages/{r['id']}", {"properties": {"Decisión": {"select": {"name": "Descartada"}}}})
    print(f"Revisadas {len(rows)} filas; duplicados descartados: {n}")


if __name__ == "__main__":
    main()
