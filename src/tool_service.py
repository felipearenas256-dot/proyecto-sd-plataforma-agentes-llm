"""Catálogo de herramientas invocables por el agente (simula AWS Lambda / AgentCore Gateway)."""
from __future__ import annotations

import re
import threading
import unicodedata
from urllib.parse import urlparse

from common import JSONHandler, ROOT, log, read_json, serve

SERVICE_NAME = "tool_service"
_CONTADORES: dict[str, int] = {}
_LOCK = threading.Lock()
_CONOCIMIENTO = ROOT / "data" / "conocimiento.json"


def _fallar(clave: str, veces: int) -> bool:
    """Simula fallos transitorios: falla las N primeras llamadas de una clave."""
    with _LOCK:
        actual = _CONTADORES.get(clave, 0)
        _CONTADORES[clave] = actual + 1
        return actual < veces


_VACIAS = {"que", "hay", "sobre", "para", "como", "cual", "cuales", "dice", "los", "las", "del",
           "una", "por", "con", "sus", "son", "existen", "documentos", "esta", "este"}


def _terminos(texto: str) -> set[str]:
    """Normaliza (minúsculas, sin tildes) y reduce cada palabra a una raíz de 6 letras,
    de modo que "reprocesa" y "reprocesamiento" coincidan."""
    plano = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    return {w[:6] for w in re.findall(r"[a-z0-9]+", plano) if len(w) >= 3 and w not in _VACIAS}


def ejecutar(nombre: str, parametros: dict, fallar_veces: int, clave: str) -> tuple[int, dict]:
    if _fallar(clave, fallar_veces):
        log(SERVICE_NAME, f"fallo transitorio simulado en '{nombre}' (clave={clave})")
        return 503, {"error": "Herramienta temporalmente no disponible", "retriable": True}

    if nombre == "consultar_conocimiento":
        docs = read_json(_CONOCIMIENTO, {"documentos": []})["documentos"]
        terminos = _terminos(str(parametros.get("consulta", "")))
        puntuados = []
        for d in docs:
            comunes = terminos & _terminos(d.get("titulo", "") + " " + d.get("contenido", ""))
            if comunes:
                puntuados.append((len(comunes), d))
        puntuados.sort(key=lambda x: x[0], reverse=True)
        return 200, {
            "herramienta": nombre,
            "resultado": [d for _, d in puntuados[:2]],
            "fuente": "Bedrock Knowledge Bases (simulado: coincidencia de términos)",
        }

    if nombre == "generar_reporte":
        return 200, {
            "herramienta": nombre,
            "resultado": {
                "reporte_id": parametros.get("id_tarea", "rep-local"),
                "registros_procesados": int(parametros.get("registros", 25)),
                "estado": "GENERADO",
            },
        }

    if nombre == "consultar_crm":
        return 200, {
            "herramienta": nombre,
            "resultado": {"cuenta": parametros.get("cuenta", "sin-cuenta"), "saldo": 1250.0},
        }

    if nombre == "eliminar_registro":
        return 200, {
            "herramienta": nombre,
            "resultado": {"registro": parametros.get("registro"), "accion": "ELIMINADA_TRAS_APROBACION"},
        }

    return 404, {"error": f"Herramienta '{nombre}' no registrada"}


class Handler(JSONHandler):
    def do_GET(self) -> None:
        if urlparse(self.path).path == "/health":
            self.send_json(200, {"estado": "OK", "servicio": SERVICE_NAME})
        elif urlparse(self.path).path == "/herramientas":
            self.send_json(200, {
                "herramientas": [
                    "consultar_conocimiento", "generar_reporte", "consultar_crm", "eliminar_registro"
                ]
            })
        elif urlparse(self.path).path == "/contadores":
            with _LOCK:
                self.send_json(200, dict(_CONTADORES))
        else:
            self.send_json(404, {"error": "Ruta no encontrada"})

    def do_POST(self) -> None:
        if urlparse(self.path).path == "/contadores/reset":
            with _LOCK:
                _CONTADORES.clear()
            self.send_json(200, {"estado": "REINICIADO"})
            return
        if urlparse(self.path).path != "/tools/invocar":
            self.send_json(404, {"error": "Ruta no encontrada"})
            return
        body = self.body_json()
        nombre = body.get("herramienta", "")
        if not nombre:
            self.send_json(400, {"error": "Se requiere 'herramienta'"})
            return
        clave = body.get("idempotency_key") or body.get("id_tarea") or nombre
        status, respuesta = ejecutar(
            nombre,
            body.get("parametros", {}),
            int(body.get("fallar_veces", 0)),
            clave,
        )
        log(SERVICE_NAME, f"invocación '{nombre}' -> {status}")
        self.send_json(status, respuesta)


if __name__ == "__main__":
    serve(SERVICE_NAME, Handler)
