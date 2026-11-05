#!/usr/bin/env python3
"""¿Quién tiene a los novios? — herramienta única para armar e imprimir las cartas del juego.

    python3 cartas.py                  abre la herramienta en el navegador (http://127.0.0.1:8765)
    python3 cartas.py --generar        genera output/cartas.pdf y output/manifest.csv sin abrir nada
    python3 cartas.py --caras DIR      usa otra carpeta de caras (con novias/ y novios/)
    python3 cartas.py --host H --puerto N --sin-navegador

Dinámica (45 cartas): carta 1 = novia real + novio real (ganadora); cartas 2-23 = novia real + novio fake;
cartas 24-45 = novia fake + novio real. Los lugares reales están bloqueados; cada fake se usa una sola vez.

Caras: solo de caras/novias y caras/novios ("real*" = la foto real). Cartas y ajustes: proyecto.json.
Diseño: assets/plantilla/carta.png + carta.json (posición de los círculos). Requiere Python 3.10+ y Pillow.
Ver README.md.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import ipaddress
import json
import mimetypes
import random
import re
import shutil
import socket
import sys
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageOps

ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "caras"          # carpeta de caras: caras/novias y caras/novios  (cambia con --caras DIR)
OUTPUT = ROOT / "output"
PROYECTO = ROOT / "proyecto.json"   # qué caras lleva cada carta + ajustes de cada foto
PLANTILLA = ROOT / "assets" / "plantilla" / "carta.png"
DATOS = json.loads((ROOT / "assets" / "plantilla" / "carta.json").read_text(encoding="utf-8"))
ROLES = {"novia": "novias", "novio": "novios"}       # lado de la carta → carpeta
EXTS = {".jpg", ".jpeg", ".png", ".webp"}
N_CARTAS = 45

# ---------------------------------------------------------------- hoja ------
DPI = 400
MM = DPI / 25.4
HOJA_W, HOJA_H = round(215.9 * MM), round(279.4 * MM)            # carta 8.5 × 11 in
MARGEN_V = 6.0                                                    # mm arriba/abajo
CARTA_H = int((279.4 - 2 * MARGEN_V) / 2 * MM)                    # ~13.37 cm
CARTA_W = round(CARTA_H * DATOS["ancho_px"] / DATOS["alto_px"])   # ~9.84 cm
X0, Y0 = (HOJA_W - 2 * CARTA_W) // 2, (HOJA_H - 2 * CARTA_H) // 2
ESC = CARTA_H / DATOS["alto_px"]                                  # plantilla → carta impresa
ESCALA_CARA = 1.02                                                # la foto tapa el contorno del círculo
# cartulina blanca mate: la tinta se expande → medios tonos más claros y algo más de saturación
PAPELES = {"cartulina": (0.92, 1.06, 6), "ninguno": (1.0, 1.0, 0)}   # (gamma, saturación, negro mínimo)


def perfil_papel(img: Image.Image, papel: str) -> Image.Image:
    gamma, sat, negro = PAPELES[papel]
    if (gamma, sat, negro) == (1.0, 1.0, 0):
        return img
    lut = [round(negro + ((v / 255) ** gamma) * (255 - negro)) for v in range(256)] * 3
    return ImageEnhance.Color(img.point(lut)).enhance(sat)


# ---------------------------------------------------------------- caras -----
# Ajuste de una foto dentro del círculo (relativo al diámetro): zoom = ancho foto / diámetro,
# x, y = desplazamiento del centro de la foto, rot = grados (horario).

_fuentes: dict[Path, tuple[int, Image.Image]] = {}


def abrir(ruta: Path) -> Image.Image:
    mt = ruta.stat().st_mtime_ns
    if ruta not in _fuentes or _fuentes[ruta][0] != mt:
        _fuentes[ruta] = (mt, ImageOps.exif_transpose(Image.open(ruta)).convert("RGBA"))
    return _fuentes[ruta][1]


def aplicar_ajuste(src: Image.Image, aj: dict, d: int) -> Image.Image:
    w = max(1, round(aj["zoom"] * d))
    h = max(1, round(w * src.height / src.width))
    im = src.resize((w, h), Image.Resampling.LANCZOS)
    if aj.get("rot"):
        im = im.rotate(-aj["rot"], resample=Image.Resampling.BICUBIC, expand=True)
    lienzo = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    cx, cy = d / 2 + aj["x"] * d, d / 2 + aj["y"] * d
    lienzo.paste(im, (round(cx - im.width / 2), round(cy - im.height / 2)))
    big = Image.new("L", (d * 4, d * 4), 0)
    ImageDraw.Draw(big).ellipse((0, 0, d * 4 - 1, d * 4 - 1), fill=255)
    lienzo.putalpha(ImageChops.multiply(lienzo.getchannel("A"), big.resize((d, d), Image.Resampling.LANCZOS)))
    return lienzo


def _anchos(alfa: Image.Image, paso: int = 4):
    out = []
    for y in range(0, alfa.height, paso):
        caja = alfa.crop((0, y, alfa.width, y + 1)).getbbox()
        if caja:
            out.append((y, caja[0], caja[2]))
    return out


def auto_ajuste(im: Image.Image) -> dict:
    """Encuadre inicial: en recortes con fondo transparente, del pelo al mentón; en fotos, cuadrado centrado."""
    w, h = im.size
    alfa = im.getchannel("A").point(lambda v: 255 if v > 16 else 0)
    filas = _anchos(alfa) if alfa.getextrema()[0] < 255 else []
    if not filas:
        lado = min(w, h)
        izq, arriba = (w - lado) / 2, (h - lado) * 0.3
    else:
        top = filas[0][0]
        mitad = [f for f in filas if f[0] < top + (filas[-1][0] - top) * 0.55]
        yc, xi, xd = max(mitad, key=lambda f: f[2] - f[1])
        ancho_cab, cx = xd - xi, (xi + xd) / 2
        debajo = [f for f in filas if f[0] > yc]
        menton = None
        if debajo:
            cuello = min(debajo[: max(1, len(debajo) * 2 // 3)], key=lambda f: f[2] - f[1])
            hombros = max((f[2] - f[1] for f in debajo if f[0] > cuello[0]), default=0)
            wc = cuello[2] - cuello[1]
            if wc < 0.62 * ancho_cab and hombros > 1.4 * wc:      # se ve el cuello → el mentón está justo debajo
                menton = cuello[0] + 0.07 * ancho_cab
                cx = (cx + (cuello[1] + cuello[2]) / 2) / 2
        if menton is None:                                        # pelo largo: por el ancho de la silueta
            x0, _, x1, _ = alfa.getbbox()
            lado, cx = (x1 - x0) * 1.08, (x0 + x1) / 2
        else:
            lado = (menton - top) / 0.82
        izq, arriba = cx - lado / 2, top - 0.04 * lado
    return {"zoom": w / lado, "x": (w / 2 - (izq + lado / 2)) / lado, "y": (h / 2 - (arriba + lado / 2)) / lado, "rot": 0}


def es_real(nombre: str | None) -> bool:
    return bool(nombre) and nombre.lower().startswith("real")


# ---------------------------------------------------------------- proyecto --
_lock = threading.RLock()


def fotos(rol: str) -> list[str]:
    carpeta = INPUT / rol
    carpeta.mkdir(parents=True, exist_ok=True)
    return sorted((p.name for p in carpeta.iterdir() if p.suffix.lower() in EXTS), key=str.lower)


REAL = "@real"   # en una carta: "la foto real de este lado" (sea cual sea el archivo marcado como real)
VERSION = 2


def foto_real(rol: str) -> str | None:
    return next((f for f in fotos(rol) if es_real(f)), None)


def estructura(n: int, previas: list[dict] | None = None) -> list[dict]:
    """Dinámica del juego: carta 1 = los dos reales; la mitad del resto lleva a la novia real (y un novio fake),
    la otra mitad al novio real (y una novia fake). Los fakes quedan vacíos para configurar
    (si hay cartas previas, se conservan los fakes que ya estaban elegidos)."""
    resto = max(0, n - 1)
    con_novia, con_novio = (resto + 1) // 2, resto // 2
    viejos = {"novio": [], "novia": []}
    for c in previas or []:
        if c.get("novia") == REAL and c.get("novio") not in (REAL, None):
            viejos["novio"].append(c["novio"])
        if c.get("novio") == REAL and c.get("novia") not in (REAL, None):
            viejos["novia"].append(c["novia"])
    cartas = [{"novia": REAL, "novio": REAL}]
    cartas += [{"novia": REAL, "novio": viejos["novio"][i] if i < len(viejos["novio"]) else None} for i in range(con_novia)]
    cartas += [{"novia": viejos["novia"][i] if i < len(viejos["novia"]) else None, "novio": REAL} for i in range(con_novio)]
    return cartas[:max(1, n)]


def cargar() -> dict:
    if PROYECTO.exists():
        d = json.loads(PROYECTO.read_text(encoding="utf-8"))
    else:
        d = {"cartas": [], "ajustes": {}}
        viejo = ROOT / "input" / "proyecto.json"           # ubicación anterior
        if viejo.exists():
            d = json.loads(viejo.read_text(encoding="utf-8"))
    d.setdefault("ajustes", {})
    d.setdefault("opciones", {"papel": "cartulina", "lineas": True})
    cambio = deduplicar(d) | reparar(d) | unicos(d)
    if d.get("version") != VERSION or not d.get("cartas"):   # dinámica nueva: 1 + mitad novia real + mitad novio real
        previas = [{lado: (REAL if es_real(c.get(lado)) else c.get(lado)) for lado in ROLES} for c in d.get("cartas", [])]
        # en el formato anterior todos los fakes estaban mezclados: se conservan en orden en su nuevo lugar
        fakes = {lado: [c[lado] for c in previas if c[lado] not in (None, REAL)] for lado in ROLES}
        d["cartas"] = estructura(len(previas) or N_CARTAS)
        for c in d["cartas"]:
            for lado in ROLES:
                if c[lado] is None and fakes[lado]:
                    c[lado] = fakes[lado].pop(0)
        d["version"] = VERSION
        cambio = True
    if cambio:
        guardar(d)
    return d


_huellas: dict[Path, tuple[int, int, str]] = {}


def huella(p: Path) -> str:
    st = p.stat()
    h = _huellas.get(p)
    if not h or h[:2] != (st.st_mtime_ns, st.st_size):
        h = (st.st_mtime_ns, st.st_size, hashlib.sha1(p.read_bytes()).hexdigest())
        _huellas[p] = h
    return h[2]


def ya_existe(rol: str, datos: bytes) -> str | None:
    """Nombre de una foto de esa carpeta con exactamente el mismo contenido (o None)."""
    h = hashlib.sha1(datos).hexdigest()
    return next((f for f in fotos(rol) if huella(INPUT / rol / f) == h), None)


def deduplicar(d: dict) -> bool:
    """Si la misma foto está guardada varias veces, deja una sola y pasa sus cartas y ajustes a esa."""
    cambio = False
    for lado, rol in ROLES.items():
        grupos: dict[str, list[str]] = {}
        for f in fotos(rol):
            grupos.setdefault(huella(INPUT / rol / f), []).append(f)
        for copias in grupos.values():
            if len(copias) < 2:
                continue
            usadas = {c.get(lado) for c in d.get("cartas", [])}
            queda = min(copias, key=lambda f: (not es_real(f), f not in usadas, f"{rol}/{f}" not in d["ajustes"], len(f), f))
            for f in copias:
                if f == queda:
                    continue
                aj = d["ajustes"].pop(f"{rol}/{f}", None)
                if aj:
                    d["ajustes"].setdefault(f"{rol}/{queda}", aj)
                for c in d.get("cartas", []):
                    if c.get(lado) == f:
                        c[lado] = queda
                for u, n in list(d.get("origenes", {}).items()):
                    if n == f"{rol}/{f}":
                        d["origenes"][u] = f"{rol}/{queda}"
                (INPUT / rol / f).unlink(missing_ok=True)
                print(f"  · {rol}/{f} era una copia de {queda}: se borró", flush=True)
                cambio = True
    return cambio


def reparar(d: dict) -> bool:
    """Si se renombraron o borraron fotos fuera de la herramienta, corrige las referencias:
    primero ignorando mayúsculas; si falta exactamente una foto y sobra exactamente una, se emparejan.
    Lo que no se pueda emparejar queda vacío."""
    cambio = False
    for lado, rol in ROLES.items():
        existentes = fotos(rol)
        refs = {c.get(lado) for c in d.get("cartas", [])} | {k.split("/", 1)[1] for k in d["ajustes"] if k.startswith(rol + "/")}
        refs -= {None, REAL}
        faltan = sorted(r for r in refs if r not in existentes)
        if not faltan:
            continue
        mapa = {}
        bajas = {e.lower(): e for e in existentes}
        for r in faltan:
            if r.lower() in bajas:
                mapa[r] = bajas[r.lower()]
        resto = [r for r in faltan if r not in mapa]
        sueltas = [e for e in existentes if e not in refs and e not in mapa.values() and not es_real(e)]
        if len(resto) == 1 and len(sueltas) == 1:
            mapa[resto[0]] = sueltas[0]
        for r in faltan:
            nuevo = mapa.get(r)
            if f"{rol}/{r}" in d["ajustes"]:
                aj = d["ajustes"].pop(f"{rol}/{r}")
                if nuevo:
                    d["ajustes"].setdefault(f"{rol}/{nuevo}", aj)
            for c in d.get("cartas", []):
                if c.get(lado) == r:
                    c[lado] = nuevo
            print(f"  · {rol}/{r} ya no existe" + (f" → ahora es {nuevo}" if nuevo else " → se quitó de las cartas (vuelve a elegirla)"), flush=True)
        cambio = True
    return cambio


def guardar(d: dict) -> None:
    tmp = PROYECTO.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PROYECTO)


def resolver(c: dict, reales: dict) -> dict:
    """Carta con nombres de archivo reales (REAL → la foto marcada como real de ese lado)."""
    return {lado: (reales[lado] if c.get(lado) == REAL else c.get(lado)) for lado in ROLES}


def tipo(c: dict) -> str:
    v, n = c.get("novia") == REAL, c.get("novio") == REAL
    return "GANADORA" if v and n else "NOVIA REAL" if v else "NOVIO REAL" if n else ""


def usada_en(d: dict, lado: str, nombre: str, salvo: int | None = None) -> int | None:
    """Índice de la carta donde ya está ese fake (o None). Los fakes no se repiten."""
    return next((i for i, c in enumerate(d["cartas"]) if c.get(lado) == nombre and i != salvo), None)


def unicos(d: dict) -> bool:
    """Regla: cada cara fake va en UNA sola carta. Si alguna está repetida, se queda en la primera."""
    cambio = False
    for lado in ROLES:
        vistas = set()
        for i, c in enumerate(d.get("cartas", [])):
            f = c.get(lado)
            if f in (None, REAL):
                continue
            if f in vistas:
                c[lado] = None
                cambio = True
                print(f"  · {f} estaba repetida: se quitó de la carta {i + 1} (los fakes no se repiten)", flush=True)
            vistas.add(f)
    return cambio


def rellenar(d: dict, todo: bool, semilla=None) -> dict:
    """Llena con fakes SIN USAR los lugares vacíos (o, si todo=True, reparte de nuevo todos los fakes).
    Cada fake va en una sola carta; si no alcanzan, los lugares que sobran quedan vacíos."""
    rng = random.Random(semilla)
    res = {"cambios": 0, "faltan": {}}
    for lado, rol in ROLES.items():
        lugares = [i for i, c in enumerate(d["cartas"]) if c[lado] != REAL and (c[lado] is None or todo)]
        for i in lugares:
            d["cartas"][i][lado] = None
        usados = {c[lado] for c in d["cartas"]}
        libres = [f for f in fotos(rol) if not es_real(f) and f not in usados]
        rng.shuffle(libres)
        for i in lugares:
            if not libres:
                break
            d["cartas"][i][lado] = libres.pop()
            res["cambios"] += 1
        faltan = sum(1 for c in d["cartas"] if c[lado] is None)
        if faltan:
            res["faltan"][rol] = faltan
    return res


# ---------------------------------------------------------------- PDF -------
def _base() -> Image.Image:
    return Image.open(PLANTILLA).convert("RGBA").resize((CARTA_W, CARTA_H), Image.Resampling.LANCZOS)


def componer(base: Image.Image, d: dict, c: dict, papel: str) -> Image.Image:
    img = base.copy()
    for lado, rol in ROLES.items():
        nombre = c.get(lado)
        ruta = INPUT / rol / nombre if nombre else None
        if not ruta or not ruta.exists():
            continue
        src = abrir(ruta)
        aj = d["ajustes"].get(f"{rol}/{nombre}") or auto_ajuste(src)
        k = DATOS[lado]
        dd = round(2 * k["r"] * ESC * ESCALA_CARA)
        img.alpha_composite(aplicar_ajuste(src, aj, dd), (round(k["cx"] * ESC) - dd // 2, round(k["cy"] * ESC) - dd // 2))
    return perfil_papel(img.convert("RGB"), papel)


def hoja(cartas: list[Image.Image], lineas: bool) -> Image.Image:
    pag = Image.new("RGB", (HOJA_W, HOJA_H), "white")
    for k, im in enumerate(cartas):
        pag.paste(im, (X0 + (k % 2) * CARTA_W, Y0 + (k // 2) * CARTA_H))
    dr = ImageDraw.Draw(pag)
    xs = [X0 + i * CARTA_W for i in range(3)]
    ys = [Y0 + i * CARTA_H for i in range(3)]
    if lineas:
        g = max(1, round(0.12 * MM))
        for x in xs:
            dr.rectangle((x - g // 2, ys[0], x + g // 2, ys[-1]), fill=(190, 190, 190))
        for y in ys:
            dr.rectangle((xs[0], y - g // 2, xs[-1], y + g // 2), fill=(190, 190, 190))
    g, sep, largo = max(1, round(0.2 * MM)), round(1.2 * MM), round(4 * MM)
    for x in xs:
        dr.rectangle((x - g // 2, ys[0] - sep - largo, x + g // 2, ys[0] - sep), fill=(60, 60, 60))
        dr.rectangle((x - g // 2, ys[-1] + sep, x + g // 2, ys[-1] + sep + largo), fill=(60, 60, 60))
    for y in ys:
        dr.rectangle((xs[0] - sep - largo, y - g // 2, xs[0] - sep, y + g // 2), fill=(60, 60, 60))
        dr.rectangle((xs[-1] + sep, y - g // 2, xs[-1] + sep + largo, y + g // 2), fill=(60, 60, 60))
    return pag


def generar(progreso=lambda hecho, total, msg: None) -> dict:
    with _lock:
        d = json.loads(json.dumps(cargar()))          # copia: se puede seguir editando mientras se genera
    papel, lineas = d["opciones"].get("papel", "cartulina"), d["opciones"].get("lineas", True)
    reales = {lado: foto_real(rol) for lado, rol in ROLES.items()}
    tipos = [tipo(c) for c in d["cartas"]]
    cartas = [resolver(c, reales) for c in d["cartas"]]
    base = _base()
    vacia, imgs = None, []
    for i, c in enumerate(cartas):
        progreso(i, len(cartas) + 2, f"carta {i + 1} de {len(cartas)}")
        if not c["novia"] and not c["novio"]:
            vacia = vacia or componer(base, d, c, papel)
            imgs.append(vacia)
        else:
            imgs.append(componer(base, d, c, papel))
    progreso(len(cartas), len(cartas) + 2, "armando el PDF…")
    paginas = [hoja(imgs[i:i + 4], lineas) for i in range(0, len(imgs), 4)]
    buf = io.BytesIO()
    paginas[0].save(buf, "PDF", resolution=DPI, save_all=True, append_images=paginas[1:], quality=92)
    progreso(len(cartas) + 2, len(cartas) + 2, "listo")
    return {"cartas": len(cartas), "hojas": len(paginas), "pdf": buf.getvalue(), "manifest": manifest_csv(d),
            "vacios": sum((not c["novia"]) + (not c["novio"]) for c in cartas)}


def manifest_csv(d: dict) -> str:
    reales = {lado: foto_real(rol) for lado, rol in ROLES.items()}
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["carta", "hoja", "fila", "columna", "novia", "novio", "tipo"])
    for i, c in enumerate(d["cartas"]):
        r = resolver(c, reales)
        w.writerow([i + 1, i // 4 + 1, (i % 4) // 2 + 1, i % 2 + 1, r["novia"] or "(vacío)", r["novio"] or "(vacío)", tipo(c)])
    return out.getvalue()


# ---------------------------------------------------------------- servidor --
def seguro(nombre: str) -> str:
    base = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "foto"


def libre(carpeta: Path, nombre: str) -> Path:
    p, k = carpeta / nombre, 2
    while p.exists():
        p = carpeta / f"{Path(nombre).stem}_{k}{Path(nombre).suffix}"
        k += 1
    return p


# ---------------------------------------------------------------- búsqueda --
GOOGLE = ROOT / "google.json"     # {"key": "...", "cx": "..."}  (API oficial de Google Custom Search)
UA_HERRAMIENTA = "quien-tiene-a-los-novios/1.0 (herramienta local para un juego de boda)"
MAX_DESCARGA = 25 * 1024 * 1024


def google_config() -> dict | None:
    if GOOGLE.exists():
        c = json.loads(GOOGLE.read_text(encoding="utf-8"))
        if c.get("key") and c.get("cx"):
            return c
    return None


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA_HERRAMIENTA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def buscar(q: str, pagina: int = 1) -> dict:
    """Busca fotos. Con clave de Google (google.json) usa la API oficial; si no, Wikimedia Commons."""
    g = google_config()
    if g:
        params = {"key": g["key"], "cx": g["cx"], "q": q, "searchType": "image", "imgType": "face",
                  "num": 10, "start": 1 + 10 * (pagina - 1), "safe": "active"}
        try:
            d = _get_json("https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode(params))
        except urllib.error.HTTPError as e:
            detalle = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"Google respondió {e.code}: {detalle}")
        res = [{"thumb": it.get("image", {}).get("thumbnailLink") or it["link"], "url": it["link"],
                "titulo": it.get("title", ""), "ancho": it.get("image", {}).get("width"), "alto": it.get("image", {}).get("height")}
               for it in d.get("items", [])]
        return {"fuente": "Google", "resultados": res}
    params = {"action": "query", "generator": "search", "gsrsearch": f"{q} filetype:bitmap", "gsrnamespace": 6,
              "gsrlimit": 24, "gsroffset": 24 * (pagina - 1), "prop": "imageinfo", "iiprop": "url|size",
              "iiurlwidth": 1000, "format": "json"}
    d = _get_json("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params))
    paginas = sorted(d.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    res = []
    for p in paginas:
        ii = (p.get("imageinfo") or [{}])[0]
        if not ii.get("thumburl"):
            continue
        res.append({"thumb": ii["thumburl"], "url": ii["thumburl"], "titulo": p["title"].removeprefix("File:"),
                    "ancho": ii.get("width"), "alto": ii.get("height")})
    return {"fuente": "Wikimedia Commons", "resultados": res}


def _url_publica(url: str) -> str:
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("la dirección debe empezar con http:// o https://")
    qs = urllib.parse.parse_qs(u.query)
    if "imgurl" in qs:                                  # enlace de resultado de Google Imágenes → la imagen original
        return _url_publica(qs["imgurl"][0])
    for info in socket.getaddrinfo(u.hostname, None):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("esa dirección no es pública")
    return url


def descargar_imagen(url: str) -> tuple[bytes, str]:
    """Baja la imagen que eligió la persona. Devuelve (bytes, extensión)."""
    url = url.strip()
    if url.startswith("data:image/"):                   # imagen arrastrada que viene embebida
        datos = base64.b64decode(url.split(",", 1)[1])
    else:
        req = urllib.request.Request(_url_publica(url), headers={"User-Agent": UA_HERRAMIENTA, "Accept": "image/*,*/*;q=0.5"})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                datos = r.read(MAX_DESCARGA + 1)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"el sitio no dejó bajar la imagen ({e.code}). Guárdala en tu compu y usa «Subir fotos».")
    if len(datos) > MAX_DESCARGA:
        raise ValueError("la imagen pesa demasiado")
    try:
        im = Image.open(io.BytesIO(datos))
        im.load()
    except Exception:
        raise ValueError("eso no es una imagen (¿copiaste la dirección de la página en vez de la de la imagen?)")
    ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}.get(im.format)
    if not ext:                                         # gif, avif, etc. → png
        b = io.BytesIO()
        ImageOps.exif_transpose(im).convert("RGBA").save(b, "PNG")
        datos, ext = b.getvalue(), ".png"
    return datos, ext


def _url(p: Path) -> str:
    return "/archivo?ruta=" + urllib.parse.quote(str(p.relative_to(INPUT))) + f"&v={p.stat().st_mtime_ns}"


def estado() -> dict:
    d = cargar()
    reales = {lado: foto_real(rol) for lado, rol in ROLES.items()}
    resueltas = [resolver(c, reales) for c in d["cartas"]]
    lib = {}
    for lado, rol in ROLES.items():
        lib[rol] = []
        for f in fotos(rol):
            p = INPUT / rol / f
            clave = f"{rol}/{f}"
            if clave in d["ajustes"]:
                aj, ok = d["ajustes"][clave], True
            else:
                try:
                    aj = auto_ajuste(abrir(p))
                except Exception:      # noqa: BLE001
                    aj = {"zoom": 1, "x": 0, "y": 0, "rot": 0}
                ok = False
            lib[rol].append({"nombre": f, "real": es_real(f), "url": _url(p), "ajuste": aj, "guardado": ok,
                             "usos": sum(c[lado] == f for c in resueltas),
                             "en": next((i for i, c in enumerate(d["cartas"]) if c[lado] == f), None)})
    k = 1000 / DATOS["alto_px"]
    return {
        "plantilla": {"w": round(DATOS["ancho_px"] * k), "h": 1000, "escala": ESCALA_CARA, "postal": 19.0 * MM / ESC * k,
                      **{lado: {c: DATOS[lado][c] * k for c in ("cx", "cy", "r")} for lado in ROLES}},
        "fotos": lib, "carpeta": str(INPUT), "opciones": d["opciones"],
        "cartas": [{**r, "tipo": tipo(c), "fijo": {lado: c[lado] == REAL for lado in ROLES}}
                   for c, r in zip(d["cartas"], resueltas)],
        "reales": reales,
        "pdf_listo": _ultimo["pdf"] is not None, "rev": revision(), "google": google_config() is not None,
    }


_plantilla_jpg: bytes | None = None
_trabajo = {"activo": False, "hecho": 0, "total": 1, "msg": "", "resultado": None, "error": None}
_ultimo = {"pdf": None}       # último PDF generado (en memoria, para descargar)


def revision() -> str:
    """Cambia cuando alguien modifica algo (cartas, ajustes o fotos): los navegadores abiertos se actualizan."""
    partes = [PROYECTO.stat().st_mtime_ns if PROYECTO.exists() else 0]
    for rol in ROLES.values():
        carpeta = INPUT / rol
        partes.append(carpeta.stat().st_mtime_ns if carpeta.exists() else 0)
    return "-".join(map(str, partes))


def _plantilla_vista() -> bytes:
    global _plantilla_jpg
    if _plantilla_jpg is None:
        im = Image.open(PLANTILLA).convert("RGB")
        im = im.resize((round(im.width * 1000 / im.height), 1000), Image.Resampling.LANCZOS)
        b = io.BytesIO()
        im.save(b, "JPEG", quality=90)
        _plantilla_jpg = b.getvalue()
    return _plantilla_jpg


def _renombrar(d: dict, rol: str, viejo: str, nuevo: str) -> None:
    (INPUT / rol / viejo).rename(INPUT / rol / nuevo)
    if f"{rol}/{viejo}" in d["ajustes"]:
        d["ajustes"][f"{rol}/{nuevo}"] = d["ajustes"].pop(f"{rol}/{viejo}")
    lado = {v: k for k, v in ROLES.items()}[rol]
    for c in d["cartas"]:
        if c[lado] == viejo:
            c[lado] = nuevo


class App(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _enviar(self, cuerpo: bytes, tipo_: str, codigo=200, adjunto: str | None = None):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo_)
        if adjunto:
            self.send_header("Content-Disposition", f'attachment; filename="{adjunto}"')
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _json(self, obj, codigo=200):
        self._enviar(json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8", codigo)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        if url.path == "/":
            return self._enviar(HTML.encode(), "text/html; charset=utf-8")
        if url.path == "/api/estado":
            with _lock:
                return self._json(estado())
        if url.path == "/api/progreso":
            return self._json(_trabajo)
        if url.path == "/plantilla.jpg":
            return self._enviar(_plantilla_vista(), "image/jpeg")
        if url.path == "/api/buscar":
            try:
                return self._json(buscar(q.get("q", [""])[0].strip(), int(q.get("pagina", ["1"])[0])))
            except Exception as e:      # noqa: BLE001
                return self._json({"error": f"No se pudo buscar: {e}"}, 502)
        if url.path == "/api/rev":
            return self._json({"rev": revision()})
        if url.path == "/descargar/cartas.pdf":
            if _ultimo["pdf"] is None:
                return self._json({"error": "todavía no se ha generado el PDF"}, 404)
            return self._enviar(_ultimo["pdf"], "application/pdf", adjunto="cartas_quien_tiene_a_los_novios.pdf")
        if url.path == "/descargar/manifest.csv":
            with _lock:
                csv_ = manifest_csv(cargar())
            return self._enviar(("\ufeff" + csv_).encode("utf-8"), "text/csv; charset=utf-8", adjunto="manifest_cartas.csv")
        if url.path == "/archivo":
            base = INPUT.resolve()
            p = (base / q.get("ruta", [""])[0]).resolve()
            if base not in p.parents or p.suffix.lower() not in EXTS or not p.is_file():
                return self._json({"error": "no existe"}, 404)
            return self._enviar(p.read_bytes(), mimetypes.guess_type(p.name)[0] or "application/octet-stream")
        self._json({"error": "no existe"}, 404)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        cuerpo = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            if url.path == "/api/generar":
                if not _trabajo["activo"]:
                    _trabajo.update(activo=True, hecho=0, total=1, msg="empezando…", resultado=None, error=None)

                    def tarea():
                        try:
                            r = generar(lambda h, t, m: _trabajo.update(hecho=h, total=t, msg=m))
                            _ultimo["pdf"] = r.pop("pdf")
                            r.pop("manifest")
                            _trabajo["resultado"] = r
                        except Exception as e:      # noqa: BLE001
                            _trabajo["error"] = str(e)
                        _trabajo["activo"] = False
                    threading.Thread(target=tarea, daemon=True).start()
                return self._json(_trabajo)
            if url.path == "/api/vista":           # carta final en calidad de impresión
                datos = json.loads(cuerpo or b"{}")
                with _lock:
                    d = cargar()
                    c = d["cartas"][int(datos["i"])]
                im = componer(_base(), d, c, d["opciones"].get("papel", "cartulina"))
                b = io.BytesIO()
                im.save(b, "JPEG", quality=90)
                return self._enviar(b.getvalue(), "image/jpeg")
            with _lock:
                d = cargar()
                if url.path == "/api/subir":
                    rol = q["rol"][0]
                    assert rol in ROLES.values()
                    nombre = seguro(urllib.parse.unquote(q["nombre"][0]))
                    if es_real(nombre) and foto_real(rol):          # ya hay una real: esta entra como fake
                        nombre = re.sub(r"^real[_\-\s]*", "", nombre, flags=re.I) or "foto" + Path(nombre).suffix
                    if Path(nombre).suffix.lower() not in EXTS:
                        return self._json({"error": "formato no soportado (usa jpg, png o webp)"}, 400)
                    igual = ya_existe(rol, cuerpo)
                    if igual:
                        return self._json({"nombre": igual, "repetida": True, "estado": estado()})
                    destino = libre(INPUT / rol, nombre)
                    destino.write_bytes(cuerpo)
                    return self._json({"nombre": destino.name, "estado": estado()})
                datos = json.loads(cuerpo or b"{}")
                r = {}
                if url.path in ("/api/ajuste", "/api/borrar-ajuste") and es_real(datos.get("nombre")) and not datos.get("desbloqueado"):
                    return self._json({"error": "La foto real está bloqueada. Usa «Desbloquear para ajustar» si de verdad hay que moverla."}, 403)
                if url.path == "/api/ajuste":
                    d["ajustes"][f"{datos['rol']}/{datos['nombre']}"] = {k: round(float(datos["ajuste"][k]), 5) for k in ("zoom", "x", "y", "rot")}
                    guardar(d)
                    return self._json({"ok": True})
                elif url.path == "/api/borrar-ajuste":
                    d["ajustes"].pop(f"{datos['rol']}/{datos['nombre']}", None)
                elif url.path == "/api/real":             # marca como real (solo si todavía no hay una real)
                    rol, nombre, real = datos["rol"], datos["nombre"], bool(datos["real"])
                    if foto_real(rol) and (real or es_real(nombre)):
                        return self._json({"error": "Ya hay una foto real de " + rol + " y está bloqueada."}, 403)
                    quitar = lambda n: re.sub(r"^real[_\-\s]*", "", Path(n).stem, flags=re.I) or "foto"
                    if real:                              # solo una real por lado
                        for otro in fotos(rol):
                            if otro != nombre and es_real(otro):
                                _renombrar(d, rol, otro, libre(INPUT / rol, quitar(otro) + Path(otro).suffix).name)
                    nuevo = nombre
                    if es_real(nombre) != real:
                        nuevo = libre(INPUT / rol, ("real_" if real else "") + quitar(nombre) + Path(nombre).suffix).name
                        _renombrar(d, rol, nombre, nuevo)
                    r["nombre"] = nuevo
                elif url.path == "/api/borrar":           # quita la foto de la biblioteca y de las cartas
                    rol, nombre = datos["rol"], datos["nombre"]
                    if es_real(nombre):
                        return self._json({"error": "La foto real está bloqueada: no se puede borrar."}, 403)
                    (INPUT / rol / nombre).unlink(missing_ok=True)
                    d["ajustes"].pop(f"{rol}/{nombre}", None)
                    lado = {v: k for k, v in ROLES.items()}[rol]
                    for c in d["cartas"]:
                        if c[lado] == nombre:
                            c[lado] = None
                elif url.path == "/api/carta":            # pone (o quita) una foto en un lado de una carta
                    nombre = datos.get("nombre") or None
                    i, lado = int(datos["i"]), datos["lado"]
                    if d["cartas"][i][lado] == REAL:
                        return self._json({"error": f"En la carta {i + 1} el lado de {lado} es la foto real y está bloqueado."}, 403)
                    if es_real(nombre):
                        return self._json({"error": "La foto real solo va en sus cartas fijas. Aquí va un fake."}, 403)
                    otra = usada_en(d, lado, nombre, salvo=i) if nombre else None
                    if otra is not None:
                        return self._json({"error": f"Esa cara ya está en la carta {otra + 1}. Los fakes no se repiten.", "carta": otra}, 409)
                    d["cartas"][i][lado] = nombre
                elif url.path == "/api/numero":           # cambia el número de cartas
                    d["cartas"] = estructura(max(1, min(200, int(datos["n"]))), d["cartas"])
                elif url.path == "/api/estructura":       # vuelve a la dinámica 1 + mitad + mitad (fakes vacíos)
                    d["cartas"] = estructura(len(d["cartas"]))
                elif url.path == "/api/rellenar":
                    r.update(rellenar(d, bool(datos.get("todo"))))
                elif url.path == "/api/descargar":        # baja una imagen de internet a caras/<rol>/
                    rol = datos["rol"]
                    assert rol in ROLES.values()
                    url_ = datos["url"].strip()
                    previa = d.get("origenes", {}).get(url_, "")
                    nombre_final = previa.split("/", 1)[1] if previa.startswith(rol + "/") and (INPUT / previa).exists() else None
                    if not nombre_final:
                        _lock.release()                   # la descarga puede tardar: no bloquea a los demás
                        try:
                            bytes_, ext = descargar_imagen(url_)
                        finally:
                            _lock.acquire()
                        d = cargar()
                        nombre_final = ya_existe(rol, bytes_)          # mismo contenido ya guardado → no se duplica
                        if not nombre_final:
                            base = seguro(datos.get("nombre") or "foto")[:60]
                            base = re.sub(r"^real[_\-\s]*", "", base, flags=re.I) or "foto"   # lo bajado siempre es fake
                            destino = libre(INPUT / rol, Path(base).stem + ext)
                            destino.write_bytes(bytes_)
                            nombre_final = destino.name
                        else:
                            r["repetida"] = True
                        if not url_.startswith("data:"):
                            d.setdefault("origenes", {})[url_] = f"{rol}/{nombre_final}"
                    else:
                        r["repetida"] = True
                    r["nombre"] = nombre_final
                    destino = INPUT / rol / nombre_final
                    i, lado = datos.get("i"), {v: k for k, v in ROLES.items()}[rol]
                    otra = usada_en(d, lado, destino.name, salvo=int(i) if i is not None else None)
                    if otra is not None:
                        r["usada_en"] = otra + 1                  # ya está en otra carta: no se repite
                    elif i is not None and d["cartas"][int(i)][lado] != REAL:     # la pone en la carta actual (si es un lugar fake)
                        d["cartas"][int(i)][lado] = destino.name
                        r["asignada"] = True
                elif url.path == "/api/google":           # guarda la clave de la API de Google
                    if datos.get("borrar"):
                        GOOGLE.unlink(missing_ok=True)
                    else:
                        GOOGLE.write_text(json.dumps({"key": datos["key"].strip(), "cx": datos["cx"].strip()}), encoding="utf-8")
                    return self._json({"ok": True, "estado": estado()})
                elif url.path == "/api/opciones":
                    d["opciones"].update({k: datos[k] for k in ("papel", "lineas") if k in datos})
                else:
                    return self._json({"error": "no existe"}, 404)
                guardar(d)
                return self._json({**r, "estado": estado()})
        except (ValueError, RuntimeError) as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:      # noqa: BLE001
            return self._json({"error": f"{type(e).__name__}: {e}"}, 400)


def servir(abrir_navegador=True, host="127.0.0.1", puerto=8765) -> None:
    while True:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", puerto)) != 0:
                break
        print(f"  (el puerto {puerto} está ocupado, uso el {puerto + 1})")
        puerto += 1
    srv = ThreadingHTTPServer((host, puerto), App)
    url = f"http://127.0.0.1:{puerto}"
    print(f"Herramienta abierta en {url}   (Ctrl+C para cerrar)", flush=True)
    if host != "127.0.0.1":
        print(f"  escuchando en {host}:{puerto}: cualquiera que llegue a ese puerto puede editar las cartas", flush=True)
    if abrir_navegador:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrado.")


def _mudar_input_viejo() -> None:
    """La versión anterior guardaba las fotos en input/. Se mudan a caras/ (solo la primera vez)."""
    viejo = ROOT / "input"
    if INPUT != ROOT / "caras" or not viejo.is_dir():
        return
    for rol in ROLES.values():
        for p in (viejo / rol).glob("*") if (viejo / rol).is_dir() else []:
            if p.suffix.lower() in EXTS:
                (INPUT / rol).mkdir(parents=True, exist_ok=True)
                if not (INPUT / rol / p.name).exists():
                    shutil.move(str(p), INPUT / rol / p.name)
    if (viejo / "proyecto.json").exists() and not PROYECTO.exists():
        shutil.move(str(viejo / "proyecto.json"), PROYECTO)
    if not any(f for f in viejo.rglob("*") if f.is_file() and f.name != ".gitkeep"):
        shutil.rmtree(viejo)
        print("  (las fotos de input/ se mudaron a caras/)")


def main() -> None:
    global INPUT
    if "--caras" in sys.argv:
        INPUT = Path(sys.argv[sys.argv.index("--caras") + 1]).expanduser().resolve()
    _mudar_input_viejo()
    for rol in ROLES.values():
        (INPUT / rol).mkdir(parents=True, exist_ok=True)
    if "--generar" in sys.argv:
        r = generar(lambda h, t, m: print(f"\r  {m:<30}", end="", flush=True))
        OUTPUT.mkdir(exist_ok=True)
        (OUTPUT / "cartas.pdf").write_bytes(r["pdf"])
        (OUTPUT / "manifest.csv").write_text(r["manifest"], encoding="utf-8")
        print(f"\nListo → {OUTPUT / 'cartas.pdf'}  ({r['cartas']} cartas en {r['hojas']} hojas"
              + (f", {r['vacios']} círculos vacíos)" if r["vacios"] else ")"))
        return
    with _lock:
        cargar()
    arg = lambda nombre, defecto: sys.argv[sys.argv.index(nombre) + 1] if nombre in sys.argv else defecto
    servir("--sin-navegador" not in sys.argv, host=arg("--host", "127.0.0.1"), puerto=int(arg("--puerto", 8765)))


# ---------------------------------------------------------------- página ---
HTML = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><title>¿Quién tiene a los novios? · cartas</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { --oro:#c9a046; --oro2:#a47a2a; --tinta:#2a2521; --fondo:#f6f1e7; --borde:#e5dccb; --suave:#7a6a52 }
  * { box-sizing:border-box }
  html, body { height:100% }
  body { margin:0; font:14px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; color:var(--tinta);
         background: radial-gradient(circle at 20% 0%, #fbf7ef, var(--fondo) 55%, #ece4d6); display:flex; flex-direction:column; overflow:hidden }
  header { padding:9px 16px; display:flex; align-items:center; gap:12px; border-bottom:1px solid var(--borde); background:#fffdf8d9; backdrop-filter:blur(6px); flex-wrap:wrap }
  header h1 { margin:0 8px 0 0; font: italic 600 19px Georgia, serif }
  header .esp { flex:1 }
  #barra { width:160px; height:8px; border-radius:9px; background:#eadfca; overflow:hidden; display:none }
  #barra div { height:100%; width:0; background: linear-gradient(90deg, #e7c97f, #c9a046); transition: width .2s }
  #msg { color:var(--suave); font-size:13px }
  main { flex:1; display:grid; grid-template-columns: 280px 1fr 280px; gap:12px; padding:12px 12px 0; min-height:0 }
  .panel { background:#fffdf9; border:1px solid var(--borde); border-radius:14px; padding:12px; overflow:auto; box-shadow:0 4px 16px #6b501a10 }
  .panel h2 { margin:2px 0 10px; font:600 15px Georgia, serif; display:flex; justify-content:space-between; align-items:center; gap:6px }
  .lista { display:grid; grid-template-columns: repeat(3,1fr); gap:8px }
  .foto { position:relative; aspect-ratio:1; border-radius:50%; cursor:pointer; border:3px solid transparent; background:#f3e6d6 center/cover no-repeat; transition: transform .12s }
  .foto:hover { transform:scale(1.05) }
  .foto.sel { border-color:var(--oro) }
  .foto .tag { position:absolute; left:50%; bottom:-2px; transform:translateX(-50%); font-size:9.5px; font-weight:700; background:var(--oro); color:#fff; padding:1px 6px; border-radius:6px }
  .foto .usos { position:absolute; right:-4px; top:-4px; min-width:19px; height:19px; border-radius:10px; font-size:11px; font-weight:700; display:flex; align-items:center; justify-content:center;
                background:#fff; border:1px solid var(--borde); color:var(--suave); padding:0 4px }
  .foto.usada { opacity:.45; filter:grayscale(.6) }
  .foto.noelegible { opacity:.55; cursor:not-allowed }
  .foto.noelegible:hover { transform:none }
  .foto.usada:hover { opacity:.8 }
  .foto .tag { white-space:nowrap; line-height:1.35 }
  .foto .tag.en { background:#8f8473 }
  .foto .candado { position:absolute; right:-3px; top:-3px; width:22px; height:22px; border-radius:50%; background:#fff;
                   border:1px solid var(--borde); display:flex; align-items:center; justify-content:center; font-size:11px;
                   box-shadow:0 1px 3px #0002 }
  .foto .pend { position:absolute; left:2px; top:2px; width:9px; height:9px; border-radius:50%; background:#e38a3a; box-shadow:0 0 0 2px #fff }
  .vacio { color:#9b8b72; font-size:13px; grid-column:1/-1; padding:4px 0 }
  button, .btn { font:600 12.5px system-ui,sans-serif; border:1px solid var(--oro); background:linear-gradient(#fffaf0,#f5e7c4); color:#6b4f16;
                 border-radius:999px; padding:6px 12px; cursor:pointer; white-space:nowrap }
  button:hover, .btn:hover { box-shadow:0 2px 8px #a07a2a33 }
  button:disabled { opacity:.5; cursor:default }
  button.prim { background:linear-gradient(#e7c97f,#c9a046); color:#fff; border-color:var(--oro2); font-size:13.5px; padding:8px 16px }
  button.peligro { border-color:#d9a3a3; background:#fff5f5; color:#9c3b3b }
  .tabs { display:flex; gap:4px }
  .tabs button.on { background:linear-gradient(#e7c97f,#c9a046); color:#fff }
  .fila { display:flex; gap:7px; flex-wrap:wrap; align-items:center; margin:8px 0 }
  .sub { color:var(--suave); font-size:12.5px }
  #centro { display:flex; flex-direction:column; align-items:center; gap:8px; min-height:0 }
  #lienzo-wrap { flex:1; min-height:0; width:100%; display:flex; align-items:center; justify-content:center; gap:10px }
  #lienzo { background:#fff; border-radius:6px; box-shadow:0 2px 4px #0001, 0 14px 40px #4a32101f; touch-action:none }
  .flecha { font-size:22px; width:40px; height:40px; padding:0; border-radius:50% }
  #titulo-carta { font:600 15px Georgia, serif }
  .badge { display:inline-block; font:700 11px system-ui; padding:2px 8px; border-radius:7px; background:var(--oro); color:#fff; margin-left:6px; vertical-align:2px }
  .badge.s { background:#d8c39a }
  label.ctrl { display:block; margin:12px 0 4px; font-weight:600; font-size:13px }
  input[type=range] { width:100%; accent-color:var(--oro) }
  .check { display:flex; align-items:center; gap:7px; font-weight:600; font-size:13px }
  #estado-guardado { font-size:12px; color:#3d7a3d; min-height:16px }
  .lados { display:flex; gap:6px; margin-bottom:6px }
  .lados button.on { background:linear-gradient(#e7c97f,#c9a046); color:#fff }
  .slot { display:flex; align-items:center; gap:8px; padding:8px; border:1px dashed var(--borde); border-radius:10px; margin:6px 0 }
  .slot .mini { width:42px; height:42px; border-radius:50%; background:#f3e6d6 center/cover; flex:none }
  footer { padding:10px 12px 12px; }
  #tira-wrap { background:#fffdf9; border:1px solid var(--borde); border-radius:14px; padding:8px 10px; box-shadow:0 4px 16px #6b501a10 }
  #tira-cab { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:6px }
  #tira-cab b { font:600 14px Georgia, serif; margin-right:6px }
  #tira { display:flex; gap:8px; overflow-x:auto; padding:4px 2px 8px }
  .mini-carta { flex:none; position:relative; cursor:pointer; border-radius:6px; border:2px solid transparent; padding:1px; background:#fff }
  .mini-carta.sel { border-color:var(--oro); box-shadow:0 0 0 3px #c9a04633 }
  .mini-carta canvas { display:block; border-radius:3px }
  .mini-carta .n { position:absolute; left:3px; top:3px; font:700 10px system-ui; background:#fffffff0; border-radius:5px; padding:0 4px; color:var(--tinta) }
  .mini-carta .g { position:absolute; right:3px; top:3px; font-size:11px }
  .mini-carta .g.falta { width:10px; height:10px; border-radius:50%; background:#e38a3a; box-shadow:0 0 0 2px #fff; top:5px; right:5px }
  .mini-carta .hoja { position:absolute; left:0; right:0; bottom:-1px; text-align:center; font:600 9px system-ui; color:var(--suave) }
  .sep-hoja { flex:none; width:1px; background:var(--borde); margin:6px 2px }
  input[type=number] { width:62px; padding:4px 6px; border:1px solid var(--borde); border-radius:8px; font:inherit }
  select { padding:4px 6px; border:1px solid var(--borde); border-radius:8px; font:inherit; background:#fff }
  #modal { position:fixed; inset:0; background:#1d160cb8; display:none; align-items:center; justify-content:center; z-index:9 }
  #buscador { position:fixed; inset:0; background:#1d160c99; display:none; align-items:center; justify-content:center; z-index:8 }
  #buscador .caja { background:#fffdf9; border-radius:16px; padding:16px 18px; width:min(920px,94vw); max-height:90vh; overflow:auto; box-shadow:0 20px 60px #0006 }
  #buscador input { padding:7px 10px; border:1px solid var(--borde); border-radius:9px; font:inherit }
  #b-res { display:grid; grid-template-columns:repeat(auto-fill,minmax(120px,1fr)); gap:8px; margin-top:8px }
  #b-res .r { position:relative; aspect-ratio:1; border-radius:10px; background:#f3e6d6 center/cover no-repeat; cursor:pointer; border:2px solid #fff; box-shadow:0 1px 4px #0002 }
  #b-res .r:hover { border-color:var(--oro) }
  #b-res .r.ok { outline:3px solid #5aa35a }
  #b-res .r.bajando { opacity:.5; cursor:progress }
  #b-res .r span { position:absolute; left:0; right:0; bottom:0; font-size:10px; background:#fffffff0; padding:1px 4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; border-radius:0 0 8px 8px }
  #soltar.encima { border-color:var(--oro); background:#fbf3df }
  #modal img { max-height:92vh; max-width:92vw; box-shadow:0 20px 60px #000a; border-radius:4px; background:#fff }
  kbd { font:11px ui-monospace,monospace; background:#f4efe6; border:1px solid #ddd2bf; border-radius:4px; padding:0 4px }
  a.btn { text-decoration:none }
</style></head>
<body>
<header>
  <h1>¿Quién tiene a los novios?</h1>
  <span class="sub" id="resumen"></span>
  <span class="esp"></span>
  <label class="check" title="Compensación de color según el papel">Papel
    <select id="papel"><option value="cartulina">cartulina mate</option><option value="ninguno">sin compensar</option></select></label>
  <label class="check"><input type="checkbox" id="lineas"> líneas de corte</label>
  <div id="barra"><div></div></div><span id="msg"></span>
  <a class="btn" id="bajar-pdf" href="/descargar/cartas.pdf" style="display:none" title="Descargar otra vez el último PDF generado">⬇ último PDF</a>
  <a class="btn" href="/descargar/manifest.csv" title="Qué caras lleva cada carta">⬇ manifest.csv</a>
  <button class="prim" id="generar">Generar y descargar PDF</button>
</header>
<main>
  <section class="panel">
    <h2>Fotos <div class="tabs"><button data-rol="novias">Novias</button><button data-rol="novios">Novios</button></div></h2>
    <div class="sub" id="ayuda-lista" style="margin-bottom:8px"></div>
    <div class="lista" id="lista"></div>
    <div class="fila"><label class="btn">＋ Subir fotos<input id="subir" type="file" accept="image/*" multiple hidden></label>
      <button id="abrir-buscar">🔍 Buscar en internet</button></div>
    <div id="soltar" class="sub" style="border:1.5px dashed var(--borde);border-radius:10px;padding:10px;text-align:center">
      o arrastra aquí una imagen (de tu compu o de otra pestaña)</div>
    <div class="sub" id="carpeta"></div>
  </section>

  <section id="centro">
    <div id="titulo-carta"></div>
    <div id="lienzo-wrap">
      <button class="flecha" id="ant" title="Carta anterior (PageUp)">‹</button>
      <canvas id="lienzo"></canvas>
      <button class="flecha" id="sig" title="Carta siguiente (PageDown)">›</button>
    </div>
    <div class="fila" style="margin:4px 0 0">
      <button id="ver">Ver esta carta en calidad de impresión</button>
      <label class="check"><input type="checkbox" id="postal"> mostrar postal (3.8 cm)</label>
    </div>
  </section>

  <section class="panel">
    <h2>Esta carta</h2>
    <div class="lados"><button data-lado="novia">Novia (izq.)</button><button data-lado="novio">Novio (der.)</button></div>
    <div class="slot" id="slot"></div>
    <div id="controles">
      <h2 style="margin-top:14px">Ajuste de la foto</h2>
      <div class="sub" id="usos"></div>
      <div id="candado" style="margin:10px 0;padding:9px 10px;border:1px solid var(--borde);border-radius:10px;background:#fbf6ea;font-size:13px;line-height:1.5"></div>
      <label class="check" id="fila-real" style="margin:10px 0"><input type="checkbox" id="real"> Es la foto <b>real</b></label>
      <label class="ctrl">Zoom <span class="sub" id="vzoom"></span></label>
      <input type="range" id="zoom" min="0.3" max="4" step="0.005">
      <label class="ctrl">Rotación <span class="sub" id="vrot"></span></label>
      <input type="range" id="rot" min="-45" max="45" step="0.5">
      <div class="sub" style="margin-top:10px">Arrastra la cara en la carta · rueda = zoom · <kbd>←↑→↓</kbd> mover (<kbd>Shift</kbd> rápido) · <kbd>+</kbd>/<kbd>−</kbd> zoom</div>
      <div class="fila"><button id="auto">Encuadre automático</button><button id="centrar">Centrar</button></div>
      <div id="estado-guardado"></div>
      <hr style="border:none;border-top:1px solid var(--borde);margin:12px 0">
      <button id="borrar" class="peligro">Borrar esta foto de la biblioteca</button>
    </div>
  </section>
</main>
<footer>
  <div id="tira-wrap">
    <div id="tira-cab">
      <b>Cartas</b>
      <span class="sub" id="tira-info"></span>
      <span style="flex:1"></span>
      <button id="rellenar" title="Pone fakes en los círculos vacíos, usando primero las caras menos usadas">Rellenar vacías con fakes</button>
      <button id="repartir" title="Vuelve a repartir TODOS los fakes (los reales no se mueven)">Repartir todos los fakes</button>
      <button id="estructura" title="Carta 1 con los dos reales; la mitad con la novia real y la otra mitad con el novio real (fakes vacíos)">Restablecer 1 + mitad + mitad</button>
      <label class="check">Número de cartas <input type="number" id="num" min="1" max="200"></label>
    </div>
    <div id="tira"></div>
  </div>
</footer>
<div id="modal"><img id="final" alt=""></div>
<div id="buscador">
  <div class="caja">
    <div class="fila" style="margin-top:0;justify-content:space-between">
      <b style="font:600 17px Georgia,serif">Buscar fotos de <span id="b-rol"></span></b>
      <button id="b-cerrar">Cerrar</button>
    </div>
    <div class="fila">
      <input id="b-q" placeholder="p. ej. Brad Pitt, Shakira, Luis Miguel…" style="flex:1;min-width:220px">
      <button class="prim" id="b-ir">Buscar</button>
      <a class="btn" id="b-google" target="_blank" rel="noopener">Abrir en Google Imágenes ↗</a>
    </div>
    <div class="sub" id="b-fuente"></div>
    <div id="b-res"></div>
    <div class="fila" id="b-mas-fila" style="display:none;justify-content:center"><button id="b-mas">Más resultados</button></div>
    <hr style="border:none;border-top:1px solid var(--borde);margin:12px 0">
    <b>¿La encontraste en Google Imágenes?</b>
    <div class="sub">Abre la imagen, haz clic derecho → <i>Copiar dirección de la imagen</i> y pégala aquí (o arrástrala a esta ventana).</div>
    <div class="fila"><input id="b-url" placeholder="https://…/foto.jpg" style="flex:1;min-width:220px"><button class="prim" id="b-url-ir">Agregar</button></div>
    <details style="margin-top:8px"><summary class="sub" style="cursor:pointer">Buscar directo con Google (API oficial)</summary>
      <div class="sub" style="margin:6px 0">Necesita una clave de la API de Google Custom Search (100 búsquedas gratis al día) y el ID de un buscador programable.
        Se guardan en <code>google.json</code> en la compu donde corre la herramienta.</div>
      <div class="fila"><input id="g-key" placeholder="API key" style="flex:1"><input id="g-cx" placeholder="ID del buscador (cx)" style="flex:1">
        <button id="g-guardar">Guardar</button><button id="g-borrar" class="peligro">Quitar</button></div>
    </details>
  </div>
</div>

<script>
const $ = s => document.querySelector(s);
let E = null, cur = 0, lado = 'novia';
const desbloq = { novia: false, novio: false };     // ajuste de la foto real (bloqueado salvo que se desbloquee)
const bloqueado = () => E && E.cartas[cur].fijo[lado] && !desbloq[lado];
const ROL = { novia: 'novias', novio: 'novios' };
const imgs = {}; const plantilla = new Image();
const cv = $('#lienzo'), ctx = cv.getContext('2d');

function img(url) { if (!imgs[url]) { const i = new Image(); i.onload = () => { dibujar(); dibujarTira(); }; i.src = url; imgs[url] = i; } return imgs[url]; }
const foto = (rol, nombre) => nombre && E.fotos[rol].find(f => f.nombre === nombre);
const fotoSlot = (i = cur, l = lado) => foto(ROL[l], E.cartas[i] && E.cartas[i][l]);

async function api(ruta, datos) {
  const r = await fetch(ruta, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(datos || {}) });
  const j = await r.json(); if (j.error) { alert(j.error); throw j.error; } return j;
}
function aplicar(est) { E = est; cur = Math.min(cur, E.cartas.length - 1); pintarTodo(); }
let primera = true;
async function refrescar() {
  aplicar(await (await fetch('/api/estado')).json());
  if (primera) { primera = false; const m = location.hash.match(/carta=(\d+)/); irA(m ? +m[1] - 1 : cur); }
}

// ---------------- dibujo (misma geometría que el PDF)
function pintarCarta(c, carta, W, H, opts = {}) {
  const P = E.plantilla, s = H / P.h;
  c.drawImage(plantilla, 0, 0, W, H);
  for (const l of ['novia', 'novio']) {
    const C = P[l], D = 2 * C.r * P.escala * s, cx = C.cx * s, cy = C.cy * s;
    const f = foto(ROL[l], carta[l]);
    if (f) {
      const im = img(f.url);
      if (im.complete && im.naturalWidth) {
        const a = f.ajuste, w = a.zoom * D, h = w * im.naturalHeight / im.naturalWidth;
        c.save(); c.beginPath(); c.arc(cx, cy, D / 2, 0, 7); c.clip();
        c.translate(cx + a.x * D, cy + a.y * D); c.rotate(a.rot * Math.PI / 180);
        c.drawImage(im, -w / 2, -h / 2, w, h); c.restore();
      }
    }
    if (opts.candados && carta.fijo && carta.fijo[l] && !opts.desbloq[l]) {
      c.save(); c.font = `${opts.px * 15}px system-ui`; c.textAlign = 'center'; c.textBaseline = 'middle';
      c.fillStyle = '#fffdf8e6'; c.beginPath(); c.arc(cx + D * 0.36, cy - D * 0.36, opts.px * 13, 0, 7); c.fill();
      c.fillText('🔒', cx + D * 0.36, cy - D * 0.36 + opts.px); c.restore();
    }
    if (opts.sel === l) {
      c.save(); c.strokeStyle = (carta.fijo && carta.fijo[l] && !(opts.desbloq || {})[l]) ? '#9b8f7a' : '#c9a046'; c.lineWidth = opts.px * 2.5; c.setLineDash([opts.px * 7, opts.px * 5]);
      c.beginPath(); c.arc(cx, cy, D / 2 + opts.px * 3, 0, 7); c.stroke(); c.restore();
    }
    if (opts.postal) {
      c.save(); c.strokeStyle = '#c0392bcc'; c.lineWidth = opts.px * 1.5; c.setLineDash([opts.px * 4, opts.px * 4]);
      c.beginPath(); c.arc(cx, cy, P.postal * s, 0, 7); c.stroke(); c.restore();
    }
  }
}
function dibujar() {
  if (!E || !plantilla.complete) return;
  const P = E.plantilla, dpr = window.devicePixelRatio || 1, wrap = $('#lienzo-wrap');
  const altoCss = Math.min(wrap.clientHeight - 4, (wrap.clientWidth - 110) * P.h / P.w);
  cv.style.height = altoCss + 'px'; cv.style.width = altoCss * P.w / P.h + 'px';
  cv.width = Math.round(altoCss * P.w / P.h * dpr); cv.height = Math.round(altoCss * dpr);
  pintarCarta(ctx, E.cartas[cur], cv.width, cv.height, { sel: lado, postal: $('#postal').checked, px: dpr, candados: true, desbloq });
}
function dibujarTira() {
  if (!E || !plantilla.complete) return;
  document.querySelectorAll('.mini-carta canvas').forEach(c => {
    const i = +c.dataset.i; pintarCarta(c.getContext('2d'), E.cartas[i], c.width, c.height);
  });
}

// ---------------- paneles
function pintarTodo() {
  const n = E.cartas.length, g = E.cartas.filter(c => c.tipo === 'GANADORA').length;
  const vac = E.cartas.reduce((s, c) => s + !c.novia + !c.novio, 0);
  $('#resumen').textContent = `${n} cartas · ${Math.ceil(n / 4)} hojas carta` + (vac ? ` · ${vac} círculos vacíos` : '');
  $('#num').value = n; $('#papel').value = E.opciones.papel; $('#lineas').checked = E.opciones.lineas;
  $('#bajar-pdf').style.display = E.pdf_listo ? '' : 'none'; rev = E.rev;
  pintarTira(); pintarLista(); pintarSlot(); dibujar();
  const cuenta = t => E.cartas.filter(c => c.tipo === t).length;
  const pend = E.cartas.reduce((s, c) => s + (!c.fijo.novia && !c.novia) + (!c.fijo.novio && !c.novio), 0);
  const faltaReal = ['novia', 'novio'].filter(l => !E.reales[l]);
  $('#tira-info').innerHTML = `${g} ganadora · ${cuenta('NOVIA REAL')} con la novia real · ${cuenta('NOVIO REAL')} con el novio real`
    + (pend ? ` · <b style="color:#c46d1f">${pend} fake(s) por elegir</b>` : ' · ✓ todos los fakes elegidos')
    + ['novia', 'novio'].map(l => { const rol = ROL[l], libres = E.fotos[rol].filter(f => !f.real && f.en === null).length,
        vacios = E.cartas.filter(c => !c.fijo[l] && !c[l]).length;
        return vacios > libres ? ` · <b style="color:#9c3b3b">faltan ${vacios - libres} foto(s) de ${rol}</b>` : ''; }).join('')
    + (faltaReal.length ? ` · <b style="color:#9c3b3b">⚠ falta marcar la foto real de: ${faltaReal.join(' y ')}</b>` : '');
}
function pintarTira() {
  const T = $('#tira'), P = E.plantilla; T.innerHTML = '';
  const h = 120, w = Math.round(h * P.w / P.h), dpr = window.devicePixelRatio || 1;
  E.cartas.forEach((c, i) => {
    if (i && i % 4 === 0) { const s = document.createElement('div'); s.className = 'sep-hoja'; T.appendChild(s); }
    const d = document.createElement('div'); d.className = 'mini-carta' + (i === cur ? ' sel' : '');
    const k = document.createElement('canvas'); k.width = w * dpr; k.height = h * dpr; k.style.width = w + 'px'; k.style.height = h + 'px'; k.dataset.i = i;
    d.appendChild(k);
    const falta = !c.novia || !c.novio;
    d.insertAdjacentHTML('beforeend', `<span class="n">${i + 1}</span>` + (c.tipo === 'GANADORA' ? '<span class="g" title="ganadora">🏆</span>'
      : falta ? '<span class="g falta" title="falta elegir el fake"></span>' : ''));
    d.onclick = () => irA(i);
    T.appendChild(d);
  });
  dibujarTira();
  const sel = T.querySelector('.mini-carta.sel'); if (sel) sel.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}
function pintarLista() {
  const rol = ROL[lado], L = $('#lista'); L.innerHTML = '';
  document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.rol === rol));
  const otro = ladoEditable(cur) !== lado ? ladoEditable(cur) : null;
  $('#ayuda-lista').textContent = E.cartas[cur].fijo[lado]
    ? `En la carta ${cur + 1} ${lado === 'novia' ? 'la novia' : 'el novio'} es la foto real (bloqueada): estas fotos solo se pueden ver.`
      + (otro ? ` Para elegir, ve a la pestaña ${ROL[otro][0].toUpperCase() + ROL[otro].slice(1)}.` : '')
    : `Toca una foto libre para ponerla en la carta ${cur + 1} (${lado} fake). Cada fake va en una sola carta: las usadas tienen 🔒. `
      + `Libres: ${E.fotos[rol].filter(f => !f.real && f.en === null).length}.`;
  if (!E.fotos[rol].length) L.innerHTML = `<div class="vacio">Todavía no hay fotos de ${rol}.</div>`;
  const actual = E.cartas[cur][lado];
  for (const f of E.fotos[rol]) {
    const usadaOtra = !f.real && f.en !== null && f.en !== cur;
    const ladoBloq = E.cartas[cur].fijo[lado];
    const d = document.createElement('div');
    const realEnFake = f.real && !ladoBloq;          // la real nunca va en un lugar fake
    d.className = 'foto' + (f.nombre === actual ? ' sel' : '') + (usadaOtra ? ' usada' : '')
      + ((ladoBloq && !usadaOtra) || realEnFake ? ' noelegible' : '');
    d.style.backgroundImage = `url("${f.url}")`;
    d.title = f.nombre + (usadaOtra ? ` · ya está en la carta ${f.en + 1} (toca para ir)`
      : realEnFake ? ' · foto real: solo va en sus cartas fijas, no en un lugar fake' : '');
    d.innerHTML = (realEnFake ? '<span class="candado" title="no se puede elegir aquí">🔒</span>' : '')
      + (f.real ? '<span class="tag">REAL</span>' : usadaOtra ? `<span class="tag en">🔒 carta ${f.en + 1}</span>` : f.en === cur ? '<span class="tag">aquí</span>' : '')
      + (f.guardado ? '' : '<span class="pend" title="sin ajustar"></span>');
    d.onclick = async () => {
      if (usadaOtra) { irA(f.en); $('#msg').textContent = `${f.nombre} ya está en la carta ${f.en + 1}: los fakes no se repiten`; return; }
      if (ladoBloq) { aviso(`En la carta ${cur + 1} ${lado === 'novia' ? 'la novia' : 'el novio'} es la foto real: aquí solo se elige ${lado === 'novia' ? 'el novio' : 'la novia'}.`); return; }
      if (f.real) { aviso(`La foto real solo va en sus cartas fijas: en la carta ${cur + 1} este lugar es para ${lado === 'novia' ? 'una novia' : 'un novio'} fake.`); return; }
      aplicar((await api('/api/carta', { i: cur, lado, nombre: f.nombre })).estado);
    };
    L.appendChild(d);
  }
  $('#carpeta').innerHTML = `o, en la compu donde corre la herramienta, cópialas a <b>${E.carpeta}/${rol}</b>.`;
}
function pintarSlot() {
  document.querySelectorAll('.lados button').forEach(b => b.classList.toggle('on', b.dataset.lado === lado));
  const c = E.cartas[cur], f = fotoSlot();
  $('#titulo-carta').innerHTML = `Carta ${cur + 1} de ${E.cartas.length} <span class="sub">· hoja ${Math.floor(cur / 4) + 1}</span>`
    + (c.tipo === 'GANADORA' ? '<span class="badge">GANADORA</span>' : c.tipo ? `<span class="badge s">${c.tipo}</span>` : '');
  const fijo = c.fijo[lado];
  $('#slot').innerHTML = fijo
    ? (f ? `<div class="mini" style="background-image:url('${f.url}')"></div><div style="flex:1;min-width:0"><b>Foto real</b><div class="sub" style="word-break:break-all">${f.nombre}</div><div class="sub">fija en esta carta</div></div>`
         : `<div class="mini"></div><div class="sub" style="flex:1">Aquí va la foto <b>real</b> de ${ROL[lado]}, pero todavía no hay ninguna marcada como real.</div>`)
    : f ? `<div class="mini" style="background-image:url('${f.url}')"></div><div style="flex:1;min-width:0"><b style="word-break:break-all">${f.nombre}</b><div class="sub">fake</div></div><button id="quitar">Quitar</button>`
        : `<div class="mini"></div><div class="sub" style="flex:1">Lugar para un <b>${lado} fake</b>: elige una foto de la izquierda.</div>`;
  if (f && !fijo) $('#quitar').onclick = async () => aplicar((await api('/api/carta', { i: cur, lado, nombre: null })).estado);
  $('#controles').style.display = f ? '' : 'none';
  if (!f) return;
  const lock = fijo && !desbloq[lado];
  ['#zoom', '#rot', '#auto', '#centrar'].forEach(s => $(s).disabled = lock);
  $('#real').disabled = f.real || !!E.reales[lado];            // la real no se puede desmarcar ni cambiar
  $('#fila-real').style.display = f.real ? 'none' : '';
  $('#borrar').style.display = f.real ? 'none' : '';
  $('#candado').style.display = fijo ? '' : 'none';
  $('#candado').innerHTML = lock
    ? '🔒 <b>Bloqueada.</b> La foto real ya está acomodada.<div style="margin-top:6px"><button id="desbloquear">Desbloquear para ajustar</button></div>'
    : '🔓 <b>Desbloqueada:</b> lo que muevas cambia la foto real en sus ' + f.usos + ' cartas.<div style="margin-top:6px"><button id="rebloquear">Volver a bloquear</button></div>';
  const b1 = $('#desbloquear'), b2 = $('#rebloquear');
  if (b1) b1.onclick = () => { if (confirm('¿Desbloquear el ajuste de la foto real? Afecta a sus ' + f.usos + ' cartas.')) { desbloq[lado] = true; pintarSlot(); dibujar(); } };
  if (b2) b2.onclick = () => { desbloq[lado] = false; pintarSlot(); dibujar(); };
  $('#usos').textContent = `El ajuste es de la foto: se aplica en las ${f.usos} carta(s) donde está.`;
  $('#real').checked = f.real;
  $('#zoom').value = f.ajuste.zoom; $('#rot').value = f.ajuste.rot;
  $('#vzoom').textContent = f.ajuste.zoom.toFixed(2) + '×'; $('#vrot').textContent = (+f.ajuste.rot).toFixed(1) + '°';
}
function ladoEditable(i) {            // carta con la novia real → se edita el novio (y al revés)
  const f = E.cartas[i].fijo;
  return f.novia && !f.novio ? 'novio' : f.novio && !f.novia ? 'novia' : lado;
}
function irA(i) {
  desbloq.novia = desbloq.novio = false; cur = Math.max(0, Math.min(E.cartas.length - 1, i));
  lado = ladoEditable(cur);
  history.replaceState(null, '', '#carta=' + (cur + 1));
  pintarTira(); pintarLista(); pintarSlot(); dibujar();
}
function elegirLado(l) { lado = l; pintarLista(); pintarSlot(); dibujar(); }

// ---------------- ajuste (se guarda solo)
let tGuardar = null;
function cambiar(fn) {
  const f = fotoSlot(); if (!f) return;
  if (bloqueado()) { avisoBloqueo(); return; }
  fn(f.ajuste); f.ajuste.zoom = Math.min(6, Math.max(0.1, f.ajuste.zoom));
  pintarSlot(); dibujar();
  $('#estado-guardado').textContent = 'guardando…'; $('#estado-guardado').style.color = '#9b8b72';
  clearTimeout(tGuardar);
  const rol = ROL[lado];
  tGuardar = setTimeout(async () => {
    await api('/api/ajuste', { rol, nombre: f.nombre, ajuste: f.ajuste, desbloqueado: f.real });
    f.guardado = true; $('#estado-guardado').textContent = 'guardado ✓'; $('#estado-guardado').style.color = '#3d7a3d';
    pintarLista(); dibujarTira(); tGuardar = null;
  }, 300);
}
async function pendiente() { if (tGuardar) { clearTimeout(tGuardar); const f = fotoSlot(); tGuardar = null; if (f) await api('/api/ajuste', { rol: ROL[lado], nombre: f.nombre, ajuste: f.ajuste, desbloqueado: f.real }); } }
let tAviso = null, tMsg = null;
function aviso(txt) { $('#msg').textContent = txt; clearTimeout(tMsg); tMsg = setTimeout(() => { if ($('#msg').textContent === txt) $('#msg').textContent = ''; }, 4000); }
function avisoBloqueo() {
  $('#estado-guardado').textContent = '🔒 la foto real está bloqueada'; $('#estado-guardado').style.color = '#9c3b3b';
  clearTimeout(tAviso); tAviso = setTimeout(() => $('#estado-guardado').textContent = '', 1800);
}

function enCirculo(e) {
  const r = cv.getBoundingClientRect(), k = r.height / E.plantilla.h;
  const x = (e.clientX - r.left) / k, y = (e.clientY - r.top) / k;
  for (const l of ['novia', 'novio']) { const C = E.plantilla[l]; if (Math.hypot(x - C.cx, y - C.cy) < C.r * 1.2) return { l, D: 2 * C.r * E.plantilla.escala * k }; }
  return null;
}
let arr = null;
cv.addEventListener('pointerdown', e => {
  const h = enCirculo(e); if (!h) return;
  if (h.l !== lado) elegirLado(h.l);
  if (!fotoSlot()) return;
  if (bloqueado()) { avisoBloqueo(); return; }
  arr = { x: e.clientX, y: e.clientY, D: h.D }; cv.setPointerCapture(e.pointerId); cv.style.cursor = 'grabbing';
});
cv.addEventListener('pointermove', e => {
  if (!arr) { const h = enCirculo(e); cv.style.cursor = !h ? 'default' : (E.cartas[cur].fijo[h.l] && !desbloq[h.l]) ? 'not-allowed' : 'grab'; return; }
  const dx = (e.clientX - arr.x) / arr.D, dy = (e.clientY - arr.y) / arr.D; arr.x = e.clientX; arr.y = e.clientY;
  cambiar(a => { a.x += dx; a.y += dy; });
});
cv.addEventListener('pointerup', () => { arr = null; cv.style.cursor = 'grab'; });
cv.addEventListener('wheel', e => {
  const h = enCirculo(e); if (!h) return; e.preventDefault();
  if (h.l !== lado) elegirLado(h.l);
  const f = Math.exp(-e.deltaY * 0.0012); cambiar(a => { a.zoom *= f; a.x *= f; a.y *= f; });
}, { passive: false });
window.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' && !['range', 'checkbox'].includes(e.target.type) || e.target.tagName === 'SELECT') return;
  if (e.key === 'PageDown') { e.preventDefault(); irA(cur + 1); return; }
  if (e.key === 'PageUp') { e.preventDefault(); irA(cur - 1); return; }
  if (!fotoSlot()) return;
  const p = e.shiftKey ? 0.02 : 0.004;
  const m = { ArrowLeft: [-p, 0], ArrowRight: [p, 0], ArrowUp: [0, -p], ArrowDown: [0, p] }[e.key];
  if (m) { e.preventDefault(); cambiar(a => { a.x += m[0]; a.y += m[1]; }); }
  if (e.key === '+' || e.key === '=') cambiar(a => { a.zoom *= 1.02; a.x *= 1.02; a.y *= 1.02; });
  if (e.key === '-') cambiar(a => { a.zoom /= 1.02; a.x /= 1.02; a.y /= 1.02; });
});
$('#zoom').oninput = e => cambiar(a => { const f = +e.target.value / a.zoom; a.zoom = +e.target.value; a.x *= f; a.y *= f; });
$('#rot').oninput = e => cambiar(a => { a.rot = +e.target.value; });
$('#centrar').onclick = () => cambiar(a => { a.x = 0; a.y = 0; });
$('#auto').onclick = async () => { clearTimeout(tGuardar); tGuardar = null; const f = fotoSlot();
  aplicar((await api('/api/borrar-ajuste', { rol: ROL[lado], nombre: f.nombre, desbloqueado: f.real })).estado); };
$('#real').onchange = async e => { await pendiente(); const f = fotoSlot();
  aplicar((await api('/api/real', { rol: ROL[lado], nombre: f.nombre, real: e.target.checked })).estado); };
$('#borrar').onclick = async () => { const f = fotoSlot();
  if (!confirm(`¿Borrar ${f.nombre}? Se quita de las ${f.usos} carta(s) donde está.`)) return;
  aplicar((await api('/api/borrar', { rol: ROL[lado], nombre: f.nombre })).estado); };

// ---------------- navegación y cartas
$('#ant').onclick = () => irA(cur - 1); $('#sig').onclick = () => irA(cur + 1);
document.querySelectorAll('.lados button').forEach(b => b.onclick = () => elegirLado(b.dataset.lado));
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => elegirLado(b.dataset.rol === 'novias' ? 'novia' : 'novio'));
$('#postal').onchange = dibujar; window.addEventListener('resize', () => dibujar());
function avisoRelleno(r) {
  const f = Object.entries(r.faltan || {});
  const txt = (r.cambios ? `Se pusieron ${r.cambios} fake(s).` : 'No se puso ningún fake.')
    + (f.length ? ` Quedan lugares vacíos porque no hay suficientes fotos sin usar: ${f.map(([rol, n]) => `${n} de ${rol}`).join(', ')}.` : '');
  $('#msg').textContent = txt; if (!r.cambios || f.length) alert(txt);
}
$('#rellenar').onclick = async () => { const r = await api('/api/rellenar', { todo: false }); aplicar(r.estado); avisoRelleno(r); };
$('#repartir').onclick = async () => { if (!confirm('¿Volver a repartir TODOS los fakes? Los reales se quedan donde están.')) return;
  const r = await api('/api/rellenar', { todo: true }); aplicar(r.estado); avisoRelleno(r); };
$('#estructura').onclick = async () => { if (!confirm('¿Restablecer la estructura? Se vacían todos los fakes elegidos.')) return; aplicar((await api('/api/estructura')).estado); };
$('#num').onchange = async e => { const n = +e.target.value;
  if (n < E.cartas.length && !confirm(`¿Quitar las últimas ${E.cartas.length - n} carta(s)?`)) { e.target.value = E.cartas.length; return; }
  aplicar((await api('/api/numero', { n })).estado); };
$('#papel').onchange = async e => aplicar((await api('/api/opciones', { papel: e.target.value })).estado);
$('#lineas').onchange = async e => aplicar((await api('/api/opciones', { lineas: e.target.checked })).estado);
$('#subir').onchange = async e => {
  const rol = ROL[lado]; let ultimo = null;
  for (const f of e.target.files) {
    const r = await fetch(`/api/subir?rol=${rol}&nombre=${encodeURIComponent(f.name)}`, { method: 'POST', body: f });
    const j = await r.json(); if (j.error) { alert(j.error); continue; } ultimo = j.nombre;
    if (j.repetida) $('#msg').textContent = `${f.name} ya estaba (como ${j.nombre}): no se duplicó`;
  }
  e.target.value = ''; await refrescar();
};
$('#ver').onclick = async () => { await pendiente(); $('#final').src = ''; $('#modal').style.display = 'flex';
  const r = await fetch('/api/vista', { method: 'POST', body: JSON.stringify({ i: cur }) }); $('#final').src = URL.createObjectURL(await r.blob()); };
$('#modal').onclick = () => $('#modal').style.display = 'none';

// ---------------- buscar en internet
let bPagina = 1, bQ = '';
function abrirBuscador() {
  $('#b-rol').textContent = ROL[lado]; $('#buscador').style.display = 'flex'; $('#b-q').focus();
  $('#b-fuente').textContent = E.google ? 'Buscando con Google (API).' : 'Sin clave de Google: se busca en Wikimedia Commons (fotos libres). Para Google usa el botón «Abrir en Google Imágenes».';
  actualizarLinkGoogle();
}
function actualizarLinkGoogle() {
  const q = $('#b-q').value.trim(); $('#b-google').href = 'https://www.google.com/search?tbm=isch&q=' + encodeURIComponent(q || (lado === 'novia' ? 'actriz famosa' : 'actor famoso'));
}
async function buscarImgs(mas) {
  const q = $('#b-q').value.trim(); if (!q) return;
  if (!mas) { bPagina = 1; bQ = q; $('#b-res').innerHTML = '<div class="sub">buscando…</div>'; } else bPagina++;
  const r = await (await fetch(`/api/buscar?q=${encodeURIComponent(bQ)}&pagina=${bPagina}`)).json();
  if (!mas) $('#b-res').innerHTML = '';
  if (r.error) { $('#b-res').innerHTML = `<div class="sub" style="color:#9c3b3b">${r.error}</div>`; return; }
  $('#b-fuente').textContent = `Resultados de ${r.fuente}. Toca una para agregarla` + (E.cartas[cur].fijo[lado] ? '.' : ` y ponerla en la carta ${cur + 1}.`);
  if (!r.resultados.length && !mas) $('#b-res').innerHTML = '<div class="sub">Sin resultados. Prueba con «Abrir en Google Imágenes».</div>';
  for (const it of r.resultados) {
    const d = document.createElement('div'); d.className = 'r'; d.style.backgroundImage = `url("${it.thumb}")`;
    d.title = it.titulo + (it.ancho ? ` (${it.ancho}×${it.alto})` : '');
    d.innerHTML = `<span>${it.titulo}</span>`;
    d.onclick = async () => {
      if (d.classList.contains('bajando')) return;
      d.classList.add('bajando');
      if (await agregarURL(it.url, bQ)) { d.classList.add('ok'); d.title = 'Ya está en la biblioteca.'; }
      d.classList.remove('bajando');
    };
    $('#b-res').appendChild(d);
  }
  $('#b-mas-fila').style.display = r.resultados.length ? '' : 'none';
}
const bajando = new Set();
async function agregarURL(url, nombre) {
  if (bajando.has(url)) return false;            // ya se está bajando: ignora los clics repetidos
  bajando.add(url);
  $('#msg').textContent = 'bajando imagen…'; $('#b-url-ir').disabled = true;
  try {
    const r = await api('/api/descargar', { rol: ROL[lado], url, nombre: nombre || '', i: cur }).catch(() => null);
    if (!r) { $('#msg').textContent = ''; return false; }
    const donde = r.asignada ? `puesta en la carta ${cur + 1}` : r.usada_en ? `ya está en la carta ${r.usada_en} (no se repite)` : `en ${ROL[lado]}`;
    $('#msg').textContent = r.repetida ? `✓ ${r.nombre} ya estaba en la biblioteca (no se duplicó) · ${donde}` : `✓ ${r.nombre} agregada · ${donde}`;
    aplicar(r.estado);
    return true;
  } finally { bajando.delete(url); $('#b-url-ir').disabled = false; }
}
$('#abrir-buscar').onclick = abrirBuscador;
$('#b-cerrar').onclick = () => $('#buscador').style.display = 'none';
$('#buscador').onclick = e => { if (e.target.id === 'buscador') $('#buscador').style.display = 'none'; };
$('#b-ir').onclick = () => buscarImgs(false);
$('#b-mas').onclick = () => buscarImgs(true);
$('#b-q').addEventListener('keydown', e => { if (e.key === 'Enter') buscarImgs(false); });
$('#b-q').addEventListener('input', actualizarLinkGoogle);
$('#b-url-ir').onclick = async () => { const u = $('#b-url').value.trim(); if (u) { await agregarURL(u, $('#b-q').value.trim()); $('#b-url').value = ''; } };
$('#b-url').addEventListener('keydown', e => { if (e.key === 'Enter') $('#b-url-ir').click(); });
$('#g-guardar').onclick = async () => { const r = await api('/api/google', { key: $('#g-key').value, cx: $('#g-cx').value }); aplicar(r.estado);
  $('#g-key').value = $('#g-cx').value = ''; abrirBuscador(); };
$('#g-borrar').onclick = async () => { const r = await api('/api/google', { borrar: true }); aplicar(r.estado); abrirBuscador(); };

// arrastrar imágenes (archivos o desde otra pestaña, p. ej. Google Imágenes)
async function soltar(e) {
  e.preventDefault(); $('#soltar').classList.remove('encima');
  const dt = e.dataTransfer, archivos = [...(dt.files || [])].filter(f => f.type.startsWith('image/'));
  if (archivos.length) {
    for (const f of archivos) {
      const r = await fetch(`/api/subir?rol=${ROL[lado]}&nombre=${encodeURIComponent(f.name)}`, { method: 'POST', body: f });
      const j = await r.json(); if (j.error) alert(j.error);
    }
    return refrescar();
  }
  const html = dt.getData('text/html'), m = html && html.match(/<img[^>]+src="([^"]+)"/i);
  const url = (m && m[1].replace(/&amp;/g, '&')) || dt.getData('text/uri-list') || dt.getData('text/plain');
  if (url) agregarURL(url.split('\n')[0].trim(), $('#b-q').value.trim());
}
for (const zona of [$('#soltar'), $('#buscador .caja')]) {
  zona.addEventListener('dragover', e => { e.preventDefault(); $('#soltar').classList.add('encima'); });
  zona.addEventListener('dragleave', () => $('#soltar').classList.remove('encima'));
  zona.addEventListener('drop', soltar);
}

// ---------------- generar PDF
$('#generar').onclick = async () => {
  await pendiente();
  $('#generar').disabled = true; $('#barra').style.display = 'block';
  await api('/api/generar');
  const t = setInterval(async () => {
    const p = await (await fetch('/api/progreso')).json();
    $('#barra div').style.width = (100 * p.hecho / p.total) + '%'; $('#msg').textContent = p.msg;
    if (!p.activo) {
      clearInterval(t); $('#generar').disabled = false; $('#barra').style.display = 'none';
      if (p.error) { $('#msg').textContent = 'error: ' + p.error; return; }
      const r = p.resultado;
      $('#msg').textContent = `✓ ${r.cartas} cartas, ${r.hojas} hojas` + (r.vacios ? ` (${r.vacios} círculos vacíos)` : '') + ' · descargando…';
      const a = document.createElement('a'); a.href = '/descargar/cartas.pdf?v=' + Date.now(); a.download = 'cartas_quien_tiene_a_los_novios.pdf';
      document.body.appendChild(a); a.click(); a.remove();
      await refrescar();
    }
  }, 400);
};

plantilla.onload = () => { dibujar(); dibujarTira(); }; plantilla.src = '/plantilla.jpg';
// sincronización: si otra persona (u otra pestaña) cambia algo, se actualiza aquí
let rev = null;
setInterval(async () => {
  if (tGuardar || arr || document.hidden) return;
  try { const r = await (await fetch('/api/rev')).json(); if (rev && r.rev !== rev) await refrescar(); } catch (e) {}
}, 2500);
window.addEventListener('focus', () => { if (!tGuardar && !arr) refrescar(); });
refrescar();
</script>
</body></html>
"""

if __name__ == "__main__":
    main()
