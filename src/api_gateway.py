"""Capa de borde: autenticación JWT (simulada), RBAC, validación y enrutamiento."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote, urlparse

from common import (
    JSONHandler,
    http_json,
    load_config,
    log,
    new_id,
    service_url,
    serve,
)

SERVICE_NAME = "api_gateway"
_CFG = load_config()
_RATE: dict[str, list[float]] = {}
_RATE_LOCK = threading.Lock()

INDEX_HTML = (Path(__file__).resolve().parent / "interfaz.html").read_text(encoding="utf-8")
SERVICIOS_INTERNOS = ["orchestrator", "tool_service", "queue_service", "audit_service", "worker"]


_SALUD: dict[str, bool] = {}
_SALUD_LOCK = threading.Lock()


def _vigilar(nombre: str) -> None:
    """Un hilo por servicio consulta /health cada segundo y guarda el último resultado.

    Así /v1/salud responde al instante aunque un contenedor caído tarde segundos en
    fallar (la resolución DNS de un contenedor detenido no respeta el timeout HTTP).
    """
    while True:
        ok = http_json("GET", service_url(nombre, "/health"), timeout=0.8)[0] == 200
        with _SALUD_LOCK:
            _SALUD[nombre] = ok
        time.sleep(1.0)


def _salud_global() -> dict:
    with _SALUD_LOCK:
        instantanea = dict(_SALUD)
    if len(instantanea) == len(SERVICIOS_INTERNOS):
        return {"api_gateway": True, **instantanea}

    def una(nombre: str) -> tuple[str, bool]:      # primer uso: consulta directa
        return nombre, http_json("GET", service_url(nombre, "/health"), timeout=0.8)[0] == 200
    with ThreadPoolExecutor(max_workers=len(SERVICIOS_INTERNOS)) as pool:
        return {"api_gateway": True, **dict(pool.map(una, SERVICIOS_INTERNOS))}


def _autorizar(handler: JSONHandler, roles_permitidos: set[str]) -> dict | None:
    """Devuelve el claims del token o envía 401/403 y devuelve None."""
    header = handler.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        handler.send_json(401, {"error": "Token ausente o malformado"})
        return None
    token = header.split(" ", 1)[1].strip()
    claims = _CFG["tokens"].get(token)
    if not claims:
        handler.send_json(401, {"error": "Token inválido"})
        return None

    ahora = time.monotonic()
    with _RATE_LOCK:
        ventanas = _RATE.setdefault(token, [])
        ventanas[:] = [t for t in ventanas if ahora - t < 60]
        if len(ventanas) >= _CFG["seguridad"]["rate_limit_por_minuto"]:
            handler.send_json(429, {"error": "Límite de tasa excedido"})
            return None
        ventanas.append(ahora)

    if claims["role"] not in roles_permitidos:
        handler.send_json(403, {"error": "Rol no autorizado para esta operación"})
        return None
    return claims


def _reenviar(metodo: str, servicio: str, ruta: str, cuerpo: dict, headers: dict):
    status, respuesta = http_json(
        metodo,
        service_url(servicio, ruta),
        cuerpo if metodo in {"POST", "PUT", "PATCH"} else None,
        headers,
        # Timeouts escalonados: el borde espera más que los servicios internos, así el
        # orquestador alcanza a responder su propio 503 cuando una herramienta cae.
        timeout=_CFG["timeouts_segundos"]["gateway_a_servicios"],
    )
    if status == 0:
        return 503, {"error": "Servicio aguas abajo no disponible", "detalle": respuesta["error"]}
    return status, respuesta


class Handler(JSONHandler):
    def do_GET(self) -> None:
        ruta = urlparse(self.path).path
        if ruta in {"/", "/index.html"}:
            self.send_html(INDEX_HTML)
        elif ruta == "/health":
            self.send_json(200, {"estado": "OK", "servicio": SERVICE_NAME})
        elif ruta == "/v1/salud":
            self.send_json(200, {"servicios": _salud_global()})
        elif ruta == "/v1/metricas":
            # Métricas de operación para el panel: cola, DLQ y circuit breaker.
            claims = _autorizar(self, {"ADMINISTRADOR", "AUDITOR"})
            if not claims:
                return
            s_cola, cola = _reenviar("GET", "queue_service", "/stats", {}, {})
            s_orq, orq = _reenviar("GET", "orchestrator", "/interno/estado", {}, {})
            self.send_json(200, {
                "cola": cola if s_cola == 200 else None,
                "orquestador": orq if s_orq == 200 else None,
            })
        elif ruta.startswith("/v1/tareas/"):
            claims = _autorizar(self, {"USUARIO_FINAL", "SISTEMA_EXTERNO", "ADMINISTRADOR", "AUDITOR"})
            if not claims:
                return
            tarea_id = unquote(ruta.split("/")[-1])
            status, respuesta = _reenviar("GET", "orchestrator", f"/tareas/{tarea_id}", {}, {})
            self.send_json(status, respuesta)
        elif ruta == "/v1/auditoria":
            claims = _autorizar(self, {"AUDITOR"})
            if not claims:
                return
            status, respuesta = _reenviar("GET", "audit_service",
                                          "/auditoria" + ("?" + urlparse(self.path).query if urlparse(self.path).query else ""),
                                          {}, {})
            self.send_json(status, respuesta)
        else:
            self.send_json(404, {"error": "Ruta no encontrada"})

    def do_POST(self) -> None:
        ruta = urlparse(self.path).path
        cuerpo = self.body_json()

        if ruta == "/v1/chat":
            claims = _autorizar(self, {"USUARIO_FINAL", "ADMINISTRADOR"})
            if not claims:
                return
            mensaje = str(cuerpo.get("mensaje", "")).strip()
            if not mensaje or len(mensaje) > _CFG["seguridad"]["max_longitud_mensaje"]:
                self.send_json(400, {"error": "Mensaje vacío o demasiado largo"})
                return
            clave = self.headers.get("Idempotency-Key", "")
            status, respuesta = _reenviar("POST", "orchestrator", "/conversaciones/mensaje", {
                "usuario": claims["sub"], "mensaje": mensaje, "idempotency_key": clave,
            }, {"X-Usuario": claims["sub"]})
            self.send_json(status, respuesta)

        elif ruta == "/v1/tareas":
            claims = _autorizar(self, {"SISTEMA_EXTERNO", "ADMINISTRADOR", "USUARIO_FINAL"})
            if not claims:
                return
            clave = self.headers.get("Idempotency-Key", "")
            status, respuesta = _reenviar("POST", "orchestrator", "/tareas", {
                "evento": cuerpo.get("evento", "webhook"),
                "payload": cuerpo.get("payload", {}),
                "idempotency_key": clave,
                "actor": claims["sub"],
            }, {})
            self.send_json(status, respuesta)

        elif ruta.startswith("/v1/aprobaciones/") and ruta.endswith("/decidir"):
            claims = _autorizar(self, {"APROBADOR", "ADMINISTRADOR"})
            if not claims:
                return
            aprobacion_id = ruta.split("/")[3]
            status, respuesta = _reenviar("POST", "orchestrator",
                                          f"/aprobaciones/{aprobacion_id}/decidir",
                                          {"decision": cuerpo.get("decision"),
                                           "aprobador": claims["sub"]}, {})
            self.send_json(status, respuesta)

        elif ruta == "/v1/tareas/reintentar-dlq":
            claims = _autorizar(self, {"ADMINISTRADOR"})
            if not claims:
                return
            status, respuesta = _reenviar("POST", "queue_service", "/queue/dlt/reintentar", {}, {})
            self.send_json(status, respuesta)

        else:
            self.send_json(404, {"error": "Ruta no encontrada"})


if __name__ == "__main__":
    for _nombre in SERVICIOS_INTERNOS:
        threading.Thread(target=_vigilar, args=(_nombre,), daemon=True).start()
    serve(SERVICE_NAME, Handler)
