"""Low-level building blocks shared by the two custody-log PDF builders:
`custody_report.py` (per transaction) and `custody_evidence_report.py` (per evidence
file). Both print the same Образац table shape (Бр./Датум/Име и презиме/Опис радње/
Потпис) - only the header block above the table differs, since that's the part that says
WHICH thing (a transaction vs a whole evidence file) this particular form is for.
"""

from __future__ import annotations

import base64
import io
from datetime import datetime
from typing import Any

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from PIL import Image


NAVY = (13, 24, 40)
TEXT_GRAY = (100, 112, 128)
TEXT_DARK = (24, 28, 36)
WHITE = (255, 255, 255)
HEADER_BG = (226, 235, 245)

ROW_HEIGHT = 16


def dmy(value: Any) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return text
    return parsed.strftime('%d.%m.%Y.')


def decode_signature(data_url: str | None) -> Image.Image | None:
    if not data_url or ',' not in data_url:
        return None
    try:
        _, encoded = data_url.split(',', 1)
        return Image.open(io.BytesIO(base64.b64decode(encoded)))
    except Exception:  # noqa: BLE001 - a malformed signature must not break the whole export
        return None


def draw_signature(pdf: FPDF, signature: Image.Image, *, x: float, y: float, box_w: float, box_h: float) -> None:
    """Places the signature scan inside its cell preserving aspect ratio (centered) -
    stretching it to fill the box would distort a handwritten signature, which is exactly
    the kind of thing that undermines a forensic document's credibility."""
    img_w, img_h = signature.size
    if img_w <= 0 or img_h <= 0:
        return
    img_ratio = img_w / img_h
    box_ratio = box_w / box_h
    if img_ratio > box_ratio:
        draw_w, draw_h = box_w, box_w / img_ratio
    else:
        draw_h, draw_w = box_h, box_h * img_ratio
    pdf.image(signature, x=x + (box_w - draw_w) / 2, y=y + (box_h - draw_h) / 2, w=draw_w, h=draw_h)


