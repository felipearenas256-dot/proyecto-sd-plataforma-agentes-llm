"""Arranca (o detiene) los seis servicios del prototipo local.

Uso:
    python scripts/run_all.py            # arranca y se queda en primer plano
    python scripts/run_all.py --stop     # detiene los procesos registrados
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from common import http_json, load_config, service_url  # noqa: E402

PID_FILE = ROOT / "data" / "servicios.pid"
SERVICIOS = [
    "queue_service", "tool_service", "audit_service", "orchestrator", "api_gateway", "worker"
]


def esperar_health(timeout: float = 20.0) -> bool:
    limite = time.time() + timeout
    while time.time() < limite:
        estados = [http_json("GET", service_url(s, "/health"))[0] for s in SERVICIOS]
        if all(e == 200 for e in estados):
            return True
        time.sleep(0.2)
    return False


def arrancar() -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    if PID_FILE.exists() and esperar_health(1.0):
        print("Los servicios ya están en ejecución.")
        return
    entorno = os.environ.copy()
    entorno["WORKER_POLL"] = "1"
    entorno["PYTHONPATH"] = str(ROOT / "src")
    procesos = []
    for nombre in SERVICIOS:
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "src" / f"{nombre}.py")],
            cwd=ROOT, env=entorno,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        procesos.append(proc)
    PID_FILE.write_text("\n".join(str(p.pid) for p in procesos), encoding="utf-8")
    if not esperar_health():
        print("ERROR: algunos servicios no respondieron a /health", file=sys.stderr)
        detener()
        sys.exit(1)
    print("Servicios iniciados:")
    for nombre in SERVICIOS:
        print(f"  - {nombre}: {service_url(nombre)}")
    print("Presiona Ctrl+C para detenerlos.")
    try:
        while all(p.poll() is None for p in procesos):
            time.sleep(0.5)
    except KeyboardInterrupt:
        detener()


def detener() -> None:
    if not PID_FILE.exists():
        print("No hay procesos registrados.")
        return
    for linea in PID_FILE.read_text(encoding="utf-8").splitlines():
        try:
            pid = int(linea)
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            else:
                os.kill(pid, 15)
        except (ProcessLookupError, ValueError, OSError):
            pass
    PID_FILE.unlink(missing_ok=True)
    print("Servicios detenidos.")


if __name__ == "__main__":
    if "--stop" in sys.argv:
        detener()
    else:
        arrancar()
