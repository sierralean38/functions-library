# pdf-report-function

Oracle Function que genera reportes PDF a partir de un JSON estructurado con elementos (títulos, subtítulos, párrafos, tablas y gráficos). El PDF generado se sube a OCI Object Storage y se retorna como URL PAR descargable.

## Variables de entorno

| Variable | Descripción | Requerida |
|---|---|---|
| `BUCKET_NAME` | Nombre del bucket donde se almacena el PDF | Sí |
| `OCI_REGION` | Región OCI para construir la URL del PAR | Sí |
| `NAMESPACE` | Namespace de OCI Object Storage | Sí |

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

## Prompt ejemplo Instrucciones agente

Este ejemplo sirve para poner instrucciones al agente de uso:

Para generar el reporte  tienes un Rest API tool y debes seguir estas instrucciones:

Esta rama solo debe ejecutarse cuando el usuario haya solicitado explícitamente un reporte, informe o PDF.

Tu tarea es generar un reporte en formato JSON con la estructura requerida por la tool REST. Debes incluir, según aplique, títulos, párrafos, tablas y gráficos para que el reporte sea completo y útil.

Reglas obligatorias:

1. Envía al tool únicamente con JSON válido.
2. El JSON debe seguir el formato requerido por la tool REST.
3. Una vez generado el JSON, envíalo a la tool REST para que genere el PDF.
4. Devuelve al usuario la URL que entregue la tool en formato de vinculo con el texto: "Descargar PDF".
5. No agregues explicación adicional fuera del JSON cuando estés preparando la solicitud para la tool.
6. Puedes usar los tipos: title, paragraph, table, chart.
7. Los tipos de gráfico permitidos son: bar, line, pie y cluster.
8. Si faltan datos, infiere lo necesario de forma razonable para completar el reporte.
9. El reporte debe incluir análisis y una recomendación final.
10. Nunca respondas con texto libre si el flujo está en esta rama: debes construir el JSON y enviarlo a la tool.

Formato base de referencia:

{
  "filename": "reporte_demo.pdf",
  "elements": [
    {"type": "title", "text": "Titulo del reporte"},
    {"type": "paragraph", "text": "Introduccion o resumen ejecutivo."},
    {"type": "chart", "chart_type": "bar", "title": "Titulo grafico", "data": {"labels": ["A", "B", "C"], "values": [10, 20, 30]}},
    {"type": "table", "headers": ["Columna 1", "Columna 2"], "rows": [["Valor 1", "Valor 2"]]},
    {"type": "paragraph", "text": "Analisis final y recomendacion."}
  ]
}



## Runtime

Docker · Python 3.9 · ReportLab + Matplotlib

---

> Parte de [functions-library](../README.md) by Diego Sierra Alean
