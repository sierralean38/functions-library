import io
import os
import json
import uuid
import logging
from datetime import datetime, timedelta

from fdk import response

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, Image, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics

import matplotlib.pyplot as plt
from PIL import Image as PILImage
import numpy as np
import pandas as pd
import oci

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _render_chart_to_image(chart_spec):
    chart_type = chart_spec.get("chart_type", "bar")
    title = chart_spec.get("title", "")
    data = chart_spec.get("data", {})
    labels = data.get("labels", [])
    values = data.get("values", [])

    n_labels = max(1, len(labels))
    fig_w = min(12, max(6, n_labels * 0.5))
    fig_h = max(3, 3 + (n_labels // 10))

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))

    if chart_type == "bar":
        x = list(range(len(labels)))
        ax.bar(x, values)
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
    elif chart_type == "line":
        x = list(range(len(labels)))
        ax.plot(x, values, marker='o')
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
    elif chart_type == "pie":
        vals = values if values else data.get('vals', [])
        labs = labels if labels else data.get('labels', [])
        if not vals:
            ax.text(0.5, 0.5, "No data for pie chart", ha='center')
        else:
            ax.pie(vals, labels=labs, autopct='%1.1f%%', startangle=90)
            ax.axis('equal')
    elif chart_type == 'cluster':
        points = None
        if 'points' in data and isinstance(data.get('points'), list):
            points = np.array(data.get('points'))
        else:
            x_d = data.get('x')
            y_d = data.get('y')
            if x_d is not None and y_d is not None:
                points = np.column_stack((np.array(x_d), np.array(y_d)))
        if points is None or len(points) == 0:
            ax.text(0.5, 0.5, 'No points for cluster chart', ha='center')
        else:
            k = int(chart_spec.get('k', max(2, min(5, len(points)//10 or 2))))
            pts = points.astype(float)
            rng = np.random.RandomState(seed=42)
            centroids = pts[rng.choice(len(pts), k, replace=False)]
            for _ in range(100):
                dists = np.linalg.norm(pts[:, None, :] - centroids[None, :, :], axis=2)
                labels_k = np.argmin(dists, axis=1)
                new_centroids = np.array([
                    pts[labels_k == i].mean(axis=0) if np.any(labels_k == i) else centroids[i]
                    for i in range(k)
                ])
                if np.allclose(new_centroids, centroids):
                    break
                centroids = new_centroids
            colors_map = plt.cm.get_cmap('tab10')
            for i in range(k):
                sel = pts[labels_k == i]
                if sel.size:
                    ax.scatter(sel[:, 0], sel[:, 1], s=30, color=colors_map(i), label=f'Cluster {i+1}', alpha=0.7)
            ax.scatter(centroids[:, 0], centroids[:, 1], s=100, color='black', marker='X')
            ax.legend(loc='best', fontsize='small')
    else:
        ax.text(0.5, 0.5, f"Unsupported chart type: {chart_type}", ha='center')

    ax.set_title(title)
    if n_labels > 6:
        plt.setp(ax.get_xticklabels(), rotation=45, ha='right', fontsize=8)
        fig.subplots_adjust(bottom=0.28)
    else:
        plt.setp(ax.get_xticklabels(), rotation=0, ha='center')
    try:
        ax.grid(True, linestyle='--', linewidth=0.5)
    except Exception:
        pass
    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf


def _table_to_reportlab(table_spec, styles):
    headers = table_spec.get("headers", [])
    rows = table_spec.get("rows", [])
    data_texts = []
    if headers:
        data_texts.append([str(h) for h in headers])
    for r in rows:
        data_texts.append([str(c) for c in r])

    base_font = 'Helvetica'
    base_size = 9
    para_style = ParagraphStyle('TableCell', parent=styles.get('BodyText'),
                                fontName=base_font, fontSize=base_size, leading=base_size+2)

    ncols = max((len(r) for r in data_texts), default=len(headers) or 1)
    norm_rows = [row + [''] * (ncols - len(row)) for row in data_texts]

    padding_pts = 12
    pref_widths = [0.0] * ncols
    for row in norm_rows:
        for i, cell in enumerate(row):
            try:
                w = pdfmetrics.stringWidth(cell, base_font, base_size)
            except Exception:
                w = len(cell) * (base_size * 0.5)
            pref_widths[i] = max(pref_widths[i], w + padding_pts)

    available_width = A4[0] - 4 * cm
    total_pref = sum(pref_widths) if pref_widths else available_width
    if total_pref <= available_width:
        col_widths = pref_widths
    else:
        scale = available_width / total_pref
        col_widths = [max(20, w * scale) for w in pref_widths]

    data = [[Paragraph(cell, para_style) for cell in row] for row in norm_rows]

    tbl = Table(data, colWidths=col_widths, hAlign='LEFT', repeatRows=1)
    style_cmds = [
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('BACKGROUND', (0,0), (-1,0), colors.lightgrey) if headers else (),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
    ]
    if len(norm_rows) > 1:
        for col in range(ncols):
            is_numeric = True
            for row in norm_rows[1:]:
                try:
                    float(row[col].replace(',', ''))
                except Exception:
                    is_numeric = False
                    break
            if is_numeric:
                style_cmds.append(('ALIGN', (col,0), (col,-1), 'RIGHT'))
    tbl.setStyle([s for s in style_cmds if s])
    return tbl


def _build_document_from_json(json_payload):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    styles = getSampleStyleSheet()
    title_style    = ParagraphStyle('Title',    parent=styles['Heading1'], spaceAfter=12)
    subtitle_style = ParagraphStyle('Subtitle', parent=styles['Heading2'], spaceAfter=8)
    story = []

    for el in json_payload.get('elements', []):
        t = el.get('type', '').lower()
        if t == 'title':
            story.append(Paragraph(el.get('text', ''), title_style))
            story.append(Spacer(1, 6))
        elif t == 'subtitle':
            story.append(Paragraph(el.get('text', ''), subtitle_style))
            story.append(Spacer(1, 6))
        elif t == 'paragraph':
            story.append(Paragraph(el.get('text', ''), styles['BodyText']))
            story.append(Spacer(1, 6))
        elif t == 'table':
            story.append(_table_to_reportlab(el, styles))
            story.append(Spacer(1, 12))
        elif t == 'chart':
            img_buf = _render_chart_to_image(el)
            pil = PILImage.open(img_buf)
            img_io = io.BytesIO()
            pil.save(img_io, format='PNG')
            img_io.seek(0)
            target_w = 16 * cm
            try:
                iw, ih = pil.size
                target_h = ih * min(target_w / float(iw), 1.0)
            except Exception:
                target_h = 9 * cm
            story.append(Image(img_io, width=target_w, height=target_h, hAlign='LEFT'))
            story.append(Spacer(1, 12))
        elif t == 'pagebreak':
            story.append(PageBreak())
        else:
            story.append(Paragraph(json.dumps(el), styles['BodyText']))
            story.append(Spacer(1, 6))

    doc.build(story)
    buffer.seek(0)
    return buffer


def _upload_to_object_storage(pdf_bytes_io, bucket_name, object_name, region=None, par_ttl_seconds=86400):
    signer = oci.auth.signers.get_resource_principals_signer()
    client = oci.object_storage.ObjectStorageClient(config={}, signer=signer)

    namespace = os.environ.get('NAMESPACE') or client.get_namespace().data

    pdf_bytes_io.seek(0)
    client.put_object(namespace, bucket_name, object_name, pdf_bytes_io.read(),
                      content_type='application/pdf')

    region = region or getattr(client.base_client, 'region', None)
    try:
        expires_at = datetime.utcnow() + timedelta(seconds=int(par_ttl_seconds))
        details = oci.object_storage.models.CreatePreauthenticatedRequestDetails(
            name=f"par-{uuid.uuid4().hex[:8]}",
            access_type='ObjectRead',
            object_name=object_name,
            time_expires=expires_at.isoformat() + 'Z'
        )
        par = client.create_preauthenticated_request(namespace, bucket_name, details)
        access_uri = getattr(par.data, 'access_uri', None) or getattr(par.data, 'accessUri', None)
        if access_uri:
            return f"https://objectstorage.{region}.oraclecloud.com{access_uri}"
    except Exception:
        logger.exception("Failed to create PAR")


def handler(ctx, data: io.BytesIO = None):
    try:
        body_bytes = data.read() if data else b'{}'
        try:
            outer = json.loads(body_bytes.decode('utf-8'))
        except Exception:
            outer = {}

        if isinstance(outer, dict) and isinstance(outer.get('respuesta'), str):
            try:
                payload = json.loads(outer['respuesta'])
            except Exception:
                try:
                    payload = json.loads(outer['respuesta'].encode('utf-8').decode('unicode_escape'))
                except Exception:
                    payload = outer
        else:
            payload = outer

        pdf_buf = _build_document_from_json(payload)

        bucket = os.environ.get('BUCKET_NAME')
        if not bucket:
            return response.Response(ctx=ctx, status_code=500,
                                     headers={"Content-Type": "application/json"},
                                     response_data=json.dumps({"status": "error", "message": "BUCKET_NAME not set"}))

        filename = f"report_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}.pdf"
        object_url = _upload_to_object_storage(pdf_buf, bucket, filename, region=os.environ.get('OCI_REGION'))

        return response.Response(ctx=ctx, status_code=200,
                                 headers={"Content-Type": "application/json"},
                                 response_data=json.dumps({"status": "ok", "URL": object_url}))

    except Exception as e:
        logger.exception("Failed to generate or upload PDF")
        return response.Response(ctx=ctx, status_code=500,
                                 headers={"Content-Type": "application/json"},
                                 response_data=json.dumps({"status": "error", "message": str(e)}))
