import base64
import hashlib
import io
import json
import logging
import mimetypes
import os
import re
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple

import oci
from fdk import response
from openai import OpenAI


LOGGER = logging.getLogger()
LOGGER.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())

SUPPORTED_CONTAINER_MEMORY = {"1g", "4g", "16g", "64g"}
SUPPORTED_RUNTIMES = {"python3.11"}
MAX_REQUIREMENT_CHARS = 8000
MAX_ADDITIONAL_INSTRUCTIONS_CHARS = 4000
MAX_REFERENCE_OBJECTS = 5

CONTENT_TYPE_BY_EXT = {
    "json": "application/json",
    "zip": "application/zip",
    "py": "text/x-python",
    "txt": "text/plain",
    "md": "text/markdown",
    "yaml": "application/yaml",
    "yml": "application/yaml",
    "toml": "application/toml",
    "csv": "text/csv",
    "pdf": "application/pdf",
}

MAGIC_EXTENSIONS = {
    b"\x50\x4b\x03\x04": "zip",
    b"\x25\x50\x44\x46": "pdf",
    b"\x7b": "json",
}

MANDATORY_PACKAGE_FILES = {
    "Dockerfile",
    "README.md",
    "func.py",
    "func.yaml",
    "manifest.json",
    "openapi.json",
    "requirements.txt",
    "samples/request.json",
    "validation_report.md",
}

BASE_GENERATOR_INSTRUCTIONS = """
You are an expert Oracle Cloud Infrastructure architect and Python OCI Functions
engineer. Use the python tool to create a complete deployable OCI Function
project from the user's functional requirement.

You must generate files in /mnt/data and validate them locally in the sandbox.
These are non-negotiable acceptance criteria. If any criterion fails, fix the
package before creating the ZIP and before returning the final response.
The generated project must be production-oriented but generic:
- Python OCI Function using FDK handler(ctx, data)
- deterministic application/json responses with status codes for every success
  and error path
- strict input validation and safe error payloads
- no hard-coded tenancy values, OCIDs, endpoints, credentials, or customer data
- OCI SDK calls must use resource principals in deployed function code
- environment-specific values must be placeholders in func.yaml and README
- include every mandatory file exactly: README.md, func.py, requirements.txt,
  Dockerfile, func.yaml, openapi.json, manifest.json, validation_report.md, and
  samples/request.json
- include focused tests when requested
- keep the function single-purpose; do not create a do-everything function

Agent Factory REST API compatibility rules:
- OpenAPI must be JSON, not YAML
- use OpenAPI 3.0.x
- use POST application/json for structured tool calls
- operationId must be lower camel case
- request and response schemas must match the implemented function

OCI Functions constraints:
- respect the 6 MB function request and response payload limit
- large generated artifacts must be packaged as files rather than inline JSON
- document Fn CLI and API Gateway route steps with placeholders

Code Interpreter constraints:
- assume the sandbox has no external network access
- do not pip install packages
- create files with Python standard library only
- run py_compile and JSON validation on generated artifacts when possible
- inspect the final ZIP with zipfile and confirm every mandatory file exists

Output requirements:
- create a directory /mnt/data/<package_name>
- create /mnt/data/<package_name>.zip containing the generated project
- create /mnt/data/<package_name>/manifest.json with generated file list,
  assumptions, validations, and any warnings
- create /mnt/data/<package_name>/validation_report.md
- final assistant text must be valid JSON only, with no markdown and no prose
  outside the JSON object. Use this shape:
  {
    "status": "succeeded",
    "package_name": "<package_name>",
    "mandatory_files_present": true,
    "generated_files": ["..."],
    "validation_results": ["..."],
    "warnings": ["..."]
  }
""".strip()


def _json_response(ctx, body: Dict[str, Any], status_code: int = 200):
    return response.Response(
        ctx,
        response_data=json.dumps(body, ensure_ascii=False, separators=(",", ":")),
        headers={"Content-Type": "application/json"},
        status_code=status_code,
    )


def _error_response(
    ctx,
    code: str,
    message: str,
    status_code: int,
    request_id: Optional[str] = None,
):
    payload: Dict[str, Any] = {
        "status": "failed",
        "error": {"code": code, "message": message},
    }
    if request_id:
        payload["request_id"] = request_id
    return _json_response(ctx, payload, status_code)


