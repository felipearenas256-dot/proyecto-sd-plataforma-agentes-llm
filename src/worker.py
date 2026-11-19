"""Worker asíncrono: consume la cola, aplica reintentos con backoff y gestiona la DLQ.

Pueden correr varias réplicas a la vez (escalado horizontal). Compiten por la misma
cola: cada una pide una tarea, la procesa y la confirma (ack). Si una réplica muere
antes de confirmar, el lease de la cola vence y otra réplica retoma la tarea.
"""
from __future__ import annotations

import os
import socket
import threading
import time
from urllib.parse import urlparse

from common import (APAGADO, JSONHandler, fijar_traza, http_json, load_config, log, registrar_span,
                    service_url, serve)

SERVICE_NAME = "worker"
# Identidad de la réplica: en Docker el hostname es el id del contenedor.
WORKER_ID = os.environ.get("WORKER_ID") or f"worker-{socket.gethostname()[:6]}-{os.getpid()}"
_CFG = load_config()
_MAX = int(_CFG["retry"]["max_intentos"])
_BASE = float(_CFG["retry"]["backoff_base_segundos"])
_LOCK = threading.Lock()
_ULTIMO: dict = {"estado": "SIN_PROCESAR", "worker": WORKER_ID}
_PROCESADAS = {"completadas": 0, "en_dlq": 0}


def procesar(tarea: dict) -> dict:
    tarea_id = tarea.get("id", "desconocida")
    for intento in range(1, _MAX + 1):
        status, respuesta = http_json("POST", service_url("orchestrator", "/interno/tareas/ejecutar"), {
            "id_tarea": tarea_id,
            "intento": intento,
            "payload": tarea.get("payload", {}),
            "worker": WORKER_ID,
        }, timeout=_CFG["timeouts_segundos"]["worker_a_orquestador"])
        if status == 200:
            log(SERVICE_NAME, f"{tarea_id} completada en intento {intento} por {WORKER_ID}")
            return {"estado": "COMPLETADA", "intentos": intento, "respuesta": respuesta}
        log(SERVICE_NAME, f"{tarea_id} fallo en intento {intento} (status={status})")
        if intento < _MAX:
            time.sleep(_BASE * (2 ** (intento - 1)))
    http_json("POST", service_url("orchestrator", "/interno/tareas/fallido"),
              {"id_tarea": tarea_id, "worker": WORKER_ID})
    http_json("POST", service_url("queue_service", "/queue/dlt"), tarea)
    log(SERVICE_NAME, f"{tarea_id} enviada a DLQ tras {_MAX} intentos")
    return {"estado": "EN_DLQ", "intentos": _MAX, "tarea": tarea_id}


def procesar_una() -> dict:
    with _LOCK:
        status, respuesta = http_json("GET", service_url("queue_service", f"/queue/next?worker={WORKER_ID}"))
        if status != 200:
            return {"estado": "SIN_TAREAS"}
        tarea = respuesta["task"]
        # La tarea trae la traza de la solicitud que la creó: el procesamiento
        # asíncrono queda unido a esa misma traza.
        fijar_traza(tarea.get("traza", ""))
        inicio, t0 = time.time(), time.perf_counter()
        try:
            resultado = procesar(tarea)
            if resultado["estado"] == "COMPLETADA":
                ack, _ = http_json("POST", service_url("queue_service", "/queue/ack"),
                                   {"id": tarea["id"], "recibo": respuesta["recibo"]})
                resultado["confirmada"] = ack == 200
                if ack != 200:
                    log(SERVICE_NAME, f"{tarea['id']}: lease vencido, otra réplica la retomó")
                _PROCESADAS["completadas"] += 1
            else:
                _PROCESADAS["en_dlq"] += 1
            registrar_span(WORKER_ID, f"procesar {tarea['id']}", inicio,
                           (time.perf_counter() - t0) * 1000, resultado["estado"],
                           {"intentos": resultado.get("intentos")})
        finally:
            fijar_traza("")
        resultado["worker"] = WORKER_ID
        _ULTIMO.clear()
        _ULTIMO.update(resultado)
        return resultado


def _bucle() -> None:
    while True:
        if APAGADO.is_set():          # apagado desde el panel: deja de consumir la cola
            time.sleep(0.5)
            continue
        try:
            procesar_una()
        except Exception as exc:  # el worker nunca debe morir por una tarea fallida
            log(SERVICE_NAME, f"error no controlado: {exc}")
        time.sleep(0.2)


class Handler(JSONHandler):
    def do_GET(self) -> None:
        ruta = urlparse(self.path).path
        if ruta == "/health":
            self.send_json(200, {"estado": "OK", "servicio": SERVICE_NAME, "worker": WORKER_ID})
        elif ruta == "/ultimo":
            with _LOCK:
                self.send_json(200, {**_ULTIMO, **_PROCESADAS})
        else:
            self.send_json(404, {"error": "Ruta no encontrada"})

    def do_POST(self) -> None:
        if urlparse(self.path).path == "/worker/run-once":
            self.send_json(200, procesar_una())
        else:
            self.send_json(404, {"error": "Ruta no encontrada"})


if __name__ == "__main__":
    if os.getenv("WORKER_POLL", "0") == "1":
        threading.Thread(target=_bucle, daemon=True).start()
    serve(SERVICE_NAME, Handler)
