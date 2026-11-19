"""Servicio de solo lectura de la bitácora de auditoría (perfil Auditor)."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from common import DATA_DIR, JSONHandler, leer_traza, read_audit, serve

SERVICE_NAME = "audit_service"


class Handler(JSONHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.send_json(200, {"estado": "OK", "servicio": SERVICE_NAME})
        elif parsed.path == "/auditoria":
            params = parse_qs(parsed.query)
            limit = int(params.get("limit", ["100"])[0])
            eventos = read_audit(min(limit, 500))
            self.send_json(200, {"total": len(eventos), "eventos": eventos})
        elif parsed.path.startswith("/trazas/"):
            traza = parsed.path.split("/")[-1]
            spans = leer_traza(traza)
            eventos = [e for e in read_audit(500) if e.get("traza") == traza]
            if not spans and not eventos:
                self.send_json(404, {"error": "Traza no encontrada"})
            else:
                self.send_json(200, {"traza": traza, "spans": spans, "eventos": list(reversed(eventos))})
        else:
            self.send_json(404, {"error": "Ruta no encontrada"})


    def do_POST(self) -> None:
        if urlparse(self.path).path == "/reset":
            (DATA_DIR / "audit.jsonl").unlink(missing_ok=True)
            (DATA_DIR / "trazas.jsonl").unlink(missing_ok=True)
            self.send_json(200, {"estado": "REINICIADO"})
        else:
            self.send_json(404, {"error": "Ruta no encontrada"})


if __name__ == "__main__":
    serve(SERVICE_NAME, Handler)
