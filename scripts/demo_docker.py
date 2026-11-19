"""Demostración extremo a extremo contra el despliegue en contenedores.

Uso (con `docker compose up -d` en ejecución):
    python scripts/demo_docker.py

Recorre: chat, tarea asíncrona procesada por el worker en su contenedor,
caída del contenedor tool_service (docker stop), DLQ y recuperación, escalado
horizontal con tres réplicas del worker, caída de una réplica y traza distribuida.
La salida se guarda en evidencias/docker_demo.txt.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GW = "http://127.0.0.1:8080"
LINEAS: list[str] = []


def out(texto: str = "") -> None:
    print(texto, flush=True)
    LINEAS.append(texto)


def llamar(metodo: str, ruta: str, cuerpo: dict | None = None, token: str = "demo-usuario-token",
           clave: str | None = None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    if clave:
        headers["Idempotency-Key"] = clave
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(GW + ruta, data=datos, headers=headers, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def esperar_tarea(id_tarea: str, estados: set[str], timeout: float = 20) -> dict:
    limite = time.time() + timeout
    tarea: dict = {}
    while time.time() < limite:
        _, tarea = llamar("GET", f"/v1/tareas/{id_tarea}", token="demo-carga-token")
        if tarea.get("estado") in estados:
            break
        time.sleep(0.5)
    return tarea


def docker(*args: str) -> str:
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True).stdout


def publicar_lote(n: int, prefijo: str) -> list[str]:
    ids = []
    for i in range(n):
        _, r = llamar("POST", "/v1/tareas", {"evento": "lote", "payload": {"registros": i + 1}},
                      token="demo-carga-sistema-token", clave=f"{prefijo}-{i}")
        ids.append(r["id_tarea"])
    return ids


def reparto(ids: list[str]) -> dict[str, int]:
    conteo: dict[str, int] = {}
    for t in ids:
        tarea = esperar_tarea(t, {"COMPLETADA", "FALLIDA"}, timeout=40)
        quien = tarea.get("procesada_por", "?")
        conteo[quien] = conteo.get(quien, 0) + 1
    return dict(sorted(conteo.items()))


def main() -> None:
    sufijo = str(int(time.time()))
    out("=== Demostración en contenedores (docker compose) ===")
    out(docker("ps", "--format", "table {{.Service}}\t{{.Status}}\t{{.Ports}}").rstrip())

    out("\n1) Chat a través del API Gateway publicado en :8080")
    s, r = llamar("POST", "/v1/chat", {"mensaje": "¿Cómo se reprocesa la DLQ?"}, clave=f"dk-chat-{sufijo}")
    out(f"   HTTP {s} · proveedor={r.get('proveedor_llm')} · documentos={[d['id'] for d in r.get('documentos', [])]}")
    out(f"   respuesta: {r.get('respuesta', '')[:160]}...")

    out("\n2) Tarea asíncrona: gateway -> orquestador -> cola -> worker (otro contenedor)")
    s, r = llamar("POST", "/v1/tareas", {"evento": "cron.reporte", "payload": {"registros": 30}},
                  token="demo-sistema-token", clave=f"dk-t1-{sufijo}")
    out(f"   HTTP {s} · {r}")
    t = esperar_tarea(r["id_tarea"], {"COMPLETADA", "FALLIDA"})
    out(f"   estado final: {t.get('estado')} · intentos={t.get('intentos')} · resultado={t.get('resultado')}")

    out("\n3) Caída de un nodo: docker compose stop tool_service")
    docker("stop", "tool_service")
    s, r = llamar("POST", "/v1/chat", {"mensaje": "política de automatización"}, clave=f"dk-chat2-{sufijo}")
    out(f"   chat con la herramienta caída -> HTTP {s} · {r.get('error')}")
    s, r = llamar("POST", "/v1/tareas", {"evento": "caida", "payload": {"registros": 7}},
                  token="demo-sistema-token", clave=f"dk-t2-{sufijo}")
    out(f"   la cola sigue aceptando tareas -> HTTP {s} · {r}")
    t = esperar_tarea(r["id_tarea"], {"FALLIDA", "COMPLETADA"}, timeout=40)
    out(f"   tras 3 reintentos con backoff: estado={t.get('estado')} (enviada a la DLQ)")

    out("\n4) Recuperación: docker compose start tool_service + reproceso de la DLQ")
    docker("start", "tool_service")
    time.sleep(8)  # healthcheck del contenedor y ventana del circuit breaker
    s, r = llamar("POST", "/v1/tareas/reintentar-dlq", {}, token="demo-admin-token")
    out(f"   reintentar-dlq -> HTTP {s} · {r}")
    t = esperar_tarea(t["id"], {"COMPLETADA"}, timeout=30)
    out(f"   estado final: {t.get('estado')} · resultado={t.get('resultado')}")

    out("\n5) Escalado horizontal: tres réplicas del worker compiten por la misma cola")
    _, salud = llamar("GET", "/v1/salud")
    out(f"   réplicas del worker: {salud['replicas']['worker']}")
    ids = publicar_lote(15, f"dk-lote1-{sufijo}")
    out(f"   15 tareas publicadas -> reparto: {reparto(ids)}")

    out("\n6) Caída de una réplica: docker kill de un contenedor worker")
    contenedores = docker("ps", "-q", "worker").split()
    subprocess.run(["docker", "kill", contenedores[0]], capture_output=True)
    time.sleep(3)
    _, salud = llamar("GET", "/v1/salud")
    out(f"   réplicas del worker: {salud['replicas']['worker']} · worker operativo={salud['servicios']['worker']}")
    ids = publicar_lote(10, f"dk-lote2-{sufijo}")
    out(f"   10 tareas publicadas -> reparto entre las réplicas vivas: {reparto(ids)}")
    docker("up", "-d", "--scale", "worker=3")
    time.sleep(8)
    _, salud = llamar("GET", "/v1/salud")
    out(f"   tras docker compose up -d --scale worker=3: {salud['replicas']['worker']}")
    _, cola = llamar("GET", "/v1/metricas", token="demo-auditor-token")
    c = cola["cola"]
    out(f"   cola: confirmadas={c['confirmadas']} · en_vuelo={c['en_vuelo']} · reentregadas={c['reentregadas']} · pendientes={c['pendientes']}")

    out("\n7) Traza distribuida de una tarea (X-Trace-Id propagado entre contenedores)")
    _, r = llamar("POST", "/v1/tareas", {"evento": "traza", "payload": {"registros": 3}},
                  token="demo-sistema-token", clave=f"dk-traza-{sufijo}")
    esperar_tarea(r["id_tarea"], {"COMPLETADA"})
    time.sleep(0.5)
    _, traza = llamar("GET", f"/v1/trazas/{r['traza']}", token="demo-auditor-token")
    out(f"   traza {r['traza']}: {len(traza['spans'])} spans")
    for sp in traza["spans"]:
        out(f"     {sp['servicio']:<22} {sp['operacion']:<38} {sp['ms']:>7} ms  {sp['estado']}")

    s, r = llamar("GET", "/v1/auditoria?limit=500", token="demo-auditor-token")
    tipos: dict[str, int] = {}
    for e in r.get("eventos", []):
        tipos[e["tipo"]] = tipos.get(e["tipo"], 0) + 1
    out(f"\n8) Bitácora de auditoría (contenedor audit_service): {r.get('total')} eventos")
    for tipo, n in sorted(tipos.items()):
        out(f"   {tipo}: {n}")

    (ROOT / "evidencias").mkdir(exist_ok=True)
    (ROOT / "evidencias" / "docker_demo.txt").write_text("\n".join(LINEAS) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