def _log_step(request_id: str, step: str, message: str, **fields: Any) -> None:
    safe_fields = {
        key: value
        for key, value in fields.items()
        if value is not None and key.lower() not in {"api_key", "token", "secret", "url"}
    }
    LOGGER.info(
        "STEP request_id=%s step=%s message=%s details=%s",
        request_id,
        step,
        message,
        json.dumps(safe_fields, ensure_ascii=False, default=str, sort_keys=True),
    )


def _read_json(data: io.BytesIO) -> Dict[str, Any]:
    raw = data.getvalue() if data else b""
    if not raw:
        raise ValueError("Request body is required and must be a JSON object")
    if len(raw) > 6 * 1024 * 1024:
        raise ValueError("Request body must be <= 6 MB for OCI Functions")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Request body must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


def _env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return str(value or "").strip()


def _secret_fingerprint(value: str) -> str:
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _config_presence() -> Dict[str, Any]:
    api_key = _env("OCI_GENAI_API_KEY")
    project_id = _env("OCI_GENAI_PROJECT_ID")
    base_url = _env("OCI_GENAI_BASE_URL")
    return {
        "api_key_present": bool(api_key),
        "api_key_length": len(api_key),
        "api_key_fingerprint": _secret_fingerprint(api_key),
        "project_id_present": bool(project_id),
        "project_id_length": len(project_id),
        "project_id_prefix": project_id[:12] if project_id else "",
        "base_url_override_present": bool(base_url),
    }


def _validate_runtime_config() -> None:
    checks = {
        "OCI_GENAI_PROJECT_ID": _env("OCI_GENAI_PROJECT_ID"),
        "OCI_GENAI_API_KEY": _env("OCI_GENAI_API_KEY"),
        "OBJECT_STORAGE_BUCKET": _env("OBJECT_STORAGE_BUCKET"),
    }
    for name, value in checks.items():
        if not value:
            raise ValueError(f"{name} is required in the function configuration")
        if value.startswith("<") and value.endswith(">"):
            raise ValueError(f"{name} still contains a placeholder value")


def _positive_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    value = _env(name, str(default))
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return parsed


@lru_cache(maxsize=1)
def _genai_client() -> OpenAI:
    region = _env("OCI_GENAI_REGION", _env("REGION", "us-chicago-1"))
    base_url = _env(
        "OCI_GENAI_BASE_URL",
        f"https://inference.generativeai.{region}.oci.oraclecloud.com/openai/v1",
    )
    return OpenAI(
        base_url=base_url,
        api_key=_env("OCI_GENAI_API_KEY", required=True),
        project=_env("OCI_GENAI_PROJECT_ID", required=True),
    )


@lru_cache(maxsize=1)
def _signer():
    return oci.auth.signers.get_resource_principals_signer()


@lru_cache(maxsize=1)
def _object_storage_client():
    return oci.object_storage.ObjectStorageClient(config={}, signer=_signer())


def _object_storage_base_url() -> str:
    endpoint = getattr(getattr(_object_storage_client(), "base_client", None), "endpoint", "")
    endpoint = str(endpoint or "").rstrip("/")
    if endpoint:
        return endpoint
    region = _env("OBJECT_STORAGE_REGION", _env("OCI_GENAI_REGION", "us-chicago-1"))
    return f"https://objectstorage.{region}.oraclecloud.com"


@lru_cache(maxsize=1)
def _namespace() -> str:
    configured = _env("OBJECT_STORAGE_NAMESPACE")
    if configured:
        return configured
    return _object_storage_client().get_namespace().data


def _slug(value: str, fallback: str = "generated-function") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9-]+", "-", value.strip().lower())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    if not cleaned:
        cleaned = fallback
    return cleaned[:64].strip("-") or fallback


def _lower_camel(value: str, fallback: str = "generateFunction") -> str:
    tokens = re.split(r"[^a-zA-Z0-9]+", value.strip())
    tokens = [token for token in tokens if token]
    if not tokens:
        return fallback
    first = tokens[0][0].lower() + tokens[0][1:]
    rest = [token[0].upper() + token[1:] for token in tokens[1:]]
    candidate = "".join([first] + rest)
    if not re.match(r"^[a-z][a-zA-Z0-9]{2,63}$", candidate):
        return fallback
    return candidate


