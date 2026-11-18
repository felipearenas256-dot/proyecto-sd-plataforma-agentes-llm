"""Núcleo de orquestación del agente (simula Bedrock AgentCore Runtime)."""
from __future__ import annotations

import contextlib
import json
import threading
import time
from urllib.parse import unquote, urlparse

from common import (
    CircuitBreaker,
    JSONHandler,
    ROOT,
    append_audit,
    http_json,
    load_config,
    log,
    mask_pii,
    new_id,
    now_iso,
    read_json,
    service_url,
    serve,
    write_json,
)
from llm import generar_respuesta

SERVICE_NAME = "orchestrator"
_STATE_PATH = ROOT / "data" / "state.json"
ACCIONES_A_HERRAMIENTAS = {
    "eliminar registro": "eliminar_registro",
    "transferir datos": "consultar_crm",
    "enviar correo masivo": "generar_reporte",
}
_CFG = load_config()
_BREAKER = CircuitBreaker(
    umbral=_CFG["retry"]["circuit_breaker"]["umbral_fallos"],
    apertura=_CFG["retry"]["circuit_breaker"]["tiempo_apertura_segundos"],
)
_LOCK = threading.Lock()
# Un candado por Idempotency-Key: dos solicitudes simultáneas con la misma clave
# se serializan, y la segunda encuentra la respuesta ya guardada por la primera.
_CANDADOS_CLAVE: dict[str, threading.Lock] = {}
_CANDADOS_GUARDA = threading.Lock()
_STATE = read_json(_STATE_PATH, {
    "conversaciones": {},
    "tareas": {},
    "aprobaciones": {},
    "idempotencia": {},
})


def _candado_clave(clave: str):
    if not clave:
        return contextlib.nullcontext()
    with _CANDADOS_GUARDA:
        return _CANDADOS_CLAVE.setdefault(clave, threading.Lock())


# Persistencia "write-behind": cada operación marca el estado como modificado y un
# hilo lo vuelca a disco como máximo cada 100 ms. Antes se reescribía el archivo
# completo dos veces por solicitud, lo que dominaba la latencia bajo carga.
_PERSIST_INTERVALO = 0.1
_SUCIO = threading.Event()


def _persist() -> None:
    _SUCIO.set()


def _volcar_estado() -> None:
    with _LOCK:
        instantanea = json.dumps(_STATE, ensure_ascii=False)
    write_json(_STATE_PATH, json.loads(instantanea))


def _persistidor() -> None:
    while True:
        _SUCIO.wait()
        time.sleep(_PERSIST_INTERVALO)
        _SUCIO.clear()
        try:
            _volcar_estado()
        except OSError as exc:
            log(SERVICE_NAME, f"no se pudo persistir el estado: {exc}")
            _SUCIO.set()


def _evaluar_guardrails(texto: str) -> list[str]:
    return [p for p in _CFG["guardrails"]["acciones_criticas"] if p in texto.lower()]


def _invocar_herramienta(nombre: str, parametros: dict, clave: str, fallar_veces: int = 0):
    if not _BREAKER.permite():
        return 503, {"error": "Circuit breaker abierto", "retriable": False}
    status, respuesta = http_json("POST", service_url("tool_service", "/tools/invocar"), {
        "herramienta": nombre,
        "parametros": parametros,
        "idempotency_key": clave,
        "fallar_veces": fallar_veces,
    }, timeout=_CFG["timeouts_segundos"]["orquestador_a_herramientas"])
    if status == 0 or status >= 500:
        _BREAKER.fallo()
    else:
        _BREAKER.exito()
    return status, respuesta


