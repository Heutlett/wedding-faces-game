# wedding-faces-game · ¿Quién tiene a los novios?

Herramienta para armar e imprimir las cartas del juego de boda **"¿Quién tiene a los novios?"**.

Cada carta tiene dos círculos: el de la **novia** (izquierda) y el del **novio** (derecha). Sobre cada círculo se pega una postal para raspar. Gana quien tenga la carta con **los dos novios reales**.

Todo vive en un solo script, `cartas.py`. Abre una página web local donde:

- se eligen y acomodan las caras de las 45 cartas, solo o entre varias personas;
- el botón **Generar y descargar PDF** baja el PDF listo para imprimir: 4 cartas por hoja carta, con marcas de corte.

---

## Requisitos

- Python **3.10 o más nuevo**
- [Pillow](https://pypi.org/project/Pillow/) (`pip install -r requirements.txt`)
- Un navegador moderno (Chrome, Firefox, Edge, Safari)

No necesita Chrome headless, OpenCV ni ningún servicio externo. La búsqueda de fotos en internet es opcional.

## Inicio rápido

```bash
git clone git@github.com:Heutlett/wedding-faces-game.git
cd wedding-faces-game
pip install -r requirements.txt

# el repo no trae fotos: pon las tuyas (la real debe empezar con "real")
cp ~/Descargas/novia.png caras/novias/real_rostro_novia.png
cp ~/Descargas/novio.png caras/novios/real_rostro_novio.png
# los fakes se pueden copiar igual o agregar/buscar desde la herramienta

python3 cartas.py
```

Se abre `http://127.0.0.1:8765`. Para cerrar la herramienta: `Ctrl+C` en la terminal.

---

## Dinámica del juego

Con 45 cartas (= 90 postales), **todas** llevan a la novia real o al novio real:

| Cartas | Círculo izquierdo (novia) | Círculo derecho (novio) | Tipo |
|---|---|---|---|
| 1 | **real** 🔒 | **real** 🔒 | GANADORA 🏆 |
| 2 – 23 (22 cartas) | **real** 🔒 | fake | NOVIA REAL |
| 24 – 45 (22 cartas) | fake | **real** 🔒 | NOVIO REAL |

Reglas que aplica la herramienta (en la página **y** en el servidor):

- **Los lugares reales están bloqueados.**
  - No se pueden quitar, reemplazar ni dejar vacíos.
  - La foto real no se puede borrar ni desmarcar.
  - Su encuadre también está bloqueado. Si hace falta moverlo, hay un botón *Desbloquear para ajustar*, con confirmación, que vuelve a bloquearse al cambiar de carta.
  - Un lugar real siempre muestra la foto marcada como real de ese lado, sea cual sea su nombre de archivo.
- **Cada cara fake se usa en una sola carta.**
  - En total se necesitan **22 fotos distintas de novios fake y 22 de novias fake**.
  - Las fotos ya usadas aparecen grises con "🔒 carta N"; si tocas una, te lleva a esa carta.
- La foto real solo puede ir en sus lugares fijos, nunca en un lugar fake.
- Si cambias el número de cartas, se mantiene la proporción: 1 + la mitad + la mitad. Con un número impar de cartas restantes, la carta extra lleva a la novia real.

---

## Estructura del repositorio

```
cartas.py                  la herramienta completa (servidor web + página + generador del PDF)
requirements.txt           dependencias (Pillow)
assets/plantilla/
  carta.png                diseño de la carta (2052 × 2788 px, sin caras)
  carta.json               tamaño de la plantilla y posición/radio de los dos círculos (en px de carta.png)
  final-design.jpeg        diseño original (4 cartas en una hoja) del que sale carta.png
caras/
  novias/                  fotos de novias (vacía en el repo: cada usuario pone las suyas)
  novios/                  fotos de novios (vacía en el repo: cada usuario pone las suyas)
```

Al usar la herramienta se crean archivos locales que **no** se suben:

- `proyecto.json`: qué cara lleva cada carta, el ajuste de cada foto y las opciones. Se crea al abrir la herramienta por primera vez.
- `google.json`: la clave de la API de Google, solo si la configuras.

No se suben al repo (ver `.gitignore`):

- **ninguna foto:** ni reales ni fakes. De `caras/` solo se suben las carpetas vacías; tampoco las imágenes sueltas en la raíz;
- **`proyecto.json`:** la configuración de cartas y ajustes de cada usuario;
- **los PDF y salidas** (`output/`, `.output/`, `*.pdf`, `manifest*.csv`), porque llevan las caras reales;
- **`google.json`**, que guarda la clave de la API de Google.

---

## Caras

La herramienta toma las fotos **solo** de `caras/novias/` y `caras/novios/`.

- **Formatos:** `.jpg`, `.jpeg`, `.png`, `.webp`. Los PNG con fondo transparente (recortes) también sirven.
- **Foto real:** es la que **empieza con `real`** (por ejemplo `real_rostro_novia.png`). Solo puede haber una por lado.
- **Cómo agregar fotos:** cópialas a la carpeta, súbelas desde la herramienta, arrástralas o búscalas en internet (ver más abajo). Lo que copies a mano aparece al volver a la ventana de la herramienta, o en unos segundos.
- **Sin duplicados:** si una imagen idéntica (byte a byte) ya está en la carpeta, no se crea otra copia y se reutiliza la existente.
- **Encuadre:** el ajuste (posición, zoom, rotación) se guarda **por foto**, en `proyecto.json`, y vale en todas las cartas donde esté esa foto. Las fotos sin ajustar usan un encuadre automático: en recortes con fondo transparente busca la cabeza completa; en fotos normales usa un cuadrado centrado.

**En otra compu:** el repo no trae fotos ni `proyecto.json`, así que cada usuario arma lo suyo: copia sus fotos a `caras/novias/` y `caras/novios/` y elige los fakes en la herramienta. Para llevarte tu avance tal cual, copia también tu `proyecto.json`. Con las fotos con el mismo nombre, se conservan las cartas y los ajustes.

Correcciones automáticas al abrir la herramienta (las avisa en la terminal):

- **Fotos repetidas:** si la misma foto está varias veces en la carpeta, se deja una y sus cartas y ajustes pasan a esa.
- **Fotos renombradas o borradas fuera de la herramienta:**
  - primero se buscan sin distinguir mayúsculas;
  - si falta exactamente una foto y sobra exactamente una, se emparejan;
  - lo que no se pueda emparejar queda vacío.
- **Fakes repetidos en el proyecto:** se quedan en la primera carta y en las demás se vacían.

---

## La herramienta

```
┌ encabezado: papel · líneas de corte · ⬇ manifest.csv · ⬇ último PDF · [Generar y descargar PDF] ┐
├ Fotos ─────────────┬ Carta actual ─────────────────────────┬ Esta carta ───────────────────────┤
│ Novias | Novios    │  ‹  vista de la carta tal como sale  › │ Novia (izq.) | Novio (der.)        │
│ biblioteca         │     impresa (círculo editado          │ foto del lado elegido              │
│ ＋ Subir fotos     │     resaltado, 🔒 en los reales)      │ zoom · rotación · encuadre auto    │
│ 🔍 Buscar en internet                                      │ candado de la foto real            │
├ Cartas ───────────────────────────────────────────────────────────────────────────────────────┤
│ resumen · Rellenar vacías · Repartir todos · Restablecer 1 + mitad + mitad · Número de cartas │
│ [1🏆][2][3][4] | [5][6][7][8] | …   (separadas por hoja; punto naranja = falta el fake)       │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Elegir los fakes**
1. Toca una carta en la tira de abajo, o cambia de carta con `‹ ›` o `PageUp` / `PageDown`.
   - Al cambiar de carta se selecciona solo el lado que se puede elegir: en una carta *NOVIA REAL* se activan el lado y la pestaña **Novios**; en una *NOVIO REAL*, el lado y la pestaña **Novias**.
   - La URL guarda la carta abierta (`…/#carta=12`), así que puedes mandarle a alguien el enlace directo a una carta.
2. Toca una foto **libre** de la biblioteca: queda puesta en esa carta. *Quitar* la deja libre otra vez.
   - Puedes cambiar a la otra pestaña para ver esas fotos, pero en esa carta no se pueden poner, porque ese lado es la foto real. Se ven atenuadas y, si tocas una, solo aparece un aviso.

**Acomodar una cara**, en el círculo de la carta:
- **arrastrar** = mover;
- **rueda del mouse** o `+` / `−` = zoom;
- **flechas** = mover fino (con `Shift`, más rápido);
- en el panel derecho: zoom, rotación (−45° a 45°), *Encuadre automático* y *Centrar*;
- se guarda solo, unos 0.3 s después del último cambio.

**Llenar varias cartas a la vez**
- **Rellenar vacías con fakes:** pone fotos **sin usar** en los lugares fake vacíos, al azar. Si no alcanzan, avisa cuántas faltan.
- **Repartir todos los fakes:** vacía todos los lugares fake y los vuelve a llenar al azar. Los reales no se mueven.
- **Restablecer 1 + mitad + mitad:** vuelve a la estructura de arriba con todos los fakes vacíos.

**Revisar**
- **Ver esta carta en calidad de impresión:** la carta tal cual saldrá en el PDF.
- **mostrar postal (3.8 cm):** dibuja el tamaño de la postal sobre los círculos.

### Buscar fotos en internet

Botón **🔍 Buscar en internet**, en el panel de Fotos. Lo que se baja se guarda en `caras/<novias|novios>/`, entra siempre como **fake** y, si el lado elegido de la carta actual es un lugar fake libre, queda puesto en ella.

1. **Búsqueda directa:** escribe un nombre y toca un resultado.
   - Sin configurar nada busca en **Wikimedia Commons** (fotos libres, menos variedad).
   - Con una clave de Google (punto 3) busca con la **API oficial de Google** (filtro de caras, búsqueda segura).
2. **Desde Google Imágenes, a mano:**
   - *Abrir en Google Imágenes ↗* abre la búsqueda en otra pestaña.
   - Ahí, clic derecho en la imagen → *Copiar dirección de la imagen*, y pégala en *Agregar*.
   - También puedes arrastrar la imagen a la ventana de la herramienta.
   - Si el sitio no deja bajarla, guárdala en tu compu y usa *Subir fotos*.
3. **Google directo (opcional):**
   - Crea una clave de la API *Custom Search JSON* en Google Cloud y un *Programmable Search Engine* con búsqueda de imágenes. Google da 100 búsquedas gratis al día.
   - Pega la clave y el ID del buscador (`cx`) en *Buscar directo con Google*. Se guardan en `google.json`, que no se sube al repo.

Por seguridad, la herramienta solo baja direcciones `http(s)` públicas: rechaza direcciones locales o privadas, y archivos de más de 25 MB o que no sean imágenes.

---

## Generar el PDF

- **Botón *Generar y descargar PDF*:** arma el PDF y lo **descarga en el navegador** de quien lo pulsa, como `cartas_quien_tiene_a_los_novios.pdf`. No se guarda en ninguna carpeta. *⬇ último PDF* lo vuelve a bajar.
- **⬇ manifest.csv:** baja la lista de qué caras lleva cada carta. Columnas: `carta, hoja, fila, columna, novia, novio, tipo`, con `tipo` = `GANADORA`, `NOVIA REAL` o `NOVIO REAL`.
- **Sin abrir la herramienta:** `python3 cartas.py --generar` escribe `output/cartas.pdf` y `output/manifest.csv`.

Los lugares vacíos salen como el círculo color piel del diseño.

### Formato de impresión

| | |
|---|---|
| Hoja | carta, 8.5 × 11 in (21.59 × 27.94 cm), 4 cartas por hoja (2 × 2), 12 hojas para 45 cartas |
| Carta | 9.84 × 13.37 cm, sin sangrado; las cartas comparten la línea de corte |
| Márgenes | 6 mm arriba y abajo, ≈ 9.6 mm a los lados |
| Círculos | Ø 2.78 cm; centros a 2.92 cm uno del otro |
| Resolución | 400 dpi, imágenes JPEG dentro del PDF (unos 20 MB) |
| Corte | marcas de corte en el margen y, opcionalmente, una línea gris fina entre cartas (casilla *líneas de corte*) |
| Color | *Papel → cartulina mate* aplica una compensación suave: medios tonos un poco más claros (gamma 0.92), saturación ×1.06 y negro mínimo 6/255. *sin compensar* imprime los colores tal cual. |

### Imprimir y armar

1. Imprime a **tamaño real / 100 %**, sin "ajustar a la página". En la impresora elige papel tipo *cartulina / grueso* y calidad alta.
2. Corta por las marcas.
3. **Postales:** la postal de Ø 3.8 cm tapa el círculo de 2.78 cm con margen. Como los centros de los círculos están a 2.92 cm, dos postales centradas se enciman unos 9 mm. Para que solo se toquen, recorre cada una unos 4–5 mm hacia afuera (la de la novia a la izquierda, la del novio a la derecha). Así siguen tapando el círculo completo, con menos de 1 mm de margen del lado de adentro.

---

## Trabajar con otras personas

La herramienta acepta varias personas a la vez.

- **Sincronización:** lo que una persona cambia (fotos subidas, fakes elegidos, ajustes) les aparece a las demás en unos 2–3 segundos.
- **Fotos subidas:** se guardan en `caras/` de la compu donde corre la herramienta.
- **El PDF:** se descarga en el navegador de quien pulsa el botón.

Para compartirla:

1. Arranca la herramienta: `python3 cartas.py`. Escucha en `127.0.0.1:8765`; si ese puerto está ocupado, usa el siguiente libre y lo dice en la terminal.
2. Reenvía ese puerto, por ejemplo desde la pestaña *Puertos* de VS Code o con `ssh -R`, y comparte la URL.
3. Si tu método de reenvío necesita que escuche en todas las interfaces: `python3 cartas.py --host 0.0.0.0`.

> ⚠️ La herramienta **no tiene contraseña**: cualquiera con la URL puede editar las cartas, subir fotos y bajar el PDF, que incluye las caras reales. En VS Code deja el puerto como *privado* y comparte el enlace solo con quien deba ayudar.

---

## Opciones de línea de comandos

| Opción | Qué hace |
|---|---|
| *(ninguna)* | abre la herramienta en `127.0.0.1:8765` y el navegador |
| `--generar` | genera `output/cartas.pdf` y `output/manifest.csv` con lo guardado y termina |
| `--caras DIR` | usa otra carpeta de caras (debe tener `novias/` y `novios/`) |
| `--host H` | interfaz donde escucha (def. `127.0.0.1`; `0.0.0.0` = todas) |
| `--puerto N` | puerto (def. `8765`) |
| `--sin-navegador` | no abre el navegador al arrancar |

---

## Archivos de datos

### `proyecto.json`

Lo escribe la herramienta y es local de cada usuario: no se sube al repo. Normalmente no hace falta editarlo a mano.

```jsonc
{
  "version": 2,
  "cartas": [                                    // en el orden del PDF
    {"novia": "@real", "novio": "@real"},        // "@real" = la foto real de ese lado (bloqueado)
    {"novia": "@real", "novio": "brad_pitt.jpg"},
    {"novia": null,    "novio": "@real"}         // null = lugar fake vacío
  ],
  "ajustes": {                                   // encuadre de cada foto, relativo al diámetro del círculo
    "novios/brad_pitt.jpg": {"zoom": 1.2, "x": 0.02, "y": 0.10, "rot": 0}
  },
  "opciones": {"papel": "cartulina", "lineas": true},
  "origenes": {"https://…/foto.jpg": "novios/brad_pitt.jpg"}   // de dónde se bajó cada foto (evita bajarla dos veces)
}
```

Significado de cada campo del ajuste:

- **`zoom`:** ancho de la foto entre el diámetro del círculo.
- **`x`, `y`:** desplazamiento del centro de la foto, en diámetros.
- **`rot`:** grados en sentido horario.

### `assets/plantilla/`

- **`carta.png`:** el diseño final de la carta, sin caras. Es la carta de arriba a la izquierda de `final-design.jpeg`, escalada ×4 con super-resolución (EDSR) para que imprima nítida a 400 dpi.
- **`carta.json`:** tamaño de `carta.png` y centro y radio de cada círculo, en píxeles. Si se cambia el diseño, hay que actualizar ambos.

---

## Solución de problemas

- **No veo un cambio que hice en el código o en el repo:** reinicia la herramienta (`Ctrl+C` y `python3 cartas.py`). El servidor carga el código solo al arrancar.
- **"el puerto 8765 está ocupado":** ya hay otra herramienta abierta. Ciérrala, o usa la URL con el puerto que indica la terminal.
- **"faltan N foto(s) de novias/novios":** hay menos fakes sin usar que lugares fake. Agrega más fotos.
- **"Esa cara ya está en la carta N":** los fakes no se repiten. Usa otra foto o quítala primero de la carta N.
- **No se puede bajar una imagen de un sitio:** algunos sitios no lo permiten. Guárdala en tu compu y usa *Subir fotos* o arrástrala.
- **Los colores salen distintos en papel brillante:** cambia *Papel* a *sin compensar*.
