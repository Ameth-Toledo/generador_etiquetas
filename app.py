"""
Página web para generar etiquetas, con listas guardadas en Upstash Redis.

Local:     pip install flask pymupdf   y   python app.py   -> http://localhost:5000
           (sin variables de entorno funciona en "modo local": listas en el navegador)

Variables de entorno en Vercel:
  UPSTASH_REDIS_REST_URL / _TOKEN  (o KV_REST_API_URL / _TOKEN, o STORAGE_REST_API_URL / _TOKEN)
      -> las agrega solas la integración de Upstash.
  USUARIOS       (opcional) usuarios con login, en JSON (ver crear_usuarios.py).
  APP_PASSWORD   (opcional) una sola contraseña compartida, si no usas USUARIOS.
  SECRET_KEY     (opcional) texto largo al azar para firmar la sesión.
"""
import hashlib
import hmac
import io
import json
import os
import re
import time
import urllib.request
from datetime import timedelta
from functools import lru_cache

import pymupdf
from flask import Flask, Response, jsonify, redirect, request, send_file, session

import etiquetas

app = Flask(__name__)

def _var(*nombres):
    """Primera variable de entorno que exista (según el prefijo que elija Vercel)."""
    for n in nombres:
        if os.environ.get(n):
            return os.environ[n]
    return None


REDIS_URL = _var("UPSTASH_REDIS_REST_URL", "KV_REST_API_URL", "STORAGE_REST_API_URL")
REDIS_TOKEN = _var("UPSTASH_REDIS_REST_TOKEN", "KV_REST_API_TOKEN", "STORAGE_REST_API_TOKEN")
PASSWORD = os.environ.get("APP_PASSWORD", "")
HASH_LISTAS = "etiquetas:listas"
CAMPOS = ("nombre", "detalle", "codigo", "medida", "precio")

app.secret_key = os.environ.get("SECRET_KEY") or hashlib.sha256(
    ("etiquetas|" + PASSWORD + "|" + os.environ.get("USUARIOS", "") + "|" + (REDIS_TOKEN or "")).encode()).hexdigest()
app.permanent_session_lifetime = timedelta(days=30)
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=bool(os.environ.get("VERCEL")))


