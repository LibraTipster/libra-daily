#!/usr/bin/env python3
"""Lee las propuestas de Claude desde Notion y deja _site/data.json CIFRADO (AES-GCM) para el área de miembros.
Env: NOTION_TOKEN, MEMBERS_KEY. Opcional: SAMPLE=1 (datos de ejemplo, sin Notion), OUT (carpeta, por defecto _site)."""
import base64, datetime as dt, hashlib, hmac, json, os, re, urllib.request, urllib.error
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

PROPUESTAS_DS = "3355f707-917c-48ef-b5bc-0fe8c66fcd89"
DAYS_BACK = 3
OUT = os.environ.get("OUT", "_site")


def lima_today():
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)).date()


def api(path, body, token):
    req = urllib.request.Request("https://api.notion.com/v1/" + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + token, "Notion-Version": "2025-09-03", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Notion HTTP {e.code}: {e.read().decode()[:300]}")


def flat(page):
    """Página de la API de Notion -> fila con el mismo formato que las vistas que lee la app."""
    row = {"url": "https://app.notion.com/p/" + page["id"].replace("-", "")}
    for name, v in page["properties"].items():
        t, x = v["type"], v.get(v["type"])
        if t in ("title", "rich_text"):
            row[name] = "".join(s.get("plain_text", "") for s in x)
        elif t in ("select", "status"):
            row[name] = x["name"] if x else None
        elif t == "multi_select":
            row[name] = ", ".join(o["name"] for o in x)
        elif t in ("number", "url"):
            row[name] = x
        elif t == "checkbox":
            row[name] = "__YES__" if x else "__NO__"
        elif t == "date":
            row["date:%s:start" % name] = x["start"] if x else None
            row["date:%s:is_datetime" % name] = 0
        elif t == "formula":
            row[name] = x.get(x["type"])
    return row


def max4(rows):
    """Regla de la casa: como máximo 4 picks Normal por partido (los de mayor probabilidad). Valor no se toca."""
    keep, per = [], {}
    for r in sorted(rows, key=lambda r: -(r.get("Prob. estimada (%)") or 0)):
        if r.get("Sección") != "Valor":
            k = (r.get("date:Fecha:start"), r.get("Partido"))
            per[k] = per.get(k, 0) + 1
            if per[k] > 4:
                continue
        keep.append(r)
    return keep


def fetch_props():
    token = os.environ["NOTION_TOKEN"]
    since = (lima_today() - dt.timedelta(days=DAYS_BACK)).isoformat()
    body = {"page_size": 100,
            "filter": {"and": [{"property": "Fecha", "date": {"on_or_after": since}},
                               {"property": "Decisión", "select": {"does_not_equal": "Descartada"}}]},
            "sorts": [{"property": "Fecha", "direction": "descending"}]}
    rows, cur = [], None
    while True:
        if cur:
            body["start_cursor"] = cur
        r = api(f"data_sources/{PROPUESTAS_DS}/query", body, token)
        rows += [flat(p) for p in r["results"]]
        if not r.get("has_more"):
            return max4(rows)
        cur = r["next_cursor"]


def sample():
    return [{"url": "https://app.notion.com/p/40bf13b8700e816c8f8fd1dfd84eee80", "Partido": "Leylah Fernandez vs Sofia Kenin", "Hora": "9:00PM", "Deporte": "Tenis",
             "Liga": "WTA 250 Hong Kong", "Mercado": "Ganador", "Selección": "Leylah Fernandez gana el partido", "Cuota mínima": 1.39, "Cuota casa": 1.32,
             "Prob. estimada (%)": 73.9, "Confianza": "Media", "Sección": "Normal", "Decisión": "Aceptada", "Resultado": "Pendiente", "En Picks": "__YES__", "Fuego": "__NO__",
             "Chequeo": "Completo", "Argumento": "Modelo: Fernandez gana 81.0%.<br />Mercado: 1.32 / 3.20.", "Razonamiento": "Precio verificado 1.32.",
             "date:Fecha:start": (lima_today() + dt.timedelta(days=1)).isoformat(), "date:Fecha:is_datetime": 0}]


def encrypt(obj, passphrase):
    plain = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    salt = b"libra-members-v1"
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=150000).derive(passphrase.encode())
    iv = hmac.new(key, plain, hashlib.sha256).digest()[:12]
    ct = AESGCM(key).encrypt(iv, plain, None)
    return {"v": 1, "iv": base64.b64encode(iv).decode(), "ct": base64.b64encode(ct).decode()}

