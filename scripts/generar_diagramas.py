"""Renderiza todos los diagramas Mermaid de docs/ a PNG.

Requiere Node.js. Uso:
    python scripts/generar_diagramas.py
Usa @mermaid-js/mermaid-cli mediante npx (lo descarga la primera vez).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"


def main() -> int:
    npx = shutil.which("npx")
    if not npx:
        print("Se necesita Node.js (npx) para renderizar los diagramas.", file=sys.stderr)
        return 1
    for fuente in sorted(DOCS.glob("*.mmd")):
        destino = fuente.with_suffix(".png")
        r = subprocess.run([npx, "-y", "@mermaid-js/mermaid-cli@11.4.2", "-i", str(fuente), "-o", str(destino),
                            "-c", str(DOCS / "mermaid-config.json"), "-s", "2", "-b", "white"],
                           capture_output=True, text=True)
        print(f"{fuente.name} -> {destino.name}: {'OK' if r.returncode == 0 else 'ERROR'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
