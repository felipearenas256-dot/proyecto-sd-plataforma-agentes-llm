"""Convierte Documento_Tecnico.md a Documento_Tecnico.docx con estilos académicos.

Soporta: encabezados, listas, tablas Markdown, bloques de código, citas,
reglas horizontales, imágenes y saltos de página mediante <!-- pagebreak -->.
"""
from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from PIL import Image
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent
ENTRADA = ROOT / "Documento_Tecnico.md"
SALIDA = ROOT / "Documento_Tecnico.docx"

AZUL = RGBColor(0x1F, 0x38, 0x64)
GRIS = RGBColor(0x47, 0x55, 0x69)


def sombrear(parrafo, color: str) -> None:
    ppr = parrafo._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), color)
    ppr.append(shd)


def borde_inferior(parrafo, color: str = "CBD5E1") -> None:
    ppr = parrafo._p.get_or_add_pPr()
    bordes = OxmlElement("w:pBdr")
    inferior = OxmlElement("w:bottom")
    inferior.set(qn("w:val"), "single")
    inferior.set(qn("w:sz"), "6")
    inferior.set(qn("w:color"), color)
    bordes.append(inferior)
    ppr.append(bordes)


def numero_pagina(parrafo) -> None:
    run = parrafo.add_run()
    inicio = OxmlElement("w:fldChar")
    inicio.set(qn("w:fldCharType"), "begin")
    instruccion = OxmlElement("w:instrText")
    instruccion.set(qn("xml:space"), "preserve")
    instruccion.text = " PAGE "
    fin = OxmlElement("w:fldChar")
    fin.set(qn("w:fldCharType"), "end")
    run._r.extend([inicio, instruccion, fin])


def tabla_de_contenido(doc: Document) -> None:
    """Inserta un campo TOC de Word; se actualiza al abrir el documento."""
    titulo = doc.add_paragraph()
    run_titulo = titulo.add_run("Tabla de contenido")
    run_titulo.bold = True
    run_titulo.font.size = Pt(17)
    run_titulo.font.color.rgb = AZUL
    p = doc.add_paragraph()
    run = p.add_run()
    inicio = OxmlElement("w:fldChar")
    inicio.set(qn("w:fldCharType"), "begin")
    instruccion = OxmlElement("w:instrText")
    instruccion.set(qn("xml:space"), "preserve")
    instruccion.text = r' TOC \o "1-2" \h \z \u '
    separador = OxmlElement("w:fldChar")
    separador.set(qn("w:fldCharType"), "separate")
    texto = OxmlElement("w:t")
    texto.text = "Haga clic derecho y elija «Actualizar campo» si la tabla no aparece."
    fin = OxmlElement("w:fldChar")
    fin.set(qn("w:fldCharType"), "end")
    run._r.extend([inicio, instruccion, separador, texto, fin])
    # Pide a Word que actualice los campos al abrir el archivo.
    ajustes = doc.settings.element
    actualizar = OxmlElement("w:updateFields")
    actualizar.set(qn("w:val"), "true")
    ajustes.append(actualizar)
    doc.add_page_break()


def configurar_estilos(doc: Document) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.15

    for nombre, tamano, color in [
        ("Title", 26, AZUL),
        ("Heading 1", 17, AZUL),
        ("Heading 2", 13, AZUL),
        ("Heading 3", 11, GRIS),
    ]:
        estilo = doc.styles[nombre]
        estilo.font.name = "Calibri"
        estilo.font.size = Pt(tamano)
        estilo.font.bold = True
        estilo.font.color.rgb = color
        estilo.paragraph_format.space_before = Pt(14 if nombre != "Title" else 0)
        estilo.paragraph_format.space_after = Pt(8)
        estilo.paragraph_format.keep_with_next = True

    for nombre in ["List Bullet", "List Number"]:
        estilo = doc.styles[nombre]
        estilo.font.name = "Calibri"
        estilo.font.size = Pt(11)
        estilo.paragraph_format.space_after = Pt(3)


INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*|\[[^\]]+\]\([^)]+\))")


def añadir_inline(parrafo, texto: str) -> None:
    texto = texto.replace(r"\*", "*")
    for parte in INLINE.split(texto):
        if not parte:
            continue
        if parte.startswith("**") and parte.endswith("**"):
            parrafo.add_run(parte[2:-2]).bold = True
        elif parte.startswith("`") and parte.endswith("`"):
            run = parrafo.add_run(parte[1:-1])
            run.font.name = "Consolas"
            run.font.size = Pt(10)
        elif parte.startswith("*") and parte.endswith("*"):
            parrafo.add_run(parte[1:-1]).italic = True
        elif parte.startswith("[") and "]" in parte:
            match = re.match(r"\[([^\]]+)\]\(([^)]+)\)", parte)
            if match:
                run = parrafo.add_run(match.group(1))
                run.font.color.rgb = AZUL
                run.font.underline = True
            else:
                parrafo.add_run(parte)
        else:
            parrafo.add_run(parte)


