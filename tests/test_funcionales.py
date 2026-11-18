"""Pruebas funcionales: operaciones principales y control de acceso."""
from __future__ import annotations

import unittest

from base import ensure_services, get, post, reset_state
from common import http_json, service_url


class PruebasFuncionales(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_services()
        reset_state()

    def test_01_chat_conversacional(self):
        status, resp = post("/v1/chat", {"mensaje": "¿Qué documentos hay sobre automatización?"},
                            key="chat-func-01")
        self.assertEqual(status, 200)
        self.assertEqual(resp["estado"], "COMPLETADA")
        self.assertIn("id_conversacion", resp)
        self.assertGreater(len(resp["documentos"]), 0)

    def test_02_sin_token_devuelve_401(self):
        status, _ = http_json("POST", service_url("api_gateway", "/v1/chat"),
                              {"mensaje": "hola"})
        self.assertEqual(status, 401)

    def test_03_rol_sin_permiso_devuelve_403(self):
        status, _ = post("/v1/chat", {"mensaje": "hola"}, token="demo-auditor-token")
        self.assertEqual(status, 403)

    def test_04_validacion_de_entrada(self):
        status, _ = post("/v1/chat", {"mensaje": ""}, key="chat-vacio")
        self.assertEqual(status, 400)

    def test_05_idempotencia_en_chat(self):
        cuerpo = {"mensaje": "consulta repetida"}
        _, primera = post("/v1/chat", cuerpo, key="chat-idem-01")
        _, segunda = post("/v1/chat", cuerpo, key="chat-idem-01")
        self.assertEqual(primera["id_conversacion"], segunda["id_conversacion"])
        _, auditoria = get("/v1/auditoria", token="demo-auditor-token")
        eventos = [e for e in auditoria["eventos"]
                   if e["tipo"] == "mensaje_recibido" and e["detalle"].get("mensaje") == "consulta repetida"]
        self.assertEqual(len(eventos), 1)

    def test_06_crear_y_consultar_tarea(self):
        status, resp = post("/v1/tareas",
                            {"evento": "webhook.crm", "payload": {"cuenta": "C-100"}},
                            token="demo-sistema-token", key="tarea-func-01")
        self.assertEqual(status, 202)
        id_tarea = resp["id_tarea"]
        status, consulta = get(f"/v1/tareas/{id_tarea}", token="demo-sistema-token")
        self.assertEqual(status, 200)
        self.assertIn(consulta["estado"], {"PENDIENTE", "EN_PROCESO"})

    def test_07_aprobacion_humana(self):
        _, resp = post("/v1/chat", {"mensaje": "Por favor eliminar registro del cliente"},
                       key="chat-critico-01")
        self.assertEqual(resp["estado"], "APROBACION_PENDIENTE")
        id_aprobacion = resp["id_aprobacion"]
        status, decision = post(f"/v1/aprobaciones/{id_aprobacion}/decidir",
                                {"decision": "APROBAR"}, token="demo-aprobador-token")
        self.assertEqual(status, 200)
        self.assertEqual(decision["estado"], "COMPLETADA")
        _, tarea = get(f"/v1/tareas/{resp['id_tarea']}")
        self.assertEqual(tarea["estado"], "COMPLETADA")

    def test_08_auditoria_solo_lectura_para_auditor(self):
        status, auditoria = get("/v1/auditoria", token="demo-auditor-token")
        self.assertEqual(status, 200)
        self.assertGreater(auditoria["total"], 0)
        status, _ = get("/v1/auditoria", token="demo-usuario-token")
        self.assertEqual(status, 403)

    def test_09_limite_de_tasa_devuelve_429(self):
        limite = 120
        codigos = [get("/v1/tareas/inexistente", token="demo-limite-token")[0] for _ in range(limite + 1)]
        self.assertNotIn(429, codigos[:limite])
        self.assertEqual(codigos[-1], 429)

    def test_10_respuesta_del_llm_y_rechazo_de_aprobacion(self):
        _, resp = post("/v1/chat", {"mensaje": "¿Qué roles y permisos existen?"}, key="chat-llm-01")
        self.assertTrue(resp["respuesta"])
        self.assertIn("simulado", resp["proveedor_llm"])
        self.assertEqual(resp["documentos"][0]["id"], "doc-005")
        _, critica = post("/v1/chat", {"mensaje": "quiero transferir datos a otro proveedor"},
                          key="chat-critico-02")
        status, decision = post(f"/v1/aprobaciones/{critica['id_aprobacion']}/decidir",
                                {"decision": "RECHAZAR"}, token="demo-aprobador-token")
        self.assertEqual((status, decision["estado"]), (200, "CANCELADA"))
        status, _ = post(f"/v1/aprobaciones/{critica['id_aprobacion']}/decidir",
                         {"decision": "APROBAR"}, token="demo-aprobador-token")
        self.assertEqual(status, 409)

    def test_11_pii_enmascarada_en_auditoria(self):
        post("/v1/chat", {"mensaje": "mi correo es ana@empresa.com y mi celular 3001234567"},
             key="chat-pii-01")
        _, auditoria = get("/v1/auditoria?limit=500", token="demo-auditor-token")
        texto = str(auditoria["eventos"])
        self.assertNotIn("ana@empresa.com", texto)
        self.assertNotIn("3001234567", texto)
        self.assertIn("[PII_ENMASCARADO]", texto)


if __name__ == "__main__":
    unittest.main()
