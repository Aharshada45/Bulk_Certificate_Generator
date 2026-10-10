"""Renders a single certificate PDF from the one predefined template.

The template is defined in code (layout + colours below) with ReportLab, so
there are no external template files or fonts to ship. Long names and titles
are shrunk (and titles wrapped) so that they always fit on the page.
"""

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PAGE_WIDTH, PAGE_HEIGHT = landscape(A4)
MARGIN = 40
TEXT_WIDTH = PAGE_WIDTH - 2 * (MARGIN + 40)

PRIMARY = colors.HexColor("#1F3A5F")
ACCENT = colors.HexColor("#C9A227")
MUTED = colors.HexColor("#555555")


@dataclass(frozen=True)
class CertificateData:
    certificate_id: str
    recipient_name: str
    title: str
    issuer: str
    issue_date: date


def _fit_font_size(text: str, font: str, max_size: float, min_size: float, width: float) -> float:
    """Largest font size (<= max_size) at which ``text`` fits in ``width``."""
    width_at_1pt = stringWidth(text, font, 1) or 1
    return max(min_size, min(max_size, width / width_at_1pt))


def _draw_centered(c: canvas.Canvas, y: float, text: str, font: str, size: float, color) -> None:
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawCentredString(PAGE_WIDTH / 2, y, text)


def _draw_template(c: canvas.Canvas) -> None:
    """Static parts of the certificate: background, borders and headings."""
    c.setFillColor(colors.HexColor("#FBF8F1"))
    c.rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT, stroke=0, fill=1)

    c.setStrokeColor(PRIMARY)
    c.setLineWidth(6)
    c.rect(MARGIN, MARGIN, PAGE_WIDTH - 2 * MARGIN, PAGE_HEIGHT - 2 * MARGIN)
    c.setStrokeColor(ACCENT)
    c.setLineWidth(1.5)
    c.rect(MARGIN + 12, MARGIN + 12, PAGE_WIDTH - 2 * (MARGIN + 12), PAGE_HEIGHT - 2 * (MARGIN + 12))

    _draw_centered(c, PAGE_HEIGHT - 135, "CERTIFICATE", "Helvetica-Bold", 44, PRIMARY)
    _draw_centered(c, PAGE_HEIGHT - 165, "OF COMPLETION", "Helvetica", 18, ACCENT)
    _draw_centered(c, PAGE_HEIGHT - 215, "This is to certify that", "Helvetica-Oblique", 16, MUTED)


def render_certificate(data: CertificateData, output_path: Path) -> Path:
    """Write the certificate PDF to ``output_path`` and return the path.

    The file is written to a temporary name first and renamed into place, so a
    crash mid-render never leaves a truncated PDF behind.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".pdf.tmp")

    c = canvas.Canvas(str(tmp_path), pagesize=(PAGE_WIDTH, PAGE_HEIGHT))
    c.setTitle(f"Certificate - {data.recipient_name}")
    c.setAuthor(data.issuer)
    _draw_template(c)

    # Recipient name, with an underline.
    name_size = _fit_font_size(data.recipient_name, "Helvetica-Bold", 40, 12, TEXT_WIDTH)
    name_y = PAGE_HEIGHT - 275
    _draw_centered(c, name_y, data.recipient_name, "Helvetica-Bold", name_size, PRIMARY)
    c.setStrokeColor(ACCENT)
    c.setLineWidth(1)
    c.line(PAGE_WIDTH / 2 - 220, name_y - 14, PAGE_WIDTH / 2 + 220, name_y - 14)

    _draw_centered(c, name_y - 50, "has successfully completed", "Helvetica-Oblique", 16, MUTED)

    # Course / event title: shrink first, then wrap onto at most two lines.
    title_size = _fit_font_size(data.title, "Helvetica-Bold", 24, 16, TEXT_WIDTH)
    lines = simpleSplit(data.title, "Helvetica-Bold", title_size, TEXT_WIDTH)[:2]
    y = name_y - 90
    for line in lines:
        _draw_centered(c, y, line, "Helvetica-Bold", title_size, PRIMARY)
        y -= title_size + 6

    # Footer: date (left), issuer (right), certificate id (bottom centre).
    footer_y = MARGIN + 85
    c.setFont("Helvetica", 13)
    c.setFillColor(colors.black)
    c.drawCentredString(PAGE_WIDTH * 0.27, footer_y, data.issue_date.strftime("%d %B %Y"))
    issuer_size = _fit_font_size(data.issuer, "Helvetica", 13, 8, PAGE_WIDTH * 0.35)
    c.setFont("Helvetica", issuer_size)
    c.drawCentredString(PAGE_WIDTH * 0.73, footer_y, data.issuer)
    c.setStrokeColor(MUTED)
    for x in (PAGE_WIDTH * 0.27, PAGE_WIDTH * 0.73):
        c.line(x - 110, footer_y - 8, x + 110, footer_y - 8)
    c.setFont("Helvetica", 10)
    c.setFillColor(MUTED)
    c.drawCentredString(PAGE_WIDTH * 0.27, footer_y - 24, "Date of issue")
    c.drawCentredString(PAGE_WIDTH * 0.73, footer_y - 24, "Issued by")
    _draw_centered(c, MARGIN + 24, f"Certificate ID: {data.certificate_id}", "Helvetica", 8, MUTED)

    c.showPage()
    c.save()
    os.replace(tmp_path, output_path)
    return output_path