class CustodyReportPDF(FPDF):
    """A4 portrait shell with the Lusi navy header/footer band - `title` names which of
    the two forms this is, printed in the top band (e.g. "Lanac dokaza po transakciji")."""

    def __init__(self, *, font_family: str, title: str) -> None:
        super().__init__(format='A4', orientation='P')
        self._font_family = font_family
        self._title = title
        self.set_auto_page_break(auto=True, margin=18)
        self.set_top_margin(24)

    def header(self) -> None:  # noqa: D102 - fpdf2 lifecycle hook
        self.set_fill_color(*NAVY)
        self.rect(0, 0, self.w, 16, style='F')
        self.set_text_color(*WHITE)
        self.set_font(self._font_family, 'B', 11)
        self.set_xy(12, 4)
        self.cell(0, 8, f'Lusi v1.0 - {self._title}', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(*TEXT_DARK)
        self.set_xy(self.l_margin, 20)

    def footer(self) -> None:  # noqa: D102 - fpdf2 lifecycle hook
        self.set_y(-12)
        self.set_font(self._font_family, '', 8)
        self.set_text_color(*TEXT_GRAY)
        self.cell(0, 8, f'Lusi v1.0 forenzicki izvoz | Strana {self.page_no()}', align='C')


def draw_obrazac_title(pdf: FPDF, font: str) -> None:
    """The fixed title block, verbatim from the reference paper form, common to both."""
    pdf.set_font(font, 'B', 14)
    pdf.set_text_color(*NAVY)
    pdf.cell(0, 7, 'ОБРАЗАЦ', align='C', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 7, 'ЕВИДЕНЦИЈЕ РУКОВАЊА ДОКАЗНИМ МАТЕРИЈАЛОМ', align='C', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)
    pdf.set_text_color(*TEXT_DARK)


def draw_context_banner(pdf: FPDF, font: str, text: str) -> None:
    """The shaded "what is this form FOR" line above the Идентификатор block (which
    transaction, or which evidence file). Uses multi_cell rather than a fixed-height
    single-line cell, so a long value (a full file name, a SHA-256 hash) wraps onto a
    second line instead of being silently truncated - every piece of it stays visible."""
    usable_width = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.set_font(font, '', 9.5)
    pdf.set_fill_color(*HEADER_BG)
    pdf.multi_cell(usable_width, 5.5, f' {text} ', fill=True)
    pdf.ln(3)


def draw_kv_header_block(pdf: FPDF, font: str, header: dict[str, Any]) -> None:
    """The Идентификатор предмета / доказног материјала / произвођач / модел / серијски
    број block - identical field set for both forms, only the VALUES differ per case.

    The label column is sized to the WIDEST label ("Идентификатор доказног материјала:"
    is much longer than the others) rather than a fixed guess - a fixed width that turns
    out too narrow makes the label text overlap the value next to it, which is exactly
    the bug this fixes. Values wrap with multi_cell instead of being confined to one line,
    so a long evidence file name or a hash never gets silently clipped."""
    rows: list[tuple[str, Any]] = [
        ('Идентификатор предмета:', header.get('identifikator_predmeta')),
        ('Идентификатор доказног материјала:', header.get('identifikator_dokaznog_materijala')),
        ('Произвођач:', header.get('proizvodjac')),
        ('Модел:', header.get('model')),
        ('Серијски број:', header.get('serijski_broj')),
    ]

    pdf.set_font(font, 'B', 9.5)
    label_width = max(pdf.get_string_width(label) for label, _ in rows) + 4
    usable_width = pdf.w - pdf.l_margin - pdf.r_margin
    value_width = usable_width - label_width
    line_height = 5.4

    for label, value in rows:
        x0, y0 = pdf.l_margin, pdf.get_y()
        pdf.set_font(font, 'B', 9.5)
        pdf.set_xy(x0, y0)
        pdf.cell(label_width, line_height, label)
        pdf.set_xy(x0 + label_width, y0)
        pdf.set_font(font, '', 9.5)
        pdf.multi_cell(value_width, line_height, str(value or 'N/A'))
        # The value may have wrapped to more than one line - the row's actual height is
        # whichever is taller, the (single-line) label or the (possibly multi-line) value.
        pdf.set_xy(x0, max(pdf.get_y(), y0 + line_height))

    pdf.ln(3)


def draw_entries_table(pdf: FPDF, font: str, entries: list[dict[str, Any]], *, usable_width: float, empty_message: str) -> None:
    """The Бр./Датум/Име и презиме/Опис радње/Потпис table - identical shape for both
    forms, the row DATA is whatever `entries` (already numbered) contains."""
    widths = [12, 24, 40, 0, 45]
    widths[3] = usable_width - sum(widths)
    headers_row = ['Бр.', 'Датум', 'Име и презиме', 'Опис радње', 'Потпис']

    def draw_header_row() -> None:
        pdf.set_font(font, 'B', 9)
        pdf.set_fill_color(*NAVY)
        pdf.set_text_color(*WHITE)
        for width, title in zip(widths, headers_row):
            pdf.cell(width, 7, title, border=1, align='C', fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.ln(7)
        pdf.set_text_color(*TEXT_DARK)

    draw_header_row()

    if not entries:
        pdf.set_font(font, '', 9.5)
        pdf.set_text_color(*TEXT_GRAY)
        pdf.cell(usable_width, 8, empty_message, border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        return

    text_line_height = 4.6
    text_pad = 1.5

    for entry in entries:
        ime = str(entry.get('ime_prezime') or '')
        opis = str(entry.get('opis_radnje') or '')
        pdf.set_font(font, '', 9)
        # Row height grows with whichever of the two free-text columns needs the most
        # lines - "Опис радње" in particular can be a full sentence, and truncating it
        # with an ellipsis is exactly the "info you can't see" the paper form doesn't
        # have this problem with (handwriting just uses more of the line).
        ime_lines = pdf.multi_cell(widths[2] - 2 * text_pad, text_line_height, ime, dry_run=True, output='LINES') or ['']
        opis_lines = pdf.multi_cell(widths[3] - 2 * text_pad, text_line_height, opis, dry_run=True, output='LINES') or ['']
        row_height = max(ROW_HEIGHT, max(len(ime_lines), len(opis_lines)) * text_line_height + 2 * text_pad)

        if pdf.get_y() + row_height > pdf.h - 20:
            pdf.add_page()
            draw_header_row()

        x0, y0 = pdf.get_x(), pdf.get_y()
        pdf.set_font(font, '', 9)
        pdf.set_text_color(*TEXT_DARK)

        pdf.cell(widths[0], row_height, str(entry.get('redni_broj', '')), border=1, align='C')
        pdf.cell(widths[1], row_height, dmy(entry.get('timestamp')), border=1, align='C')

        # Border box drawn first (fixed row_height), text overlaid inside it afterwards -
        # multi_cell's own border only wraps the text it actually drew, which would be
        # shorter than row_height whenever the OTHER column needed more lines.
        ime_x = pdf.get_x()
        pdf.cell(widths[2], row_height, '', border=1)
        pdf.set_xy(ime_x + text_pad, y0 + text_pad)
        pdf.multi_cell(widths[2] - 2 * text_pad, text_line_height, ime)

        opis_x = ime_x + widths[2]
        pdf.set_xy(opis_x, y0)
        pdf.cell(widths[3], row_height, '', border=1)
        pdf.set_xy(opis_x + text_pad, y0 + text_pad)
        pdf.multi_cell(widths[3] - 2 * text_pad, text_line_height, opis)

        sig_x = opis_x + widths[3]
        pdf.set_xy(sig_x, y0)
        pdf.cell(widths[4], row_height, '', border=1)
        signature = decode_signature(entry.get('signature_image'))
        if signature is not None:
            pad = 1.5
            draw_signature(pdf, signature, x=sig_x + pad, y=y0 + pad, box_w=widths[4] - 2 * pad, box_h=row_height - 2 * pad)

        pdf.set_xy(x0, y0 + row_height)