class Handler(JSONHandler):
    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path == "/health":
            self.send_json(200, {"estado": "OK", "servicio": SERVICE_NAME})
        elif path.startswith("/tareas/"):
            tarea_id = path.split("/")[-1]
            with _LOCK:
                tarea = _STATE["tareas"].get(tarea_id)
            if not tarea:
                self.send_json(404, {"error": "Tarea no encontrada"})
            else:
                self.send_json(200, tarea)
        elif path == "/interno/estado":
            with _LOCK:
                self.send_json(200, {
                    "conversaciones": len(_STATE["conversaciones"]),
                    "tareas": len(_STATE["tareas"]),
                    "aprobaciones": len(_STATE["aprobaciones"]),
                    "circuit_breaker": _BREAKER.estado,
                })
        else:
            self.send_json(404, {"error": "Ruta no encontrada"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self.body_json()

        if path == "/conversaciones/mensaje":
            with _candado_clave(body.get("idempotency_key", "")):
                self._mensaje(body)
        elif path == "/tareas":
            with _candado_clave(body.get("idempotency_key", "")):
                self._crear_tarea(body)
        elif path.startswith("/aprobaciones/") and path.endswith("/decidir"):
            aprobacion_id = path.split("/")[2]
            self._decidir(aprobacion_id, body)
        elif path == "/interno/tareas/ejecutar":
            self._ejecutar_tarea(body)
        elif path == "/interno/tareas/fallido":
            self._tarea_fallida(body)
        elif path == "/interno/circuit/reset":
            _BREAKER.reset()
            self.send_json(200, {"estado": "REINICIADO"})
        elif path == "/interno/reset":
            self._reset()
        else:
            self.send_json(404, {"error": "Ruta no encontrada"})

    def _mensaje(self, body: dict) -> None:
        usuario = body.get("usuario", "anónimo")
        mensaje = str(body.get("mensaje", "")).strip()
        clave = body.get("idempotency_key", "")
        if not mensaje:
            self.send_json(400, {"error": "El mensaje no puede estar vacío"})
            return
        with _LOCK:
            if clave and clave in _STATE["idempotencia"]:
                self.send_json(200, _STATE["idempotencia"][clave])
                return

        seguras = mask_pii(mensaje)
        criticas = _evaluar_guardrails(seguras)
        append_audit("mensaje_recibido", usuario, "conversacion", "OK", {"mensaje": seguras[:200]})
        append_audit("guardrail_evaluado", "agente", "mensaje", "CRITICO" if criticas else "SEGURO")

        if criticas:
            aprobacion_id = new_id("apr-")
            tarea_id = new_id("tsk-")
            with _LOCK:
                _STATE["aprobaciones"][aprobacion_id] = {
                    "id": aprobacion_id,
                    "tarea_id": tarea_id,
                    "accion": criticas[0],
                    "solicitado_por": usuario,
                    "estado": "PENDIENTE",
                    "creado_en": now_iso(),
                }
                _STATE["tareas"][tarea_id] = {
                    "id": tarea_id,
                    "estado": "APROBACION_PENDIENTE",
                    "tipo": "ACCION_CRITICA",
                    "aprobacion_id": aprobacion_id,
                    "usuario": usuario,
                }
                _persist()
            append_audit("aprobacion_solicitada", "agente", aprobacion_id, "PENDIENTE")
            respuesta = {
                "estado": "APROBACION_PENDIENTE",
                "id_aprobacion": aprobacion_id,
                "id_tarea": tarea_id,
                "accion": criticas[0],
                "mensaje": "La acción requiere aprobación humana antes de ejecutarse.",
            }
            status = 202
        else:
            status, herramienta = _invocar_herramienta(
                "consultar_conocimiento", {"consulta": seguras}, f"chat-{clave or new_id()}"
            )
            if status != 200:
                append_audit("herramienta_fallida", "agente", "consultar_conocimiento", str(status))
                self.send_json(503, {"error": "No fue posible consultar la base de conocimiento"})
                return
            documentos = herramienta.get("resultado", [])
            texto, proveedor = generar_respuesta(seguras, documentos)
            conv_id = new_id("conv-")
            with _LOCK:
                _STATE["conversaciones"][conv_id] = {
                    "id": conv_id,
                    "usuario": usuario,
                    "mensajes": [
                        {"rol": "usuario", "contenido": seguras},
                        {"rol": "agente", "contenido": texto, "proveedor_llm": proveedor},
                    ],
                }
                _persist()
            append_audit("respuesta_generada", "agente", conv_id, "OK", {"proveedor_llm": proveedor})
            respuesta = {
                "estado": "COMPLETADA",
                "id_conversacion": conv_id,
                "respuesta": _STATE["conversaciones"][conv_id]["mensajes"][1]["contenido"],
                "proveedor_llm": proveedor,
                "documentos": documentos,
            }
            status = 200

        with _LOCK:
            if clave:
                _STATE["idempotencia"][clave] = respuesta
                _persist()
        self.send_json(status, respuesta)

    def _crear_tarea(self, body: dict) -> None:
        clave = body.get("idempotency_key", "")
        with _LOCK:
            if clave and clave in _STATE["idempotencia"]:
                self.send_json(202, _STATE["idempotencia"][clave])
                return
        tarea_id = new_id("tsk-")
        tarea = {
            "id": tarea_id,
            "estado": "PENDIENTE",
            "tipo": "ASINCRONA",
            "evento": body.get("evento", "webhook"),
            "payload": body.get("payload", {}),
            "intentos": 0,
        }
        with _LOCK:
            _STATE["tareas"][tarea_id] = tarea
            _persist()
        status, _ = http_json("POST", service_url("queue_service", "/queue"), {"task": {
            "id": tarea_id,
            "intento": 0,
            "payload": tarea["payload"],
        }})
        append_audit("tarea_creada", body.get("actor", "sistema_externo"), tarea_id,
                     "EN_COLA" if status == 202 else "ERROR_COLA")
        respuesta = {"id_tarea": tarea_id, "estado": tarea["estado"]}
        with _LOCK:
            if clave:
                _STATE["idempotencia"][clave] = respuesta
                _persist()
        self.send_json(202, respuesta)

    def _ejecutar_tarea(self, body: dict) -> None:
        tarea_id = body.get("id_tarea")
        intento = int(body.get("intento", 1))
        with _LOCK:
            tarea = _STATE["tareas"].get(tarea_id)
            if not tarea:
                self.send_json(404, {"error": "Tarea no encontrada"})
                return
            tarea["estado"] = "EN_PROCESO"
            tarea["intentos"] = intento
            _persist()
        parametros = dict(body.get("payload", {}))
        parametros["id_tarea"] = tarea_id
        status, herramienta = _invocar_herramienta(
            "generar_reporte", parametros, tarea_id, int(parametros.get("fallar_veces", 0))
        )
        if status != 200:
            append_audit("tarea_reintentable", "worker", tarea_id, f"FALLO_{intento}")
            with _LOCK:
                tarea["estado"] = "REINTENTO_PENDIENTE"
                _persist()
            self.send_json(503, {"error": "Fallo transitorio en herramienta", "intento": intento})
            return
        with _LOCK:
            tarea["estado"] = "COMPLETADA"
            tarea["resultado"] = herramienta.get("resultado")
            _persist()
        append_audit("tarea_completada", "worker", tarea_id, "OK", {"intento": intento})
        log(SERVICE_NAME, f"tarea {tarea_id} completada en intento {intento}")
        self.send_json(200, {"estado": "COMPLETADA", "resultado": herramienta.get("resultado")})

    def _tarea_fallida(self, body: dict) -> None:
        tarea_id = body.get("id_tarea")
        with _LOCK:
            tarea = _STATE["tareas"].get(tarea_id)
            if tarea:
                tarea["estado"] = "FALLIDA"
                _persist()
        append_audit("tarea_en_dlq", "worker", tarea_id, "AGOTADOS_REINTENTOS")
        self.send_json(200, {"estado": "FALLIDA"})

    def _decidir(self, aprobacion_id: str, body: dict) -> None:
        decision = str(body.get("decision", "")).upper()
        aprobador = body.get("aprobador", "aprobador")
        with _LOCK:
            aprobacion = _STATE["aprobaciones"].get(aprobacion_id)
            if not aprobacion:
                self.send_json(404, {"error": "Aprobación no encontrada"})
                return
            if aprobacion["estado"] != "PENDIENTE":
                self.send_json(409, {"error": "La aprobación ya fue resuelta"})
                return
            if decision not in {"APROBAR", "RECHAZAR"}:
                self.send_json(400, {"error": "Decisión debe ser APROBAR o RECHAZAR"})
                return
            aprobacion["estado"] = decision
            aprobacion["aprobador"] = aprobador
            tarea = _STATE["tareas"][aprobacion["tarea_id"]]
        append_audit("aprobacion_decidida", aprobador, aprobacion_id, decision)
        if decision == "APROBAR":
            herramienta_nombre = ACCIONES_A_HERRAMIENTAS.get(
                aprobacion["accion"], aprobacion["accion"]
            )
            status, herramienta = _invocar_herramienta(
                herramienta_nombre, {"registro": "reg-001"}, f"apr-{aprobacion_id}"
            )
            with _LOCK:
                tarea["estado"] = "COMPLETADA" if status == 200 else "FALLIDA"
                tarea["resultado"] = herramienta.get("resultado")
                _persist()
            append_audit("accion_critica_ejecutada", aprobador, aprobacion["tarea_id"], str(status))
            self.send_json(200, {"estado": tarea["estado"], "resultado": herramienta.get("resultado")})
        else:
            with _LOCK:
                tarea["estado"] = "CANCELADA"
                _persist()
            self.send_json(200, {"estado": "CANCELADA"})

    def _reset(self) -> None:
        global _STATE
        with _LOCK:
            _STATE = {"conversaciones": {}, "tareas": {}, "aprobaciones": {}, "idempotencia": {}}
            _persist()
        _BREAKER.reset()
        http_json("POST", service_url("tool_service", "/contadores/reset"))
        http_json("POST", service_url("queue_service", "/stats/reset"))
        http_json("POST", service_url("audit_service", "/reset"))
        self.send_json(200, {"estado": "REINICIADO"})


if __name__ == "__main__":
    threading.Thread(target=_persistidor, daemon=True).start()
    serve(SERVICE_NAME, Handler)
