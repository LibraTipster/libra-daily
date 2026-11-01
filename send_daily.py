#!/usr/bin/env python3
"""Libra Tipster: imagen diaria con un abreboca de los picks de mañana, enviada al canal de Telegram.

Variables de entorno (secretos de GitHub):
  NOTION_TOKEN   clave de la integración de Notion (solo lectura)
  BOT_TOKEN      token del bot de Telegram (BotFather)
  CHANNEL_ID     id del canal o grupo (p. ej. -1001234567890)
Opcionales:
  MEMBERS_URL    enlace al área de miembros (va en el texto del mensaje)
  TARGET_DATE    AAAA-MM-DD; por defecto, mañana en Lima
  DRY_RUN=1      genera la imagen pero NO la envía
  SAMPLE=1       usa datos de ejemplo (no lee Notion)
"""
import base64, datetime as dt, html, json, os, sys, urllib.request, urllib.error, uuid

PROPUESTAS_DS = "3355f707-917c-48ef-b5bc-0fe8c66fcd89"  # Propuestas Claude (data source)
NOTION_VERSION = "2025-09-03"
MAX_SHOWN = 3
DIAS = ["LUN", "MAR", "MIÉ", "JUE", "VIE", "SÁB", "DOM"]
MESES = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]
ICONOS = {"Fútbol": "⚽", "Tenis": "🎾", "E-sports": "🎮", "Básquet": "🏀"}
HERE = os.path.dirname(os.path.abspath(__file__))


def lima_today():
    return (dt.datetime.utcnow() - dt.timedelta(hours=5)).date()


def http_json(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP {e.code} en {url}: {e.read().decode()[:400]}")


def prop(p, name):
    """Lee una propiedad de Notion según su tipo."""
    v = p.get(name)
    if not v:
        return None
    t = v["type"]
    x = v.get(t)
    if t in ("title", "rich_text"):
        return "".join(s.get("plain_text", "") for s in x) or None
    if t in ("select", "status"):
        return x["name"] if x else None
    if t == "number":
        return x
    if t == "checkbox":
        return bool(x)
    if t == "date":
        return x["start"] if x else None
    if t == "formula":
        return x.get(x["type"])
    return None


def fetch_rows(target):
    token = os.environ["NOTION_TOKEN"]
    hdr = {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"}
    body = {"page_size": 100,
            "filter": {"and": [
                {"property": "Fecha", "date": {"equals": target}},
                {"property": "Decisión", "select": {"does_not_equal": "Descartada"}},
            ]}}
    rows, cursor = [], None
    while True:
        if cursor:
            body["start_cursor"] = cursor
        r = http_json(f"https://api.notion.com/v1/data_sources/{PROPUESTAS_DS}/query", body, hdr, "POST")
        for pg in r["results"]:
            p = pg["properties"]
            rows.append({
                "partido": prop(p, "Partido"), "seleccion": prop(p, "Selección"), "liga": prop(p, "Liga"),
                "hora": prop(p, "Hora"), "deporte": prop(p, "Deporte") or "Fútbol",
                "seccion": prop(p, "Sección") or "Normal", "fuego": bool(prop(p, "Fuego")),
                "prob": prop(p, "Prob. estimada (%)"), "cuota": prop(p, "Cuota casa"),
                "resultado": prop(p, "Resultado") or "Pendiente",
            })
        if not r.get("has_more"):
            break
        cursor = r["next_cursor"]
    return [x for x in rows if x["partido"] and x["resultado"] == "Pendiente"]


def pick_teaser(rows):
    normal = [x for x in rows if x["seccion"] != "Valor" and x["prob"] and x["cuota"]]
    normal.sort(key=lambda x: (not x["fuego"], -x["prob"]))
    shown, seen = [], set()
    for x in normal:
        if x["partido"] in seen:
            continue
        seen.add(x["partido"])
        shown.append(x)
        if len(shown) == MAX_SHOWN:
            break
    return shown, max(len(rows) - len(shown), 0)


def sample_rows():
    return [
        {"partido": "Roma vs Lazio", "seleccion": "Menos de 3.5 goles", "liga": "Serie A", "hora": "11:00AM", "deporte": "Fútbol", "seccion": "Normal", "fuego": True, "prob": 79, "cuota": 1.25},
        {"partido": "Musetti vs Hurkacz", "seleccion": "Gana al menos un set", "liga": "ATP Masters 1000", "hora": "7:00AM", "deporte": "Tenis", "seccion": "Normal", "fuego": False, "prob": 84, "cuota": 1.28},
        {"partido": "Newcastle vs Everton", "seleccion": "Más de 7.5 córners", "liga": "Premier League", "hora": "3:00PM", "deporte": "Fútbol", "seccion": "Normal", "fuego": False, "prob": 78, "cuota": 1.33},
    ] + [{"partido": f"Otro {i}", "seleccion": "x", "seccion": "Normal", "prob": 75, "cuota": 1.3, "fuego": False, "deporte": "Fútbol", "liga": "x", "hora": "x"} for i in range(14)]


def b64(path):
    with open(os.path.join(HERE, "assets", path.replace(".png", ".b64"))) as f:
        return f.read().strip()


def fmt_hora(h):
    h = (h or "").strip()
    if h and h[-2:].upper() in ("AM", "PM") and " " not in h:
        return f"{h[:-2]} {h[-2:].upper()}"
    return h


def render_html(shown, rest, target):
    d = dt.date.fromisoformat(target)
    fecha = f"{DIAS[d.weekday()]} {d.day} {MESES[d.month - 1]}"
    cards = []
    for i, x in enumerate(shown):
        top = " · ".join(s for s in (html.escape(str(x["liga"] or "")).upper(), fmt_hora(x["hora"])) if s)
        cards.append(f"""<div class="card{' hot' if x['fuego'] else ''}"><div class=ic>{ICONOS.get(x['deporte'], '⚽')}</div>
<div class=mid><div class=lg>{top}</div><div class=mt>{html.escape(x['partido'])}</div><div class=pk>{html.escape(x['seleccion'] or '')}</div></div>
<div class=pr><b>{x['prob']:.0f}%</b><small>@ {x['cuota']:.2f}</small></div></div>""")
    locked = (f'<div class=locked><div class=lk>🔒</div><div><b>+ {rest} picks más</b> con probabilidad y análisis completo, solo para miembros</div></div>'
              if rest > 0 else "")
    tpl = open(os.path.join(HERE, "template.html"), encoding="utf8").read()
    return (tpl.replace("{{LION}}", b64("lion.png")).replace("{{WORD}}", b64("word.png"))
               .replace("{{FECHA}}", fecha).replace("{{CARDS}}", "\n".join(cards)).replace("{{LOCKED}}", locked))


def render_png(page_html, out):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1080, "height": 1350})
        pg.set_content(page_html, wait_until="networkidle")
        pg.wait_for_timeout(800)
        pg.screenshot(path=out)
        b.close()


