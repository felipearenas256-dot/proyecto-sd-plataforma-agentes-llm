"""Pruebas de concurrencia: múltiples solicitudes simultáneas."""
from __future__ import annotations

import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed

from base import ensure_services, get, post, reset_state
from common import http_json, service_url


class PruebasConcurrencia(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_services()

    def setUp(self):
        reset_state()

    def test_01_40_chat_simultaneos(self):
        inicio = time.perf_counter()
        with ThreadPoolExecutor(max_workers=20) as pool:
            futuros = [pool.submit(post, "/v1/chat",
                                   {"mensaje": f"consulta concurrente {i}"},
                                   "demo-carga-token", f"conc-chat-{i}")
                       for i in range(40)]
            resultados = [f.result() for f in as_completed(futuros)]
        elapsed = time.perf_counter() - inicio
        estados = [r[0] for r in resultados]
        ids = [r[1].get("id_conversacion") for r in resultados]
        self.assertTrue(all(e == 200 for e in estados), estados)
        self.assertEqual(len(set(ids)), 40)
        _, auditoria = get("/v1/auditoria?limit=500", token="demo-auditor-token")
        mensajes = [e for e in auditoria["eventos"] if e["tipo"] == "mensaje_recibido"]
        self.assertEqual(len(mensajes), 40)
        print(f"\n[concurrencia] 40 solicitudes en {elapsed:.3f}s "
              f"({40 / elapsed:.1f} req/s), 0 errores")

    def test_02_20_tareas_simultaneas(self):
        with ThreadPoolExecutor(max_workers=10) as pool:
            futuros = [pool.submit(post, "/v1/tareas",
                                   {"evento": "concurrente", "payload": {"i": i}},
                                   "demo-carga-sistema-token", f"conc-task-{i}")
                       for i in range(20)]
            resultados = [f.result() for f in as_completed(futuros)]
        self.assertTrue(all(r[0] == 202 for r in resultados))
        ids = [r[1]["id_tarea"] for r in resultados]
        self.assertEqual(len(set(ids)), 20)
        _, stats = http_json("GET", service_url("queue_service", "/stats"))
        self.assertEqual(stats["pendientes"], 20)

    def test_03_misma_clave_idempotencia_concurrente(self):
        """20 reintentos simultáneos con la misma Idempotency-Key producen un solo efecto."""
        with ThreadPoolExecutor(max_workers=20) as pool:
            tareas = list(pool.map(lambda _: post("/v1/tareas", {"evento": "duplicado"},
                                                  "demo-carga-sistema-token", "conc-misma-clave"),
                                   range(20)))
            chats = list(pool.map(lambda _: post("/v1/chat", {"mensaje": "consulta duplicada"},
                                                 "demo-carga-token", "conc-misma-clave-chat"),
                                  range(20)))
        self.assertEqual(len({r[1]["id_tarea"] for r in tareas}), 1)
        self.assertEqual(len({r[1]["id_conversacion"] for r in chats}), 1)
        _, stats = http_json("GET", service_url("queue_service", "/stats"))
        self.assertEqual(stats["pendientes"], 1)
        print("\n[concurrencia] 20 solicitudes con la misma clave -> 1 tarea y 1 conversación")


if __name__ == "__main__":
    unittest.main()
