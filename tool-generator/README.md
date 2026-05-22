# OCI Function Generator Tool

OCI Function que genera paquetes completos de proyectos OCI Function a partir de un requerimiento en lenguaje natural. Sigue el patrón `code-interpreter` del repositorio de referencia: llama al Code Interpreter de OCI Generative AI, recupera los archivos generados del contenedor sandbox, los almacena en OCI Object Storage y retorna URLs descargables.

## Propósito

Usa esta Function como herramienta REST de Agent Factory cuando un agente necesite producir un paquete desplegable de OCI Function para un caso de uso definido por el usuario.

El paquete generado incluye código fuente, `func.yaml`, `Dockerfile`, `requirements.txt`, README, OpenAPI JSON, ejemplos de payload, tests, manifiesto, reporte de validación y un ZIP.

La Function valida el ZIP generado antes de retornar éxito. Un paquete es rechazado si no contiene estos archivos obligatorios:

```text
Dockerfile
README.md
func.py
func.yaml
manifest.json
openapi.json
requirements.txt
samples/request.json
validation_report.md
```

Cuando `include_tests` es `true`, el ZIP también debe contener al menos un archivo Python bajo `tests/`.

## Endpoint

`POST /generate-oci-function`

Importa `openapi.json` como fuente de datos REST API en Agent Factory o expónla a través de API Gateway.

## Inputs

| Nombre | Tipo | Requerido | Descripción | Ejemplo |
|---|---|---:|---|---|
| `requirement` | string | sí | Descripción en lenguaje natural de la OCI Function objetivo. | `Crea una función que valide facturas...` |
| `function_name` | string | no | Nombre preferido para la función generada. Se normaliza a slug seguro. | `invoice-validator` |
| `operation_id` | string | no | Operation id lower camel case para OpenAPI de la función generada. | `validateInvoice` |
| `package_name` | string | no | Nombre base de la carpeta y el ZIP. | `invoice-validator` |
| `runtime` | string | no | Actualmente `python3.11`. | `python3.11` |
| `memory_mb` | integer | no | Memoria de la función generada, de 128 a 2048. | `512` |
| `timeout_seconds` | integer | no | Timeout de la función generada, de 30 a 300. | `120` |
| `expose_as_rest_tool` | boolean | no | Si la función generada debe incluir guía OpenAPI/API Gateway. | `true` |
| `include_tests` | boolean | no | Si se deben generar tests. | `true` |
| `include_openapi` | boolean | no | Si se debe generar OpenAPI JSON. | `true` |
| `code_interpreter_memory` | string | no | Memoria del contenedor Code Interpreter. Opciones: `1g`, `4g`, `16g`, `64g`. | `4g` |
| `output_language` | string | no | Idioma de la documentación del paquete generado: `es` o `en`. | `es` |
| `oci_integrations` | array | no | Servicios OCI que debe usar la función generada. | `["object_storage"]` |
| `environment_variables` | array | no | Variables de entorno esperadas por la función generada. | ver sample |
| `reference_objects` | array | no | Archivos en Object Storage para pasar al Code Interpreter como contexto de referencia. | ver OpenAPI |
| `additional_instructions` | string | no | Restricciones adicionales para la generación. | `Usar README en español.` |

Ver [samples/request.json](samples/request.json).

## Respuesta exitosa

```json
{
  "status": "succeeded",
  "request_id": "a1b2...",
  "function_name": "customer-usage-analyzer",
  "operation_id": "analyzeCustomerUsage",
  "package_name": "customer-usage-analyzer",
  "result": "Resumen del paquete generado...",
  "generation_summary": {
    "status": "succeeded",
    "mandatory_files_present": true,
    "validation_results": []
  },
  "package": {
    "file_name": "customer-usage-analyzer.zip",
    "object_name": "generated-functions/customer-usage-analyzer/...",
    "content_type": "application/zip",
    "size_bytes": 12345,
    "url": "https://objectstorage.<region>.oraclecloud.com/p/...",
    "url_expires_at": "2026-05-23T14:00:00Z"
  },
  "artifacts": [],
  "warnings": []
}
```

## Respuesta de error

```json
{
  "status": "failed",
  "request_id": "a1b2...",
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "requirement is required and must be at least 20 characters"
  }
}
```

## Variables de entorno

