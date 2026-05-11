# code-interpreter-tool

Oracle Function que recibe un prompt en lenguaje natural y ejecuta código Python con IA para analizar datos y generar archivos (Excel, Word, PDF, imágenes, CSV, etc.).

Los archivos generados se almacenan en OCI Object Storage y se retornan como URLs descargables (Pre-Authenticated Request).

## Variables de entorno

| Variable | Descripción | Requerida |
|---|---|---|
| `PROJECT_ID` | ID del proyecto OCI GenAI | Sí |
| `OCI_GENAI_API_KEY` | API Key de OCI GenAI / OpenAI | Sí |
| `BUCKET_NAME` | Nombre del bucket de salida | Sí |
| `REGION` | Región OCI (default: `us-chicago-1`) | No |
| `MODEL` | Modelo a usar (default: `openai.gpt-oss-120b`) | No |
| `NAMESPACE` | Namespace de Object Storage | No |
| `PAR_TTL_SECONDS` | TTL de los PARs generados en segundos (default: `86400`) | No |

## Endpoint

Ver [`openapi.json`](./openapi.json) para la especificación completa del API.

## Runtime

Docker · Python 3.11

---

> Parte de [functions-library](../README.md) by Diego Sierra Alean
