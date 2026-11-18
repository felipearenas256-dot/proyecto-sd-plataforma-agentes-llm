# Manual de instalación y ejecución

**Proyecto:** Arquitectura distribuida para una plataforma genérica de agentes LLM
**Asignatura:** Sistemas Distribuidos

Este manual explica cómo poner en marcha el prototipo de dos formas: con Docker (recomendada, seis contenedores) o como seis procesos locales de Python. También explica cómo ejecutar las pruebas y regenerar los artefactos.

---

## 1. Requisitos

| Requisito | Para qué se usa | Obligatorio |
|---|---|---|
| Git | Clonar el repositorio | Sí |
| Docker Desktop (Windows/macOS) o Docker Engine 24+ con Compose v2 (Linux) | Despliegue en contenedores | Para la opción A |
| Python 3.12 o superior | Ejecución local, pruebas y scripts de demostración | Para la opción B y las pruebas |
| 2 núcleos, 4 GB de RAM (8 GB con Docker), 1 GB de disco | — | Sí |
| `pip install anthropic` + clave de API | Usar Claude como LLM real en lugar del modo simulado | No |
| Node.js 18+ | Regenerar los diagramas | No |
| `pip install python-docx pillow` | Regenerar el informe `.docx` y las capturas de consola | No |

El prototipo no necesita bases de datos ni dependencias externas: usa solo la biblioteca estándar de Python.

## 2. Obtener el código

```bash
git clone https://github.com/felipearenas256-dot/proyecto-sd-plataforma-agentes-llm.git
cd proyecto-sd-plataforma-agentes-llm
```

## 3. Opción A · Ejecución con Docker (recomendada)

```bash
docker compose up -d --build --wait
docker compose ps
```

El primer comando construye la imagen `plataforma-agentes-llm:1.0` y levanta los seis contenedores en orden: primero la cola, las herramientas y la auditoría; luego el orquestador; y al final el worker y el gateway. `--wait` espera a que todos estén *healthy*. `docker compose ps` debe mostrar los seis servicios como `(healthy)`.

- **Interfaz web:** http://127.0.0.1:8080/
- **Demostración guiada con caída y recuperación de un nodo:** `python scripts/demo_docker.py` (guarda el resultado en `evidencias/docker_demo.txt`)
- **Registros de un servicio:** `docker compose logs -f worker`
- **Simular la caída de un nodo:** en la vista *Servicios* del panel, desactive el interruptor de `tool_service` (o de cualquier servicio interno) y vuelva a activarlo. Para una caída real del contenedor: `docker compose stop tool_service` y luego `docker compose start tool_service`
- **Detener todo:** `docker compose down` (añada `-v` para borrar también el volumen de datos)

Solo se publica el puerto 8080. Los demás servicios viven en la red privada `plataforma` y no son accesibles desde el equipo anfitrión.

## 4. Opción B · Ejecución local sin Docker

```bash
python -m venv .venv
# Windows:      .venv\Scripts\activate
# Linux/macOS:  source .venv/bin/activate
python scripts/run_all.py
```

El script inicia los seis procesos, espera a que todos respondan `/health` y muestra sus URL. Se detiene con `Ctrl+C` o, desde otra terminal, con `python scripts/run_all.py --stop`.

## 5. Ejecutar las pruebas

```bash
python -m unittest discover -s tests -v
```

- Las pruebas **levantan sus propios procesos** (con el worker en modo manual) y los detienen al terminar. Antes de ejecutarlas, detenga `run_all.py` y `docker compose`, porque usan los mismos puertos.
- Duración aproximada: 30 segundos. Resultado esperado: `Ran 25 tests ... OK`.
- La prueba de rendimiento escribe sus métricas en `evidencias/rendimiento.json`.
- Para ejecutar una sola categoría: `cd tests` y luego `python -m unittest test_tolerancia_fallos -v`.

## 6. Configuración

### 6.1 Archivo `config/config.json`

| Sección | Contenido |
|---|---|
| `services` | Host y puerto de cada servicio |
| `tokens` | Tokens de demostración, cada uno con su sujeto (`sub`) y su rol |
| `seguridad` | Longitud máxima del mensaje (2.000), límite de tasa (120/min) y objetivo de p95 (500 ms) |
| `timeouts_segundos` | Gateway → servicios 30 s · worker → orquestador 5 s · orquestador → herramientas 1,5 s |
| `retry` | Intentos (3), espera base del backoff (0,05 s) y parámetros del circuit breaker |
| `guardrails` | Acciones críticas que exigen aprobación y patrones de PII a enmascarar |

### 6.2 Variables de entorno

| Variable | Valor por defecto | Descripción |
|---|---|---|
| `LLM_PROVIDER` | `simulado` | `simulado`, `anthropic` (API de Anthropic) o `bedrock` (Amazon Bedrock) |
| `ANTHROPIC_API_KEY` | — | Clave para `LLM_PROVIDER=anthropic` |
| `LLM_MODEL` | `claude-opus-5-5` (`anthropic.claude-opus-5-5` en Bedrock) | Modelo a usar |
| `AWS_REGION` | `us-east-1` | Región de Bedrock; las credenciales se toman de la configuración estándar de AWS |
| `WORKER_POLL` | `1` en Docker y `run_all.py` | `1` = el worker consume la cola de forma automática |
| `BIND_HOST` | `127.0.0.1` (`0.0.0.0` en Docker) | Interfaz en la que escucha cada servicio |
| `<SERVICIO>_HOST` | `127.0.0.1` | Host de cada servicio (en Docker, el nombre del contenedor) |