# --- Marca: en la app de miembros no se menciona a Claude ---
def marca(x):
    if isinstance(x, str):
        return re.sub(r"Claude", "Libra", x.replace("claude.ai", "la app"))
    if isinstance(x, list):
        return [marca(i) for i in x]
    if isinstance(x, dict):
        return {k: marca(v) for k, v in x.items()}
    return x


# --- "Por qué": razón en español claro por pick (se respeta el campo "Por qué" de Notion si el análisis lo escribió) ---
INTERNO = re.compile(r"Worker|cumple\s*=|\bEV\b|OddsPapi|tarea local|presupuesto|corrida|cuotas\.activo|navegador|bo3\.gg|/es/|/matchup", re.I)

JERGA = [
    (r"\bDERIVADA\b", "calculada a partir de otra línea"),
    (r"Chequeo (Parcial|Completo)", ""),
    (r"\bcons(ervadora)?\b\s*[\d.]+%?", ""),
]


def _num(s):
    return float(s.replace(",", "."))


def _pct(x):
    return f"{x:.1f}%"


def _lineas(arg):
    out = {}
    for ln in re.split(r"<br\s*/?>|\n", arg or ""):
        m = re.match(r"\s*(Modelo|Mercado|Contexto|Alerta|Fuentes?)\s*:\s*(.*)$", ln.strip(), re.I)
        if m:
            out[m.group(1).lower().rstrip("s") if m.group(1).lower().startswith("fuente") else m.group(1).lower()] = m.group(2).strip()
    return out


def _forma(letras):
    letras = letras.upper()
    g, e, p = letras.count("G"), letras.count("E"), letras.count("P")
    partes = []
    if g:
        partes.append(f"{g} ganado{'s' if g != 1 else ''}")
    if e:
        partes.append(f"{e} empatado{'s' if e != 1 else ''}")
    if p:
        partes.append(f"{p} perdido{'s' if p != 1 else ''}")
    return f"últimos {len(letras)}: " + ", ".join(partes)


def _contexto(txt):
    """Reescribe el Contexto técnico en frases naturales."""
    if not txt:
        return ""
    t = txt.strip().rstrip(".")
    t = re.sub(r"(\S[^,;()]*?)\s+(\d+(?:\.\d+)?)\s*pts/p\s*\(([GEP]{3,8})\)",
               lambda m: f"{m.group(1).strip()} suma {m.group(2)} puntos por partido ({_forma(m.group(3))})", t)
    t = re.sub(r"(?<=, )([A-Za-zÀ-ÿ.]+)\s+(\d+(?:\.\d+)?)\s*\(([GEP]{3,8})\)",
               lambda m: f"{m.group(1)} {m.group(2)} ({_forma(m.group(3))})", t)
    t = re.sub(r"(?<=; )(\d+(?:\.\d+)?)\s*pts/p\s*\(([GEP]{3,8})\)",
               lambda m: f"promedia {m.group(1)} puntos por partido ({_forma(m.group(2))})", t)
    t = re.sub(r"descanso\s+(\d+)\s*d\s+ambos", r"ambos llegan con \1 días de descanso", t)
    t = re.sub(r"descanso\s+(\d+)\s*d\b", r"llega con \1 días de descanso", t)
    t = re.sub(r"(\d+)/(\d+)\s+(?:últimos|recientes)\s+con\s+(\d+)\+\s*(\w+)\s+propios",
               lambda m: f"en {m.group(1)} de sus últimos {m.group(2)} partidos sacó {m.group(3)} o más {m.group(4)} propios", t)
    t = re.sub(r"(\d+)/(\d+)\s+(?:últimos|recientes)\s+con\s+(\d+)\s+o menos\s+(\w+)",
               lambda m: f"en {m.group(1)} de los últimos {m.group(2)} partidos hubo {m.group(3)} {m.group(4)} o menos", t)
    t = re.sub(r"(\d+)/(\d+)\s+(?:últimos|recientes)\s+con\s+(\d+)\+\s*(\w+)",
               lambda m: f"en {m.group(1)} de los últimos {m.group(2)} partidos hubo {m.group(3)} o más {m.group(4)}", t)
    t = re.sub(r"\bpts/p\b", "puntos por partido", t)
    t = t.replace("; ", ". ")
    t = re.sub(r"(\. )([a-zà-ÿ])", lambda m: m.group(1) + m.group(2).upper(), t)
    t = t[0].upper() + t[1:] if t else t
    return t + ("" if t.endswith(".") else ".")


