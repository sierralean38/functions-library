import io
import base64
import json
import os
import logging
import uuid
import zipfile
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import oci
from fdk import response
from openai import OpenAI

logger = logging.getLogger()
logger.setLevel(logging.INFO)

try:
    PROJECT_ID  = str(os.environ["PROJECT_ID"]).strip()
    API_KEY     = str(os.environ["OCI_GENAI_API_KEY"]).strip()
    BUCKET_NAME = str(os.environ["BUCKET_NAME"]).strip()
except KeyError as e:
    logger.error("ENV_VAR_MISSING: Variable de entorno requerida no encontrada: %s", str(e))
    raise

REGION        = str(os.environ.get("REGION", "us-chicago-1")).strip()
REGION_BUCKET = str(os.environ.get("REGION_BUCKET", REGION)).strip()
MODEL         = str(os.environ.get("MODEL", "openai.gpt-oss-120b")).strip()
NAMESPACE     = str(os.environ.get("NAMESPACE", "")).strip()
PAR_TTL       = int(os.environ.get("PAR_TTL_SECONDS", "86400"))

DEFAULT_INSTRUCTION = """
Eres un analista de datos experto. Escribe y ejecuta código Python para responder
la solicitud del usuario siguiendo estas reglas estrictamente.

REGLAS DE CÓDIGO:
- Escribe el código más simple y directo posible
- Evita clases, funciones auxiliares y abstracciones innecesarias
- No agregues comentarios extensos ni prints de diagnóstico
- Usa solo las librerías necesarias: pandas, matplotlib, openpyxl, python-docx, reportlab
- Siempre usa matplotlib.use('Agg') antes de importar pyplot
- Siempre cierra las figuras con plt.close() después de guardar
- Nunca uses tight_layout() junto con subplots_adjust() al mismo tiempo

REGLAS DE ARCHIVOS:
- Genera ÚNICAMENTE los archivos que el usuario solicitó explícitamente
- No generes archivos intermedios ni de apoyo (ej: no generes PNG si solo pidieron PDF)
- Guarda siempre los archivos en /mnt/data/
- Para Word: guarda el gráfico como PNG primero, luego insértalo con add_picture()
- Usa savefig() con bbox_inches='tight' y dpi=150 siempre
- Usa savefig() de matplotlib para PDF/PNG en lugar de reportlab cuando sea posible

REGLAS DE DATOS:
- Si necesitas datos de ejemplo, créalos con la mínima cantidad necesaria para ilustrar
- No generes más de 20 filas de datos de ejemplo salvo que el usuario lo pida
- Usa numpy.random con seed fijo para reproducibilidad

REGLAS DE CALIDAD VISUAL:
- Títulos siempre con pad=20 para evitar solapamiento con el contenido
- Etiquetas de datos encima de barras con separación de max(valores)*0.01
- set_ylim(0, max(valores)*1.15) para dar espacio a las etiquetas
- Elimina bordes superiores y derechos con spines['top/right'].set_visible(False)
- Rota etiquetas del eje X con tick_params(axis='x', rotation=45) si son largas
- Usa colores profesionales: azul #1F457C, gris #666666, fondo blanco #FFFFFF

Muestra un resumen conciso de los resultados al finalizar.
""".strip()

try:
    client = OpenAI(
        base_url=f"https://inference.generativeai.{REGION}.oci.oraclecloud.com/openai/v1",
        api_key=API_KEY,
        project=PROJECT_ID
    )
    logger.info("CLIENT_INITIALIZED: region=%s model=%s", REGION, MODEL)
except Exception as e:
    logger.error("CLIENT_INIT_ERROR: %s", str(e))
    raise

try:
    _signer    = oci.auth.signers.get_resource_principals_signer()
    _os_client = oci.object_storage.ObjectStorageClient(config={}, signer=_signer)
    _namespace = _os_client.get_namespace().data if not NAMESPACE else NAMESPACE
    logger.info("OS_CLIENT_INITIALIZED: namespace=%s", _namespace)
except Exception as e:
    logger.error("OS_CLIENT_INIT_ERROR: %s", str(e))
    raise

CONTENT_TYPE_MAP = {
    "pdf":  "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc":  "application/msword",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls":  "application/vnd.ms-excel",
    "csv":  "text/csv",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "ppt":  "application/vnd.ms-powerpoint",
    "png":  "image/png",
    "jpg":  "image/jpeg",
    "jpeg": "image/jpeg",
    "gif":  "image/gif",
    "svg":  "image/svg+xml",
    "txt":  "text/plain",
    "json": "application/json",
    "html": "text/html",
    "xml":  "application/xml",
}

