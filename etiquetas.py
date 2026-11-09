"""
Llena tu plantilla de Canva (PRECIOS_ALEXIS.pdf) con los datos de cada producto.

Instalar:   pip install pymupdf
Usar:       python etiquetas.py                 -> te pide los datos
            python etiquetas.py productos.csv   -> lee un CSV
            (columnas: nombre,detalle,codigo,medida,precio)

Archivos junto al script:
  - PRECIOS_ALEXIS.pdf  (la plantilla, 1 página con 2 etiquetas)
  - Fuentes: Roboto-Bold.ttf y Roboto-Regular.ttf (muy parecida a la de Canva).
    Si pones Araboto-Bold.ttf y Araboto-Normal.ttf, usa esas primero.
    Si no hay ninguna, usa Helvetica.
"""
import csv
import os
import sys

import pymupdf

AQUI = os.path.dirname(os.path.abspath(__file__))
PLANTILLA = os.path.join(AQUI, "PRECIOS_ALEXIS.pdf")
SALIDA = os.path.join(AQUI, "etiquetas.pdf")

# ---- Fuentes -------------------------------------------------------------
def cargar_fuente(archivos, interna):
    """Usa el primer archivo de fuente que exista; si no, la interna (Helvetica)."""
    for archivo in archivos:
        ruta = os.path.join(AQUI, archivo)
        if os.path.exists(ruta):
            return {"fontfile": ruta, "fontname": archivo[:-4].replace("-", ""),
                    "font": pymupdf.Font(fontfile=ruta)}
    return {"fontname": interna, "font": pymupdf.Font(interna)}

NEGRITA = cargar_fuente(["Araboto-Bold.ttf", "Roboto-Bold.ttf"], "hebo")
NORMAL = cargar_fuente(["Araboto-Normal.ttf", "Roboto-Regular.ttf"], "helv")

# ---- Posiciones (puntos PDF) tomadas de tu plantilla ----------------------
CENTRO = 309
ETIQUETAS = [
    dict(nombre=101.9, detalle=144.4, codigo=189.8, medida=192.4, precio=298.8, cent=254.5,
         borrar=[(290, 55, 330, 120), (295, 115, 322, 156), (120, 160, 160, 202),
                 (435, 163, 460, 204), (270, 195, 345, 335), (475, 200, 556, 275)]),
    dict(nombre=473.4, detalle=516.3, codigo=562.7, medida=565.3, precio=677.3, cent=635.6,
         borrar=[(290, 427, 330, 492), (295, 487, 322, 528), (120, 533, 160, 575),
                 (435, 536, 460, 577), (265, 572, 342, 714), (475, 581, 556, 656)]),
]


def medir(f, texto, tam):
    return f["font"].text_length(texto, fontsize=tam)


def escribir(page, f, x, y, texto, tam):
    kw = {"fontfile": f["fontfile"]} if "fontfile" in f else {}
    page.insert_text((x, y), texto, fontname=f["fontname"], fontsize=tam, **kw)


def ajustar(f, texto, tam, max_ancho):
    while medir(f, texto, tam) > max_ancho and tam > 8:
        tam -= 0.5
    return tam


def partir_renglones(texto, tam, ancho_max):
    """Parte el texto en renglones por palabras, sin pasarse del ancho."""
    renglones, actual = [], ""
    for palabra in texto.split():
        prueba = (actual + " " + palabra).strip()
        if actual and medir(NORMAL, prueba, tam) > ancho_max:
            renglones.append(actual)
            actual = palabra
        else:
            actual = prueba
    if actual:
        renglones.append(actual)
    return renglones


def separar_precio(valor):
    num = float(str(valor).replace("$", "").replace(",", "").strip())
    entero, cent = f"{num:.2f}".split(".")
    return f"{int(entero):,}", cent