# ---------------------------------------------------------------- Redis ----
def redis(*comando):
    """Ejecuta un comando en Upstash por su API REST."""
    if not (REDIS_URL and REDIS_TOKEN):
        raise RuntimeError("nube no configurada")
    req = urllib.request.Request(
        REDIS_URL, data=json.dumps(list(comando)).encode(), method="POST",
        headers={"Authorization": f"Bearer {REDIS_TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        datos = json.loads(r.read())
    if "error" in datos:
        raise RuntimeError(datos["error"])
    return datos["result"]


def limpiar_lista(d, id_):
    if not isinstance(d, dict) or not isinstance(d.get("productos"), list) or len(d["productos"]) > 1000:
        raise ValueError("lista no válida")
    return {
        "id": id_,
        "fecha": str(d.get("fecha", ""))[:20],
        "nombre": str(d.get("nombre", ""))[:80],
        "creada": int(d.get("creada") or 0),
        "productos": [{k: str(p.get(k, ""))[:120] for k in CAMPOS} for p in d["productos"]],
    }


# ---------------------------------------------------------------- Acceso ----
# Usuarios en la variable de entorno USUARIOS (JSON). Las contraseñas se guardan
# como hash, nunca en texto. Se generan con:  python crear_usuarios.py
def hacer_clave(clave, sal=None):
    sal = sal or os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", clave.encode(), sal, 200_000)
    return f"v1.{sal.hex()}.{h.hex()}"


def clave_ok(clave, guardada):
    try:
        _, sal, h = guardada.split(".")
        calc = hashlib.pbkdf2_hmac("sha256", clave.encode(), bytes.fromhex(sal), 200_000)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


def _cargar_usuarios():
    crudo = os.environ.get("USUARIOS", "").strip()
    if not crudo:
        return []
    try:
        return [{"usuario": str(u["usuario"]), "correo": str(u.get("correo", "")),
                 "clave": str(u["clave"])} for u in json.loads(crudo)]
    except Exception:
        print("USUARIOS no es un JSON válido: nadie podrá entrar hasta corregirlo.")
        return []


USUARIOS = _cargar_usuarios()
# Si USUARIOS existe (aunque esté mal escrito) hay login: así nunca queda abierta por error.
AUTH = bool(os.environ.get("USUARIOS", "").strip() or PASSWORD)
CLAVE_FALSA = hacer_clave("sin-usuario")  # para tardar igual cuando el usuario no existe
MAX_INTENTOS = 8


def _ip():
    return request.headers.get("x-forwarded-for", "").split(",")[0].strip() or request.remote_addr or "?"


def _clave_intentos():
    return f"etiquetas:intentos:{_ip()}"


def bloqueado():
    try:
        return int(redis("GET", _clave_intentos()) or 0) >= MAX_INTENTOS
    except Exception:
        return False


def registrar_fallo():
    try:
        n = redis("INCR", _clave_intentos())
        if n == 1:
            redis("EXPIRE", _clave_intentos(), 900)  # se olvida a los 15 minutos
    except Exception:
        pass


def limpiar_fallos():
    try:
        redis("DEL", _clave_intentos())
    except Exception:
        pass


# Rutas que el navegador pide sin sesión (no tienen datos): login, ping, logo y archivos de la app instalable
PUBLICAS = ("/login", "/ping", "/logo-etiquetas.png", "/manifest.webmanifest", "/sw.js",
            "/icono-192.png", "/icono-512.png")

LOGIN = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Entrar</title>
<link rel="icon" type="image/png" href="/logo-etiquetas.png">
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600&display=swap" rel="stylesheet">
<style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#191919;color:#f1f1f1;font-family:'Poppins',sans-serif;color-scheme:dark}
form{background:#222;border:1px solid #383838;border-radius:14px;padding:26px;width:min(92vw,340px)}
img{display:block;width:56px;height:56px;object-fit:contain;margin:0 0 12px}
h1{font-size:20px;margin:0 0 16px;font-weight:600}
label{display:block;font-size:13px;color:#a3a3a3;margin:12px 0 5px}
input{width:100%;padding:11px 12px;font-size:15px;font-family:inherit;color:#f1f1f1;background:#1d1d1d;border:1px solid #383838;border-radius:10px;box-sizing:border-box}
input:focus{outline:2px solid #4f7cff}
button{margin-top:16px;width:100%;padding:11px;font-size:15px;font-family:inherit;font-weight:600;color:#fff;background:#4f7cff;border:0;border-radius:10px;cursor:pointer}
p{color:#ff6b6b;font-size:13px;margin:12px 0 0}
</style></head><body><form method="post">
<img src="/logo-etiquetas.png" alt="" onerror="this.remove()">
<h1>Generador de etiquetas</h1>
__USUARIO__
<label for="clave">Contraseña</label>
<input type="password" id="clave" name="clave" autocomplete="current-password" required>
<button>Entrar</button>__ERROR__</form></body></html>"""

CAMPO_USUARIO = """<label for="usuario">Correo o usuario</label>
<input type="text" id="usuario" name="usuario" autocomplete="username" autocapitalize="none" autofocus required>"""


def pagina_login(error=""):
    html = LOGIN.replace("__USUARIO__", CAMPO_USUARIO if USUARIOS else "")
    return html.replace("__ERROR__", f"<p>{error}</p>" if error else "")


@app.before_request
def proteger():
    if not AUTH or request.path in PUBLICAS or session.get("ok"):
        return None
    if request.path.startswith("/api/") or request.path == "/generar":
        return jsonify(error="No autorizado"), 401
    return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not AUTH:
        return redirect("/")
    if request.method == "GET":
        return pagina_login()
    if bloqueado():
        return pagina_login("Demasiados intentos. Espera 15 minutos."), 429
    clave = request.form.get("clave", "")
    nombre = None
    if USUARIOS:
        ident = request.form.get("usuario", "").strip().lower()
        u = next((x for x in USUARIOS if ident and ident in (x["usuario"].lower(), x["correo"].lower())), None)
        if clave_ok(clave, u["clave"] if u else CLAVE_FALSA) and u:
            nombre = u["usuario"]
    elif PASSWORD and hmac.compare_digest(clave.encode(), PASSWORD.encode()):
        nombre = "Usuario"
    if not nombre:
        registrar_fallo()
        return pagina_login("Usuario o contraseña incorrectos."), 401
    limpiar_fallos()
    session.clear()
    session.permanent = True
    session["ok"] = True
    session["usuario"] = nombre
    return redirect("/")


@app.get("/salir")
def salir():
    session.clear()
    return redirect("/login" if AUTH else "/")


@app.get("/api/yo")
def api_yo():
    return jsonify(auth=AUTH, usuario=session.get("usuario"))


# ------------------------------------------------------------------ API ----
@app.get("/api/listas")
def api_listar():
    try:
        res = redis("HGETALL", HASH_LISTAS)
    except Exception:
        return jsonify(error="La nube no está disponible."), 503
    pares = res.items() if isinstance(res, dict) else zip(res[0::2], res[1::2])
    listas = []
    for _, valor in pares:
        try:
            lista = json.loads(valor)
        except (ValueError, TypeError):
            continue
        if isinstance(lista, dict):
            listas.append(lista)
    return jsonify(listas)


@app.put("/api/listas/<id_>")
def api_guardar(id_):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", id_):
        return jsonify(error="Id no válido."), 400
    try:
        lista = limpiar_lista(request.get_json(silent=True), id_)
    except (ValueError, TypeError):
        return jsonify(error="Lista no válida."), 400
    try:
        redis("HSET", HASH_LISTAS, id_, json.dumps(lista, ensure_ascii=False))
    except Exception:
        return jsonify(error="No se pudo guardar."), 503
    return jsonify(ok=True)


@app.delete("/api/listas/<id_>")
def api_borrar(id_):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", id_):
        return jsonify(error="Id no válido."), 400
    try:
        redis("HDEL", HASH_LISTAS, id_)
    except Exception:
        return jsonify(error="No se pudo borrar."), 503
    return jsonify(ok=True)


@app.get("/ping")
def ping():
    """Lo llama el Cron de Vercel cada día para que Upstash no archive la base."""
    try:
        redis("SET", "etiquetas:ultimo_ping", str(int(time.time())))
    except Exception:
        return "sin nube", 200
    return "ok", 200


# ------------------------------------------------------------------ Web ----
HTML = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"), encoding="utf-8").read()


# ------------------------------------------------- App instalable (PWA) ----
AQUI = os.path.dirname(os.path.abspath(__file__))


@lru_cache(maxsize=4)
def _icono(tam):
    """Icono cuadrado (PNG) hecho con logo-etiquetas.png sobre fondo blanco.
    Si el logo no está, dibuja uno sencillo para que la app se pueda instalar igual."""
    doc = pymupdf.open()
    pg = doc.new_page(width=tam, height=tam)
    ruta = os.path.join(AQUI, "logo-etiquetas.png")
    if os.path.exists(ruta):
        pg.draw_rect(pg.rect, color=None, fill=(1, 1, 1))
        m = tam * 0.12
        pg.insert_image(pymupdf.Rect(m, m, tam - m, tam - m), filename=ruta, keep_proportion=True)
    else:
        pg.draw_rect(pg.rect, color=None, fill=(0.31, 0.49, 1.0))
        size = tam * 0.6
        ancho = pymupdf.get_text_length("E", fontname="hebo", fontsize=size)
        pg.insert_text(((tam - ancho) / 2, tam / 2 + size * 0.35), "E",
                       fontname="hebo", fontsize=size, color=(1, 1, 1))
    return pg.get_pixmap(alpha=False).tobytes("png")


@app.get("/icono-<int:tam>.png")
def icono(tam):
    if tam not in (192, 512):
        return "", 404
    return Response(_icono(tam), mimetype="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/manifest.webmanifest")
def manifiesto():
    datos = {
        "id": "/",
        "name": "Generador de etiquetas",
        "short_name": "Etiquetas",
        "description": "Genera etiquetas de precio en PDF.",
        "lang": "es",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#191919",
        "theme_color": "#191919",
        "icons": [
            {"src": "/icono-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/icono-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        ],
    }
    return Response(json.dumps(datos), mimetype="application/manifest+json")


SERVICE_WORKER = """// Service worker mínimo: hace la página instalable y avisa si no hay internet.
// No guarda listas ni respuestas de la API (siempre se piden en línea).
const CACHE = "etiquetas-v1";
const SIN_RED = "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>" +
  "<title>Sin conexión</title><body style='font-family:system-ui,sans-serif;background:#191919;color:#f1f1f1;" +
  "display:grid;place-items:center;min-height:100vh;margin:0;text-align:center'><div><h1>Sin conexión</h1>" +
  "<p>Conéctate a internet para usar el generador de etiquetas.</p></div>";

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE)
    .then(c => c.put("/sin-conexion", new Response(SIN_RED, {headers: {"Content-Type": "text/html; charset=utf-8"}})))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", e => {
  if (e.request.mode === "navigate") {
    e.respondWith(fetch(e.request).catch(() => caches.match("/sin-conexion")));
  }
});
"""


@app.get("/sw.js")
def service_worker():
    return Response(SERVICE_WORKER, mimetype="application/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/logo-etiquetas.png")
def logo():
    """Icono de la pestaña. Poner logo-etiquetas.png junto a app.py."""
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo-etiquetas.png")
    if not os.path.exists(ruta):
        return "", 404
    return send_file(ruta, mimetype="image/png", max_age=86400)


@app.get("/")
def inicio():
    return HTML


@app.post("/generar")
def generar():
    datos = request.get_json(silent=True)
    if not isinstance(datos, list) or not datos:
        return jsonify(error="No hay productos."), 400
    limpios = []
    try:
        for p in datos:
            prod = {k: str(p.get(k, "")).strip() for k in CAMPOS}
            etiquetas.separar_precio(prod["precio"])  # valida el precio
            limpios.append(prod)
        por_hoja = 8 if request.args.get("plantilla") == "8" else 2
        pdf = etiquetas.construir_pdf(limpios, por_hoja)
    except ValueError:
        return jsonify(error="Hay un precio que no es válido."), 400
    return send_file(io.BytesIO(pdf), mimetype="application/pdf",
                     download_name="etiquetas.pdf")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
