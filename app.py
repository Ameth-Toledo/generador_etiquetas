"""
Página web para generar etiquetas.

Instalar:  pip install flask pymupdf
Correr:    python app.py
Abrir:     http://localhost:5000
Otras PCs de la red:  http://IP-DE-ESTA-PC:5000
"""
import io

from flask import Flask, jsonify, request, send_file

import etiquetas

app = Flask(__name__)

HTML = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Generador de etiquetas</title>
<style>
  :root { --azul:#1f4fd8; --gris:#f2f4f8; --borde:#d5d9e2; }
  * { box-sizing:border-box; }
  body { font-family:system-ui,Arial,sans-serif; margin:0; background:var(--gris); color:#1c1f26; }
  main { max-width:900px; margin:0 auto; padding:24px 16px 80px; }
  h1 { font-size:24px; margin:0 0 4px; }
  p.sub { margin:0 0 20px; color:#5a6272; }
  .card { background:#fff; border:1px solid var(--borde); border-radius:12px; padding:18px; margin-bottom:18px; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
  .full { grid-column:1 / -1; }
  label { display:block; font-size:13px; font-weight:600; margin-bottom:4px; }
  input[type=text], select { width:100%; padding:10px; font-size:16px; border:1px solid var(--borde); border-radius:8px; background:#fff; }
  input:focus, select:focus { outline:2px solid var(--azul); border-color:var(--azul); }
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:14px; }
  button { font-size:15px; padding:10px 16px; border-radius:8px; border:1px solid var(--borde); background:#fff; cursor:pointer; }
  button.primary { background:var(--azul); color:#fff; border-color:var(--azul); font-weight:600; }
  button.danger { color:#b3261e; }
  button:disabled { opacity:.5; cursor:not-allowed; }
  table { width:100%; border-collapse:collapse; font-size:14px; }
  th, td { text-align:left; padding:8px 6px; border-bottom:1px solid var(--borde); }
  th { font-size:12px; color:#5a6272; text-transform:uppercase; }
  td.num { text-align:right; white-space:nowrap; }
  .vacio { color:#5a6272; padding:10px 0; }
  .msg { margin-top:10px; font-size:14px; }
  .msg.error { color:#b3261e; }
  .nota { font-size:12px; color:#5a6272; margin-top:8px; }
  @media (max-width:600px) { .grid { grid-template-columns:1fr; } }
</style>
</head>
<body>
<main>
  <h1>Generador de etiquetas</h1>
  <p class="sub">Trabaja por listas: cada lista guarda sus productos y genera su propio PDF (2 etiquetas por hoja).</p>

  <div class="card">
    <label for="selLista">Lista activa</label>
    <select id="selLista"></select>
    <div class="row" style="margin-top:10px">
      <button id="nuevaLista">+ Nueva lista</button>
      <button id="renombrar">Renombrar</button>
      <button id="duplicar">Duplicar</button>
      <button class="danger" id="eliminarLista">Eliminar lista</button>
    </div>
  </div>

  <div class="card">
    <div class="grid">
      <div class="full"><label for="nombre">Nombre</label><input type="text" id="nombre" placeholder="TANQUE ESTACIONARIO"></div>
      <div class="full"><label for="detalle">Detalle</label><input type="text" id="detalle" placeholder="120 LTS COD: PT120ES"></div>
      <div><label for="codigo">Código</label><input type="text" id="codigo" placeholder="0013841"></div>
      <div><label for="medida">Medida / unidad</label><input type="text" id="medida" placeholder="TANQUE ESTA 005"></div>
      <div><label for="precio">Precio</label><input type="text" id="precio" inputmode="decimal" placeholder="6819.18"></div>
    </div>
    <div class="row">
      <button class="primary" id="agregar">Agregar a esta lista</button>
      <label style="margin:0;font-weight:400"><input type="checkbox" id="mayus" checked> Convertir a MAYÚSCULAS</label>
    </div>
    <div class="msg error" id="error"></div>
  </div>

  <div class="card">
    <h2 style="margin:0 0 10px;font-size:18px"><span id="tituloLista"></span> (<span id="contador">0</span>)</h2>
    <div id="lista"></div>
    <div class="row">
      <button class="primary" id="generar" disabled>Generar PDF de esta lista</button>
      <button class="danger" id="limpiar" disabled>Vaciar lista</button>
    </div>
    <div class="msg" id="estado"></div>
  </div>

  <div class="card">
    <b>Respaldo</b>
    <div class="row" style="margin-top:8px">
      <button id="respaldar">Descargar respaldo de todas las listas</button>
      <button id="restaurar">Restaurar respaldo</button>
      <input type="file" id="archivo" accept=".json" hidden>
    </div>
    <div class="nota">Las listas se guardan en este navegador. Descarga un respaldo de vez en cuando por si limpias los datos del navegador o cambias de PC.</div>
  </div>
</main>

<script>
const $ = id => document.getElementById(id);
const campos = ["nombre", "detalle", "codigo", "medida", "precio"];
const CLAVE = "etiquetas_db";

function nuevoId() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }

function cargarDB() {
  try {
    const d = JSON.parse(localStorage.getItem(CLAVE));
    if (d && Array.isArray(d.listas) && d.listas.length) return d;
  } catch (e) {}
  // Migrar la lista de la versión anterior, si existe
  let viejos = [];
  try { viejos = JSON.parse(localStorage.getItem("productos") || "[]"); } catch (e) {}
  const id = nuevoId();
  return { activa: id, listas: [{ id, nombre: "Lista 1", productos: viejos }] };
}

let db = cargarDB();

function guardar() {
  try { localStorage.setItem(CLAVE, JSON.stringify(db)); } catch (e) {}
}

function activa() {
  return db.listas.find(l => l.id === db.activa) || db.listas[0];
}

function esc(s) { return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

function formatoPrecio(p) {
  const n = parseFloat(String(p).replace(/[$,\s]/g, ""));
  return isNaN(n) ? p : "$" + n.toLocaleString("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

function pintar() {
  const l = activa();
  db.activa = l.id;
  $("selLista").innerHTML = db.listas.map(x =>
    `<option value="${x.id}" ${x.id === l.id ? "selected" : ""}>${esc(x.nombre)} (${x.productos.length})</option>`).join("");
  $("tituloLista").textContent = l.nombre;
  $("contador").textContent = l.productos.length;
  $("generar").disabled = $("limpiar").disabled = l.productos.length === 0;
  if (!l.productos.length) { $("lista").innerHTML = '<div class="vacio">Esta lista todavía no tiene productos.</div>'; return; }
  const filas = l.productos.map((p, i) => `
    <tr>
      <td>${i + 1}</td><td>${esc(p.nombre)}</td><td>${esc(p.detalle)}</td>
      <td>${esc(p.codigo)}</td><td>${esc(p.medida)}</td>
      <td class="num">${esc(formatoPrecio(p.precio))}</td>
      <td style="white-space:nowrap"><button data-editar="${i}">Editar</button> <button class="danger" data-borrar="${i}">Quitar</button></td>
    </tr>`).join("");
  $("lista").innerHTML = `<div style="overflow-x:auto"><table>
    <tr><th>#</th><th>Nombre</th><th>Detalle</th><th>Código</th><th>Medida</th><th>Precio</th><th></th></tr>${filas}</table></div>`;
}

// ---- Listas ----
$("selLista").onchange = e => { db.activa = e.target.value; guardar(); pintar(); };

$("nuevaLista").onclick = () => {
  const hoy = new Date().toLocaleDateString("es-MX");
  const nombre = prompt("Nombre de la nueva lista:", "Lista " + hoy);
  if (!nombre || !nombre.trim()) return;
  const id = nuevoId();
  db.listas.push({ id, nombre: nombre.trim(), productos: [] });
  db.activa = id; guardar(); pintar(); $("nombre").focus();
};

$("renombrar").onclick = () => {
  const l = activa();
  const nombre = prompt("Nuevo nombre:", l.nombre);
  if (!nombre || !nombre.trim()) return;
  l.nombre = nombre.trim(); guardar(); pintar();
};

$("duplicar").onclick = () => {
  const l = activa();
  const id = nuevoId();
  db.listas.push({ id, nombre: l.nombre + " (copia)", productos: JSON.parse(JSON.stringify(l.productos)) });
  db.activa = id; guardar(); pintar();
};

$("eliminarLista").onclick = () => {
  const l = activa();
  if (!confirm(`¿Eliminar la lista "${l.nombre}" con sus ${l.productos.length} productos? No se puede deshacer.`)) return;
  db.listas = db.listas.filter(x => x.id !== l.id);
  if (!db.listas.length) db.listas.push({ id: nuevoId(), nombre: "Lista 1", productos: [] });
  db.activa = db.listas[0].id; guardar(); pintar();
};

// ---- Productos ----
$("agregar").onclick = () => {
  $("error").textContent = "";
  const p = {};
  campos.forEach(c => p[c] = $(c).value.trim());
  if (!p.nombre) return $("error").textContent = "Falta el nombre.";
  const num = parseFloat(p.precio.replace(/[$,\s]/g, ""));
  if (isNaN(num)) return $("error").textContent = "El precio no es válido (ej. 6819.18).";
  if ($("mayus").checked) ["nombre", "detalle", "medida"].forEach(c => p[c] = p[c].toUpperCase());
  activa().productos.push(p);
  guardar(); pintar();
  campos.forEach(c => $(c).value = "");
  $("nombre").focus();
};

$("lista").onclick = e => {
  const l = activa();
  const b = e.target.dataset.borrar, ed = e.target.dataset.editar;
  if (b !== undefined) { l.productos.splice(+b, 1); guardar(); pintar(); }
  if (ed !== undefined) {
    const p = l.productos.splice(+ed, 1)[0];
    campos.forEach(c => $(c).value = p[c]);
    guardar(); pintar(); $("nombre").focus();
  }
};

$("limpiar").onclick = () => {
  const l = activa();
  if (confirm(`¿Vaciar todos los productos de "${l.nombre}"?`)) { l.productos = []; guardar(); pintar(); }
};

$("precio").addEventListener("keydown", e => { if (e.key === "Enter") $("agregar").click(); });

// ---- PDF ----
$("generar").onclick = async () => {
  const l = activa();
  $("estado").textContent = "Generando PDF...";
  $("generar").disabled = true;
  try {
    const r = await fetch("/generar", {method: "POST", headers: {"Content-Type": "application/json"},
                                       body: JSON.stringify(l.productos)});
    if (!r.ok) throw new Error((await r.json()).error || "Error al generar");
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const archivo = l.nombre.replace(/[^\w\-. ]+/g, "_").trim() || "etiquetas";
    const a = document.createElement("a");
    a.href = url; a.download = archivo + ".pdf"; a.click();
    window.open(url, "_blank");
    $("estado").textContent = `Listo: ${archivo}.pdf descargado.`;
  } catch (err) {
    $("estado").textContent = "Error: " + err.message;
  }
  $("generar").disabled = activa().productos.length === 0;
};

// ---- Respaldo ----
$("respaldar").onclick = () => {
  const blob = new Blob([JSON.stringify(db, null, 2)], {type: "application/json"});
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
    if (!d || !Array.isArray(d.listas)) throw new Error();
    if (!confirm("Esto reemplaza todas las listas actuales por las del respaldo. ¿Continuar?")) return;
    db = d;
    if (!db.listas.length) db = cargarDB();
    db.activa = db.listas[0].id;
    guardar(); pintar();
  } catch (err) { alert("El archivo no es un respaldo válido."); }
  e.target.value = "";
};

pintar();
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
            prod = {k: str(p.get(k, "")).strip() for k in
                    ("nombre", "detalle", "codigo", "medida", "precio")}
            etiquetas.separar_precio(prod["precio"])  # valida el precio
            limpios.append(prod)
        pdf = etiquetas.construir_pdf(limpios)
    except ValueError:
        return jsonify(error="Hay un precio que no es válido."), 400
    return send_file(io.BytesIO(pdf), mimetype="application/pdf",
                     download_name="etiquetas.pdf")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
