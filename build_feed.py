#!/usr/bin/env python3
"""Lee las propuestas de Claude desde Notion y deja _site/data.json CIFRADO (AES-GCM) para el área de miembros.
Env: NOTION_TOKEN, MEMBERS_KEY. Opcional: SAMPLE=1 (datos de ejemplo, sin Notion), OUT (carpeta, por defecto _site)."""
import base64, datetime as dt, hashlib, hmac, json, os, urllib.request, urllib.error
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
            return rows
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


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    props = sample() if os.environ.get("SAMPLE") == "1" else fetch_props()
    print(f"{len(props)} propuestas")
    os.makedirs(OUT, exist_ok=True)
    page = open(os.path.join(here, "site", "index.html"), encoding="utf8").read()
    for ph, name in (("__LION__", "lion"), ("__WORD__", "word")):
        with open(os.path.join(here, "assets", name + ".b64")) as f:
            page = page.replace(ph, "data:image/png;base64," + f.read().strip())
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf8") as f:
        f.write(page)
    with open(os.path.join(OUT, "data.json"), "w") as f:
        json.dump(encrypt({"props": props, "picks": [], "ops": [], "cov": []}, os.environ["MEMBERS_KEY"]), f)
    open(os.path.join(OUT, ".nojekyll"), "w").close()


if __name__ == "__main__":
    main()#!/usr/bin/env python3
"""Lee las propuestas de Claude desde Notion y deja _site/data.json CIFRADO (AES-GCM) para el área de miembros.
Env: NOTION_TOKEN, MEMBERS_KEY. Opcional: SAMPLE=1 (datos de ejemplo, sin Notion), OUT (carpeta, por defecto _site)."""
import base64, datetime as dt, hashlib, hmac, json, os, urllib.request, urllib.error
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


def fetch_props():
    token = os.environ["NOTION_TOKEN"]
    since = (lima_today() - dt.timedelta(days=DAYS_BACK)).isoformat()
    body = {"page_size": 100, "filter": {"property": "Fecha", "date": {"on_or_after": since}},
            "sorts": [{"property": "Fecha", "direction": "descending"}]}
    rows, cur = [], None
    while True:
        if cur:
            body["start_cursor"] = cur
        r = api(f"data_sources/{PROPUESTAS_DS}/query", body, token)
        rows += [flat(p) for p in r["results"]]
        if not r.get("has_more"):
            return rows
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


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    props = sample() if os.environ.get("SAMPLE") == "1" else fetch_props()
    print(f"{len(props)} propuestas")
    os.makedirs(OUT, exist_ok=True)
    page = open(os.path.join(here, "site", "index.html"), encoding="utf8").read()
    for ph, name in (("__LION__", "lion"), ("__WORD__", "word")):
        with open(os.path.join(here, "assets", name + ".b64")) as f:
            page = page.replace(ph, "data:image/png;base64," + f.read().strip())
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf8") as f:
        f.write(page)
    with open(os.path.join(OUT, "data.json"), "w") as f:
        json.dump(encrypt({"props": props, "picks": [], "ops": [], "cov": []}, os.environ["MEMBERS_KEY"]), f)
    open(os.path.join(OUT, ".nojekyll"), "w").close()


if __name__ == "__main__":
    main()