def _limpia_alerta(txt):
    """Deja solo lo que le sirve al miembro: sin notas internas, jerga ni restos de formato."""
    if not txt:
        return ""
    t = txt.strip()
    if re.match(r"^(ninguna|sin alertas?)", t, re.I):
        return ""
    t = re.sub(r"\bDERIVADA\b:?\s*(\([^)]*\)|cola, fuera de tabla)?", "probabilidad calculada a partir de otra línea", t)
    t = t.replace("el feed marca", "la fuente marca")
    out = []
    for f in re.split(r"[;.]\s+|;|\.$", t):
        f = re.sub(r"\(\s*\)", "", f)
        f = re.sub(r"Chequeo (Parcial|Completo)", "", f, flags=re.I)
        f = re.sub(r"\+?\s*Más only", "", f, flags=re.I)
        f = f.strip(" ;,.:+")
        if len(f) < 10 or INTERNO.search(f) or re.search(r"se escribe igual|m[ií]nima|ventaja\s*(:|no\b|sí\b)|piso\s*\d", f, re.I):
            continue
        if f.startswith("calculada"):
            f = "probabilidad " + f
        if f not in out:
            out.append(f)
    return ("; ".join(out) + ".") if out else ""


def _modelo_mercado(l, row):
    modelo = l.get("modelo", "")
    merc = l.get("mercado", "")
    mm = re.search(r"=\s*([\d.]+)\s*%", modelo) or re.search(r"([\d.]+)\s*%", modelo)
    odds = row.get("Cuota casa")
    imp = re.search(r"(?:sin margen|impl[ií]cito)\s*([\d.]+)\s*%", merc)
    prob = row.get("Prob. estimada (%)")
    partes = []
    if mm:
        partes.append(f"El modelo le da {_pct(_num(mm.group(1)))} de probabilidad")
        if prob is not None and abs(prob - _num(mm.group(1))) >= 0.8:
            partes[-1] += f" y, tras prudencia, Libra la deja en {_pct(prob)}"
    elif prob is not None:
        partes.append(f"Libra estima {_pct(prob)} de probabilidad")
    if odds:
        txt = f"la casa paga {odds:.2f}"
        if imp:
            txt += f" (equivale a {_pct(_num(imp.group(1)))})"
        partes.append(txt)
    if not partes:
        return ""
    return (partes[0] + (", " + partes[1] if len(partes) > 1 else "") + ".")


def _desde_razonamiento(r):
    """Respaldo cuando no hay Argumento estructurado (p. ej. tenis/E-sports antiguos)."""
    out = []
    bruto = re.search(r"[Mm]odelo bruto\s*([\d.]+)\s*%", r)
    mer = re.search(r"mercado sin margen\s*([\d.]+)\s*%", r)
    if bruto and mer:
        out.append(f"El modelo da {_pct(_num(bruto.group(1)))} y el mercado, sin margen, {_pct(_num(mer.group(1)))}.")
    elif bruto:
        out.append(f"El modelo da {_pct(_num(bruto.group(1)))} de probabilidad.")
    bajas = re.search(r"Bajas?:\s*(.*?)(?=\s*(?:Revisar antes|Fuente del|$))", r, re.S)
    if bajas and bajas.group(1).strip() and not INTERNO.search(bajas.group(1)):
        out.append("Estado físico y bajas: " + bajas.group(1).strip().rstrip(".") + ". Conviene revisarlo antes de apostar.")
    return " ".join(out)


