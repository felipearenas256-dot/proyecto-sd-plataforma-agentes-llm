"""Adaptador del modelo de lenguaje (LLM) del agente.

Tres proveedores, elegidos con la variable de entorno LLM_PROVIDER:

* ``simulado`` (por defecto): genera una respuesta extractiva a partir de los
  documentos recuperados. No necesita red ni credenciales, es determinista y
  permite reproducir las pruebas en cualquier equipo.
* ``anthropic``: llama a Claude mediante la API de Anthropic
  (requiere ``pip install anthropic`` y la variable ANTHROPIC_API_KEY).
* ``bedrock``: llama a Claude en Amazon Bedrock, que es el destino de la
  arquitectura AWS (requiere ``pip install anthropic`` y credenciales AWS).

Si el proveedor real falla (sin red, sin credenciales, rechazo del modelo), el
adaptador degrada al modo simulado y lo indica en el campo ``proveedor``.
Esto es en sí un mecanismo de tolerancia a fallos: la conversación nunca se
cae por una dependencia externa.
"""
from __future__ import annotations

import os

MODELO_ANTHROPIC = os.environ.get("LLM_MODEL", "claude-opus-5-5")
MODELO_BEDROCK = os.environ.get("LLM_MODEL", "anthropic.claude-opus-5-5")
REGION_BEDROCK = os.environ.get("AWS_REGION", "us-east-1")

SISTEMA = (
    "Eres el agente de una plataforma corporativa de automatización. "
    "Responde en español, en máximo cuatro oraciones, usando solo la información "
    "de los documentos entregados. Si los documentos no responden la pregunta, dilo."
)


def _contexto(documentos: list[dict]) -> str:
    return "\n\n".join(f"[{d.get('id')}] {d.get('titulo')}: {d.get('contenido')}" for d in documentos)


def _simulado(consulta: str, documentos: list[dict]) -> str:
    if not documentos:
        return "No encontré documentos relacionados con tu consulta en la base de conocimiento."
    titulos = "; ".join(d.get("titulo", "") for d in documentos)
    primero = documentos[0].get("contenido", "")
    return (f"Según la base de conocimiento ({titulos}): {primero} "
            f"[Respuesta generada en modo simulado a partir de {len(documentos)} documento(s).]")


def _texto(respuesta) -> str:
    if getattr(respuesta, "stop_reason", None) == "refusal":
        raise RuntimeError("El modelo rechazó la solicitud")
    return "".join(b.text for b in respuesta.content if b.type == "text").strip()


def _anthropic(consulta: str, documentos: list[dict]) -> str:
    import anthropic

    cliente = anthropic.Anthropic(timeout=20.0, max_retries=2)
    respuesta = cliente.beta.messages.create(
        model=MODELO_ANTHROPIC,
        max_tokens=1024,
        system=SISTEMA,
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": f"Documentos:\n{_contexto(documentos)}\n\nPregunta: {consulta}"}],
    )
    return _texto(respuesta)


def _bedrock(consulta: str, documentos: list[dict]) -> str:
    from anthropic import AnthropicBedrockMantle

    cliente = AnthropicBedrockMantle(aws_region=REGION_BEDROCK, timeout=20.0, max_retries=2)
    respuesta = cliente.messages.create(
        model=MODELO_BEDROCK,
        max_tokens=1024,
        system=SISTEMA,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": f"Documentos:\n{_contexto(documentos)}\n\nPregunta: {consulta}"}],
    )
    return _texto(respuesta)


def generar_respuesta(consulta: str, documentos: list[dict]) -> tuple[str, str]:
    """Devuelve (texto, proveedor_usado)."""
    proveedor = os.environ.get("LLM_PROVIDER", "simulado").lower()
    if proveedor in {"anthropic", "bedrock"}:
        try:
            texto = (_anthropic if proveedor == "anthropic" else _bedrock)(consulta, documentos)
            if texto:
                return texto, proveedor
        except Exception as exc:  # degradación controlada
            return _simulado(consulta, documentos), f"simulado (fallback: {type(exc).__name__})"
    return _simulado(consulta, documentos), "simulado"
