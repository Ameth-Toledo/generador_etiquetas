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
    if not PASSWORD or request.path in ("/login", "/ping") or session.get("ok"):
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
HTML = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Generador de etiquetas</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"></script>
<style>
  :root {
    --bg:#191919; --card:#222222; --campo:#1d1d1d; --borde:#383838;
    --texto:#f1f1f1; --suave:#a3a3a3; --acento:#4f7cff; --peligro:#ff6b6b;
    color-scheme: dark;
  }
  * { box-sizing:border-box; }
  body { font-family:'Poppins', system-ui, sans-serif; margin:0; background:var(--bg); color:var(--texto); }
  main { max-width:920px; margin:0 auto; padding:28px 16px 80px; }
  h1 { font-size:26px; font-weight:600; margin:0 0 4px; }
  p.sub { margin:0 0 22px; color:var(--suave); font-size:14px; }
  .card { background:var(--card); border:1px solid var(--borde); border-radius:14px; padding:18px; margin-bottom:18px; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
  .full { grid-column:1 / -1; }
  label { display:block; font-size:13px; font-weight:500; margin-bottom:5px; color:var(--suave); }
  input[type=text], select {
    width:100%; padding:11px 12px; font-size:15px; font-family:inherit; color:var(--texto);
    background:var(--campo); border:1px solid var(--borde); border-radius:10px;
  }
  input::placeholder { color:#6a6a6a; }
  input:focus, select:focus { outline:2px solid var(--acento); border-color:var(--acento); }
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:14px; }
  button {
    font-family:inherit; font-size:14px; padding:10px 16px; border-radius:10px;
    border:1px solid var(--borde); background:#2b2b2b; color:var(--texto); cursor:pointer;
  }
  button:hover:not(:disabled) { background:#343434; }
  button.primary { background:var(--acento); border-color:var(--acento); color:#fff; font-weight:600; }
  button.primary:hover:not(:disabled) { background:#6a90ff; }
  button.danger { color:var(--peligro); }
  button:disabled { opacity:.45; cursor:not-allowed; }
  button[hidden] { display:none; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:9px 6px; border-bottom:1px solid var(--borde); }
  th { font-size:11px; color:var(--suave); text-transform:uppercase; font-weight:500; }
  td.num { text-align:right; white-space:nowrap; }
  .vacio { color:var(--suave); padding:10px 0; font-size:14px; }
  .msg { margin-top:10px; font-size:13px; color:var(--suave); }
  .msg.error { color:var(--peligro); }
  .nota { font-size:12px; color:var(--suave); margin-top:8px; }
  .check { display:flex; align-items:center; gap:8px; margin:0; font-weight:400; }
  h2 { margin:0 0 10px; font-size:18px; font-weight:600; }
  @media (max-width:600px) { .grid { grid-template-columns:1fr; } }
</style>
</head>
<body>
<main>
  <h1>Generador de etiquetas</h1>
  <p class="sub">Trabaja por listas: cada lista guarda sus productos y genera su propio PDF (2 etiquetas por hoja).</p>
  <div class="msg" id="nube" style="margin:-14px 0 16px">Conectando...</div>

  <div class="card">
    <label for="selLista">Lista activa</label>
    <select id="selLista"></select>
    <div class="row" style="margin-top:10px">
      <button id="nuevaLista">+ Nueva lista</button>
      <button id="renombrar">Renombrar</button>
      <button id="duplicar">Duplicar</button>
      <button id="actualizar">↻ Actualizar</button>
      <button class="danger" id="eliminarLista">Eliminar lista</button>
    </div>
  </div>

  <div class="card">
    <div class="grid">
      <div class="full"><label for="nombre">Nombre</label><input type="text" id="nombre" placeholder="TANQUE ESTACIONARIO" autocomplete="off"></div>
      <div class="full"><label for="detalle">Detalle</label><input type="text" id="detalle" placeholder="120 LTS COD: PT120ES" autocomplete="off"></div>
      <div><label for="codigo">Código</label><input type="text" id="codigo" placeholder="0013841" autocomplete="off"></div>
      <div><label for="medida">Medida / unidad</label><input type="text" id="medida" placeholder="TANQUE ESTA 005" autocomplete="off"></div>
      <div><label for="precio">Precio</label><input type="text" id="precio" inputmode="decimal" placeholder="6819.18" autocomplete="off"></div>
    </div>
    <div class="row">
      <button class="primary" id="agregar">Agregar a esta lista</button>
      <button id="cancelarEdicion" hidden>Cancelar edición</button>
      <label class="check"><input type="checkbox" id="mayus" checked> Convertir a MAYÚSCULAS</label>
    </div>
    <div class="nota">Enter pasa al siguiente campo. Enter en el precio agrega el producto a la lista.</div>
    <div class="msg error" id="error"></div>
  </div>

  <div class="card">
    <h2><span id="tituloLista"></span> (<span id="contador">0</span>)</h2>
    <div id="lista"></div>
    <div class="row">
      <button class="primary" id="generar" disabled>Generar PDF de esta lista</button>
      <button class="danger" id="limpiar" disabled>Vaciar lista</button>
    </div>
    <div class="msg" id="estado"></div>
  </div>

  <div class="card">
    <b style="font-weight:600">Importar desde archivo</b>
    <div class="nota">Acepta .ods, .xlsx, .xls y .csv. Los productos se agregan al final de la lista activa.</div>
    <div class="row" style="margin-top:8px">
      <button id="btnArchivo">Elegir archivo</button>
      <span class="nota" id="nombreArchivo" style="margin:0"></span>
      <input type="file" id="archivoImp" accept=".ods,.xlsx,.xls,.csv" hidden>
    </div>
    <div id="panelImp" hidden>
      <div class="grid" style="margin-top:14px">
        <div><label for="selHoja">Hoja</label><select id="selHoja"></select></div>
        <div><label for="palabras">Palabras del nombre (máx.)</label><input type="text" id="palabras" value="3" inputmode="numeric" autocomplete="off"></div>
        <div><label for="mCodigo">Código</label><select id="mCodigo"></select></div>
        <div><label for="mDescripcion">Descripción (se divide en nombre y detalle)</label><select id="mDescripcion"></select></div>
        <div><label for="mMedida">Medida / unidad</label><select id="mMedida"></select></div>
        <div><label for="mPrecio">Precio</label><select id="mPrecio"></select></div>
      </div>
      <div id="vistaImp" style="margin-top:14px;overflow-x:auto"></div>
      <div class="row"><button class="primary" id="importar" disabled>Agregar a esta lista</button></div>
      <div class="msg" id="msgImp"></div>
    </div>
  </div>

  <div class="card">
    <b style="font-weight:600">Respaldo</b>
    <div class="row" style="margin-top:8px">
      <button id="respaldar">Descargar respaldo de todas las listas</button>
      <button id="restaurar">Restaurar respaldo</button>
      <input type="file" id="archivo" accept=".json" hidden>
    </div>
    <div class="nota">Las listas se guardan en la nube y todos las ven. Descarga un respaldo de vez en cuando. Al restaurar, las listas del respaldo se agregan a las que ya hay.</div>
  </div>
</main>

<script>
const $ = id => document.getElementById(id);
const campos = ["nombre", "detalle", "codigo", "medida", "precio"];
const CLAVE = "etiquetas_db";      // modo local (sin nube)
const ACTIVA = "etiquetas_activa"; // lista que estaba abierta
let nube = false;
let editIdx = null;

function nuevoId() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }

function hoy() {
  const d = new Date(), p = n => String(n).padStart(2, "0");
  return `${p(d.getDate())}/${p(d.getMonth() + 1)}/${String(d.getFullYear()).slice(2)}`;
}

// Nombre completo que se muestra: 07/11/26-Lista 1
function titulo(l) { return `${l.fecha}-${l.nombre}`; }

function listaVacia(nombre) {
  return { id: nuevoId(), fecha: hoy(), nombre: nombre || nombrePorDefecto(), creada: Date.now(), productos: [] };
}

function nombrePorDefecto() {
  const h = hoy();
  const delDia = db.listas.filter(l => l.fecha === h);
  const usados = new Set(delDia.map(l => l.nombre));
  let n = delDia.length + 1;
  while (usados.has("Lista " + n)) n++;
  return "Lista " + n;
}

function cargarLocal() {
  let d = null;
  try { d = JSON.parse(localStorage.getItem(CLAVE)); } catch (e) {}
  if (!(d && Array.isArray(d.listas) && d.listas.length)) {
    let viejos = [];
    try { viejos = JSON.parse(localStorage.getItem("productos") || "[]"); } catch (e) {}
    const id = nuevoId();
    d = { activa: id, listas: [{ id, nombre: "Lista 1", productos: viejos }] };
  }
  d.listas.forEach(l => { if (!l.fecha) l.fecha = hoy(); if (!l.creada) l.creada = 0; });
  return d;
}

let db = { activa: null, listas: [] };

function activa() { return db.listas.find(l => l.id === db.activa) || db.listas[0]; }

function esc(s) { return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

function formatoPrecio(p) {
  const n = parseFloat(String(p).replace(/[$,\s]/g, ""));
  return isNaN(n) ? p : "$" + n.toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

function aviso(texto, error) {
  const el = $("nube");
  el.textContent = texto;
  el.style.color = error ? "var(--peligro)" : "var(--suave)";
}

// ---------- Guardado (nube o local) ----------
const JSON_H = {"Content-Type": "application/json"};

function guardarLocal() { try { localStorage.setItem(CLAVE, JSON.stringify(db)); } catch (e) {} }

async function guardar(l) {
  if (!nube) { guardarLocal(); return; }
  aviso("Guardando...");
  try {
    const r = await fetch("/api/listas/" + l.id, {method: "PUT", headers: JSON_H, body: JSON.stringify(l)});
    if (r.status === 401) { location.href = "/login"; return; }
    if (!r.ok) throw new Error();
    aviso("☁ Guardado en la nube");
  } catch (e) { aviso("No se pudo guardar. Revisa tu conexión e inténtalo de nuevo.", true); }
}

async function borrarRemoto(id) {
  if (!nube) { guardarLocal(); return; }
  try {
    const r = await fetch("/api/listas/" + id, {method: "DELETE"});
    if (r.status === 401) { location.href = "/login"; return; }
    if (!r.ok) throw new Error();
    aviso("☁ Guardado en la nube");
  } catch (e) { aviso("No se pudo borrar en la nube.", true); }
}

function guardarActiva() {
  if (nube) { try { localStorage.setItem(ACTIVA, db.activa); } catch (e) {} }
  else guardarLocal();
}

// ---------- Carga inicial ----------
async function traerDeLaNube() {
  const r = await fetch("/api/listas");
  if (r.status === 401) { location.href = "/login"; throw new Error("auth"); }
  if (!r.ok) throw new Error("sin nube");
  const listas = await r.json();
  listas.forEach(l => { if (!l.creada) l.creada = 0; });
  listas.sort((a, b) => a.creada - b.creada);
  return listas;
}

async function iniciar() {
  const local = cargarLocal();
  try {
    let listas = await traerDeLaNube();
    nube = true;
    db = { activa: null, listas };
    if (!listas.length) {
      // Nube vacía: sube las listas que ya tenías en este navegador
      const conDatos = local.listas.filter(l => l.productos.length);
      listas = conDatos.length ? conDatos : [listaVacia("Lista 1")];
      db.listas = listas;
      for (const l of listas) await guardar(l);
    }
    let guardada = null;
    try { guardada = localStorage.getItem(ACTIVA); } catch (e) {}
    db.activa = db.listas.find(l => l.id === guardada) ? guardada : db.listas[0].id;
    aviso("☁ Guardado en la nube");
  } catch (e) {
    if (e.message === "auth") return;
    nube = false;
    db = local;
    aviso("Modo local: las listas se guardan solo en este navegador.", true);
  }
  pintar();
}

$("actualizar").onclick = async () => {
  if (!nube) return aviso("Modo local: no hay nube que actualizar.", true);
  aviso("Actualizando...");
  try {
    const listas = await traerDeLaNube();
    const actual = db.activa;
    db.listas = listas.length ? listas : [listaVacia("Lista 1")];
    db.activa = db.listas.find(l => l.id === actual) ? actual : db.listas[0].id;
    editIdx = null; limpiarCampos();
    pintar(); aviso("☁ Listas actualizadas");
  } catch (e) { aviso("No se pudo actualizar.", true); }
};

// ---------- Pantalla ----------
function pintar() {
  const l = activa();
  db.activa = l.id;
  $("selLista").innerHTML = db.listas.map(x =>
    `<option value="${x.id}" ${x.id === l.id ? "selected" : ""}>${esc(titulo(x))} (${x.productos.length})</option>`).join("");
  $("tituloLista").textContent = titulo(l);
  $("contador").textContent = l.productos.length;
  $("generar").disabled = $("limpiar").disabled = l.productos.length === 0;
  $("agregar").textContent = editIdx === null ? "Agregar a esta lista" : "Guardar cambios";
  $("cancelarEdicion").hidden = editIdx === null;
  if (!l.productos.length) { $("lista").innerHTML = '<div class="vacio">Esta lista todavía no tiene productos.</div>'; return; }
  const filas = l.productos.map((p, i) => `
    <tr ${i === editIdx ? 'style="background:#2c3550"' : ""}>
      <td>${i + 1}</td><td>${esc(p.nombre)}</td><td>${esc(p.detalle)}</td>
      <td>${esc(p.codigo)}</td><td>${esc(p.medida)}</td>
      <td class="num">${esc(formatoPrecio(p.precio))}</td>
      <td style="white-space:nowrap"><button data-editar="${i}">Editar</button> <button class="danger" data-borrar="${i}">Quitar</button></td>
    </tr>`).join("");
  $("lista").innerHTML = `<div style="overflow-x:auto"><table>
    <tr><th>#</th><th>Nombre</th><th>Detalle</th><th>Código</th><th>Medida</th><th>Precio</th><th></th></tr>${filas}</table></div>`;
}

function limpiarCampos() { campos.forEach(c => $(c).value = ""); }

// ---------- Listas ----------
$("selLista").onchange = e => { db.activa = e.target.value; editIdx = null; limpiarCampos(); guardarActiva(); pintar(); };

$("nuevaLista").onclick = () => {
  const def = nombrePorDefecto();
  const entrada = prompt(`Nombre de la lista (se guarda como ${hoy()}-nombre).\nDeja vacío para usar "${def}":`, "");
  if (entrada === null) return;
  const l = listaVacia(entrada.trim() || def);
  db.listas.push(l); db.activa = l.id; editIdx = null; limpiarCampos();
  guardarActiva(); pintar(); guardar(l); $("nombre").focus();
};

$("renombrar").onclick = () => {
  const l = activa();
  const nombre = prompt(`Nuevo nombre (la fecha ${l.fecha} se mantiene):`, l.nombre);
  if (!nombre || !nombre.trim()) return;
  l.nombre = nombre.trim(); pintar(); guardar(l);
};

$("duplicar").onclick = () => {
  const l = activa();
  const copia = listaVacia(l.nombre + " (copia)");
  copia.productos = JSON.parse(JSON.stringify(l.productos));
  db.listas.push(copia); db.activa = copia.id; editIdx = null;
  guardarActiva(); pintar(); guardar(copia);
};

$("eliminarLista").onclick = async () => {
  const l = activa();
  if (!confirm(`¿Eliminar la lista "${titulo(l)}" con sus ${l.productos.length} productos? No se puede deshacer.`)) return;
  db.listas = db.listas.filter(x => x.id !== l.id);
  editIdx = null; limpiarCampos();
  let nueva = null;
  if (!db.listas.length) { nueva = listaVacia("Lista 1"); db.listas.push(nueva); }
  db.activa = db.listas[0].id;
  guardarActiva(); pintar();
  await borrarRemoto(l.id);
  if (nueva) guardar(nueva);
};

// ---------- Productos ----------
$("agregar").onclick = () => {
  $("error").textContent = "";
  const p = {};
  campos.forEach(c => p[c] = $(c).value.trim());
  if (!p.nombre) { $("error").textContent = "Falta el nombre."; return $("nombre").focus(); }
  const num = parseFloat(p.precio.replace(/[$,\s]/g, ""));
  if (isNaN(num)) { $("error").textContent = "El precio no es válido (ej. 6819.18)."; return $("precio").focus(); }
  if ($("mayus").checked) ["nombre", "detalle", "medida"].forEach(c => p[c] = p[c].toUpperCase());
  const l = activa();
  if (editIdx !== null && l.productos[editIdx]) l.productos[editIdx] = p;
  else l.productos.push(p);
  editIdx = null;
  limpiarCampos(); pintar(); guardar(l);
  $("nombre").focus();
};

$("cancelarEdicion").onclick = () => { editIdx = null; limpiarCampos(); pintar(); };

// Enter: pasa al siguiente campo; en el precio agrega el producto
campos.forEach((c, i) => {
  $(c).addEventListener("keydown", e => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    if (i === campos.length - 1) $("agregar").click();
    else $(campos[i + 1]).focus();
  });
});

$("lista").onclick = e => {
  const l = activa();
  const b = e.target.dataset.borrar, ed = e.target.dataset.editar;
  if (b !== undefined) {
    l.productos.splice(+b, 1);
    if (editIdx !== null) { editIdx = null; limpiarCampos(); }
    pintar(); guardar(l);
  }
  if (ed !== undefined) {
    const p = l.productos[+ed];
    campos.forEach(c => $(c).value = p[c]);
    editIdx = +ed; pintar(); $("nombre").focus();
  }
};

$("limpiar").onclick = () => {
  const l = activa();
  if (confirm(`¿Vaciar todos los productos de "${titulo(l)}"?`)) {
    l.productos = []; editIdx = null; limpiarCampos(); pintar(); guardar(l);
  }
};

// ---------- PDF ----------
$("generar").onclick = async () => {
  const l = activa();
  $("estado").textContent = "Generando PDF...";
  $("generar").disabled = true;
  try {
    const r = await fetch("/generar", {method: "POST", headers: JSON_H, body: JSON.stringify(l.productos)});
    if (r.status === 401) { location.href = "/login"; return; }
    if (!r.ok) throw new Error((await r.json()).error || "Error al generar");
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const archivo = titulo(l).replace(/\//g, "-").replace(/[^\w\-. ()]+/g, "_").trim() || "etiquetas";
    const a = document.createElement("a");
    a.href = url; a.download = archivo + ".pdf"; a.click();
    window.open(url, "_blank");
    $("estado").textContent = `Listo: ${archivo}.pdf descargado.`;
  } catch (err) {
    $("estado").textContent = "Error: " + err.message;
  }
  $("generar").disabled = activa().productos.length === 0;
};

// ---------- Respaldo ----------
$("respaldar").onclick = () => {
  const blob = new Blob([JSON.stringify({listas: db.listas}, null, 2)], {type: "application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "respaldo-etiquetas.json"; a.click();
};

$("restaurar").onclick = () => $("archivo").click();
$("archivo").onchange = async e => {
  const f = e.target.files[0];
  if (!f) return;
  try {
    const d = JSON.parse(await f.text());
    if (!d || !Array.isArray(d.listas) || !d.listas.length) throw new Error();
    if (!confirm(`Se agregarán ${d.listas.length} lista(s) del respaldo a las que ya tienes. ¿Continuar?`)) return;
    const ids = new Set(db.listas.map(l => l.id));
    for (const l of d.listas) {
      if (!l.fecha) l.fecha = hoy();
      if (!l.creada) l.creada = Date.now();
      if (!Array.isArray(l.productos)) l.productos = [];
      if (!l.id || ids.has(l.id)) l.id = nuevoId();
      ids.add(l.id);
      db.listas.push(l);
      await guardar(l);
    }
    pintar();
  } catch (err) { alert("El archivo no es un respaldo válido."); }
  e.target.value = "";
};

// ---------- Importar desde archivo ----------
// <<PURAS (funciones sin pantalla; se prueban aparte)
const normal = s => String(s == null ? "" : s).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toUpperCase().trim();
const limpiarTexto = s => String(s == null ? "" : s).replace(/\s+/g, " ").trim();
const textoCelda = c => c ? limpiarTexto(c.w != null ? c.w : c.v) : "";

// Acepta 1856, "1,856.00", "1.856,00", "$1.856,00"
function parsePrecio(v) {
  if (typeof v === "number") return v;
  let s = String(v == null ? "" : v).replace(/[$\s]/g, "");
  if (!s) return NaN;
  const c = s.lastIndexOf(","), p = s.lastIndexOf(".");
  if (c >= 0 && p >= 0) {
    s = c > p ? s.replace(/\./g, "").replace(",", ".") : s.replace(/,/g, "");
  } else if (c >= 0) {
    s = (s.indexOf(",") === c && /,\d{1,2}$/.test(s)) ? s.replace(",", ".") : s.replace(/,/g, "");
  } else if (p >= 0) {
    if ((s.match(/\./g) || []).length > 1 || /\.\d{3}$/.test(s)) s = s.replace(/\./g, "");
  }
  return Number(s);
}

// "TRUPER CARRETILLA NEUMAT LIGERA 8 PIES..." -> nombre "TRUPER CARRETILLA NEUMAT", detalle "LIGERA 8 PIES..."
function dividirDescripcion(desc, max) {
  const palabras = limpiarTexto(desc).replace(/^["'“”]+|["'“”]+$/g, "").split(" ").filter(Boolean);
  const CORTES = /^(A|CON|SIN|TIPO|PARA|COD|MOD|MODELO)$/i;
  const esCorte = w => /\d/.test(w) || /[:(\/]/.test(w) || CORTES.test(w.replace(/[,;]+$/, ""));
  const nombre = [];
  let n = 0, i = 0;
  for (; i < palabras.length; i++) {
    const w = palabras[i];
    const sinComa = w.replace(/[,;]+$/, "");
    if (/^(DE|DEL|Y)$/i.test(sinComa) && nombre.length) {
      const sig = palabras[i + 1];
      if (sig && sinComa === w && !esCorte(sig) && n < max) { nombre.push(w); continue; }
      break;
    }
    if (nombre.length && (esCorte(w) || n >= max)) break;
    nombre.push(sinComa); n++;
    if (sinComa !== w) { i++; break; }  // si trae coma, ahí termina el nombre
  }
  return { nombre: nombre.join(" "), detalle: palabras.slice(i).join(" ") };
}

function leerHoja(ws) {
  if (!ws || !ws["!ref"]) return { filas: [], r0: 0 };
  const r = XLSX.utils.decode_range(ws["!ref"]);
  const filas = [];
  for (let R = r.s.r; R <= r.e.r; R++) {
    const fila = [];
    for (let C = r.s.c; C <= r.e.c; C++) fila.push(ws[XLSX.utils.encode_cell({r: R, c: C})]);
    filas.push(fila);
  }
  return { filas, r0: r.s.r };
}

const PAT = {
  codigo: /^(ID_?SAP|ID|CODIGO|COD|SKU|CLAVE)$/,
  descripcion: /DESCRIP/,
  medida: /CREES|MEDIDA|UNIDAD|PRESENTACION/,
  precio: /PRECIO|IMPORTE|PRICE/,
};

function detectarEncabezado(filas) {
  let mejor = 0, mejorN = -1;
  filas.slice(0, 10).forEach((f, i) => {
    const n = f.filter(c => c && Object.values(PAT).some(p => p.test(normal(textoCelda(c))))).length;
    if (n > mejorN) { mejorN = n; mejor = i; }
  });
  return mejor;
}

function columnasAuto(encabezado) {
  const cols = {codigo: -1, descripcion: -1, medida: -1, precio: -1};
  encabezado.forEach((c, i) => {
    const t = normal(textoCelda(c));
    for (const k of ["medida", "descripcion", "precio", "codigo"]) {  // medida antes que codigo (CODIGO_CREES)
      if (cols[k] < 0 && PAT[k].test(t)) { cols[k] = i; break; }
    }
  });
  return cols;
}

function construirProductos(filas, hdr, r0, cols, max, mayus) {
  const productos = [], sinPrecio = [];
  for (let i = hdr + 1; i < filas.length; i++) {
    const f = filas[i];
    const desc = cols.descripcion >= 0 ? textoCelda(f[cols.descripcion]) : "";
    if (!desc) continue;
    const celdaPrecio = cols.precio >= 0 ? f[cols.precio] : null;
    const precio = celdaPrecio ? parsePrecio(celdaPrecio.t === "n" ? celdaPrecio.v : textoCelda(celdaPrecio)) : NaN;
    if (!isFinite(precio) || precio < 0) { sinPrecio.push(i + r0 + 1); continue; }
    const d = dividirDescripcion(desc, max);
    const p = {
      nombre: d.nombre,
      detalle: d.detalle,
      codigo: cols.codigo >= 0 ? textoCelda(f[cols.codigo]) : "",
      medida: cols.medida >= 0 ? textoCelda(f[cols.medida]) : "",
      precio: precio.toFixed(2),
    };
    if (mayus) ["nombre", "detalle", "medida"].forEach(k => p[k] = p[k].toUpperCase());
    productos.push(p);
  }
  return { productos, sinPrecio };
}
// PURAS>>

const imp = { libro: null, filas: [], hdr: 0, r0: 0 };
const selCols = { codigo: "mCodigo", descripcion: "mDescripcion", medida: "mMedida", precio: "mPrecio" };

function letraCol(i) { let s = "", n = i; do { s = String.fromCharCode(65 + n % 26) + s; n = Math.floor(n / 26) - 1; } while (n >= 0); return s; }

$("btnArchivo").onclick = () => $("archivoImp").click();

$("archivoImp").onchange = async e => {
  const f = e.target.files[0];
  if (!f) return;
  if (typeof XLSX === "undefined") { alert("No se pudo cargar el lector de archivos. Revisa tu conexión y recarga la página."); return; }
  try {
    imp.libro = XLSX.read(await f.arrayBuffer(), {type: "array", raw: true});
    $("nombreArchivo").textContent = f.name;
    $("selHoja").innerHTML = imp.libro.SheetNames.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
    cargarHoja();
    $("panelImp").hidden = false;
  } catch (err) { alert("No pude leer ese archivo. ¿Es .ods, .xlsx, .xls o .csv?"); }
  e.target.value = "";
};

function cargarHoja() {
  const h = leerHoja(imp.libro.Sheets[$("selHoja").value]);
  imp.filas = h.filas; imp.r0 = h.r0;
  imp.hdr = detectarEncabezado(h.filas);
  const encabezado = h.filas[imp.hdr] || [];
  const opciones = '<option value="-1">(ninguna)</option>' + encabezado.map((c, i) =>
    `<option value="${i}">${letraCol(i)} – ${esc(textoCelda(c) || "(vacía)")}</option>`).join("");
  const auto = columnasAuto(encabezado);
  Object.entries(selCols).forEach(([k, id]) => { $(id).innerHTML = opciones; $(id).value = String(auto[k]); });
  vistaPrevia();
}

function productosImportar() {
  const cols = {};
  Object.entries(selCols).forEach(([k, id]) => cols[k] = +$(id).value);
  const max = Math.max(1, Math.min(10, parseInt($("palabras").value, 10) || 3));
  return construirProductos(imp.filas, imp.hdr, imp.r0, cols, max, $("mayus").checked);
}

function vistaPrevia() {
  const { productos, sinPrecio } = productosImportar();
  $("importar").disabled = productos.length === 0;
  $("importar").textContent = productos.length ? `Agregar ${productos.length} productos a esta lista` : "Agregar a esta lista";
  let aviso = productos.length ? `Se encontraron ${productos.length} productos. Vista previa de los primeros 5:` : "No se encontraron productos. Revisa qué columna es la descripción y cuál el precio.";
  if (sinPrecio.length) aviso += ` Se omitirán ${sinPrecio.length} fila(s) sin precio válido (filas ${sinPrecio.slice(0, 8).join(", ")}${sinPrecio.length > 8 ? "…" : ""}).`;
  $("msgImp").textContent = aviso;
  $("vistaImp").innerHTML = productos.length ? `<table>
    <tr><th>Nombre</th><th>Detalle</th><th>Código</th><th>Medida</th><th>Precio</th></tr>
    ${productos.slice(0, 5).map(p => `<tr><td>${esc(p.nombre)}</td><td>${esc(p.detalle)}</td><td>${esc(p.codigo)}</td><td>${esc(p.medida)}</td><td class="num">${esc(formatoPrecio(p.precio))}</td></tr>`).join("")}
    </table>` : "";
}

["selHoja"].forEach(id => $(id).onchange = cargarHoja);
Object.values(selCols).forEach(id => $(id).onchange = vistaPrevia);
$("palabras").oninput = vistaPrevia;

$("importar").onclick = () => {
  const { productos } = productosImportar();
  const l = activa();
  if (!productos.length) return;
  if (l.productos.length + productos.length > 1000) {
    $("msgImp").textContent = "Una lista admite hasta 1000 productos. Crea una lista nueva e importa ahí.";
    return;
  }
  l.productos.push(...productos);
  pintar(); guardar(l);
  $("estado").textContent = `Listo: se agregaron ${productos.length} productos a "${titulo(l)}".`;
  $("panelImp").hidden = true;
  $("nombreArchivo").textContent = "";
};

iniciar();
</script>
</body>
</html>"""


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
