# Presupuesto estimado del proyecto

**Moneda:** pesos colombianos (COP). **Tasa de referencia:** 1 USD ≈ 4.000 COP. **Horizonte:** 3 meses.
El tiempo de desarrollo se valora a una tarifa de referencia y no representa un desembolso. Los costos de AWS son estimaciones de planeación que deben confirmarse con la AWS Pricing Calculator.

## Escenario A · Costo real del proyecto entregado (prototipo)

| Ítem | Detalle | Cantidad | Valor unitario | Subtotal |
|---|---|---:|---:|---:|
| Desarrollo y documentación | 2 integrantes × 60 h | 120 h | $55.000 | $6.600.000 |
| Infraestructura | Equipos personales; Docker Desktop (gratuito para uso personal y educativo) | — | $0 | $0 |
| Software y licencias | Python, Docker, VS Code, Git, GitHub, Mermaid | — | $0 | $0 |
| Modelo de lenguaje | Modo simulado; opcionalmente ~1.000 consultas a Claude para pruebas (~USD 5) | 1 | $20.000 | $20.000 |
| Materiales | Impresión, diagramas y grabación del video | 1 | $150.000 | $150.000 |
| **Subtotal** | | | | **$6.770.000** |
| Contingencia | 10 % | | | $677.000 |
| **Total escenario A** | | | | **$7.447.000** |

Desembolso efectivo: **$170.000** (LLM opcional + materiales). El resto corresponde al valor del tiempo invertido.

## Escenario B · Entorno de desarrollo y pruebas en AWS (3 meses)

| Ítem | Detalle | Mensual | 3 meses |
|---|---|---:|---:|
| Cómputo y borde | API Gateway, Lambda, SQS y Step Functions (mayormente nivel gratuito) + Fargate | $120.000 | $360.000 |
| Bedrock (LLM) | Consumo de tokens en pruebas | $120.000 | $360.000 |
| Datos | DynamoDB bajo demanda, Aurora Serverless v2 mínimo, S3 | $160.000 | $480.000 |
| Búsqueda vectorial | OpenSearch Serverless mínimo, o pgvector en Aurora como alternativa económica | $50.000 | $150.000 |
| Seguridad y monitoreo | WAF, KMS, CloudWatch, X-Ray | $40.000 | $120.000 |
| **Subtotal infraestructura** | | **$490.000** | **$1.470.000** |

| Concepto | Valor |
|---|---:|
| Escenario A sin contingencia (desarrollo, materiales y LLM) | $6.770.000 |
| Infraestructura AWS (3 meses) | $1.470.000 |
| Contingencia 10 % | $824.000 |
| **Total escenario B** | **$9.064.000** |

## Observaciones

- En producción, el costo dominante es el consumo de tokens del LLM, que crece con el número de conversaciones. El modo simulado del prototipo permite desarrollar y probar sin ese costo.
- Antes de desplegar en AWS se deben configurar AWS Budgets con alarmas al 50 %, 80 % y 100 % del presupuesto mensual.
- Si el proyecto se desarrolla de forma individual, el rubro de desarrollo se reduce a la mitad ($3.300.000).
