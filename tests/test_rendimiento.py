"""Pruebas de rendimiento: latencia (p50/p95/p99) y throughput del chat."""
from __future__ import annotations

import json
import statistics
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from base import ROOT, ensure_services, post, reset_state
from common import load_config

SOLICITUDES = 250
CONCURRENCIA = 25
TOKENS = [f"demo-rend-{i}-token" for i in range(1, 6)]  # 50 solicitudes por token < límite de 120/min


def _percentil(valores: list[float], p: float) -> float:
    ordenados = sorted(valores)
    k = max(0, min(len(ordenados) - 1, round(p / 100 * len(ordenados)) - 1))
    return ordenados[k]


class PruebasRendimiento(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_services()
        reset_state()

    def test_01_latencia_y_throughput_del_chat(self):
        def una(i: int) -> tuple[int, float]:
            inicio = time.perf_counter()
            status, _ = post("/v1/chat", {"mensaje": f"¿Qué dice la política de automatización? #{i}"},
                             TOKENS[i % len(TOKENS)], f"rend-{i}")
            return status, (time.perf_counter() - inicio) * 1000

        # Calentamiento: carga módulos y estabiliza los procesos.
        for i in range(5):
            post("/v1/chat", {"mensaje": "calentamiento"}, "demo-carga-token", f"rend-warm-{i}")

        inicio = time.perf_counter()
        with ThreadPoolExecutor(max_workers=CONCURRENCIA) as pool:
            resultados = list(pool.map(una, range(SOLICITUDES)))
        total = time.perf_counter() - inicio

        latencias = [ms for _, ms in resultados]
        errores = sum(1 for s, _ in resultados if s != 200)
        objetivo = load_config()["seguridad"]["p95_objetivo_ms"]
        metricas = {
            "solicitudes": SOLICITUDES,
            "concurrencia": CONCURRENCIA,
            "errores": errores,
            "duracion_s": round(total, 3),
            "throughput_rps": round(SOLICITUDES / total, 1),
            "latencia_ms": {
                "min": round(min(latencias), 1),
                "media": round(statistics.mean(latencias), 1),
                "p50": round(_percentil(latencias, 50), 1),
                "p95": round(_percentil(latencias, 95), 1),
                "p99": round(_percentil(latencias, 99), 1),
                "max": round(max(latencias), 1),
            },
            "p95_objetivo_ms": objetivo,
            "cumple_rnf09": _percentil(latencias, 95) <= objetivo,
        }
        (ROOT / "evidencias").mkdir(exist_ok=True)
        (ROOT / "evidencias" / "rendimiento.json").write_text(
            json.dumps(metricas, indent=2, ensure_ascii=False), encoding="utf-8")
        lat = metricas["latencia_ms"]
        print(f"\n[rendimiento] {SOLICITUDES} solicitudes, concurrencia {CONCURRENCIA}: "
              f"{metricas['throughput_rps']} req/s, p50={lat['p50']} ms, p95={lat['p95']} ms, "
              f"p99={lat['p99']} ms, errores={errores}")
        self.assertEqual(errores, 0)
        self.assertLessEqual(lat["p95"], objetivo)


if __name__ == "__main__":
    unittest.main()