**Ejemplo con Claude real en Docker:**

```bash
# Linux/macOS
INSTALAR_ANTHROPIC=1 LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-ant-... docker compose up -d --build
# Windows PowerShell
$env:INSTALAR_ANTHROPIC="1"; $env:LLM_PROVIDER="anthropic"; $env:ANTHROPIC_API_KEY="sk-ant-..."; docker compose up -d --build
```

`INSTALAR_ANTHROPIC=1` incluye el SDK `anthropic` en la imagen al construirla. Si la llamada falla (sin red, clave inválida), la respuesta se genera en modo simulado y el campo `proveedor_llm` lo indica (`simulado (fallback: ...)`).

### 6.3 Puertos

| Puerto | Servicio |
|---|---|
| 8080 | `api_gateway` + interfaz web |
| 8081 | `orchestrator` |
| 8082 | `tool_service` |
| 8083 | `queue_service` |
| 8084 | `audit_service` |
| 8085 | `worker` |

## 7. Uso de la API

### 7.1 Tokens de demostración

| Token | Rol |
|---|---|
| `demo-usuario-token` | USUARIO_FINAL |
| `demo-sistema-token` | SISTEMA_EXTERNO |
| `demo-aprobador-token` | APROBADOR |
| `demo-admin-token` | ADMINISTRADOR |
| `demo-auditor-token` | AUDITOR |

### 7.2 Ejemplos con curl

```bash
# Chat (RAG + LLM)
curl -s -X POST http://127.0.0.1:8080/v1/chat \
  -H "Authorization: Bearer demo-usuario-token" -H "Idempotency-Key: ej-001" \
  -H "Content-Type: application/json" -d '{"mensaje":"¿Cómo se reprocesa la DLQ?"}'

# Acción crítica → queda pendiente de aprobación (202)
curl -s -X POST http://127.0.0.1:8080/v1/chat \
  -H "Authorization: Bearer demo-usuario-token" -H "Content-Type: application/json" \
  -d '{"mensaje":"eliminar registro del cliente 204"}'

# Aprobarla (use el id_aprobacion devuelto)
curl -s -X POST http://127.0.0.1:8080/v1/aprobaciones/<id_aprobacion>/decidir \
  -H "Authorization: Bearer demo-aprobador-token" -H "Content-Type: application/json" \
  -d '{"decision":"APROBAR"}'

# Tarea asíncrona con 2 fallos simulados (se completa al tercer intento)
curl -s -X POST http://127.0.0.1:8080/v1/tareas \
  -H "Authorization: Bearer demo-sistema-token" -H "Content-Type: application/json" \
  -d '{"evento":"webhook.crm","payload":{"registros":10,"fallar_veces":2}}'

# Consultar la tarea, reprocesar la DLQ y leer la auditoría
curl -s http://127.0.0.1:8080/v1/tareas/<id_tarea> -H "Authorization: Bearer demo-sistema-token"
curl -s -X POST http://127.0.0.1:8080/v1/tareas/reintentar-dlq -H "Authorization: Bearer demo-admin-token"
curl -s "http://127.0.0.1:8080/v1/auditoria?limit=20" -H "Authorization: Bearer demo-auditor-token"
```

En PowerShell use `curl.exe` en lugar de `curl`.

## 8. Archivos de estado

| Archivo | Contenido |
|---|---|
| `data/state.json` | Conversaciones, tareas, aprobaciones y claves de idempotencia del orquestador |
| `data/audit.jsonl` | Bitácora append-only (un evento JSON por línea) |
| `data/conocimiento.json` | Base de conocimiento usada por la búsqueda RAG |
| `data/servicios.pid` | PID de los procesos lanzados por `run_all.py` |

En Docker, estos archivos viven en el volumen `datos`. Para reiniciar el estado: `docker compose down -v`, o en local, borre `data/state.json` y `data/audit.jsonl`.

## 9. Regenerar artefactos

```bash
python scripts/generar_diagramas.py      # docs/*.mmd → docs/*.png (requiere Node.js)
python scripts/generar_docx.py           # Documento_Tecnico.md → Documento_Tecnico.docx
python scripts/captura_consola.py evidencias/resultados_pruebas.txt docs/capturas/pruebas_unittest.png "unittest"
```

Al abrir el `.docx`, Word pregunta si desea actualizar los campos: responda **Sí** para que se genere la tabla de contenido.

## 10. Solución de problemas

| Síntoma | Causa probable | Solución |
|---|---|---|
| `Address already in use` / `[WinError 10048]` | Otro proceso usa los puertos 8080–8085 | Detenga `run_all.py` o `docker compose down`; o cambie los puertos en `config/config.json` |
| Las pruebas fallan con conteos inesperados en la cola | Hay un worker con sondeo automático corriendo (Docker o `run_all.py`) | Detenga Docker y `run_all.py` antes de ejecutar las pruebas |
| `docker compose up` se queda esperando | Un contenedor no supera el healthcheck | `docker compose logs <servicio>` |
| El chat responde 503 | `tool_service` no está disponible o el circuit breaker está abierto | Revise `/v1/salud`; el breaker se cierra solo tras 1 s y una llamada exitosa |
| `proveedor_llm: simulado (fallback: ...)` | Falta el SDK `anthropic`, la clave o la conexión | Instale el SDK y defina `ANTHROPIC_API_KEY` |
| Error 429 | Se superaron 120 solicitudes por minuto con el mismo token | Espere un minuto o use otro token |
