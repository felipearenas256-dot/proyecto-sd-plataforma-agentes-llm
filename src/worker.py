"""Worker asíncrono: consume la cola, aplica reintentos con backoff y gestiona la DLQ."""
from __future__ import annotations

import os
import threading
import time
from urllib.parse import urlparse

from common import JSONHandler, http_json, load_config, log, service_url, serve

SERVICE_NAME = "worker"
_CFG = load_config()
_MAX = int(_CFG["retry"]["max_intentos"])
_BASE = float(_CFG["retry"]["backoff_base_segundos"])
_LOCK = threading.Lock()
_ULTIMO: dict = {"estado": "SIN_PROCESAR"}


def procesar(tarea: dict) -> dict:
    tarea_id = tarea.get("id", "desconocida")
    for intento in range(1, _MAX + 1):
        status, respuesta = http_json("POST", service_url("orchestrator", "/interno/tareas/ejecutar"), {
            "id_tarea": tarea_id,
            "intento": intento,
            "payload": tarea.get("payload", {}),
        }, timeout=_CFG["timeouts_segundos"]["worker_a_orquestador"])
        if status == 200:
            log(SERVICE_NAME, f"{tarea_id} completada en intento {intento}")
            return {"estado": "COMPLETADA", "intentos": intento, "respuesta": respuesta}
        log(SERVICE_NAME, f"{tarea_id} fallo en intento {intento} (status={status})")
        if intento < _MAX:
            time.sleep(_BASE * (2 ** (intento - 1)))
    http_json("POST", service_url("orchestrator", "/interno/tareas/fallido"), {"id_tarea": tarea_id})
    http_json("POST", service_url("queue_service", "/queue/dlt"), tarea)
    log(SERVICE_NAME, f"{tarea_id} enviada a DLQ tras {_MAX} intentos")
    return {"estado": "EN_DLQ", "intentos": _MAX, "tarea": tarea_id}


def procesar_una() -> dict:
    with _LOCK:
        status, respuesta = http_json("GET", service_url("queue_service", "/queue/next"))
        if status != 200:
            return {"estado": "SIN_TAREAS"}
        resultado = procesar(respuesta["task"])
        _ULTIMO.update(resultado)
        return resultado


def _bucle() -> None:
    while True:
        try:
            procesar_una()
        except Exception as exc:  # el worker nunca debe morir por una tarea fallida
            log(SERVICE_NAME, f"error no controlado: {exc}")
        time.sleep(0.2)


class Handler(JSONHandler):
    def do_GET(self) -> None:
        ruta = urlparse(self.path).path
        if ruta == "/health":
            self.send_json(200, {"estado": "OK", "servicio": SERVICE_NAME})
        elif ruta == "/ultimo":
            with _LOCK:
                self.send_json(200, dict(_ULTIMO))
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
