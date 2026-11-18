"""Convierte la salida de texto de una ejecución real en una imagen tipo consola.

Uso:
    python scripts/captura_consola.py evidencias/resultados_pruebas.txt docs/capturas/pruebas.png "Título"
Requiere Pillow. La imagen se genera a partir del archivo de evidencia, sin editarlo.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def fuente(tamano: int) -> ImageFont.ImageFont:
    for nombre in ["consola.ttf", "DejaVuSansMono.ttf", "Menlo.ttc"]:
        try:
            return ImageFont.truetype(nombre, tamano)
        except OSError:
            continue
    return ImageFont.load_default()


def main(origen: str, destino: str, titulo: str) -> None:
    lineas: list[str] = []
    for ln in Path(origen).read_text(encoding="utf-8").splitlines():
        lineas.extend(textwrap.wrap(ln, 130, subsequent_indent="    ") or [""])
    f = fuente(15)
    alto_linea, margen, barra = 20, 18, 34
    ancho = 1180
    img = Image.new("RGB", (ancho, barra + margen * 2 + alto_linea * len(lineas)), "#11161f")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, ancho, barra], fill="#232b38")
    for i, color in enumerate(["#ff5f57", "#febc2e", "#28c840"]):
        d.ellipse([14 + i * 20, 11, 26 + i * 20, 23], fill=color)
    d.text((86, 9), titulo, fill="#c9d1d9", font=fuente(14))
    y = barra + margen
    for ln in lineas:
        color = "#c9d1d9"
        if ln.rstrip().endswith("ok") or ln.startswith("OK") or "COMPLETADA" in ln:
            color = "#7ee787"
        elif "FAIL" in ln or "ERROR" in ln:
            color = "#ff7b72"
        elif ln.startswith("[") or ln.startswith("==="):
            color = "#79c0ff"
        d.text((margen, y), ln, fill=color, font=f)
        y += alto_linea
    Path(destino).parent.mkdir(parents=True, exist_ok=True)
    img.save(destino)
    print(f"Captura generada: {destino}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
