"""Builds the per-transaction chain-of-custody PDF - "Obrazac evidencije rukovanja
dokaznim materijalom" applied to one blockchain transaction instead of a physical exhibit.

Generated on the server, straight from the custody log (see `app.evidence.custody_log`),
for the same reason the activity report is: a document that claims "these are every access
to this transaction" has to be built from the authoritative log, not from whatever the
browser happened to have loaded.

See `custody_evidence_report.py` for the coarser sibling (per evidence FILE rather than per
transaction) - the two share their low-level PDF drawing code via `custody_pdf_common.py`.
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
    dmy,
)
from app.exports.pdf_fonts import register_unicode_font


def build_custody_pdf(header: dict[str, Any], entries: list[dict[str, Any]]) -> bytes:
    pdf = CustodyReportPDF(font_family='helvetica', title='Lanac dokaza po transakciji')
    font = register_unicode_font(pdf)
    pdf._font_family = font  # noqa: SLF001 - header/footer need the resolved family
    pdf.add_page()

    draw_obrazac_title(pdf, font)

    # Which transaction this form is FOR - context the paper form doesn't need for a
    # physical exhibit, but which is exactly what identifies a transaction here.
    usable_width = pdf.w - pdf.l_margin - pdf.r_margin
    tx_line = f'{header.get("sender_address", "?")}  ->  {header.get("recipient_address", "?")}'
    amount = header.get('amount')
    if amount is not None:
        tx_line += f'   |   {amount} {header.get("currency") or "nije navedena valuta"}'
    tx_time = dmy(header.get('tx_timestamp'))
    if tx_time:
        tx_line += f'   |   {tx_time}'
    draw_context_banner(pdf, font, f'Transakcija: {tx_line}')

    draw_kv_header_block(pdf, font, header)

    draw_entries_table(
        pdf, font, entries,
        usable_width=usable_width,
        empty_message='Nema zabelezenih pristupa ovoj transakciji.',
    )

    pdf.ln(6)
    pdf.set_font(font, '', 8)
    pdf.set_text_color(*TEXT_GRAY)
    pdf.multi_cell(
        usable_width, 4.5,
        'Svaka stavka predstavlja poseban pristup ovoj konkretnoj transakciji prilikom pokretanja taint analize. '
        'Datum i potpis odgovaraju trenutku pristupa (pokretanja analize), a ne trenutku same transakcije na lancu. '
        'Puni tekst opisa radnje, ako je skracen u tabeli, dostupan je na stranici "Lanac dokaza" u aplikaciji.',
    )

    return bytes(pdf.output())