def _validate_object_name(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("reference_objects[].object_name is required")
    if len(value) > 1024:
        raise ValueError("Object names must be <= 1024 characters")
    parts = value.replace("\\", "/").split("/")
    if value.startswith("/") or any(part == ".." for part in parts):
        raise ValueError("Object names must be relative keys without path traversal")


def _validate_bucket(value: Optional[str]) -> Optional[str]:
    if value is None or value == "":
        return None
    value = str(value).strip()
    if not re.match(r"^[A-Za-z0-9._-]{1,256}$", value):
        raise ValueError("Bucket names may only contain letters, digits, dot, dash, and underscore")
    return value


def _validate_reference_objects(items: Any) -> List[Dict[str, str]]:
    if items in (None, ""):
        return []
    if not isinstance(items, list):
        raise ValueError("reference_objects must be an array")
    if len(items) > MAX_REFERENCE_OBJECTS:
        raise ValueError(f"reference_objects supports at most {MAX_REFERENCE_OBJECTS} files")

    normalized: List[Dict[str, str]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"reference_objects[{index}] must be an object")
        object_name = str(item.get("object_name", "")).strip()
        _validate_object_name(object_name)
        normalized.append(
            {
                "object_name": object_name,
                "bucket": _validate_bucket(item.get("bucket")) or _env("OBJECT_STORAGE_BUCKET", required=True),
                "namespace": str(item.get("namespace") or _namespace()).strip(),
            }
        )
    return normalized


def _validate_env_vars(items: Any) -> List[Dict[str, Any]]:
    if items in (None, ""):
        return []
    if not isinstance(items, list):
        raise ValueError("environment_variables must be an array")
    if len(items) > 30:
        raise ValueError("environment_variables supports at most 30 entries")

    normalized = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"environment_variables[{index}] must be an object")
        name = str(item.get("name", "")).strip().upper()
        if not re.match(r"^[A-Z][A-Z0-9_]{1,63}$", name):
            raise ValueError(f"environment_variables[{index}].name is invalid")
        normalized.append(
            {
                "name": name,
                "description": str(item.get("description", "")).strip()[:500],
                "required": bool(item.get("required", True)),
                "sensitive": bool(item.get("sensitive", False)),
            }
        )
    return normalized


def _validate_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    requirement = str(payload.get("requirement", "")).strip()
    if len(requirement) < 20:
        raise ValueError("requirement is required and must be at least 20 characters")
    if len(requirement) > MAX_REQUIREMENT_CHARS:
        raise ValueError(f"requirement must be <= {MAX_REQUIREMENT_CHARS} characters")

    function_name = _slug(str(payload.get("function_name") or requirement[:80]))
    operation_id = _lower_camel(str(payload.get("operation_id") or function_name), "invokeGeneratedFunction")
    package_name = _slug(str(payload.get("package_name") or function_name), "generated-function")

    runtime = str(payload.get("runtime", "python3.11")).strip()
    if runtime not in SUPPORTED_RUNTIMES:
        raise ValueError(f"runtime must be one of {sorted(SUPPORTED_RUNTIMES)}")

    try:
        memory_mb = int(payload.get("memory_mb", 512))
    except (TypeError, ValueError) as exc:
        raise ValueError("memory_mb must be an integer") from exc
    if memory_mb < 128 or memory_mb > 2048:
        raise ValueError("memory_mb must be between 128 and 2048")

    try:
        timeout_seconds = int(payload.get("timeout_seconds", 120))
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be an integer") from exc
    if timeout_seconds < 30 or timeout_seconds > 300:
        raise ValueError("timeout_seconds must be between 30 and 300")

    code_interpreter_memory = str(payload.get("code_interpreter_memory", "4g")).strip().lower()
    if code_interpreter_memory not in SUPPORTED_CONTAINER_MEMORY:
        raise ValueError(f"code_interpreter_memory must be one of {sorted(SUPPORTED_CONTAINER_MEMORY)}")

    additional = str(payload.get("additional_instructions", "")).strip()
    if len(additional) > MAX_ADDITIONAL_INSTRUCTIONS_CHARS:
        raise ValueError(
            f"additional_instructions must be <= {MAX_ADDITIONAL_INSTRUCTIONS_CHARS} characters"
        )

    output_language = str(payload.get("output_language", "es")).strip().lower()
    if output_language not in {"es", "en"}:
        raise ValueError("output_language must be 'es' or 'en'")

    integrations = payload.get("oci_integrations", [])
    if integrations in (None, ""):
        integrations = []
    if not isinstance(integrations, list) or not all(isinstance(x, str) for x in integrations):
        raise ValueError("oci_integrations must be an array of strings")

    return {
        "requirement": requirement,
        "function_name": function_name,
        "operation_id": operation_id,
        "package_name": package_name,
        "runtime": runtime,
        "memory_mb": memory_mb,
        "timeout_seconds": timeout_seconds,
        "expose_as_rest_tool": bool(payload.get("expose_as_rest_tool", True)),
        "include_tests": bool(payload.get("include_tests", True)),
        "include_openapi": bool(payload.get("include_openapi", True)),
        "code_interpreter_memory": code_interpreter_memory,
        "output_language": output_language,
        "oci_integrations": sorted({x.strip().lower() for x in integrations if x.strip()}),
        "environment_variables": _validate_env_vars(payload.get("environment_variables")),
        "reference_objects": _validate_reference_objects(payload.get("reference_objects")),
        "additional_instructions": additional,
    }


