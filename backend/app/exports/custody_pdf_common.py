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


def fit(pdf: FPDF, text: Any, max_width: float) -> str:
    """Truncates with an ellipsis so a long value never spills into the next column - the
    full text stays available on the "Lanac dokaza" screen; only the printed cell is
    length-capped, same technique as the activity report's `_fit`."""
    text = str(text or '')
    if pdf.get_string_width(text) <= max_width:
        return text
    ellipsis = '…'
    while text and pdf.get_string_width(text + ellipsis) > max_width:
        text = text[:-1]
    return text + ellipsis


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


def draw_kv_header_block(pdf: FPDF, font: str, header: dict[str, Any]) -> None:
    """The Идентификатор предмета / доказног материјала / произвођач / модел / серијски
    број block - identical field set for both forms, only the VALUES differ per case."""

    def kv(label: str, value: Any) -> None:
        pdf.set_font(font, 'B', 9.5)
        pdf.cell(68, 6.5, label, new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.set_font(font, '', 9.5)
        pdf.cell(0, 6.5, str(value or 'N/A'), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    kv('Идентификатор предмета:', header.get('identifikator_predmeta'))
    kv('Идентификатор доказног материјала:', header.get('identifikator_dokaznog_materijala'))
    kv('Произвођач:', header.get('proizvodjac'))
    kv('Модел:', header.get('model'))
    kv('Серијски број:', header.get('serijski_broj'))
    pdf.ln(4)


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

    for entry in entries:
        if pdf.get_y() + ROW_HEIGHT > pdf.h - 20:
            pdf.add_page()
            draw_header_row()

        x0, y0 = pdf.get_x(), pdf.get_y()
        pdf.set_font(font, '', 9)
        pdf.set_text_color(*TEXT_DARK)

        pdf.cell(widths[0], ROW_HEIGHT, str(entry.get('redni_broj', '')), border=1, align='C')
        pdf.cell(widths[1], ROW_HEIGHT, dmy(entry.get('timestamp')), border=1, align='C')
        pdf.cell(widths[2], ROW_HEIGHT, fit(pdf, entry.get('ime_prezime'), widths[2] - 2), border=1)
        pdf.cell(widths[3], ROW_HEIGHT, ' ' + fit(pdf, entry.get('opis_radnje'), widths[3] - 4), border=1)

        sig_x, sig_y = pdf.get_x(), y0
        pdf.cell(widths[4], ROW_HEIGHT, '', border=1)
        signature = decode_signature(entry.get('signature_image'))
        if signature is not None:
            pad = 1.5
            draw_signature(pdf, signature, x=sig_x + pad, y=sig_y + pad, box_w=widths[4] - 2 * pad, box_h=ROW_HEIGHT - 2 * pad)

        pdf.set_xy(x0, y0 + ROW_HEIGHT)