MAGIC_BYTES = {
    b"\x25\x50\x44\x46": "pdf",
    b"\x50\x4B\x03\x04": "zip",
    b"\xD0\xCF\x11\xE0": "xls",
    b"\x89\x50\x4E\x47": "png",
    b"\xFF\xD8\xFF":      "jpg",
    b"\x47\x49\x46":      "gif",
    b"\x3C\x73\x76\x67": "svg",
    b"\x3C\x68\x74\x6D": "html",
}


def _get_content_type(file_name):
    try:
        ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
        return CONTENT_TYPE_MAP.get(ext, "application/octet-stream")
    except Exception:
        return "application/octet-stream"


def _detect_office_format(file_bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
            names = z.namelist()
            if any("word/" in n for n in names):
                return "docx"
            if any("xl/" in n for n in names):
                return "xlsx"
            if any("ppt/" in n for n in names):
                return "pptx"
        return "zip"
    except Exception:
        return "zip"


def _generate_file_name(file_bytes, file_id):
    try:
        header = file_bytes[:8] if len(file_bytes) >= 8 else file_bytes
        ext    = "bin"
        for magic, magic_ext in MAGIC_BYTES.items():
            if header.startswith(magic):
                ext = magic_ext
                break
        if ext == "zip":
            ext = _detect_office_format(file_bytes)
        suffix    = file_id[-8:] if len(file_id) >= 8 else file_id
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        fname     = f"output_{timestamp}_{suffix}.{ext}"
        logger.info("FILE_NAME_GENERATED: %s (id=%s)", fname, file_id)
        return fname
    except Exception as e:
        logger.error("FILE_NAME_GENERATION_ERROR: %s -> %s", file_id, str(e))
        return f"output_{uuid.uuid4().hex[:8]}.bin"


def _download_from_object_storage(object_name, bucket_name=None, namespace=None):
    try:
        ns     = namespace or _namespace
        bucket = bucket_name or BUCKET_NAME
        resp       = _os_client.get_object(
            namespace_name=ns,
            bucket_name=bucket,
            object_name=object_name
        )
        file_bytes = resp.data.content
        fname      = os.path.basename(object_name)
        logger.info("OBJECT_DOWNLOADED: %s (%d bytes)", object_name, len(file_bytes))
        return file_bytes, fname
    except Exception as e:
        logger.exception("DOWNLOAD_ERROR: %s -> %s", object_name, str(e))
        raise


def _upload_to_object_storage(file_bytes, file_name):
    try:
        content_type = _get_content_type(file_name)
        object_name  = f"code-interpreter/{uuid.uuid4().hex[:8]}_{file_name}"
        put_response = _os_client.put_object(
            namespace_name=_namespace,
            bucket_name=BUCKET_NAME,
            object_name=object_name,
            put_object_body=file_bytes,
            content_type=content_type
        )
        logger.info("OBJECT_UPLOADED: %s -> status %s", object_name,
                    getattr(put_response, "status", None))
        try:
            expires_at = datetime.utcnow() + timedelta(seconds=PAR_TTL)
            details = oci.object_storage.models.CreatePreauthenticatedRequestDetails(
                name=f"par-{uuid.uuid4().hex[:8]}",
                access_type="ObjectRead",
                object_name=object_name,
                time_expires=expires_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            )
            par        = _os_client.create_preauthenticated_request(_namespace, BUCKET_NAME, details)
            access_uri = getattr(par.data, "access_uri", None) or getattr(par.data, "accessUri", None)
            if access_uri:
                par_url = f"https://objectstorage.{REGION_BUCKET}.oraclecloud.com{access_uri}"
                logger.info("PAR_CREATED: %s", par_url)
                return par_url
            logger.warning("PAR_NO_URI: PAR creado pero sin access_uri para %s", object_name)
            return None
        except Exception as e:
            logger.exception("PAR_CREATE_ERROR: %s -> %s", object_name, str(e))
            return None
    except Exception as e:
        logger.exception("UPLOAD_ERROR: %s -> %s", file_name, str(e))
        return None


def _retrieve_and_upload(f, container_id):
    content    = client.containers.files.content.retrieve(
        container_id=container_id,
        file_id=f.id
    )
    file_bytes = content.read()
    fname      = _generate_file_name(file_bytes, f.id)
    par_url    = _upload_to_object_storage(file_bytes, fname)
    return fname, par_url, file_bytes


def _error_response(ctx, message, status_code=500):
    logger.error("ERROR_RESPONSE [%d]: %s", status_code, message)
    return response.Response(
        ctx,
        response_data=json.dumps({"error": message}),
        headers={"Content-Type": "application/json"},
        status_code=status_code
    )


def handler(ctx, data: io.BytesIO = None):
    try:
        body = json.loads(data.getvalue())
    except Exception as e:
        logger.error("JSON_PARSE_ERROR: %s", str(e))
        return _error_response(ctx, f"Invalid JSON: {str(e)}", 400)

    prompt            = body.get("prompt")
    agent_instruction = body.get("instruction", "").strip()
    instruction       = DEFAULT_INSTRUCTION + (f"\n\nCONTEXTO ESPECÍFICO DE LA TAREA:\n{agent_instruction}" if agent_instruction else "")
    memory_gb         = body.get("memory_gb", "4g")
    input_objects     = body.get("input_objects", [])
    file_b64          = body.get("file_base64")
    file_name         = body.get("file_name", "data.csv")

    if not prompt:
        logger.error("PROMPT_MISSING: el campo prompt es requerido")
        return _error_response(ctx, "El campo 'prompt' es requerido.", 400)

    file_ids = []

    for obj in input_objects:
        object_name = obj.get("object_name")
        if not object_name:
            logger.warning("INPUT_OBJECT_SKIP: falta object_name en %s", obj)
            continue
        try:
            file_bytes, fname = _download_from_object_storage(
                object_name,
                bucket_name=obj.get("bucket"),
                namespace=obj.get("namespace")
            )
            cf = client.files.create(
                file=(fname, file_bytes, _get_content_type(fname)),
                purpose="assistants"
            )
            file_ids.append(cf.id)
            logger.info("INPUT_FILE_UPLOADED: %s -> %s", fname, cf.id)
        except Exception as e:
            logger.error("INPUT_FILE_ERROR: %s -> %s", object_name, str(e))
            return _error_response(ctx, f"Error descargando '{object_name}': {str(e)}")

    if file_b64:
        try:
            raw = base64.b64decode(file_b64)
            cf  = client.files.create(
                file=(file_name, raw, _get_content_type(file_name)),
                purpose="assistants"
            )
            file_ids.append(cf.id)
            logger.info("INPUT_FILE_B64_UPLOADED: %s -> %s", file_name, cf.id)
        except Exception as e:
            logger.error("FILE_B64_UPLOAD_ERROR: %s", str(e))
            return _error_response(ctx, f"Error subiendo archivo base64: {str(e)}")

    try:
        tools = [{
            "type": "code_interpreter",
            "container": {
                "type":         "auto",
                "memory_limit": memory_gb,
                **({"file_ids": file_ids} if file_ids else {})
            }
        }]
        resp = client.responses.create(
            model=MODEL,
            instructions=instruction,
            input=prompt,
            tools=tools
        )
        logger.info("RESPONSE_OUTPUT: %s", json.dumps(
            [item.model_dump() for item in resp.output], default=str
        ))
    except Exception as e:
        logger.error("RESPONSES_API_ERROR: %s", str(e))
        return _error_response(ctx, f"Error llamando Responses API: {str(e)}")

    container_id = None
    try:
        for item in resp.output:
            if item.type == "code_interpreter_call":
                container_id = item.container_id
                logger.info("CONTAINER_ID: %s", container_id)
                break
        if not container_id:
            logger.info("NO_CONTAINER: la respuesta no incluyó ejecución de código")
    except Exception as e:
        logger.error("CONTAINER_ID_EXTRACT_ERROR: %s", str(e))

    output_files = []
    result_text  = resp.output_text or ""

    if container_id:
        try:
            files_page = client.containers.files.list(container_id=container_id)
            if not files_page.data:
                logger.info("CONTAINER_EMPTY: no se generaron archivos")
            else:
                logger.info("CONTAINER_FILES_COUNT: %d", len(files_page.data))
                with ThreadPoolExecutor(max_workers=4) as executor:
                    futures = {
                        executor.submit(_retrieve_and_upload, f, container_id): f
                        for f in files_page.data
                    }
                    for future in as_completed(futures):
                        try:
                            fname, par_url, file_bytes = future.result()
                            if par_url:
                                output_files.append({"file_name": fname, "url": par_url})
                                result_text += f"\n\n📎 **{fname}**: {par_url}"
                                logger.info("OUTPUT_FILE_READY: %s -> %s", fname, par_url)
                            else:
                                logger.warning("PAR_FALLBACK_BASE64: %s", fname)
                                output_files.append({
                                    "file_name": fname,
                                    "data_b64":  base64.b64encode(file_bytes).decode()
                                })
                        except Exception as e:
                            logger.error("PARALLEL_UPLOAD_ERROR: %s", str(e))
        except Exception as e:
            logger.error("CONTAINER_LIST_ERROR: %s", str(e))

    try:
        return response.Response(
            ctx,
            response_data=json.dumps({
                "result":       result_text,
                "output_files": output_files
            }),
            headers={"Content-Type": "application/json"},
            status_code=200
        )
    except Exception as e:
        logger.error("RESPONSE_SERIALIZE_ERROR: %s", str(e))
        return _error_response(ctx, f"Error serializando respuesta: {str(e)}")
