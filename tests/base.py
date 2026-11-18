"""Infraestructura común para las pruebas del prototipo."""
from __future__ import annotations

import atexit
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from common import http_json, service_url  # noqa: E402

SERVICIOS = ["queue_service", "tool_service", "audit_service", "orchestrator", "api_gateway", "worker"]
_PROCESOS: dict[str, subprocess.Popen] = {}


def _salud() -> list[int]:
    return [http_json("GET", service_url(s, "/health"))[0] for s in SERVICIOS]


def ensure_services() -> None:
    """Arranca los servicios en modo prueba (sin sondeo automático del worker)."""
    if all(e == 200 for e in _salud()):
        return
    for nombre in SERVICIOS:
        iniciar_servicio(nombre)
    limite = time.time() + 20
    while time.time() < limite and not all(e == 200 for e in _salud()):
        time.sleep(0.2)
    if not all(e == 200 for e in _salud()):
        raise RuntimeError("No fue posible iniciar los servicios de prueba")
    atexit.register(stop_services)


def iniciar_servicio(nombre: str) -> None:
    """Lanza un servicio como proceso independiente (modo prueba, sin sondeo del worker)."""
    entorno = os.environ.copy()
    entorno["WORKER_POLL"] = "0"
    entorno["LLM_PROVIDER"] = "simulado"
    entorno["PYTHONPATH"] = str(ROOT / "src")
    _PROCESOS[nombre] = subprocess.Popen(
        [sys.executable, str(ROOT / "src" / f"{nombre}.py")],
        cwd=ROOT, env=entorno,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def servicios_propios() -> bool:
    """True si las pruebas lanzaron los procesos (y por tanto pueden detenerlos)."""
    return set(_PROCESOS) == set(SERVICIOS)


def detener_servicio(nombre: str) -> None:
    """Mata el proceso real de un servicio para simular una caída."""
    proc = _PROCESOS.pop(nombre)
    proc.kill()
    proc.wait(timeout=5)


def esperar_salud(nombre: str, timeout: float = 15.0) -> bool:
    limite = time.time() + timeout
    while time.time() < limite:
        if http_json("GET", service_url(nombre, "/health"), timeout=0.5)[0] == 200:
            return True
        time.sleep(0.2)
    return False


def stop_services() -> None:
    procesos = list(_PROCESOS.values())
    for proc in procesos:
        proc.terminate()
    for proc in procesos:
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    _PROCESOS.clear()


def reset_state() -> None:
    http_json("POST", service_url("orchestrator", "/interno/reset"))
    http_json("POST", service_url("orchestrator", "/interno/circuit/reset"))


def post(ruta: str, cuerpo: dict, token: str = "demo-usuario-token", key: str | None = None):
    headers = {"Authorization": f"Bearer {token}"}
    if key:
        headers["Idempotency-Key"] = key
    return http_json("POST", service_url("api_gateway", ruta), cuerpo, headers)


def get(ruta: str, token: str = "demo-usuario-token"):
    return http_json("GET", service_url("api_gateway", ruta),
                     headers={"Authorization": f"Bearer {token}"})
