"""Servicio de colas en memoria (simula Amazon SQS + DLQ)."""
from __future__ import annotations

import queue
import threading
from urllib.parse import urlparse

from common import JSONHandler, log, new_id, serve, load_config

SERVICE_NAME = "queue_service"
_COLA: queue.Queue = queue.Queue()
_DLQ: list[dict] = []
_ESTADISTICAS = {"encoladas": 0, "entregadas": 0, "fallidas": 0, "reintentadas": 0}
_LOCK = threading.Lock()


class Handler(JSONHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self.send_json(200, {"estado": "OK", "servicio": SERVICE_NAME})
        elif path == "/queue/next":
            try:
                tarea = _COLA.get_nowait()
                with _LOCK:
                    _ESTADISTICAS["entregadas"] += 1
                self.send_json(200, {"task": tarea})
            except queue.Empty:
                self.send_json(404, {"error": "Cola vacía"})
        elif path == "/queue/dlt":
            with _LOCK:
                self.send_json(200, {"items": list(_DLQ)})
        elif path == "/stats":
            with _LOCK:
                self.send_json(200, {
                    **_ESTADISTICAS,
                    "pendientes": _COLA.qsize(),
                    "en_dlq": len(_DLQ),
                })
        else:
            self.send_json(404, {"error": "Ruta no encontrada"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self.body_json()
        if path == "/queue":
            if "task" not in body:
                self.send_json(400, {"error": "Se requiere el campo 'task'"})
                return
            tarea = body["task"]
            tarea.setdefault("id", new_id("tsk-"))
            with _LOCK:
                _ESTADISTICAS["encoladas"] += 1
            _COLA.put(tarea)
            log(SERVICE_NAME, f"encolada tarea {tarea['id']}")
            self.send_json(202, {"id": tarea["id"], "estado": "EN_COLA"})
        elif path == "/queue/dlt":
            with _LOCK:
                _DLQ.append(body)
                _ESTADISTICAS["fallidas"] += 1
            log(SERVICE_NAME, f"tarea {body.get('id')} enviada a DLQ")
            self.send_json(201, {"estado": "EN_DLQ"})
        elif path == "/queue/dlt/reintentar":
            with _LOCK:
                if not _DLQ:
                    self.send_json(404, {"error": "DLQ vacía"})
                    return
                tarea = _DLQ.pop(0)
                tarea["intento"] = 0
                # El operador ajusta los parámetros antes de reintroducir la tarea.
                tarea.setdefault("payload", {}).pop("fallar_veces", None)
                _ESTADISTICAS["reintentadas"] += 1
            _COLA.put(tarea)
            log(SERVICE_NAME, f"tarea {tarea.get('id')} reintroducida desde DLQ")
            self.send_json(202, {"id": tarea.get("id"), "estado": "REINTENTADA"})
        elif path == "/stats/reset":
            with _LOCK:
                while not _COLA.empty():
                    _COLA.get_nowait()
                _DLQ.clear()
                for key in _ESTADISTICAS:
                    _ESTADISTICAS[key] = 0
            self.send_json(200, {"estado": "REINICIADO"})
        else:
            self.send_json(404, {"error": "Ruta no encontrada"})


if __name__ == "__main__":
    serve(SERVICE_NAME, Handler)
