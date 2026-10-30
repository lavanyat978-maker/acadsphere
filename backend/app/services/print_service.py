"""Controlled printable PDF generation.

The PDF is produced entirely in memory and streamed to the HOD. No plaintext
file is written to disk or object storage.
"""
from __future__ import annotations

import io
from datetime import datetime
from html import escape
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_REPLACEMENTS = {
    "\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00a0": " ",
    "\u2192": "->", "\u2265": ">=", "\u2264": "<=", "\u00d7": "x",
}


def _safe(text: str) -> str:
    for source, target in _REPLACEMENTS.items():
        text = text.replace(source, target)
    return escape(text or "")


def _fmt_date(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%d %B %Y, %H:%M UTC")
    except Exception:  # noqa: BLE001
        return value


def render_paper_pdf(payload: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    exam = payload.get("exam", {})
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"{exam.get('subject', '')} - {exam.get('exam_type', '')}",
        author=payload.get("institution", "Praxis Institute of Technology"),
        subject="Confidential examination paper",
    )
    base = getSampleStyleSheet()
    styles = {
        "institution": ParagraphStyle(
            "institution", parent=base["Title"], fontSize=16, leading=20, alignment=TA_CENTER,
            textColor=colors.HexColor("#1b2a41"),
        ),
        "meta": ParagraphStyle(
            "meta", parent=base["Normal"], fontSize=9.5, leading=13, alignment=TA_CENTER,
            textColor=colors.HexColor("#4a5568"),
        ),
        "section": ParagraphStyle(
            "section", parent=base["Heading2"], fontSize=11.5, leading=15, spaceBefore=10,
            spaceAfter=3, textColor=colors.HexColor("#1b2a41"),
        ),
        "instruction": ParagraphStyle(
            "instruction", parent=base["Normal"], fontSize=8.8, leading=12, textColor=colors.HexColor("#5b6577"),
        ),
        "question": ParagraphStyle(
            "question", parent=base["Normal"], fontSize=10.2, leading=14.5, alignment=TA_JUSTIFY,
        ),
        "mark": ParagraphStyle(
            "mark", parent=base["Normal"], fontSize=9, leading=12, alignment=TA_RIGHT,
            textColor=colors.HexColor("#334155"),
        ),
        "small": ParagraphStyle("small", parent=base["Normal"], fontSize=8.4, leading=11),
    }

    story: list[Any] = []
    story.append(Paragraph(_safe(payload.get("institution", "")), styles["institution"]))
    story.append(
        Paragraph(
            _safe("CONFIDENTIAL - CONTROLLED EXAMINATION COPY"),
            ParagraphStyle(
                "conf", parent=styles["meta"], textColor=colors.HexColor("#9b1c1c"),
                fontName="Helvetica-Bold", fontSize=8.5, spaceBefore=2,
            ),
        )
    )
    story.append(Spacer(1, 6))
    story.append(
        HRFlowable(width="100%", thickness=1.1, color=colors.HexColor("#1b2a41"), spaceAfter=8)
    )

    meta_rows = [
        ["Examination", _safe(exam.get("exam_type", ""))],
        ["Subject", _safe(f"{exam.get('subject', '')} ({exam.get('subject_code', '')})")],
        ["Date & time", _safe(_fmt_date(exam.get("exam_date", "")))],
        ["Duration", _safe(f"{exam.get('duration_minutes', '')} minutes")],
        ["Maximum marks", _safe(str(exam.get("total_marks", "")))],
        ["Venue", _safe(exam.get("venue", ""))],
        ["Paper version", _safe(f"v{exam.get('paper_version', 1)}")],
    ]
    table = Table(meta_rows, colWidths=[38 * mm, 120 * mm])
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9),
                ("FONT", (1, 0), (1, -1), "Helvetica", 9),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#1b2a41")),
                ("TEXTCOLOR", (1, 0), (1, -1), colors.HexColor("#374151")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#e2e8f0")),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 8))

    instructions = exam.get("instructions") or "Answer all questions. Write legibly in the answer booklet."
    story.append(Paragraph("<b>Instructions to candidates</b>", styles["instruction"]))
    for line in instructions.split("\n"):
        if line.strip():
            story.append(Paragraph(_safe(f"- {line.strip().lstrip('-').strip()}"), styles["instruction"]))
    story.append(
        Paragraph(
            _safe(
                "Mobile phones and smart devices are not permitted. This paper is confidential and "
                "must be collected back after the examination."
            ),
            styles["instruction"],
        )
    )

    counter = 0
    for section in payload.get("sections", []):
        block: list[Any] = []
        block.append(Paragraph(_safe(section.get("label", "")), styles["section"]))
        block.append(Paragraph(_safe(section.get("instructions", "")), styles["instruction"]))
        block.append(Spacer(1, 3))
        for question in section.get("questions", []):
            counter += 1
            block.append(
                Table(
                    [
                        [
                            Paragraph(_safe(f"{question.get('number', counter)}. {question.get('question_text', '')}"), styles["question"]),
                            Paragraph(_safe(f"[{question.get('marks', 0)}]"), styles["mark"]),
                        ]
                    ],
                    colWidths=[140 * mm, 18 * mm],
                    style=TableStyle(
                        [
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 0),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                            ("TOPPADDING", (0, 0), (-1, -1), 3),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ]
                    ),
                )
            )
        story.append(KeepTogether(block[:3]))
        for flowable in block[3:]:
            story.append(flowable)
        story.append(Spacer(1, 4))

    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=0.7, color=colors.HexColor("#94a3b8")))
    story.append(
        Paragraph(
            _safe(
                f"END OF QUESTION PAPER - {payload.get('question_count', counter)} questions, "
                f"{payload.get('total_marks', exam.get('total_marks', ''))} marks."
            ),
            styles["meta"],
        )
    )

    story.append(PageBreak())
    story.append(Paragraph("Marking Scheme (Internal - HOD copy)", styles["section"]))
    story.append(
        Paragraph(
            "This appendix is a controlled marking reference. Collect and shred after evaluation.",
            styles["instruction"],
        )
    )
    story.append(Spacer(1, 6))
    for section in payload.get("sections", []):
        story.append(Paragraph(_safe(section.get("label", "")), styles["instruction"]))
        for question in section.get("questions", []):
            story.append(
                Paragraph(
                    _safe(
                        f"Q{question.get('number', '')} ({question.get('marks', 0)} marks): "
                        f"{question.get('expected_answer', 'Marking guidance unavailable.')}"
                    ),
                    styles["small"],
                )
            )
            story.append(Spacer(1, 3))

    def footer(canvas, doc_obj):  # pragma: no cover - rendering callback
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#94a3b8"))
        canvas.drawString(20 * mm, 10 * mm, "Praxis Secure Assessment Platform - controlled copy")
        canvas.drawRightString(190 * mm, 10 * mm, f"Page {doc_obj.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    pdf = buffer.getvalue()
    buffer.close()
    return pdf


def stamp_uploaded_pdf(pdf_bytes: bytes, label: str) -> bytes:
    """Overlay a controlled-copy footer on every page of an uploaded paper.

    The stamp identifies which of the capped copies a printout is, so a leaked
    sheet can be traced back to one audited print.
    """
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas as pdf_canvas

    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    total = len(reader.pages)
    for index, page in enumerate(reader.pages, start=1):
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        overlay_buffer = io.BytesIO()
        overlay = pdf_canvas.Canvas(overlay_buffer, pagesize=(width, height))
        overlay.setFont("Helvetica", 7)
        overlay.setFillColor(colors.HexColor("#64748b"))
        overlay.drawString(12 * mm, 6 * mm, f"Praxis - {label}")
        overlay.drawRightString(width - 12 * mm, 6 * mm, f"Page {index} of {total}")
        overlay.save()
        overlay_page = PdfReader(io.BytesIO(overlay_buffer.getvalue())).pages[0]
        page.merge_page(overlay_page)
        writer.add_page(page)
    writer.add_metadata({"/Producer": "Praxis controlled print"})
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
