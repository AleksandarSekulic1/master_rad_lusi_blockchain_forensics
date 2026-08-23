"""Builds the per-evidence-file chain-of-custody PDF - "Obrazac evidencije rukovanja
dokaznim materijalom" applied to a whole imported evidence file (CSV/on-chain export),
the way the paper form applies to a whole exhibit (a seized hard drive) rather than to
each individual file recorded on it.

See `custody_report.py` for the finer-grained sibling (per individual transaction) - the
two share their low-level PDF drawing code via `custody_pdf_common.py`.
"""

from __future__ import annotations

from typing import Any

from app.exports.custody_pdf_common import (
    CustodyReportPDF,
    TEXT_GRAY,
    draw_context_banner,
    draw_entries_table,
    draw_kv_header_block,
    draw_obrazac_title,
)
from app.exports.pdf_fonts import register_unicode_font


def build_custody_evidence_pdf(header: dict[str, Any], entries: list[dict[str, Any]]) -> bytes:
    pdf = CustodyReportPDF(font_family='helvetica', title='Lanac dokaza po dokaznom fajlu')
    font = register_unicode_font(pdf)
    pdf._font_family = font  # noqa: SLF001 - header/footer need the resolved family
    pdf.add_page()

    draw_obrazac_title(pdf, font)

    # Which evidence file this form is FOR - the exhibit itself, the paper-form analogue
    # of "Seagate ST1000DM010, S/N 3660619402182" but for a digital export.
    usable_width = pdf.w - pdf.l_margin - pdf.r_margin
    file_line = str(header.get('evidence_file_name') or header.get('evidence_stored_name') or '?')
    row_count = header.get('evidence_row_count')
    if row_count is not None:
        file_line += f'   |   {row_count} transakcija'
    currency = header.get('evidence_currency')
    file_line += f'   |   valuta: {currency}' if currency else '   |   valuta nije navedena'
    sha256 = header.get('evidence_sha256')
    if sha256:
        file_line += f'   |   SHA-256: {sha256}'
    draw_context_banner(pdf, font, f'Dokazni fajl: {file_line}')

    draw_kv_header_block(pdf, font, header)

    draw_entries_table(
        pdf, font, entries,
        usable_width=usable_width,
        empty_message='Nema zabelezenih pristupa ovom dokaznom fajlu.',
    )

    pdf.ln(6)
    pdf.set_font(font, '', 8)
    pdf.set_text_color(*TEXT_GRAY)
    pdf.multi_cell(
        usable_width, 4.5,
        'Svaka stavka predstavlja poseban pristup CELOM dokaznom fajlu (svim transakcijama koje sadrzi) prilikom '
        'pokretanja analize (Taint analiza ili Graf). Za istoriju pristupa POJEDINACNOJ transakciji iz ovog fajla, '
        'vidi njen sopstveni lanac dokaza na stranici "Lanac dokaza" u aplikaciji.',
    )

    return bytes(pdf.output())
