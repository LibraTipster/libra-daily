#!/usr/bin/env python3
"""Post-mortem AUTOMATICO de cada pick Perdio (Manual §24.2 punto 2 y §24.6).
Lee el "Dato real" que ya escribe liquidar.py ("... -> valor vs linea") y escribe en la columna "Leccion"
la causa que se puede decidir solo con numeros:
  (a) varianza normal: el dato real quedo a 1 o menos de la linea;
  (f) correlacion probable: 2 o mas picks del mismo mercado cayeron en el mismo partido;
  REVISAR: dato muy lejos de la linea o sin formato numerico; la tarea diaria completa la causa (d)/(b)/(c)/(e)/(g).
No usa internet salvo la API de Notion. No imprime nombres de partidos (el repositorio es publico).
Env: NOTION_TOKEN. DRY=1 no escribe. DAYS_BACK (30 por defecto)."""
import datetime as dt, json, os, re, time, urllib.error, urllib.request

PROP_DS = "3355f707-917c-48ef-b5bc-0fe8c66fcd89"
TOKEN = os.environ["NOTION_TOKEN"]
DRY = os.environ.get("DRY") == "1"
DAYS_BACK = int(os.environ.get("DAYS_BACK", "30"))
LEJOS = {"Goles": 2, "Ambos anotan": 99, "Doble oportunidad": 99, "Ganador": 99}  # resto: 3
PAT = re.compile(r"(?:→|->)\s*(-?\d+(?:[.,]\d+)?)\s*vs\s*(-?\d+(?:[.,]\d+)?)")


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


def val(p):
    if not p: return None
    t = p["type"]; x = p.get(t)
    if t in ("title", "rich_text"): return "".join(s.get("plain_text", "") for s in x)
    if t == "select": return x["name"] if x else None
    if t == "number": return x
    if t == "date": return (x or {}).get("start")
    return None


def num(s): return float(s.replace(",", "."))


def main():
    desde = (dt.date.today() - dt.timedelta(days=DAYS_BACK)).isoformat()
    rows = query_all(PROP_DS, {"and": [
        {"property": "Resultado", "select": {"equals": "Perdió"}},
        {"property": "Decisión", "select": {"does_not_equal": "Descartada"}},
        {"property": "Fecha", "date": {"on_or_after": desde}}]})
    por_partido = {}
    for r in rows:
        p = r["properties"]
        k = ((val(p.get("Fecha")) or "")[:10], (val(p.get("Partido")) or "").lower(), val(p.get("Mercado")))
        por_partido[k] = por_partido.get(k, 0) + 1
    cuenta, nuevas = {}, 0
    for r in rows:
        p = r["properties"]
        if (val(p.get("Lección")) or "").strip():
            continue
        fecha = (val(p.get("Fecha")) or "")[:10]
        merc = val(p.get("Mercado")) or "Otro"
        dato = val(p.get("Dato real")) or ""
        if not dato.strip():  # picks liquidados a mano antes del 03/11: sin dato por fila, no se inventa la lección
            cuenta["sin_dato_se_omite"] = cuenta.get("sin_dato_se_omite", 0) + 1
            continue
        m = PAT.search(dato)
        pr, cu = val(p.get("Prob. estimada (%)")), val(p.get("Cuota casa"))
        base = f"{fecha} · {merc} · prob {pr if pr is not None else '?'}% · cuota {cu if cu is not None else '?'}"
        if not m:
            causa, clave = "REVISAR: el Dato real no trae el formato 'valor vs línea'; buscar la causa (g) o (d)", "revisar_sin_formato"
            txt = f"{base} · dato: {dato[:80] or 'vacío'} · {causa}"
        else:
            v, ln = num(m.group(1)), num(m.group(2))
            mar = abs(v - ln)
            lejos = LEJOS.get(merc, 3)
            if mar <= 1:
                causa, clave = "(a) varianza normal (dato a 1 o menos de la línea)", "a_varianza"
            elif mar >= lejos:
                causa, clave = f"REVISAR: dato muy lejos de la línea (margen {mar:g}); revisar contexto (d) expulsión, goleada, alineación, árbitro", "revisar_lejos"
            else:
                causa, clave = f"REVISAR: margen {mar:g} sin causa clara; comprobar (b) sobreestimación, (c) historial o (e) línea unilateral", "revisar_medio"
            if por_partido.get((fecha, (val(p.get('Partido')) or '').lower(), val(p.get('Mercado'))), 0) >= 2:
                causa += " + (f) correlación probable: 2 o más picks del mismo mercado cayeron en este partido"
            txt = f"{base} · dato {v:g} vs línea {ln:g} (margen {mar:g}) · causa: {causa}"
        cuenta[clave] = cuenta.get(clave, 0) + 1
        nuevas += 1
        if not DRY:
            notion("PATCH", f"pages/{r['id']}", {"properties": {"Lección": {"rich_text": [{"type": "text", "text": {"content": txt[:1900]}}]}}})
    print(f"Perdió revisados: {len(rows)}; lecciones nuevas: {nuevas}; por causa: {cuenta}")


if __name__ == "__main__":
    main()
