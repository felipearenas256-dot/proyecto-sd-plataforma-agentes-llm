"""Utilidades compartidas por los servicios del prototipo distribuido.

El prototipo usa únicamente la biblioteca estándar de Python para que pueda
ejecutarse en cualquier equipo sin instalar dependencias de AWS.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.json"
DATA_DIR = ROOT / "data"

_CONFIG_CACHE: dict | None = None
_WRITE_LOCK = threading.Lock()
_AUDIT_LOCK = threading.Lock()
# Interruptor de energía del servicio: cuando está activo, el proceso sigue vivo pero
# responde 503 a todo (incluido /health), igual que un servicio caído para sus clientes.
APAGADO = threading.Event()

# ---------------------------------------------------------------------------
# Trazabilidad distribuida (equivalente local a AWS X-Ray / OpenTelemetry).
# El gateway asigna un X-Trace-Id a cada operación; cada servicio lo guarda en un
# contexto por hilo, lo propaga en sus llamadas salientes y registra un "span"
# (servicio, operación, inicio, duración, estado) en data/trazas.jsonl.
# ---------------------------------------------------------------------------
_CTX = threading.local()
_TRAZAS_LOCK = threading.Lock()


def traza_actual() -> str:
    return getattr(_CTX, "traza", "")


def fijar_traza(traza: str) -> None:
    _CTX.traza = traza or ""


def registrar_span(servicio: str, operacion: str, inicio: float, duracion_ms: float,
                   estado: int | str, detalle: dict | None = None) -> None:
    traza = traza_actual()
    if not traza:
        return
    span = {"traza": traza, "servicio": servicio, "operacion": operacion,
            "inicio": round(inicio, 4), "ms": round(duracion_ms, 1), "estado": estado}
    if detalle:
        span["detalle"] = detalle
    # Escritura diferida por lotes: la solicitud solo agrega el span a memoria y un
    # hilo lo vuelca a disco cada 100 ms, para no sumar E/S a la latencia.
    global _ESCRITOR_SPANS
    with _TRAZAS_LOCK:
        _SPANS_PENDIENTES.append(json.dumps(span, ensure_ascii=False))
        if _ESCRITOR_SPANS is None:
            _ESCRITOR_SPANS = threading.Thread(target=_volcar_spans, daemon=True)
            _ESCRITOR_SPANS.start()


_SPANS_PENDIENTES: list[str] = []
_ESCRITOR_SPANS: threading.Thread | None = None


def _volcar_spans() -> None:
    while True:
        time.sleep(0.1)
        with _TRAZAS_LOCK:
            lote = _SPANS_PENDIENTES[:]
            _SPANS_PENDIENTES.clear()
        if not lote:
            continue
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with (DATA_DIR / "trazas.jsonl").open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lote) + "\n")
        except OSError as exc:
            log("trazas", f"no se pudieron guardar {len(lote)} spans: {exc}")


def leer_traza(traza: str, max_lineas: int = 20000) -> list[dict]:
    path = DATA_DIR / "trazas.jsonl"
    if not path.exists():
        return []
    spans = []
    for ln in path.read_text(encoding="utf-8").splitlines()[-max_lineas:]:
        if traza not in ln:
            continue
        try:
            span = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if span.get("traza") == traza:
            spans.append(span)
    return sorted(spans, key=lambda x: x["inicio"])


def load_config() -> dict:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        with CONFIG_PATH.open("r", encoding="utf-8") as fh:
            _CONFIG_CACHE = json.load(fh)
    return _CONFIG_CACHE


def service_url(name: str, path: str = "") -> str:
    """URL de un servicio. En Docker, <NOMBRE>_HOST apunta al contenedor (p. ej. ORCHESTRATOR_HOST=orchestrator)."""
    svc = load_config()["services"][name]
    host = os.environ.get(f"{name.upper()}_HOST", svc["host"])
    return f"http://{host}:{svc['port']}{path}"


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def log(service: str, message: str) -> None:
    print(f"[{now_iso()}] [{service}] {message}", flush=True)


def http_json(
    method: str,
    url: str,
    payload: dict | None = None,
    headers: dict | None = None,
    timeout: float = 3.0,
) -> tuple[int, dict]:
    """Cliente HTTP minimalista. Estado 0 significa fallo de comunicación."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req_headers = {"Content-Type": "application/json; charset=utf-8"}
    if traza_actual():
        req_headers["X-Trace-Id"] = traza_actual()   # propagación del contexto de traza
    req_headers.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return exc.code, (json.loads(raw) if raw else {})
    except Exception as exc:  # timeout, connection refused, etc.
        return 0, {"error": str(exc)}


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, value: Any) -> None:
    """Escritura atómica mediante archivo temporal + reemplazo."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _WRITE_LOCK:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def append_audit(
    tipo: str,
    actor: str,
    entidad: str,
    resultado: str,
    detalle: dict | None = None,
) -> dict:
    """Bitácora de auditoría append-only (simula un registro inmutable)."""
    event = {
        "id": new_id("aud-"),
        "ts": now_iso(),
        "tipo": tipo,
        "actor": actor,
        "entidad": entidad,
        "resultado": resultado,
        "detalle": detalle or {},
    }
    if traza_actual():
        event["traza"] = traza_actual()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    linea = json.dumps(event, ensure_ascii=False) + "\n"
    # Sin este candado, hilos concurrentes intercalaban líneas y corrompían la bitácora.
    with _AUDIT_LOCK, (DATA_DIR / "audit.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(linea)
    return event


def read_audit(limit: int = 100) -> list[dict]:
    path = DATA_DIR / "audit.jsonl"
    if not path.exists():
        return []
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    eventos = []
    for ln in reversed(lines[-limit:]):
        try:
            eventos.append(json.loads(ln))
        except json.JSONDecodeError:
            continue  # una línea a medio escribir no debe tumbar la consulta
    return eventos


def mask_pii(text: str) -> str:
    """Enmascaramiento básico de PII, equivalente local a Bedrock Guardrails."""
    patrones = load_config()["guardrails"]["patrones_pii"]
    for patron in patrones:
        text = re.sub(patron, "[PII_ENMASCARADO]", text)
    return text


class CircuitBreaker:
    """Circuit breaker en memoria: CERRADO -> ABIERTO -> SEMIABIERTO."""

    def __init__(self, umbral: int = 3, apertura: float = 1.0):
        self.umbral = umbral
        self.apertura = apertura
        self.estado = "CERRADO"
        self.fallos = 0
        self.abierto_desde = 0.0
        self._lock = threading.Lock()

    def permite(self) -> bool:
        with self._lock:
            if self.estado == "ABIERTO":
                if time.monotonic() - self.abierto_desde >= self.apertura:
                    self.estado = "SEMIABIERTO"
                    return True
                return False
            return True

    def exito(self) -> None:
        with self._lock:
            self.fallos = 0
            self.estado = "CERRADO"

    def fallo(self) -> None:
        with self._lock:
            self.fallos += 1
            if self.fallos >= self.umbral:
                self.estado = "ABIERTO"
                self.abierto_desde = time.monotonic()

    def reset(self) -> None:
        with self._lock:
            self.fallos = 0
            self.estado = "CERRADO"
            self.abierto_desde = 0.0


def _con_interruptor(metodo_original):
    """Envuelve do_GET/do_POST: atiende /interno/energia y bloquea todo lo demás si está apagado."""
    from urllib.parse import urlparse

    def envuelto(self) -> None:
        ruta = urlparse(self.path).path
        fijar_traza(self.headers.get("X-Trace-Id", ""))
        inicio, t0 = time.time(), time.perf_counter()
        try:
            _atender(self, ruta)
        finally:
            if ruta != "/health":
                registrar_span(self.server_name_svc, f"{self.command} {ruta}", inicio,
                               (time.perf_counter() - t0) * 1000, getattr(self, "_estado", 0))
            fijar_traza("")

    def _atender(self, ruta: str) -> None:
        if not getattr(self, "APAGABLE", True):   # el gateway público nunca se apaga
            metodo_original(self)
        elif ruta == "/interno/energia":
            if self.command == "POST":
                encendido = bool(self.body_json().get("encendido", True))
                (APAGADO.clear if encendido else APAGADO.set)()
                log(self.server_name_svc, "encendido desde el panel" if encendido else "apagado desde el panel")
            self.send_json(200, {"encendido": not APAGADO.is_set()})
        elif APAGADO.is_set():
            self.send_json(503, {"error": "Servicio apagado desde el panel", "apagado": True})
        else:
            metodo_original(self)
    return envuelto


class JSONHandler(BaseHTTPRequestHandler):
    """Handler base con respuestas JSON y logging silencioso."""

    server_version = "AgentsPlatformDemo/1.0"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for nombre in ("do_GET", "do_POST"):
            if nombre in cls.__dict__:
                setattr(cls, nombre, _con_interruptor(cls.__dict__[nombre]))

    @property
    def server_name_svc(self) -> str:
        return getattr(self.server, "nombre_servicio", "servicio")

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def send_json(self, status: int, payload: dict, headers: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._estado = status
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if traza_actual():
            self.send_header("X-Trace-Id", traza_actual())
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Request-Id", new_id("req-"))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def body_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # pragma: no cover - sobrescrito por cada servicio
        self.send_json(404, {"error": "Ruta no encontrada"})

    def do_POST(self) -> None:  # pragma: no cover - sobrescrito por cada servicio
        self.send_json(404, {"error": "Ruta no encontrada"})


def serve(service_name: str, handler: type[BaseHTTPRequestHandler]) -> None:
    cfg = load_config()["services"][service_name]
    # BIND_HOST=0.0.0.0 permite que otros contenedores alcancen el servicio.
    host = os.environ.get("BIND_HOST", cfg["host"])
    # PUERTO permite lanzar varias réplicas de un mismo servicio en un solo equipo.
    puerto = int(os.environ.get("PUERTO", cfg["port"]))
    server = ThreadingHTTPServer((host, puerto), handler)
    server.nombre_servicio = os.environ.get("NOMBRE_INSTANCIA", service_name)
    log(service_name, f"escuchando en http://{host}:{puerto}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def run_service(module_name: str) -> None:
    """Punto de entrada usado por los scripts de arranque."""
    module = __import__(module_name)
    handler = getattr(module, "Handler")
    serve(module.SERVICE_NAME, handler)


if __name__ == "__main__":
    run_service(sys.argv[1])
