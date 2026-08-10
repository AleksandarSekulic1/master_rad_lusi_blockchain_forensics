"""Builds the activity-log report (PDF and CSV).

Generated on the server, straight from the append-only log file, rather than in the
browser from whatever the page happens to have loaded. For a document that claims "these
are all the actions in period X", the data has to come from the authoritative source.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fpdf import FPDF
from fpdf.enums import XPos, YPos


_NAVY = (13, 24, 40)
_ACCENT = (43, 130, 191)
_LIGHT_ROW = (240, 245, 250)
_TEXT_GRAY = (100, 112, 128)
_TEXT_DARK = (24, 28, 36)
_WHITE = (255, 255, 255)

_UNICODE_FONT_CANDIDATES: tuple[tuple[Path, Path], ...] = (
    (Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'), Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')),
    (Path('/usr/share/fonts/dejavu/DejaVuSans.ttf'), Path('/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf')),
    (Path('C:/Windows/Fonts/arial.ttf'), Path('C:/Windows/Fonts/arialbd.ttf')),
)

ACTION_LABELS: dict[str, str] = {
    'csv_upload': 'Otpremljena CSV evidencija',
    'analytics_run': 'Pokrenuta analiza',
    'path_finding': 'Pretraga putanja',
    'case_created': 'Kreiran slučaj',
    'case_status_changed': 'Promenjen status slučaja',
    'case_deleted': 'Obrisan slučaj',
    'test_suite_run': 'Pokrenuti sistemski testovi',
    'test_scenarios_run': 'Pokrenuti validacioni scenariji',
    'test_scenario_created': 'Kreiran validacioni scenario',
    'test_scenario_updated': 'Izmenjen validacioni scenario',
    'test_scenario_deleted': 'Obrisan validacioni scenario',
    'activity_report_exported': 'Izvezen izveštaj aktivnosti',
}


def action_label(action: str) -> str:
    """Human label for an action, matching what the Log aktivnosti page shows.

    Unknown actions fall back to their raw name rather than being relabelled or hidden - a
    report that quietly omitted an action it did not recognise would be worse than one
    showing a technical string.
    """
    if action in ACTION_LABELS:
        return ACTION_LABELS[action]
    if action.startswith('onchain_fetch'):
        return 'Povučene transakcije sa blockchain-a'
    return action


def format_tz_label(tz_offset_minutes: int) -> str:
    """-120 (JS convention for UTC+2) -> "UTC+02:00"."""
    total = -tz_offset_minutes
    sign = '+' if total >= 0 else '-'
    total = abs(total)
    return f'UTC{sign}{total // 60:02d}:{total % 60:02d}'


def format_period(date_from: str | None, date_to: str | None) -> str:
    if not date_from and not date_to:
        return 'Sve aktivnosti (od početka korišćenja sistema)'
    if date_from and date_to and date_from == date_to:
        return f'Jedan dan: {_dmy(date_from)}'
    if date_from and date_to:
        return f'Od {_dmy(date_from)} do {_dmy(date_to)}'
    if date_from:
        return f'Od {_dmy(date_from)} do danas'
    return f'Do {_dmy(date_to)}'


def _dmy(iso_date: str | None) -> str:
    if not iso_date:
        return ''
    try:
        return datetime.strptime(iso_date, '%Y-%m-%d').strftime('%d.%m.%Y.')
    except ValueError:
        return iso_date


def _local_stamp(timestamp: str | None, tz_offset_minutes: int) -> str:
    """Renders a stored UTC timestamp in the requested local zone, so the report reads the
    same as the screen it was exported from."""
    if not timestamp:
        return ''
    try:
        parsed = datetime.fromisoformat(str(timestamp))
    except ValueError:
        return str(timestamp)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    from datetime import timedelta

    local = parsed - timedelta(minutes=tz_offset_minutes)
    return local.strftime('%d.%m.%Y. %H:%M:%S')


def summarize_details(entry: dict[str, Any]) -> str:
    """One-line "what exactly happened", mirroring the summary shown on the page."""
    action = str(entry.get('action') or '')
    details = entry.get('details') or {}

    if action == 'analytics_run':
        seed_count = details.get('seed_count', 0)
        scope = details.get('evidence_scope', 'combined')
        scope_text = 'sva evidencija (kombinovano)' if scope == 'combined' else str(scope)
        return f'{seed_count} izvora (seed) · {scope_text}'
    if action == 'test_suite_run':
        return f'{details.get("passed", 0)}/{details.get("total", 0)} testova prošlo'
    if action == 'test_scenarios_run':
        return f'{details.get("passed", 0)}/{details.get("total", 0)} scenarija prošlo'
    if action in ('test_scenario_created', 'test_scenario_updated', 'test_scenario_deleted'):
        return str(details.get('name') or details.get('scenario_id') or '')
    if action == 'path_finding':
        return f'{details.get("source_address", "?")} -> {details.get("target_address", "?")}'
    if action == 'case_status_changed':
        return f'{details.get("from", "?")} -> {details.get("to", "?")}'
    if action == 'csv_upload':
        return str(details.get('original_name') or entry.get('file_name') or '')
    if action.startswith('onchain_fetch'):
        rows = details.get('rows_fetched')
        query = details.get('query', '')
        return f'{query} · {rows} transakcija' if rows is not None else str(query)
    if action == 'activity_report_exported':
        return f'{details.get("format", "")} · {details.get("entry_count", 0)} zapisa'
    return str(entry.get('file_name') or '')


def _scope_text(entry: dict[str, Any]) -> str:
    if entry.get('case_id'):
        name = entry.get('case_name') or 'naziv nije zabeležen'
        return f'{name} ({entry.get("case_id")})'
    action = str(entry.get('action') or '')
    if action.startswith('test_'):
        return 'Testovi'
    if action == 'path_finding':
        return 'Graf'
    return '-'


def build_activity_csv(
    entries: list[dict[str, Any]],
    *,
    tz_offset_minutes: int,
) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['vreme_lokalno', 'vreme_utc', 'korisnik', 'akcija', 'akcija_kod', 'slucaj', 'slucaj_id', 'detalji', 'fajl', 'sha256'])
    for entry in entries:
        writer.writerow([
            _local_stamp(entry.get('timestamp'), tz_offset_minutes),
            entry.get('timestamp') or '',
            entry.get('user') or '',
            action_label(str(entry.get('action') or '')),
            entry.get('action') or '',
            entry.get('case_name') or '',
            entry.get('case_id') or '',
            summarize_details(entry),
            entry.get('file_name') or '',
            entry.get('sha256') or '',
        ])
    return buffer.getvalue()


class _ActivityReportPDF(FPDF):
    def __init__(self, *, font_family: str) -> None:
        super().__init__(format='A4', orientation='L')
        self._font_family = font_family
        self.set_auto_page_break(auto=True, margin=18)
        self.set_top_margin(30)

    def header(self) -> None:  # noqa: D102 - fpdf2 lifecycle hook
        self.set_fill_color(*_NAVY)
        self.rect(0, 0, self.w, 22, style='F')
        self.set_text_color(*_WHITE)
        self.set_font(self._font_family, 'B', 14)
        self.set_xy(12, 5)
        self.cell(0, 8, 'Lusi v1.0 - Izveštaj aktivnosti', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font(self._font_family, '', 9)
        self.set_xy(12, 13)
        self.cell(0, 6, 'Chain of custody - zapis radnji analitičara', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(*_TEXT_DARK)
        self.set_xy(self.l_margin, 28)

    def footer(self) -> None:  # noqa: D102 - fpdf2 lifecycle hook
        self.set_y(-12)
        self.set_font(self._font_family, '', 8)
        self.set_text_color(*_TEXT_GRAY)
        self.cell(0, 8, f'Lusi v1.0 forensic export | Strana {self.page_no()}', align='C')


def _register_font(pdf: FPDF) -> str:
    """Registers a Unicode TTF so č/ć/š/ž/đ render properly; falls back to an ASCII-only
    core font only if no TTF is present on the host."""
    for regular_path, bold_path in _UNICODE_FONT_CANDIDATES:
        if not regular_path.exists():
            continue
        pdf.add_font('LusiSans', '', str(regular_path))
        pdf.add_font('LusiSans', 'B', str(bold_path if bold_path.exists() else regular_path))
        return 'LusiSans'
    return 'helvetica'


def _section_title(pdf: FPDF, font: str, title: str) -> None:
    pdf.ln(2)
    pdf.set_font(font, 'B', 12)
    pdf.set_text_color(*_NAVY)
    pdf.cell(0, 8, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    y = pdf.get_y()
    pdf.set_draw_color(*_ACCENT)
    pdf.set_line_width(0.6)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(3)
    pdf.set_text_color(*_TEXT_DARK)


def _kv_row(pdf: FPDF, font: str, label: str, value: str) -> None:
    pdf.set_font(font, 'B', 9)
    pdf.set_text_color(*_TEXT_GRAY)
    pdf.cell(45, 5.5, label, new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.set_font(font, '', 9.5)
    pdf.set_text_color(*_TEXT_DARK)
    pdf.multi_cell(0, 5.5, value, new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def build_activity_pdf(
    entries: list[dict[str, Any]],
    *,
    generated_by: str,
    date_from: str | None,
    date_to: str | None,
    tz_offset_minutes: int,
    selected_users: list[str],
    scope: str,
) -> bytes:
    pdf = _ActivityReportPDF(font_family='helvetica')
    font = _register_font(pdf)
    pdf._font_family = font  # noqa: SLF001 - header/footer need the resolved family
    pdf.add_page()

    tz_label = format_tz_label(tz_offset_minutes)

    _section_title(pdf, font, 'Podaci o izveštaju')
    _kv_row(pdf, font, 'IZVEZAO', generated_by)
    _kv_row(pdf, font, 'GENERISANO', f'{_local_stamp(datetime.now(timezone.utc).isoformat(), tz_offset_minutes)} ({tz_label})')
    _kv_row(pdf, font, 'PERIOD', f'{format_period(date_from, date_to)} — vremenska zona {tz_label}')
    _kv_row(
        pdf, font, 'OBUHVAĆENI KORISNICI',
        ', '.join(selected_users) if selected_users else ('svi korisnici' if scope == 'all' else generated_by),
    )
    _kv_row(pdf, font, 'UKUPNO ZAPISA', str(len(entries)))
    pdf.ln(2)

    # Per-action tally, so the reader gets the shape of the period before the raw rows.
    tally: dict[str, int] = {}
    for entry in entries:
        key = action_label(str(entry.get('action') or ''))
        tally[key] = tally.get(key, 0) + 1
    if tally:
        _section_title(pdf, font, 'Rezime po tipu akcije')
        pdf.set_font(font, '', 9.5)
        for label, count in sorted(tally.items(), key=lambda item: -item[1]):
            pdf.cell(0, 5.5, f'{count} x  {label}', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)

    _section_title(pdf, font, 'Hronologija akcija')
    if not entries:
        pdf.set_font(font, '', 9.5)
        pdf.set_text_color(*_TEXT_GRAY)
        pdf.cell(0, 6, 'Nema zabeleženih akcija u izabranom periodu.', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        return bytes(pdf.output())

    widths = [38, 26, 58, 62, 0]
    widths[4] = (pdf.w - pdf.l_margin - pdf.r_margin) - sum(widths[:4])
    headers = ['Vreme', 'Korisnik', 'Akcija', 'Slučaj / opseg', 'Detalji']

    def draw_header_row() -> None:
        pdf.set_font(font, 'B', 8.5)
        pdf.set_fill_color(*_NAVY)
        pdf.set_text_color(*_WHITE)
        for width, title in zip(widths, headers):
            pdf.cell(width, 7, title, border=0, fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.ln(7)
        pdf.set_text_color(*_TEXT_DARK)

    draw_header_row()
    pdf.set_font(font, '', 8)
    for index, entry in enumerate(entries):
        # Reprint the header after an automatic page break, otherwise continuation pages
        # arrive as unlabelled columns.
        if pdf.get_y() > pdf.h - 26:
            pdf.add_page()
            draw_header_row()
            pdf.set_font(font, '', 8)

        if index % 2 == 1:
            pdf.set_fill_color(*_LIGHT_ROW)
        row = [
            _local_stamp(entry.get('timestamp'), tz_offset_minutes),
            str(entry.get('user') or ''),
            action_label(str(entry.get('action') or '')),
            _scope_text(entry),
            summarize_details(entry),
        ]
        for width, value in zip(widths, row):
            pdf.cell(width, 6, _fit(pdf, value, width - 2), border=0, fill=index % 2 == 1, new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.ln(6)

    return bytes(pdf.output())


def _fit(pdf: FPDF, text: str, max_width: float) -> str:
    """Truncates with an ellipsis so a long value can never spill into the next column."""
    if pdf.get_string_width(text) <= max_width:
        return text
    ellipsis = '…'
    while text and pdf.get_string_width(text + ellipsis) > max_width:
        text = text[:-1]
    return text + ellipsis
