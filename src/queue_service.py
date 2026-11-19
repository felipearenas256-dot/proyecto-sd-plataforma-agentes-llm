"""Servicio de colas en memoria (simula Amazon SQS + DLQ).

Entrega con "lease" (visibility timeout de SQS): al entregar una tarea, la cola no
la borra; la marca "en vuelo" a nombre del worker durante unos segundos. El worker
debe confirmarla (ack) al terminar. Si el worker muere a mitad de la tarea, el lease
vence y la tarea vuelve a la cola para que otra réplica la retome. Esto da entrega
"al menos una vez"; el orquestador hace idempotente la ejecución para que una tarea
reentregada no produzca dos efectos.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from urllib.parse import parse_qs, urlparse

from common import JSONHandler, load_config, log, new_id, serve

SERVICE_NAME = "queue_service"
_CFG = load_config()
_VISIBILIDAD = float(_CFG.get("cola", {}).get("visibilidad_segundos", 30))
_WORKER_ACTIVO_SEG = 3.0

_PENDIENTES: deque[dict] = deque()
_EN_VUELO: dict[str, dict] = {}       # id_tarea -> {task, worker, recibo, vence}
_DLQ: list[dict] = []
_WORKERS: dict[str, dict] = {}        # id_worker -> {visto, entregadas, confirmadas}
_ESTADISTICAS = {"encoladas": 0, "entregadas": 0, "confirmadas": 0, "reentregadas": 0,
                 "fallidas": 0, "reintentadas": 0}
_LOCK = threading.Lock()


def _recuperar_vencidas() -> None:
    """Devuelve a la cola las tareas cuyo lease venció (worker caído o colgado). Requiere _LOCK."""
    ahora = time.monotonic()
    for tarea_id, vuelo in list(_EN_VUELO.items()):
        if vuelo["vence"] <= ahora:
            del _EN_VUELO[tarea_id]
            _PENDIENTES.appendleft(vuelo["task"])
            _ESTADISTICAS["reentregadas"] += 1
            log(SERVICE_NAME, f"lease vencido: {tarea_id} (worker {vuelo['worker']}) vuelve a la cola")


def _worker(nombre: str) -> dict:
    return _WORKERS.setdefault(nombre, {"visto": 0.0, "entregadas": 0, "confirmadas": 0})


def _vigilante() -> None:
    while True:
        time.sleep(0.5)
        with _LOCK:
            _recuperar_vencidas()


class Handler(JSONHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self.send_json(200, {"estado": "OK", "servicio": SERVICE_NAME})
        elif path == "/queue/next":
            params = parse_qs(parsed.query)
            nombre = params.get("worker", ["anonimo"])[0][:40]
            visibilidad = float(params.get("visibilidad", [_VISIBILIDAD])[0])
            with _LOCK:
                _recuperar_vencidas()
                w = _worker(nombre)
                w["visto"] = time.monotonic()
                if not _PENDIENTES:
                    self.send_json(404, {"error": "Cola vacía"})
                    return
                # popleft bajo el candado: dos workers nunca reciben la misma tarea a la vez.
                tarea = _PENDIENTES.popleft()
                recibo = new_id("rcb-")
                _EN_VUELO[tarea["id"]] = {"task": tarea, "worker": nombre, "recibo": recibo,
                                          "vence": time.monotonic() + visibilidad}
                _ESTADISTICAS["entregadas"] += 1
                w["entregadas"] += 1
            self.send_json(200, {"task": tarea, "recibo": recibo, "visibilidad_segundos": visibilidad})
        elif path == "/queue/dlt":
            with _LOCK:
                self.send_json(200, {"items": list(_DLQ)})
        elif path == "/stats":
            with _LOCK:
                _recuperar_vencidas()
                ahora = time.monotonic()
                workers = [{"id": n, "activo": ahora - d["visto"] < _WORKER_ACTIVO_SEG,
                            "entregadas": d["entregadas"], "confirmadas": d["confirmadas"]}
                           for n, d in sorted(_WORKERS.items())]
                self.send_json(200, {
                    **_ESTADISTICAS,
                    "pendientes": len(_PENDIENTES),
                    "en_vuelo": len(_EN_VUELO),
                    "en_dlq": len(_DLQ),
                    "visibilidad_segundos": _VISIBILIDAD,
                    "workers": workers,
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
                _PENDIENTES.append(tarea)
            log(SERVICE_NAME, f"encolada tarea {tarea['id']}")
            self.send_json(202, {"id": tarea["id"], "estado": "EN_COLA"})
        elif path == "/queue/ack":
            tarea_id, recibo = body.get("id"), body.get("recibo")
            with _LOCK:
                vuelo = _EN_VUELO.get(tarea_id)
                if not vuelo or vuelo["recibo"] != recibo:
                    # El lease venció y la tarea ya se reentregó a otro worker.
                    self.send_json(409, {"error": "Recibo vencido o desconocido"})
                    return
                del _EN_VUELO[tarea_id]
                _ESTADISTICAS["confirmadas"] += 1
                _worker(vuelo["worker"])["confirmadas"] += 1
            self.send_json(200, {"estado": "CONFIRMADA"})
        elif path == "/queue/dlt":
            with _LOCK:
                _EN_VUELO.pop(body.get("id"), None)
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
                _PENDIENTES.append(tarea)
            log(SERVICE_NAME, f"tarea {tarea.get('id')} reintroducida desde DLQ")
            self.send_json(202, {"id": tarea.get("id"), "estado": "REINTENTADA"})
        elif path == "/stats/reset":
            with _LOCK:
                _PENDIENTES.clear()
                _EN_VUELO.clear()
                _DLQ.clear()
                _WORKERS.clear()
                for key in _ESTADISTICAS:
                    _ESTADISTICAS[key] = 0
            self.send_json(200, {"estado": "REINICIADO"})
        else:
            self.send_json(404, {"error": "Ruta no encontrada"})


if __name__ == "__main__":
    threading.Thread(target=_vigilante, daemon=True).start()
    serve(SERVICE_NAME, Handler)