def añadir_tabla(doc: Document, filas: list[list[str]]) -> None:
    if not filas:
        return
    ncols = max(len(f) for f in filas)
    tabla = doc.add_table(rows=0, cols=ncols)
    tabla.style = "Table Grid"
    tabla.alignment = WD_TABLE_ALIGNMENT.CENTER
    tabla.autofit = True
    for i, fila in enumerate(filas):
        celdas = tabla.add_row().cells
        for j in range(ncols):
            texto = fila[j] if j < len(fila) else ""
            celda = celdas[j]
            celda.text = ""
            p = celda.paragraphs[0]
            p.paragraph_format.space_after = Pt(2)
            run = p.add_run(texto.replace("<br>", " "))
            run.font.size = Pt(9)
            if i == 0:
                run.bold = True
                run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                shd = OxmlElement("w:shd")
                shd.set(qn("w:val"), "clear")
                shd.set(qn("w:fill"), "1F3864")
                celda._tc.get_or_add_tcPr().append(shd)
    doc.add_paragraph()


def convertir() -> None:
    lineas = ENTRADA.read_text(encoding="utf-8").splitlines()
    doc = Document()
    configurar_estilos(doc)
    seccion = doc.sections[0]
    seccion.left_margin = Inches(0.9)
    seccion.right_margin = Inches(0.9)
    seccion.top_margin = Inches(0.9)
    seccion.bottom_margin = Inches(0.9)

    pie = seccion.footer.paragraphs[0]
    pie.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pie.add_run("Proyecto Final — Sistemas Distribuidos | Plataforma de Agentes LLM    Pág. ")
    numero_pagina(pie)

    i = 0
    es_titulo = True
    secciones = 0
    salto_previo = False
    while i < len(lineas):
        linea = lineas[i].rstrip()
        if not linea.strip():
            i += 1
            continue

        if linea.strip() == "<!-- toc -->":
            doc.add_page_break()
            tabla_de_contenido(doc)
            salto_previo = True
            i += 1
            continue

        if linea.strip() == "<!-- pagebreak -->":
            doc.add_page_break()
            salto_previo = True
            i += 1
            continue

        if linea.startswith("```"):
            bloque: list[str] = []
            i += 1
            while i < len(lineas) and not lineas[i].startswith("```"):
                bloque.append(lineas[i])
                i += 1
            i += 1
            p = doc.add_paragraph()
            sombrear(p, "F1F5F9")
            p.paragraph_format.left_indent = Inches(0.15)
            p.paragraph_format.space_after = Pt(8)
            for n, ln in enumerate(bloque):
                run = p.add_run(ln or " ")
                run.font.name = "Consolas"
                run.font.size = Pt(8.5)
                if n < len(bloque) - 1:
                    run.add_break()
            continue

        if linea.startswith("|") and i + 1 < len(lineas) and re.match(r"^\|[\s:|-]+\|$", lineas[i + 1].strip()):
            filas = [[c.strip() for c in linea.strip().strip("|").split("|")]]
            i += 2
            while i < len(lineas) and lineas[i].strip().startswith("|"):
                filas.append([c.strip() for c in lineas[i].strip().strip("|").split("|")])
                i += 1
            añadir_tabla(doc, filas)
            continue

        foto = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", linea.strip())
        if foto:
            ruta = ROOT / foto.group(2)
            if ruta.exists():
                p = doc.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                with Image.open(ruta) as im:
                    ancho_px, alto_px = im.size
                ancho = 6.4
                if ancho * alto_px / ancho_px > 7.6:      # no exceder el alto útil de la hoja
                    ancho = 7.6 * ancho_px / alto_px
                p.add_run().add_picture(str(ruta), width=Inches(ancho))
                caption = doc.add_paragraph(foto.group(1))
                caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
                caption.runs[0].italic = True
                caption.runs[0].font.size = Pt(9)
            i += 1
            continue

        if linea.startswith("# "):
            p = doc.add_paragraph(style="Title")
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            añadir_inline(p, linea[2:])
            if es_titulo:
                borde_inferior(p, "1F3864")
                es_titulo = False
        elif linea.startswith("## "):
            if secciones > 0 and not salto_previo:
                doc.add_page_break()      # cada sección principal inicia en página nueva
            secciones += 1
            salto_previo = False
            doc.add_paragraph(linea[3:], style="Heading 1")
        elif linea.startswith("### "):
            doc.add_paragraph(linea[4:], style="Heading 2")
        elif linea.startswith("#### "):
            doc.add_paragraph(linea[5:], style="Heading 3")
        elif linea.startswith("- "):
            p = doc.add_paragraph(style="List Bullet")
            añadir_inline(p, linea[2:])
        elif re.match(r"^\d+\. ", linea):
            # Numeración manual: evita que Word continúe el contador entre listas.
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Inches(0.3)
            p.paragraph_format.first_line_indent = Inches(-0.3)
            p.paragraph_format.space_after = Pt(3)
            añadir_inline(p, linea)
        elif linea.strip() == "---":
            p = doc.add_paragraph()
            borde_inferior(p)
        elif linea.startswith("> "):
            p = doc.add_paragraph(style="Intense Quote")
            añadir_inline(p, linea[2:])
        else:
            p = doc.add_paragraph()
            añadir_inline(p, linea)
        i += 1

    doc.save(SALIDA)
    print(f"Documento generado: {SALIDA}")


if __name__ == "__main__":
    convertir()