def porque(row):
    manual = (row.get("Por qué") or "").strip()
    if manual:
        return manual
    l = _lineas(row.get("Argumento"))
    partes = []
    ctx = _contexto(l.get("contexto", ""))
    if ctx and not re.search(r"NO CONFIRMADO|Valor v2 simple", ctx, re.I):
        partes.append(ctx)
    elif "Valor v2" in l.get("contexto", ""):
        hist = re.search(r"historial\s*(\d+)/(\d+)", l.get("modelo", ""))
        if hist:
            partes.append(f"En {hist.group(1)} de sus últimos {hist.group(2)} partidos se cumplió esta misma línea.")
    mm = _modelo_mercado(l, row) if l else _desde_razonamiento(row.get("Razonamiento") or "")
    if mm:
        partes.append(mm)
    al = _limpia_alerta(l.get("alerta", ""))
    if al:
        partes.append("Ojo: " + al[0].lower() + al[1:])
    sin_ctx = not ctx or re.search(r"NO CONFIRMADO", l.get("contexto", ""), re.I)
    if sin_ctx and not (l.get("contexto") and "Valor v2" in l.get("contexto", "")):
        partes.append("Sin contexto de plantel ni forma verificado: se apoya solo en el modelo y el precio.")
    return "\n".join(x for x in partes if x).strip()


# --- Diseño compacto para el área de miembros (más filas visibles, controles arriba más bajos) ---
COMPACT_CSS = """
.top{position:sticky;top:0;z-index:30}
.stick{position:sticky;top:0;z-index:30;background:#0A0C11;border-bottom:1px solid #1d2433;box-shadow:0 8px 8px -8px rgba(0,0,0,.7)}
.stick .top{position:static}
.top .wrap{padding-block:5px;gap:8px;flex-wrap:nowrap}
.lion{width:30px!important}
.brand{gap:8px}
.wm{width:84px!important}
.brand .cap{font-size:11px!important;margin-top:0!important;white-space:nowrap}
@media (min-width:520px){.brand>div{display:flex;align-items:center;gap:10px}}
.icon-btn{width:34px;height:34px;border-radius:10px}
.icon-btn svg{width:17px;height:17px}
.wrap.page{padding-block:8px 18px!important;padding-inline:12px}
.stick .wrap{padding-inline:12px}
.layout{gap:0}
.ctl{padding:7px 0}
.ctl>*{margin-top:0!important;margin-bottom:0!important}
.ctl>*+*{margin-top:6px!important}
@media (min-width:920px){.ctl{max-width:430px}}
.modes{padding:3px!important;display:flex!important;gap:2px}
.modes button{min-height:32px!important;font-size:14px;flex:1 1 auto!important;justify-content:center;white-space:nowrap}
.modes button span{white-space:nowrap;flex:none}
.day{padding:4px 2px!important;border-radius:10px!important}
.day .d1{font-size:16px!important}
.day .d2{font-size:11px!important}
.day .d3{font-size:11px!important;margin-top:2px!important;padding:0 6px!important}
.day .d3 .w{display:none!important}
.sports{gap:6px!important;margin-top:0!important}
.sport{flex-direction:column!important;gap:0!important;padding:5px 4px!important;border-radius:12px!important;justify-content:center;min-height:46px}
.sport .badge{display:none!important}
.sport .s-name{font-size:14px!important}
.sport .s-n{font-size:11.5px!important;white-space:nowrap}
.toggle,.seg button{min-height:32px!important}
.filters{gap:4px 10px!important;margin-top:8px!important}
.viewseg{margin-top:8px!important}
.league{margin-top:12px!important}
.league h2 b{font-size:19px!important}
.match{padding:8px 6px!important;gap:8px!important}
.m-time{font-size:19px!important}
.foot .wrap{padding-block:6px!important}
.foot p{font-size:11px!important;line-height:1.35!important}
.foot img{width:24px!important;height:auto}
.pc-body details.fold.porque{margin-top:10px!important;padding-top:4px!important}
.porque>summary{font:700 14px/1.3 var(--f-body,system-ui)!important;font-style:normal!important;min-height:30px!important}
.porque .sec{margin-top:0!important;padding-top:0!important}
.porque p{margin:6px 0 0;font-size:14px;line-height:1.5;max-width:62ch}
.pc-body:has(details.porque) .why-row{display:none!important}
.pq-chk{display:inline-block;margin-top:8px;font-size:12px;font-weight:700;opacity:.8}
"""