| Nombre | Requerida | Default | Sensible | Descripción |
|---|---:|---|---:|---|
| `LOG_LEVEL` | no | `INFO` | no | Nivel de log Python. |
| `OCI_GENAI_REGION` | no | `us-chicago-1` | no | Región del endpoint de OCI Generative AI. |
| `OCI_GENAI_BASE_URL` | no | derivada | no | Override opcional del endpoint compatible con OpenAI. |
| `OCI_GENAI_MODEL` | no | `openai.gpt-oss-120b` | no | Modelo usado por Responses API. |
| `OCI_GENAI_PROJECT_ID` | sí | ninguno | no | Identificador del proyecto OCI Generative AI. |
| `OCI_GENAI_API_KEY` | sí | ninguno | sí | API key/token para el endpoint compatible con OpenAI. Guardar como config secreta. |
| `OBJECT_STORAGE_BUCKET` | sí | ninguno | no | Bucket para los artefactos generados. |
| `OBJECT_STORAGE_NAMESPACE` | no | descubierto | no | Namespace de Object Storage. Si se omite, la Function llama a `get_namespace`. |
| `OBJECT_STORAGE_REGION` | no | `OCI_GENAI_REGION` | no | Región para construir URLs de descarga PAR. |
| `OUTPUT_PREFIX` | no | `generated-functions` | no | Prefijo del nombre de objeto para artefactos generados. |
| `PAR_TTL_SECONDS` | no | `86400` | no | Tiempo de vida de la URL PAR, de 60 segundos a 7 días. |
| `MAX_REFERENCE_OBJECT_BYTES` | no | `26214400` | no | Tamaño máximo por objeto de referencia. |

## Logs de observabilidad

Cada invocación escribe logs paso a paso con un `request_id` generado. El formato es:

```text
STEP request_id=<id> step=<STEP_NAME> message=<mensaje> details=<JSON seguro>
```

Pasos relevantes:

| Step | Significado |
|---|---|
| `INVOCATION_RECEIVED` | Invocación recibida. |
| `REQUEST_PARSE_COMPLETE` | JSON de entrada parseado. |
| `VALIDATION_COMPLETE` | Input normalizado y validado. |
| `GENAI_CLIENT_READY` | Región, modelo, memoria y cuenta de referencias resuelta. |
| `CODE_INTERPRETER_START` | Llamada a Responses API con Code Interpreter iniciada. |
| `CODE_INTERPRETER_COMPLETE` | Llamada a Responses API completada. |
| `CONTAINER_LIST_COMPLETE` | Archivos generados listados desde el contenedor. |
| `ARTIFACT_UPLOAD_COMPLETE` | Artefacto generado subido a Object Storage y acceso PAR preparado. |
| `INVOCATION_SUCCEEDED` | Función completada exitosamente. |
| `INVOCATION_FAILED` | Función falló con error interno. |

Los logs evitan imprimir prompts, secretos, tokens, API keys y URLs de descarga completas.

## Despliegue

Prerrequisitos:

- Fn CLI configurado para OCI Functions.
- Docker o runtime de contenedor compatible.
- Aplicación de OCI Functions existente.
- Bucket de Object Storage para artefactos generados.
- API Gateway si se llama desde Agent Factory.

Comandos:

```powershell
fn -v deploy --app <functions-app-name>
fn config function <functions-app-name> oci-function-generator-tool OCI_GENAI_PROJECT_ID "<project-id>"
fn config function <functions-app-name> oci-function-generator-tool OCI_GENAI_API_KEY "<secret-value>"
fn config function <functions-app-name> oci-function-generator-tool OBJECT_STORAGE_BUCKET "<bucket-name>"
fn config function <functions-app-name> oci-function-generator-tool OBJECT_STORAGE_REGION "<region>"
```

Para API Gateway, crea una ruta:

- Método: `POST`
- Path: `/generate-oci-function`
- Backend: `ORACLE_FUNCTIONS_BACKEND`
- Target: OCID de esta Function
- Timeout: 300 segundos
- Autenticación: API key, bearer, OAuth2 o acceso privado según tu modelo de seguridad

Luego importa `openapi.json` en Agent Factory como fuente de datos REST API.

## IAM

Grupo dinámico:

```text
ALL {resource.type = 'fnfunc', resource.compartment.id = '<compartment-ocid>'}
```

Política de mínimo privilegio:

```text
allow dynamic-group <dynamic-group-name> to read buckets in compartment <compartment-name>
allow dynamic-group <dynamic-group-name> to manage objects in compartment <compartment-name> where target.bucket.name = '<output-bucket-name>'
```

Si `OBJECT_STORAGE_NAMESPACE` se omite:

```text
allow dynamic-group <dynamic-group-name> to inspect objectstorage-namespaces in tenancy
```

## Verificación local

Estas verificaciones no invocan servicios OCI:

```powershell
python -m py_compile .\func.py
python -m pytest .\tests
```

## Revisión de seguridad

- Sin secretos, OCIDs, endpoints, nombres de cliente ni valores específicos de tenancy en el código.
- Object Storage usa resource principals.
- La API key de GenAI es configuración de runtime y debe tratarse como secreto.
- Los inputs se validan para campos requeridos, tipos, longitudes, valores enum y traversal de rutas.
- Las respuestas son JSON determinístico.
- Los errores no exponen stack traces.
- Los paquetes generados se retornan como artefactos de Object Storage para evitar superar el límite de payload de la función.
- Sin `eval`, `exec`, construcción de comandos shell, pickle ni deserialización insegura en esta Function.