def send_photo(png_path, caption):
    token, chat = os.environ["BOT_TOKEN"], os.environ["CHANNEL_ID"]
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in (("chat_id", chat), ("caption", caption)):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    with open(png_path, "rb") as f:
        img = f.read()
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="libra.png"\r\nContent-Type: image/png\r\n\r\n'.encode() + img + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendPhoto", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Telegram HTTP {e.code}: {e.read().decode()[:400]}")


def main():
    target = os.environ.get("TARGET_DATE") or (lima_today() + dt.timedelta(days=1)).isoformat()
    rows = sample_rows() if os.environ.get("SAMPLE") == "1" else fetch_rows(target)
    shown, rest = pick_teaser(rows)
    print(f"Fecha {target}: {len(rows)} picks, se muestran {len(shown)}, +{rest} más")
    if not shown:
        print("No hay picks Normal con probabilidad y cuota para mañana: no se envía nada.")
        return
    out = os.path.join(HERE, "libra_diaria.png")
    render_png(render_html(shown, rest, target), out)
    print("Imagen:", out)
    if os.environ.get("DRY_RUN") == "1" or os.environ.get("SAMPLE") == "1":
        print("DRY_RUN: no se envía a Telegram.")
        return
    url = os.environ.get("MEMBERS_URL", "").strip()
    caption = "Los picks de mañana ya están listos 🔥\nEntra a tu área de miembros de LibraTips Premium para ver todos los picks, cuotas y análisis." + (f"\n👉 {url}" if url else "")
    r = send_photo(out, caption)
    print("Telegram OK:", r.get("ok"))


if __name__ == "__main__":
    main()
