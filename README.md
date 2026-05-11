# Functions tools Library - Agrega valor a tu Agente de inteligencia Artificial por medio de REST API en Oracle Cloud Functions

---

Ejemplo de tools realizadas en Oracle Functions listas para desplegar, diseñadas para potenciar agentes de IA con capacidades reales.

Cada función vive en su propia carpeta con todo lo necesario para desplegarla en Oracle Cloud Infrastructure (OCI).

---

## Funciones disponibles

| Carpeta | Nombre interno | Descripción |
|---|---|---|
| [`code-interpreter/`](./code-interpreter) | `code-interpreter-tool` | Ejecuta código Python con IA sobre datos enviados por el agente. Genera archivos Excel, Word, PDF, imágenes y más, almacenándolos en OCI Object Storage y retornando URLs descargables (PAR). |
| [`image-to-pdf/`](./image-to-pdf) | `pdf-to-image-fn` | Convierte cada página de un PDF almacenado en OCI Object Storage a imágenes JPG, retornándolas en base64 y subiéndolas al bucket. |
| [`pdf-report/`](./pdf-report) | `pdf-report-function` | Genera reportes PDF a partir de un JSON estructurado con elementos (títulos, párrafos, tablas y gráficos) y los publica en OCI Object Storage con un PAR descargable. |

---

## Estructura de cada función

```
<nombre-funcion>/
├── func.py          # Lógica de la función
├── func.yaml        # Configuración para Oracle Functions
├── Dockerfile       # Imagen de contenedor
└── requirements.txt # Dependencias Python
```

Sigue la documentación oficial de [Oracle Functions](https://docs.oracle.com/en-us/iaas/Content/Functions/home.htm) para desplegar cada una en tu tenancy de OCI.

---

## Variables de entorno requeridas

Cada función lee su configuración desde variables de entorno definidas en la consola de OCI Functions. Revisa el `func.yaml` y el encabezado de cada `func.py` para ver qué variables necesita.

---

## ⚠️ Aviso de Responsabilidad

Estas funciones se publican con fines **educativos y de referencia**. Son ejemplos funcionales desarrollados como parte de proyectos reales de IA y cloud.

**Al usar, copiar, modificar o desplegar cualquier código de este repositorio, aceptas expresamente que:**

- El uso es bajo tu **exclusiva responsabilidad**.
- **No se asume ninguna responsabilidad** por daños directos, indirectos, incidentales, especiales o consecuentes derivados del uso, mal uso o imposibilidad de uso de este código.
- No se garantiza que el código sea adecuado para entornos productivos sin una revisión y adaptación previa a tus requerimientos específicos de seguridad, rendimiento y compliance.
- Es tu responsabilidad cumplir con los términos de servicio de OCI, OpenAI y cualquier otro proveedor tercero involucrado.
- El autor no se hace responsable de costos de infraestructura, pérdida de datos ni ningún otro perjuicio derivado del despliegue de estas funciones.


---

## Contacto

- GitHub: [@sierralean38](https://github.com/sierralean38)

---

<p align="center">
  <sub>Made with dedication by Diego Sierra Alean &nbsp;·&nbsp; AI para impulsar el valor de negocio en las organizaciones</sub>
</p>
