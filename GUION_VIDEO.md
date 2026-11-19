# Guion de sustentación (video de 10 minutos)

**Formato sugerido:** diapositivas + demostración en vivo con grabación de pantalla (OBS Studio o Zoom). Ambos integrantes hablan; se indica quién lleva cada bloque, pero pueden repartirlo como prefieran.

**Antes de grabar:**

1. `docker compose down -v` y luego `docker compose up -d --build --wait` (estado limpio).
2. Tener abiertos: el navegador en http://127.0.0.1:8080/, una terminal en la carpeta del proyecto y las figuras de `docs/`.
3. No mostrar claves reales de API ni datos personales.

| Tiempo | Bloque | Quién | Qué se muestra | Qué se dice (ideas clave) |
|---|---|---|---|---|
| 0:00 – 0:40 | Presentación | Integrante 1 | Portada | Nombres, asignatura, título y qué se va a demostrar. |
| 0:40 – 1:40 | Problema | Integrante 1 | Diapositiva de problemática | RPA rígido, IA adoptada sin gobierno y duplicidad de integraciones. Hace falta una plataforma común para el chat y las tareas asíncronas. |
| 1:40 – 2:30 | Por qué distribuido | Integrante 1 | Tabla centralizado vs. distribuido (sección 4) | Escalar por partes, aislar fallos y gobernar de forma centralizada. El costo es más complejidad, y por eso se usan patrones de resiliencia. |
| 2:30 – 3:40 | Arquitectura | Integrante 2 | `docs/arquitectura.png` y luego `docs/prototipo.png` | Las seis capas en AWS. Cómo se traduce cada una en los seis servicios del prototipo. REST síncrono para el chat y cola para las tareas. Solo el gateway expone un puerto. |
| 3:40 – 4:30 | Código clave | Integrante 2 | `api_gateway.py` (`_autorizar`), `worker.py` (reintentos), `orchestrator.py` (`_candado_clave`), `queue_service.py` (lease y ack) | Autenticación y roles en el borde, reintentos con backoff hacia la DLQ, idempotencia con un candado por clave, y entrega con lease: si una réplica muere, su tarea vuelve a la cola. |
| 4:30 – 5:00 | Despliegue | Integrante 2 | Terminal: `docker compose ps` | Ocho contenedores *healthy* (el worker con tres réplicas) en una red privada, con healthchecks y reinicio automático. |
| 5:00 – 7:00 | **Demo en vivo** | Integrante 1 | Interfaz web | (1) Chat: "¿Cómo se reprocesa la DLQ?" → respuesta con el documento citado. (2) Tarea con 2 fallos → se completa en 3 intentos. (3) Chat con "eliminar registro…" → queda pendiente → se aprueba. (4) En la vista Servicios se apaga `tool_service` con su interruptor (o en la terminal: `docker compose stop tool_service`) → el indicador se pone rojo y el chat da 503, pero el resto funciona. Se crea una tarea → termina en la DLQ. (5) Se vuelve a encender `tool_service` → "Reintentar desde la DLQ" → COMPLETADA. (6) Vista Tareas: publicar varios eventos y mostrar cómo se reparten entre las tres réplicas; en la terminal, `docker kill` de una réplica → las otras dos siguen. (7) «Ver traza» de una tarea: la cascada muestra gateway, orquestador, cola, worker y herramienta con el mismo X-Trace-Id. (8) Bitácora con los eventos y la PII enmascarada. |
| 7:00 – 8:20 | Pruebas y resultados | Integrante 2 | `docs/capturas/pruebas_unittest.png` y la tabla de rendimiento (13.3) | 29 pruebas en cinco categorías, todas correctas. Hallazgos reales: la carrera en la idempotencia (7 tareas → 1), la bitácora corrupta y la pérdida de tareas al escalar, resuelta con lease y confirmación. Rendimiento: p95 de 576 → 134 ms tras quitar el cuello de botella de persistencia. |
| 8:20 – 9:00 | Presupuesto | Integrante 1 | Sección 16 | Escenario A: el prototipo, con desembolso real de $170.000. Escenario B: 3 meses en AWS por $9,06 millones, donde el costo dominante en producción son los tokens del LLM. |
| 9:00 – 10:00 | Conclusiones y limitaciones | Ambos | Sección 17 | Qué aprendimos (aislamiento real de fallos, la concurrencia como fuente de errores, medir antes de optimizar). Limitaciones: cola en memoria, tokens estáticos, sin AWS real. Trabajo futuro. |

**Consejos:** ensayar la demostración una vez antes de grabar; si algo falla en vivo, `docker compose restart` recupera el estado en segundos. Exportar en 1080p y verificar el audio antes de subir.