def llenar_etiqueta(page, pos, p):
    # Dato nuevo (None = etiqueta vacía)
    if p is None:
        return
    nombre = p["nombre"]
    tam = ajustar(NEGRITA, nombre, 44, 455)
    escribir(page, NEGRITA, CENTRO - medir(NEGRITA, nombre, tam) / 2, pos["nombre"], nombre, tam)

    # Detalle: 1 renglón si cabe a buen tamaño; si es largo, 2 renglones más chicos
    detalle = " ".join(p["detalle"].split())
    ancho_det = 380
    if medir(NORMAL, detalle, 22) <= ancho_det:
        tam = ajustar(NORMAL, detalle, 27.4, ancho_det)
        escribir(page, NORMAL, CENTRO - medir(NORMAL, detalle, tam) / 2, pos["detalle"], detalle, tam)
    else:
        tam = 21
        while tam > 11 and len(partir_renglones(detalle, tam, ancho_det)) > 2:
            tam -= 1
        renglones = partir_renglones(detalle, tam, ancho_det)[:2]
        if len(renglones) == 2 and medir(NORMAL, renglones[1], tam) > ancho_det:
            r = renglones[1]
            while r and medir(NORMAL, r + "...", tam) > ancho_det:
                r = r[:-1]
            renglones[1] = r.rstrip() + "..."
        base_y = pos["detalle"] - 10
        for n, r in enumerate(renglones):
            escribir(page, NORMAL, CENTRO - medir(NORMAL, r, tam) / 2, base_y + n * (tam + 2), r, tam)

    # Código: centrado en su caja (a la izquierda)
    codigo = p["codigo"]
    tam = ajustar(NORMAL, codigo, 27.4, 150)
    escribir(page, NORMAL, 138 - medir(NORMAL, codigo, tam) / 2, pos["codigo"], codigo, tam)

    # Medida: alineada a la derecha
    medida = p["medida"]
    tam = ajustar(NORMAL, medida, 27.4, 230)
    escribir(page, NORMAL, 552 - medir(NORMAL, medida, tam), pos["medida"], medida, tam)

    # Precio: "$" + entero centrados, centavos a la derecha
    entero, cent = separar_precio(p["precio"])
    texto = "$" + entero
    tam = ajustar(NEGRITA, texto, 107.7, 335)
    x = CENTRO - 5 - medir(NEGRITA, texto, tam) / 2
    escribir(page, NEGRITA, x, pos["precio"], texto, tam)
    c = "." + cent
    escribir(page, NEGRITA, 552 - medir(NEGRITA, c, 54.3), pos["cent"], c, 54.3)


def construir_pdf(productos):
    """Devuelve el PDF como bytes (lo usa también la página web)."""
    # Se limpia la plantilla UNA vez: borra solo los textos de ejemplo
    # (T, 1, 00, $, .18) y deja el diseño. Después se copia para cada hoja.
    plantilla = pymupdf.open(PLANTILLA)
    base = plantilla[0]
    for pos in ETIQUETAS:
        for r in pos["borrar"]:
            base.add_redact_annot(pymupdf.Rect(*r))
    base.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    salida = pymupdf.open()
    for i in range(0, len(productos), 2):
        salida.insert_pdf(plantilla, from_page=0, to_page=0)
        page = salida[-1]
        par = productos[i:i + 2] + [None] * (2 - len(productos[i:i + 2]))
        for pos, prod in zip(ETIQUETAS, par):
            llenar_etiqueta(page, pos, prod)
    try:
        salida.subset_fonts()  # deja solo los caracteres usados de cada fuente
    except Exception:
        pass
    return salida.tobytes(garbage=4, deflate=True)


def generar(productos):
    datos = construir_pdf(productos)
    with open(SALIDA, "wb") as f:
        f.write(datos)
    print(f"\nListo: {SALIDA} ({len(productos)} etiquetas)")


def leer_csv(ruta):
    with open(ruta, newline="", encoding="utf-8-sig") as f:
        return [{k: (r.get(k) or "").strip()
                 for k in ("nombre", "detalle", "codigo", "medida", "precio")}
                for r in csv.DictReader(f)]


def pedir_datos():
    productos = []
    print("Datos de cada producto (nombre vacío para terminar).\n")
    while True:
        nombre = input("Nombre: ").strip()
        if not nombre:
            break
        detalle = input("Detalle (ej. 1 pieza): ").strip()
        codigo = input("Código: ").strip()
        medida = input("Medida/unidad: ").strip()
        while True:
            precio = input("Precio (ej. 6078.50): ").strip()
            try:
                separar_precio(precio)
                break
            except ValueError:
                print("  Precio no válido.")
        productos.append(dict(nombre=nombre, detalle=detalle, codigo=codigo,
                              medida=medida, precio=precio))
        print()
    return productos


if __name__ == "__main__":
    lista = leer_csv(sys.argv[1]) if len(sys.argv) > 1 else pedir_datos()
    if lista:
        generar(lista)
    else:
        print("No se capturó ningún producto.")
