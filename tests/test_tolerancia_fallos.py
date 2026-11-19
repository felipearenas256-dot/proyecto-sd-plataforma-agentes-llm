"""Pruebas de tolerancia a fallos: reintentos, DLQ, circuit breaker e idempotencia."""
from __future__ import annotations

import time
import unittest

from base import (detener_servicio, ensure_services, esperar_salud, get, iniciar_servicio, post,
                  reset_state, servicios_propios)
from common import CircuitBreaker, http_json, service_url


class PruebasTolerancia(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_services()

    def setUp(self):
        reset_state()

    def test_01_reintentos_con_backoff(self):
        _, creada = post("/v1/tareas",
                         {"evento": "fallo-transitorio",
                          "payload": {"fallar_veces": 2, "registros": 8}},
                         token="demo-sistema-token", key="tol-retry-01")
        status, worker = http_json("POST", service_url("worker", "/worker/run-once"))
        self.assertEqual(status, 200)
        self.assertEqual(worker["estado"], "COMPLETADA")
        self.assertEqual(worker["intentos"], 3)
        _, tarea = get(f"/v1/tareas/{creada['id_tarea']}")
        self.assertEqual(tarea["estado"], "COMPLETADA")
        _, auditoria = get("/v1/auditoria", token="demo-auditor-token")
        reintentos = [e for e in auditoria["eventos"] if e["tipo"] == "tarea_reintentable"]
        self.assertEqual(len(reintentos), 2)
        print("\n[tolerancia] tarea completada tras 3 intentos (2 reintentos)")

    def test_02_agotar_reintentos_envia_a_dlq(self):
        _, creada = post("/v1/tareas",
                         {"evento": "fallo-persistente",
                          "payload": {"fallar_veces": 10, "registros": 4}},
                         token="demo-sistema-token", key="tol-dlq-01")
        _, worker = http_json("POST", service_url("worker", "/worker/run-once"))
        self.assertEqual(worker["estado"], "EN_DLQ")
        _, tarea = get(f"/v1/tareas/{creada['id_tarea']}")
        self.assertEqual(tarea["estado"], "FALLIDA")
        _, dlq = http_json("GET", service_url("queue_service", "/queue/dlt"))
        self.assertEqual(len(dlq["items"]), 1)
        _, stats = http_json("GET", service_url("queue_service", "/stats"))
        self.assertEqual(stats["en_dlq"], 1)

    def test_03_recuperacion_desde_dlq(self):
        _, creada = post("/v1/tareas",
                         {"evento": "fallo-persistente",
                          "payload": {"fallar_veces": 10, "registros": 6}},
                         token="demo-sistema-token", key="tol-dlq-rec-01")
        http_json("POST", service_url("worker", "/worker/run-once"))
        # El circuit breaker permanece abierto durante la ventana de enfriamiento;
        # la intervención del operador ocurre después de ese periodo.
        time.sleep(1.1)
        status, _ = post("/v1/tareas/reintentar-dlq", {}, token="demo-admin-token")
        self.assertEqual(status, 202)
        _, worker = http_json("POST", service_url("worker", "/worker/run-once"))
        self.assertEqual(worker["estado"], "COMPLETADA")
        _, tarea = get(f"/v1/tareas/{creada['id_tarea']}")
        self.assertEqual(tarea["estado"], "COMPLETADA")

    def test_04_idempotencia_ante_reintentos_de_red(self):
        cuerpo = {"evento": "webhook.duplicado", "payload": {"n": 1}}
        _, a = post("/v1/tareas", cuerpo, token="demo-sistema-token", key="tol-idem-01")
        _, b = post("/v1/tareas", cuerpo, token="demo-sistema-token", key="tol-idem-01")
        self.assertEqual(a["id_tarea"], b["id_tarea"])
        _, stats = http_json("GET", service_url("queue_service", "/stats"))
        self.assertEqual(stats["pendientes"], 1)

    def test_05_circuit_breaker(self):
        # Ventana amplia (0,5 s): con 50 ms la prueba fallaba de forma intermitente
        # si el equipo se demoraba más que eso entre fallo() y permite().
        breaker = CircuitBreaker(umbral=2, apertura=0.5)
        breaker.fallo()
        breaker.fallo()
        self.assertEqual(breaker.estado, "ABIERTO")
        self.assertFalse(breaker.permite())
        time.sleep(0.55)
        self.assertTrue(breaker.permite())
        self.assertEqual(breaker.estado, "SEMIABIERTO")
        breaker.exito()
        self.assertEqual(breaker.estado, "CERRADO")

    def test_06_caida_real_de_un_servicio_y_recuperacion(self):
        """Se mata el proceso tool_service y se observa aislamiento, DLQ y recuperación."""
        if not servicios_propios():
            self.skipTest("Requiere que las pruebas hayan lanzado los servicios")
        detener_servicio("tool_service")
        try:
            # 1. Detección: el servicio caído no responde.
            self.assertEqual(http_json("GET", service_url("tool_service", "/health"), timeout=0.5)[0], 0)
            # 2. Aislamiento: el resto de la plataforma sigue respondiendo.
            for nombre in ["api_gateway", "orchestrator", "queue_service", "audit_service", "worker"]:
                self.assertEqual(http_json("GET", service_url(nombre, "/health"))[0], 200, nombre)
            # 3. El chat degrada con un 503 controlado en lugar de colgarse.
            status, _ = post("/v1/chat", {"mensaje": "política de automatización"}, key="tol-caida-chat")
            self.assertEqual(status, 503)
            # 4. La tarea asíncrona se acepta (la cola desacopla) y, al fallar, va a la DLQ.
            status, creada = post("/v1/tareas", {"evento": "caida", "payload": {"registros": 5}},
                                  token="demo-sistema-token", key="tol-caida-tarea")
            self.assertEqual(status, 202)
            _, worker = http_json("POST", service_url("worker", "/worker/run-once"), timeout=30)
            self.assertEqual(worker["estado"], "EN_DLQ")
            _, estado = http_json("GET", service_url("orchestrator", "/interno/estado"))
            self.assertEqual(estado["circuit_breaker"], "ABIERTO")
        finally:
            # 5. Recuperación del servicio.
            iniciar_servicio("tool_service")
            self.assertTrue(esperar_salud("tool_service"))
        time.sleep(1.1)  # ventana de enfriamiento del circuit breaker
        status, _ = post("/v1/tareas/reintentar-dlq", {}, token="demo-admin-token")
        self.assertEqual(status, 202)
        _, worker = http_json("POST", service_url("worker", "/worker/run-once"))
        self.assertEqual(worker["estado"], "COMPLETADA")
        _, tarea = get(f"/v1/tareas/{creada['id_tarea']}")
        self.assertEqual(tarea["estado"], "COMPLETADA")
        _, estado = http_json("GET", service_url("orchestrator", "/interno/estado"))
        self.assertEqual(estado["circuit_breaker"], "CERRADO")
        print("\n[tolerancia] tool_service caído: chat 503, tarea en DLQ, breaker ABIERTO; "
              "tras reiniciarlo la tarea se completó y el breaker volvió a CERRADO")

    def test_07_apagar_y_encender_servicio_desde_el_panel(self):
        """El interruptor del panel apaga un servicio (503) sin matar el proceso y lo vuelve a encender."""
        energia = lambda nombre, on, token="demo-admin-token": post(  # noqa: E731
            f"/v1/servicios/{nombre}/energia", {"encendido": on}, token=token)
        # Solo el administrador puede usarlo y el gateway no es apagable.
        self.assertEqual(energia("worker", False, token="demo-auditor-token")[0], 403)
        self.assertEqual(energia("api_gateway", False)[0], 404)
        status, cuerpo = energia("tool_service", False)
        try:
            self.assertEqual((status, cuerpo["encendido"]), (200, False))
            self.assertEqual(http_json("GET", service_url("tool_service", "/health"))[0], 503)
            _, salud = get("/v1/salud")
            self.assertFalse(salud["servicios"]["tool_service"])
            self.assertIn("tool_service", salud["apagados"])
            status, _ = post("/v1/chat", {"mensaje": "política de automatización"}, key="tol-apagado-chat")
            self.assertEqual(status, 503)
        finally:
            self.assertEqual(energia("tool_service", True)[0], 200)
        self.assertEqual(http_json("GET", service_url("tool_service", "/health"))[0], 200)
        _, salud = get("/v1/salud")
        self.assertNotIn("tool_service", salud["apagados"])
        status, _ = post("/v1/chat", {"mensaje": "política de automatización"}, key="tol-encendido-chat")
        self.assertEqual(status, 200)
        tipos = [e["tipo"] for e in get("/v1/auditoria?limit=20", token="demo-auditor-token")[1]["eventos"]]
        self.assertIn("servicio_apagado", tipos)
        self.assertIn("servicio_encendido", tipos)

    def test_08_lease_vencido_reentrega_sin_duplicar_efectos(self):
        """Un worker toma una tarea y muere sin confirmarla: el lease vence y otro la completa."""
        _, creada = post("/v1/tareas", {"evento": "lease", "payload": {"registros": 1}},
                         token="demo-admin-token", key="tol-lease")
        tarea_id = creada["id_tarea"]
        # 1. El worker "fantasma" recibe la tarea con un lease de 0,5 s y nunca la confirma.
        status, entrega = http_json("GET", service_url("queue_service", "/queue/next?worker=fantasma&visibilidad=0.5"))
        self.assertEqual((status, entrega["task"]["id"]), (200, tarea_id))
        _, cola = http_json("GET", service_url("queue_service", "/stats"))
        self.assertEqual((cola["pendientes"], cola["en_vuelo"]), (0, 1))
        time.sleep(1.0)
        # 2. El lease venció: la tarea vuelve a la cola y otro worker la completa.
        _, worker = http_json("POST", service_url("worker", "/worker/run-once"))
        self.assertEqual(worker["estado"], "COMPLETADA")
        self.assertTrue(worker["confirmada"])
        _, cola = http_json("GET", service_url("queue_service", "/stats"))
        self.assertEqual((cola["reentregadas"], cola["confirmadas"], cola["en_vuelo"]), (1, 1, 0))
        # 3. Si el fantasma revive e intenta confirmar con su recibo viejo, la cola lo rechaza.
        status, _ = http_json("POST", service_url("queue_service", "/queue/ack"),
                              {"id": tarea_id, "recibo": entrega["recibo"]})
        self.assertEqual(status, 409)
        # 4. Si además ejecuta la tarea, el orquestador no repite el efecto.
        status, r = http_json("POST", service_url("orchestrator", "/interno/tareas/ejecutar"),
                              {"id_tarea": tarea_id, "intento": 1, "payload": {}, "worker": "fantasma"})
        self.assertEqual((status, r.get("duplicada")), (200, True))
        eventos = get("/v1/auditoria?limit=100", token="demo-auditor-token")[1]["eventos"]
        self.assertEqual(sum(1 for e in eventos if e["tipo"] == "tarea_completada" and e["entidad"] == tarea_id), 1)
        print("\n[tolerancia] lease vencido: la tarea se reentregó, se completó una sola vez y el ack tardío fue rechazado (409)")

if __name__ == "__main__":
    unittest.main()
