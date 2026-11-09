"""
Página web para generar etiquetas, con listas guardadas en Upstash Redis.

Local:     pip install flask pymupdf   y   python app.py   -> http://localhost:5000
           (sin variables de entorno funciona en "modo local": listas en el navegador)

Variables de entorno en Vercel:
  UPSTASH_REDIS_REST_URL / _TOKEN  (o KV_REST_API_URL / _TOKEN, o STORAGE_REST_API_URL / _TOKEN)
      -> las agrega solas la integración de Upstash.
  APP_PASSWORD   (opcional) contraseña de acceso a la página.
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

from flask import Flask, jsonify, redirect, request, send_file, session

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
    ("etiquetas|" + PASSWORD + "|" + (REDIS_TOKEN or "")).encode()).hexdigest()
app.permanent_session_lifetime = timedelta(days=30)


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
LOGIN = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Entrar</title>
<link rel="icon" type="image/png" href="/logo-etiquetas.png">
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600&display=swap" rel="stylesheet">
<style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#191919;color:#f1f1f1;font-family:'Poppins',sans-serif;color-scheme:dark}
form{background:#222;border:1px solid #383838;border-radius:14px;padding:26px;width:min(92vw,340px)}
h1{font-size:20px;margin:0 0 16px;font-weight:600}
input{width:100%;padding:11px 12px;font-size:15px;font-family:inherit;color:#f1f1f1;background:#1d1d1d;border:1px solid #383838;border-radius:10px;box-sizing:border-box}
input:focus{outline:2px solid #4f7cff}
button{margin-top:12px;width:100%;padding:11px;font-size:15px;font-family:inherit;font-weight:600;color:#fff;background:#4f7cff;border:0;border-radius:10px;cursor:pointer}
p{color:#ff6b6b;font-size:13px;margin:10px 0 0}
</style></head><body><form method="post">
<h1>Generador de etiquetas</h1>
<input type="password" name="clave" placeholder="Contraseña" autofocus>
<button>Entrar</button>__ERROR__</form></body></html>"""


@app.before_request
def proteger():
    if not PASSWORD or request.path in ("/login", "/ping", "/logo-etiquetas.png") or session.get("ok"):
        return None
    if request.path.startswith("/api/") or request.path == "/generar":
        return jsonify(error="No autorizado"), 401
    return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not PASSWORD:
        return redirect("/")
    if request.method == "POST":
        if hmac.compare_digest(request.form.get("clave", "").encode(), PASSWORD.encode()):
            session.permanent = True
            session["ok"] = True
            return redirect("/")
        return LOGIN.replace("__ERROR__", "<p>Contraseña incorrecta.</p>"), 401
    return LOGIN.replace("__ERROR__", "")


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
        pdf = etiquetas.construir_pdf(limpios)
    except ValueError:
        return jsonify(error="Hay un precio que no es válido."), 400
    return send_file(io.BytesIO(pdf), mimetype="application/pdf",
                     download_name="etiquetas.pdf")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
