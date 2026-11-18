"""Pruebas de integración: comunicación entre gateway, orquestador, cola, worker y herramientas."""
from __future__ import annotations

import unittest

from base import ensure_services, get, post, reset_state
from common import http_json, service_url

SERVICIOS = ["api_gateway", "orchestrator", "tool_service", "queue_service", "audit_service", "worker"]


class PruebasIntegracion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_services()

    def setUp(self):
        reset_state()

    def test_01_health_de_todos_los_servicios(self):
        for nombre in SERVICIOS:
            with self.subTest(servicio=nombre):
                status, _ = http_json("GET", service_url(nombre, "/health"))
                self.assertEqual(status, 200)

    def test_02_flujo_asincrono_extremo_a_extremo(self):
        _, creada = post("/v1/tareas",
                         {"evento": "cron.reporte", "payload": {"registros": 12}},
                         token="demo-sistema-token", key="int-async-01")
        id_tarea = creada["id_tarea"]
        status, worker = http_json("POST", service_url("worker", "/worker/run-once"))
        self.assertEqual(status, 200)
        self.assertEqual(worker["estado"], "COMPLETADA")
        self.assertEqual(worker["intentos"], 1)
        _, tarea = get(f"/v1/tareas/{id_tarea}")
        self.assertEqual(tarea["estado"], "COMPLETADA")
        self.assertEqual(tarea["resultado"]["registros_procesados"], 12)

    def test_03_recuperacion_aumentada_por_generacion(self):
        status, respuesta = post("/v1/chat", {"mensaje": "¿Qué dice la política de automatización?"},
                                 key="int-rag-01")
        self.assertEqual(status, 200)
        titulos = [d["titulo"] for d in respuesta["documentos"]]
        self.assertTrue(any("automatización" in t.lower() for t in titulos))

    def test_04_cola_desacopla_productor_y_consumidor(self):
        for i in range(3):
            status, _ = post("/v1/tareas", {"evento": "webhook", "payload": {"n": i}},
                             token="demo-sistema-token", key=f"int-cola-{i}")
            self.assertEqual(status, 202)
        _, stats = http_json("GET", service_url("queue_service", "/stats"))
        self.assertEqual(stats["pendientes"], 3)
        for _ in range(3):
            http_json("POST", service_url("worker", "/worker/run-once"))
        _, stats = http_json("GET", service_url("queue_service", "/stats"))
        self.assertEqual(stats["pendientes"], 0)
        _, auditoria = get("/v1/auditoria", token="demo-auditor-token")
        completadas = [e for e in auditoria["eventos"] if e["tipo"] == "tarea_completada"]
        self.assertEqual(len(completadas), 3)


if __name__ == "__main__":
    unittest.main()
