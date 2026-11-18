# Imagen única para los seis servicios del prototipo; cada contenedor ejecuta uno.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/src BIND_HOST=0.0.0.0
# Opcional: SDK de Anthropic para usar Claude como LLM real (INSTALAR_ANTHROPIC=1).
ARG INSTALAR_ANTHROPIC=0
RUN if [ "$INSTALAR_ANTHROPIC" = "1" ]; then pip install --no-cache-dir anthropic; fi
COPY config/ config/
COPY data/conocimiento.json data/conocimiento.json
COPY src/ src/
RUN useradd --create-home app && chown -R app /app
USER app
# El servicio concreto se elige en docker-compose.yml con "command".
CMD ["python", "src/api_gateway.py"]
