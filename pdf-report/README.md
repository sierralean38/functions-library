# pdf-report-function

Oracle Function que genera reportes PDF a partir de un JSON estructurado con elementos (títulos, subtítulos, párrafos, tablas y gráficos). El PDF generado se sube a OCI Object Storage y se retorna como URL PAR descargable.

## Variables de entorno

| Variable | Descripción | Requerida |
|---|---|---|
| `BUCKET_NAME` | Nombre del bucket donde se almacena el PDF | Sí |
| `OCI_REGION` | Región OCI para construir la URL del PAR | Sí |
| `NAMESPACE` | Namespace de OCI Object Storage | No |

## Request

El body puede enviarse directamente o envuelto bajo la clave `respuesta`:

```json
{
  "elements": [
    { "type": "title", "text": "Mi Reporte" },
    { "type": "paragraph", "text": "Introducción del reporte." },
    {
      "type": "table",
      "headers": ["Región", "Ventas"],
      "rows": [["Norte", 100], ["Sur", 200]]
    },
    {
      "type": "chart",
      "chart_type": "bar",
      "title": "Ventas por Región",
      "data": { "labels": ["Norte", "Sur"], "values": [100, 200] }
    }
  ]
}
```

### Tipos de elemento soportados

| Tipo | Descripción |
|---|---|
| `title` | Título principal |
| `subtitle` | Subtítulo |
| `paragraph` | Párrafo de texto |
| `table` | Tabla con headers y filas |
| `chart` | Gráfico (`bar`, `line`, `pie`, `cluster`) |
| `pagebreak` | Salto de página |

## Response

```json
{ "status": "ok", "URL": "https://objectstorage.<region>.oraclecloud.com/p/..." }
```

## Runtime

Docker · Python 3.9 · ReportLab + Matplotlib

---

> Parte de [functions-library](../README.md) by Diego Sierra Alean