def _content_type(file_name: str, file_bytes: Optional[bytes] = None) -> str:
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if ext in CONTENT_TYPE_BY_EXT:
        return CONTENT_TYPE_BY_EXT[ext]
    guessed = mimetypes.guess_type(file_name)[0]
    if guessed:
        return guessed
    if file_bytes:
        for magic, magic_ext in MAGIC_EXTENSIONS.items():
            if file_bytes.startswith(magic):
                return CONTENT_TYPE_BY_EXT.get(magic_ext, "application/octet-stream")
    return "application/octet-stream"


def _detect_office_or_zip(file_bytes: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
            names = archive.namelist()
            if any(name.startswith("word/") for name in names):
                return "docx"
            if any(name.startswith("xl/") for name in names):
                return "xlsx"
            if any(name.startswith("ppt/") for name in names):
                return "pptx"
    except zipfile.BadZipFile:
        return "bin"
    return "zip"


def _file_name_from_container_item(file_item: Any, file_bytes: bytes, package_name: str) -> str:
    for attr in ("filename", "path", "name"):
        value = getattr(file_item, attr, None)
        if isinstance(value, str) and value.strip():
            base_name = os.path.basename(value.strip())
            if base_name:
                return _safe_file_name(base_name)

    ext = "bin"
    for magic, magic_ext in MAGIC_EXTENSIONS.items():
        if file_bytes.startswith(magic):
            ext = _detect_office_or_zip(file_bytes) if magic_ext == "zip" else magic_ext
            break
    suffix = str(getattr(file_item, "id", uuid.uuid4().hex))[-8:]
    return f"{package_name}_{suffix}.{ext}"


def _normalized_zip_names(zip_names: Iterable[str]) -> List[str]:
    normalized = []
    for name in zip_names:
        clean = name.replace("\\", "/").strip("/")
        if not clean or clean.endswith("/"):
            continue
        parts = clean.split("/")
        if len(parts) > 1:
            normalized.append("/".join(parts[1:]))
        normalized.append(clean)
    return sorted(set(normalized))


def _validate_generated_package_zip(
    file_name: str,
    file_bytes: bytes,
    normalized_request: Dict[str, Any],
    request_id: str,
) -> Dict[str, Any]:
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
            zip_names = archive.namelist()
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Generated package {file_name} is not a valid ZIP file") from exc

    normalized_names = _normalized_zip_names(zip_names)
    normalized_set = set(normalized_names)
    missing = sorted(name for name in MANDATORY_PACKAGE_FILES if name not in normalized_set)

    if normalized_request.get("include_tests", True):
        has_test = any(name.startswith("tests/") and name.endswith(".py") for name in normalized_set)
        if not has_test:
            missing.append("tests/*.py")

    if missing:
        _log_step(
            request_id,
            "PACKAGE_VALIDATION_FAILED",
            "Generated package ZIP is missing mandatory files",
            file_name=file_name,
            missing_files=missing,
            file_count=len(normalized_names),
        )
        raise ValueError(
            "Generated package is incomplete. Missing mandatory files: "
            + ", ".join(missing)
        )

    validation = {
        "mandatory_files_present": True,
        "file_count": len(normalized_names),
        "mandatory_files": sorted(MANDATORY_PACKAGE_FILES),
    }
    _log_step(
        request_id,
        "PACKAGE_VALIDATION_COMPLETE",
        "Generated package ZIP contains all mandatory files",
        file_name=file_name,
        file_count=len(normalized_names),
    )
    return validation


def _safe_file_name(value: str) -> str:
    base = os.path.basename(value.replace("\\", "/"))
    base = re.sub(r"[^a-zA-Z0-9._-]+", "_", base).strip("._")
    return base[:180] or f"artifact_{uuid.uuid4().hex[:8]}.bin"


def _download_reference_object(item: Dict[str, str]) -> Tuple[bytes, str, str]:
    response_obj = _object_storage_client().get_object(
        namespace_name=item["namespace"],
        bucket_name=item["bucket"],
        object_name=item["object_name"],
    )
    content = response_obj.data.content
    max_bytes = _positive_int_env(
        "MAX_REFERENCE_OBJECT_BYTES",
        25 * 1024 * 1024,
        1,
        100 * 1024 * 1024,
    )
    if len(content) > max_bytes:
        raise ValueError(
            f"Reference object {item['object_name']} exceeds MAX_REFERENCE_OBJECT_BYTES"
        )
    file_name = _safe_file_name(os.path.basename(item["object_name"]) or "reference.bin")
    return content, file_name, _content_type(file_name, content)


def _upload_references_to_genai(
    reference_objects: Iterable[Dict[str, str]],
    request_id: str,
) -> List[str]:
    file_ids = []
    client = _genai_client()
    reference_list = list(reference_objects)
    _log_step(
        request_id,
        "REFERENCE_UPLOAD_START",
        "Uploading reference objects to GenAI Files API",
        reference_count=len(reference_list),
    )
    for index, item in enumerate(reference_list, start=1):
        content, file_name, content_type = _download_reference_object(item)
        _log_step(
            request_id,
            "REFERENCE_DOWNLOADED",
            "Reference object downloaded from Object Storage",
            index=index,
            file_name=file_name,
            content_type=content_type,
            size_bytes=len(content),
        )
        created = client.files.create(
            file=(file_name, content, content_type),
            purpose="assistants",
        )
        file_ids.append(created.id)
        _log_step(
            request_id,
            "REFERENCE_UPLOADED",
            "Reference object uploaded to GenAI Files API",
            index=index,
            file_name=file_name,
        )
    _log_step(
        request_id,
        "REFERENCE_UPLOAD_COMPLETE",
        "Reference upload stage completed",
        uploaded_count=len(file_ids),
    )
    return file_ids


def _build_prompt(normalized: Dict[str, Any]) -> str:
    generation_contract = {
        "function_name": normalized["function_name"],
        "operation_id": normalized["operation_id"],
        "package_name": normalized["package_name"],
        "runtime": normalized["runtime"],
        "memory_mb": normalized["memory_mb"],
        "timeout_seconds": normalized["timeout_seconds"],
        "expose_as_rest_tool": normalized["expose_as_rest_tool"],
        "include_tests": normalized["include_tests"],
        "include_openapi": normalized["include_openapi"],
        "oci_integrations": normalized["oci_integrations"],
        "environment_variables": normalized["environment_variables"],
        "output_language": normalized["output_language"],
    }
    return (
        "Create a complete OCI Function project from this request.\n\n"
        "Normalized generation contract:\n"
        f"{json.dumps(generation_contract, indent=2, sort_keys=True)}\n\n"
        "User requirement:\n"
        f"{normalized['requirement']}\n\n"
        "Additional instructions:\n"
        f"{normalized['additional_instructions'] or 'None'}\n\n"
        "Generation steps you must perform with the python tool:\n"
        f"1. Create /mnt/data/{normalized['package_name']}.\n"
        "2. Write all project files in that directory.\n"
        "3. Validate generated JSON files with json.loads.\n"
        "4. Validate generated Python files with py_compile.\n"
        "5. Create validation_report.md and manifest.json.\n"
        f"6. Zip the directory as /mnt/data/{normalized['package_name']}.zip.\n"
        "7. Return a concise summary with validation results and assumptions.\n"
    )


def _extract_container_id(resp_obj: Any) -> Optional[str]:
    for item in getattr(resp_obj, "output", []) or []:
        if getattr(item, "type", None) == "code_interpreter_call":
            return getattr(item, "container_id", None)
    return None


def _create_par_url(object_name: str, expires_at: datetime, request_id: str) -> Optional[str]:
    details = oci.object_storage.models.CreatePreauthenticatedRequestDetails(
        name=f"function-generator-{uuid.uuid4().hex[:12]}",
        access_type="ObjectRead",
        object_name=object_name,
        time_expires=expires_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    par = _object_storage_client().create_preauthenticated_request(
        _namespace(),
        _env("OBJECT_STORAGE_BUCKET", required=True),
        details,
    )
    access_uri = getattr(par.data, "access_uri", None) or getattr(par.data, "accessUri", None)
    if not access_uri:
        _log_step(
            request_id,
            "PAR_CREATE_NO_URI",
            "Pre-authenticated request was created without an access URI",
            object_name=object_name,
        )
        return None
    base_url = _object_storage_base_url()
    _log_step(
        request_id,
        "PAR_CREATE_COMPLETE",
        "Pre-authenticated request created",
        object_name=object_name,
        object_storage_base_url=base_url,
        access_uri_prefix=access_uri[:24],
    )
    return f"{base_url}{access_uri}"


def _upload_generated_artifact(
    file_name: str,
    file_bytes: bytes,
    package_name: str,
    request_id: str,
) -> Dict[str, Any]:
    bucket = _env("OBJECT_STORAGE_BUCKET", required=True)
    prefix = _env("OUTPUT_PREFIX", "generated-functions").strip("/ ") or "generated-functions"
    object_name = f"{prefix}/{package_name}/{uuid.uuid4().hex[:10]}_{_safe_file_name(file_name)}"
    content_type = _content_type(file_name, file_bytes)

    _log_step(
        request_id,
        "ARTIFACT_UPLOAD_START",
        "Uploading generated artifact to Object Storage",
        file_name=file_name,
        object_name=object_name,
        content_type=content_type,
        size_bytes=len(file_bytes),
    )
    _object_storage_client().put_object(
        namespace_name=_namespace(),
        bucket_name=bucket,
        object_name=object_name,
        put_object_body=file_bytes,
        content_type=content_type,
        if_none_match="*",
    )

    ttl_seconds = _positive_int_env("PAR_TTL_SECONDS", 86400, 60, 7 * 24 * 60 * 60)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    par_url = _create_par_url(object_name, expires_at, request_id)
    _log_step(
        request_id,
        "ARTIFACT_UPLOAD_COMPLETE",
        "Generated artifact uploaded and download access prepared",
        file_name=file_name,
        object_name=object_name,
        par_created=bool(par_url),
        url_expires_at=expires_at.isoformat().replace("+00:00", "Z"),
    )

    artifact: Dict[str, Any] = {
        "file_name": file_name,
        "object_name": object_name,
        "content_type": content_type,
        "size_bytes": len(file_bytes),
        "url_expires_at": expires_at.isoformat().replace("+00:00", "Z"),
    }
    if par_url:
        artifact["url"] = par_url
    else:
        artifact["data_base64"] = base64.b64encode(file_bytes).decode("ascii")
    return artifact


def _retrieve_container_file(
    file_item: Any,
    container_id: str,
    package_name: str,
    request_id: str,
    normalized_request: Dict[str, Any],
) -> Dict[str, Any]:
    file_id = getattr(file_item, "id", "")
    _log_step(
        request_id,
        "CONTAINER_FILE_DOWNLOAD_START",
        "Retrieving generated file from Code Interpreter container",
        file_id_suffix=str(file_id)[-8:] if file_id else "",
    )
    content = _genai_client().containers.files.content.retrieve(
        container_id=container_id,
        file_id=file_id,
    )
    file_bytes = content.read()
    file_name = _file_name_from_container_item(file_item, file_bytes, package_name)
    _log_step(
        request_id,
        "CONTAINER_FILE_DOWNLOAD_COMPLETE",
        "Generated file retrieved from Code Interpreter container",
        file_name=file_name,
        size_bytes=len(file_bytes),
    )
    package_validation = None
    if file_name.endswith(".zip") or _content_type(file_name, file_bytes) == "application/zip":
        package_validation = _validate_generated_package_zip(
            file_name,
            file_bytes,
            normalized_request,
            request_id,
        )
    artifact = _upload_generated_artifact(file_name, file_bytes, package_name, request_id)
    if package_validation:
        artifact["package_validation"] = package_validation
    return artifact


def _retrieve_generated_artifacts(
    container_id: str,
    package_name: str,
    request_id: str,
    normalized_request: Dict[str, Any],
) -> List[Dict[str, Any]]:
    _log_step(
        request_id,
        "CONTAINER_LIST_START",
        "Listing generated files from Code Interpreter container",
        container_id_suffix=container_id[-12:] if container_id else "",
    )
    files_page = _genai_client().containers.files.list(container_id=container_id)
    files = getattr(files_page, "data", []) or []
    artifacts: List[Dict[str, Any]] = []
    _log_step(
        request_id,
        "CONTAINER_LIST_COMPLETE",
        "Code Interpreter container file listing completed",
        file_count=len(files),
    )
    if not files:
        return artifacts

    with ThreadPoolExecutor(max_workers=min(4, len(files))) as executor:
        futures = [
            executor.submit(
                _retrieve_container_file,
                item,
                container_id,
                package_name,
                request_id,
                normalized_request,
            )
            for item in files
        ]
        for future in as_completed(futures):
            artifacts.append(future.result())
    artifacts.sort(key=lambda item: item.get("file_name", ""))
    _log_step(
        request_id,
        "ARTIFACT_RETRIEVAL_COMPLETE",
        "Generated artifact retrieval completed",
        artifact_count=len(artifacts),
    )
    return artifacts


def _call_code_interpreter(normalized: Dict[str, Any], request_id: str) -> Tuple[str, Optional[str]]:
    _validate_runtime_config()
    file_ids = _upload_references_to_genai(normalized["reference_objects"], request_id)
    container: Dict[str, Any] = {
        "type": "auto",
        "memory_limit": normalized["code_interpreter_memory"],
    }
    if file_ids:
        container["file_ids"] = file_ids

    region = _env("OCI_GENAI_REGION", _env("REGION", "us-chicago-1"))
    model = _env("OCI_GENAI_MODEL", "openai.gpt-oss-120b")
    _log_step(
        request_id,
        "GENAI_CLIENT_READY",
        "OCI Generative AI client configuration resolved",
        region=region,
        model=model,
        code_interpreter_memory=normalized["code_interpreter_memory"],
        reference_file_count=len(file_ids),
        **_config_presence(),
    )
    _log_step(
        request_id,
        "CODE_INTERPRETER_START",
        "Calling OCI Generative AI Responses API with Code Interpreter",
        package_name=normalized["package_name"],
        operation_id=normalized["operation_id"],
    )
    resp_obj = _genai_client().responses.create(
        model=model,
        instructions=BASE_GENERATOR_INSTRUCTIONS,
        input=_build_prompt(normalized),
        tools=[{"type": "code_interpreter", "container": container}],
    )
    container_id = _extract_container_id(resp_obj)
    result_text = getattr(resp_obj, "output_text", "") or ""
    _log_step(
        request_id,
        "CODE_INTERPRETER_COMPLETE",
        "Responses API call completed",
        container_found=bool(container_id),
        result_chars=len(result_text),
    )
    return result_text, container_id


def _parse_generation_summary(result_text: str, request_id: str) -> Dict[str, Any]:
    if not result_text.strip():
        return {
            "status": "unknown",
            "mandatory_files_present": False,
            "warnings": ["Model returned an empty textual summary."],
        }
    try:
        parsed = json.loads(result_text)
    except json.JSONDecodeError:
        _log_step(
            request_id,
            "GENERATION_SUMMARY_NOT_JSON",
            "Model textual summary was not valid JSON; wrapping it in a JSON field",
            result_chars=len(result_text),
        )
        return {
            "status": "succeeded",
            "mandatory_files_present": None,
            "summary_text": result_text[:4000],
            "warnings": ["Model textual summary was not valid JSON."],
        }
    if not isinstance(parsed, dict):
        return {
            "status": "succeeded",
            "mandatory_files_present": None,
            "summary": parsed,
            "warnings": ["Model textual summary JSON was not an object."],
        }
    return parsed


def handler(ctx, data: io.BytesIO = None):
    request_id = uuid.uuid4().hex
    started_at = time.monotonic()
    _log_step(request_id, "INVOCATION_RECEIVED", "Function invocation received")
    try:
        _log_step(request_id, "REQUEST_PARSE_START", "Parsing request JSON")
        payload = _read_json(data)
        _log_step(
            request_id,
            "REQUEST_PARSE_COMPLETE",
            "Request JSON parsed",
            field_count=len(payload),
        )
        _log_step(request_id, "VALIDATION_START", "Validating request payload")
        normalized = _validate_payload(payload)
        _log_step(
            request_id,
            "VALIDATION_COMPLETE",
            "Request payload validated",
            function_name=normalized["function_name"],
            operation_id=normalized["operation_id"],
            package_name=normalized["package_name"],
            reference_count=len(normalized["reference_objects"]),
            env_var_count=len(normalized["environment_variables"]),
        )
    except ValueError as exc:
        _log_step(
            request_id,
            "VALIDATION_FAILED",
            "Request validation failed",
            error=str(exc),
        )
        return _error_response(ctx, "VALIDATION_ERROR", str(exc), 400, request_id)

    try:
        result_text, container_id = _call_code_interpreter(normalized, request_id)
        warnings: List[str] = []
        artifacts: List[Dict[str, Any]] = []

        if container_id:
            artifacts = _retrieve_generated_artifacts(
                container_id,
                normalized["package_name"],
                request_id,
                normalized,
            )
        else:
            warnings.append("The model did not execute Code Interpreter or return a container id.")
            _log_step(
                request_id,
                "CONTAINER_MISSING",
                "No Code Interpreter container id was returned",
            )

        zip_artifacts = [
            item for item in artifacts
            if item.get("file_name", "").endswith(".zip")
            or item.get("content_type") == "application/zip"
        ]
        if not zip_artifacts:
            raise ValueError("The generator did not produce a ZIP package artifact.")

        generation_summary = _parse_generation_summary(result_text, request_id)

        duration_ms = int((time.monotonic() - started_at) * 1000)
        _log_step(
            request_id,
            "INVOCATION_SUCCEEDED",
            "Function invocation completed successfully",
            duration_ms=duration_ms,
            artifact_count=len(artifacts),
            zip_artifact_count=len(zip_artifacts),
            warning_count=len(warnings),
        )
        return _json_response(
            ctx,
            {
                "status": "succeeded",
                "request_id": request_id,
                "function_name": normalized["function_name"],
                "operation_id": normalized["operation_id"],
                "package_name": normalized["package_name"],
                "result": result_text,
                "generation_summary": generation_summary,
                "package": zip_artifacts[0] if zip_artifacts else None,
                "artifacts": artifacts,
                "warnings": warnings,
            },
            200,
        )
    except ValueError as exc:
        _log_step(
            request_id,
            "GENERATION_INPUT_FAILED",
            "User-correctable generation error",
            error=str(exc),
        )
        return _error_response(ctx, "GENERATION_INPUT_ERROR", str(exc), 400, request_id)
    except Exception as exc:
        error_text = str(exc)
        if "401" in error_text or "Authentication" in exc.__class__.__name__:
            _log_step(
                request_id,
                "GENAI_AUTH_FAILED",
                "OCI Generative AI authentication failed; check API key, project id, region, and base URL",
                error_class=exc.__class__.__name__,
                error_preview=error_text[:500],
                **_config_presence(),
            )
        else:
            _log_step(
                request_id,
                "GENERATION_FAILED",
                "Generation failed before completion",
                error_class=exc.__class__.__name__,
                error_preview=error_text[:500],
            )
        LOGGER.exception("Unhandled function-generator error request_id=%s", request_id)
        duration_ms = int((time.monotonic() - started_at) * 1000)
        _log_step(
            request_id,
            "INVOCATION_FAILED",
            "Function invocation failed with an internal error",
            duration_ms=duration_ms,
        )
        return _error_response(
            ctx,
            "INTERNAL_ERROR",
            "Internal error while generating the OCI Function package",
            500,
            request_id,
        )