COMPACT_JS = """
(function(){
  var busy=false;
  function wrap(){
    if(busy)return;busy=true;
    try{
      var app=document.getElementById('app');if(!app)return;
      var top=app.querySelector(':scope>.top'),lp=app.querySelector('.list-pane'),lay=app.querySelector('.layout');
      if(!top||!lp||!lay)return;
      if(app.querySelector(':scope>.stick'))return;
      if(lay.classList.contains('has-sel')&&window.innerWidth<920)return;
      var kids=[].filter.call(lp.children,function(c){return c.matches('.modes,.days')});
      if(!kids.length)return;
      var st=document.createElement('div');st.className='stick';
      var cw=document.createElement('div');cw.className='wrap';
      var ct=document.createElement('div');ct.className='ctl';
      app.insertBefore(st,top);st.appendChild(top);
      kids.forEach(function(k){ct.appendChild(k)});
      cw.appendChild(ct);st.appendChild(cw);
    }catch(e){}finally{busy=false}
  }
  new MutationObserver(wrap).observe(document.documentElement,{childList:true,subtree:true});
  wrap();
})();
"""


PQ_JS = (
    'function PQ(a){var t=String(a.porque||"").split(/\\n/).map(function(x){return x.trim()}).filter(Boolean);'
    'if(!t.length)return"";var c=a.chk,k="Completo"===c?"Contexto verificado":"Parcial"===c?"Contexto verificado en parte":"Contexto sin verificar";'
    'return\'<details class="fold porque"><summary>Por qué</summary><div class="sec">\'+t.map(function(x){return"<p>"+m(x)+"</p>"}).join("")'
    '+\'<span class="pq-chk">\'+k+"</span></div></details>"}'
)


def parchea(page):
    """Añade el desplegable "Por qué" a cada pick de la app (falla en voz alta si el HTML base cambió)."""
    a1 = 'arg:b(a.Argumento),chk:a.Chequeo||"",valor:'
    a2 = '</dl>"+wa(a)+\'<details class="fold"><summary>Ver análisis completo</summary>\''
    a3 = 'function j(a){var e=/([0-9a-f]{32})'
    a4 = '"Sin razonamiento registrado."}var c=a.pr.bullets'
    for k in (a1, a2, a3, a4):
        assert page.count(k) == 1, "parche no aplicable: " + k[:40]
    page = page.replace(a1, 'arg:b(a.Argumento),porque:b(a["Por qué"]),chk:a.Chequeo||"",valor:')
    page = page.replace(a2, '</dl>"+wa(a)+PQ(a)+\'<details class="fold"><summary>Ver análisis completo</summary>\'')
    page = page.replace(a3, PQ_JS + a3)
    page = page.replace(a4, 'a.porque?a.porque.split("\\n")[0]:"Sin razonamiento registrado."}var c=a.pr.bullets')
    return page


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    props = sample() if os.environ.get("SAMPLE") == "1" else fetch_props()
    for p in props:
        p["Por qué"] = porque(p)
    props = marca(props)
    print(f"{len(props)} propuestas")
    os.makedirs(OUT, exist_ok=True)
    page = open(os.path.join(here, "site", "index.html"), encoding="utf8").read()
    for ph, name in (("__LION__", "lion"), ("__WORD__", "word")):
        with open(os.path.join(here, "assets", name + ".b64")) as f:
            page = page.replace(ph, "data:image/png;base64," + f.read().strip())
    page = parchea(page)
    page = page.replace("Propuestas de Claude", "Propuestas")
    page = marca(page)
    page = page.replace("</head>", "<style id=\"compacto\">" + COMPACT_CSS + "</style></head>", 1)
    page = page.replace("</body>", "<script>" + COMPACT_JS + "</script></body>", 1)
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf8") as f:
        f.write(page)
    with open(os.path.join(OUT, "data.json"), "w") as f:
        json.dump(encrypt({"props": props, "picks": [], "ops": [], "cov": []}, os.environ["MEMBERS_KEY"]), f)
    open(os.path.join(OUT, ".nojekyll"), "w").close()


if __name__ == "__main__":
    main()
