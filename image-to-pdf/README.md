# pdf-to-image-fn

Oracle Function que convierte cada página de un PDF almacenado en OCI Object Storage a imágenes JPG, las sube al bucket y las retorna también en base64.

## Variables de entorno

| Variable | Descripción | Requerida |
|---|---|---|
| `BUCKET_NAME` | Nombre del bucket donde está el PDF y donde se suben las imágenes | Sí |
| `NAMESPACE_NAME` | Namespace de OCI Object Storage | Sí |

## Request

```json
{
  "file_name": "ruta/en/bucket/documento.pdf"
}
```

## Response

```json
{
  "uploaded_files": ["documento.pdf_page_1.jpg"],
  "images_base64": [
    { "name": "documento.pdf_page_1.jpg", "base64": "..." }
  ]
}
```

## Runtime

Docker · Python 3.9 · pdf2image + poppler

---

> Parte de [functions-library](../README.md) by Diego Sierra Alean
