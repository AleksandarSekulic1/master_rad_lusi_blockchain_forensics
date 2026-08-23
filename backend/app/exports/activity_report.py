"""Builds the activity-log report (PDF and CSV).

Generated on the server, straight from the append-only log file, rather than in the
browser from whatever the page happens to have loaded. For a document that claims "these
are all the actions in period X", the data has to come from the authoritative source.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Any

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from app.exports.pdf_fonts import register_unicode_font


_NAVY = (13, 24, 40)
_ACCENT = (43, 130, 191)
_LIGHT_ROW = (240, 245, 250)
_TEXT_GRAY = (100, 112, 128)
_TEXT_DARK = (24, 28, 36)
_WHITE = (255, 255, 255)

_GROUP_EVIDENCE = (43, 130, 191)
_GROUP_ANALYSIS = (217, 119, 6)
_GROUP_CASE = (124, 92, 200)
_GROUP_TEST = (34, 150, 88)
_GROUP_REPORT = (99, 110, 200)
_GROUP_CUSTODY = (190, 60, 120)
_GROUP_OTHER = (120, 130, 145)
_CARD_BG = (243, 247, 252)

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
    'custody_pdf_exported': 'Izvezen lanac dokaza (PDF)',
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


def action_color(action: str) -> tuple[int, int, int]:
    """Colour by KIND of action, matching the Log aktivnosti page - so someone reading the
    report and someone reading the screen are looking at the same visual language."""
    if action.startswith('test_'):
        return _GROUP_TEST
    if action.startswith('case_'):
        return _GROUP_CASE
    if action in ('analytics_run', 'path_finding'):
        return _GROUP_ANALYSIS
    if action == 'csv_upload' or action.startswith('onchain_fetch'):
        return _GROUP_EVIDENCE
    if action == 'activity_report_exported':
        return _GROUP_REPORT
    if action == 'custody_pdf_exported':
        return _GROUP_CUSTODY
    return _GROUP_OTHER


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


def _to_local(timestamp: str | None, tz_offset_minutes: int) -> datetime | None:
    if not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(timestamp))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed - timedelta(minutes=tz_offset_minutes)


def _local_stamp(timestamp: str | None, tz_offset_minutes: int) -> str:
    """Renders a stored UTC timestamp in the requested local zone, so the report reads the
    same as the screen it was exported from."""
    local = _to_local(timestamp, tz_offset_minutes)
    return local.strftime('%d.%m.%Y. %H:%M:%S') if local else str(timestamp or '')


_WEEKDAYS = ('ponedeljak', 'utorak', 'sreda', 'četvrtak', 'petak', 'subota', 'nedelja')


def _local_day_heading(timestamp: str | None, tz_offset_minutes: int) -> str:
    local = _to_local(timestamp, tz_offset_minutes)
    if not local:
        return 'Nepoznat datum'
    return f'{local.strftime("%d.%m.%Y.")} — {_WEEKDAYS[local.weekday()]}'


def summarize_details(entry: dict[str, Any]) -> str:
    """One-line "what exactly happened", mirroring the summary shown on the page."""
    action = str(entry.get('action') or '')
    details = entry.get('details') or {}

    if action == 'analytics_run':
        seed_count = details.get('seed_count', 0)
        scope = details.get('evidence_scope', 'combined')
        scope_text = 'sva evidencija (kombinovano)' if scope == 'combined' else str(scope)
        summary = f'{seed_count} izvora (seed) · {scope_text}'
        if details.get('custody_recorded'):
            tx_rows = details.get('custody_transaction_rows', 0)
            evidence_files = details.get('custody_evidence_files', 0)
            summary += f' · lanac dokaza: {tx_rows} transakcija, {evidence_files} fajl(ova)'
        return summary
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
        period = _short_period(details.get('date_from'), details.get('date_to'))
        users = details.get('users')
        users_text = f' · {", ".join(users)}' if isinstance(users, list) and users else ''
        return f'{str(details.get("format", "")).upper()} · {details.get("entry_count", 0)} zapisa · {period}{users_text}'
    if action == 'custody_pdf_exported':
        scope = details.get('scope')
        target = details.get('tx_id') or details.get('evidence_stored_name') or '?'
        scope_text = 'transakcija' if scope == 'transaction' else 'dokazni fajl'
        return f'{scope_text}: {target} · {details.get("entry_count", 0)} zapisa'
    return str(entry.get('file_name') or '')


def _short_period(date_from: Any, date_to: Any) -> str:
    """Compact form of the window a report covered, for one table cell."""
    if not date_from and not date_to:
        return 'sve aktivnosti'
    if date_from and date_to and date_from == date_to:
        return f'jedan dan: {_dmy(str(date_from))}'
    if date_from and date_to:
        return f'{_dmy(str(date_from))} – {_dmy(str(date_to))}'
    return f'od {_dmy(str(date_from))}' if date_from else f'do {_dmy(str(date_to))}'


def _scope_text(entry: dict[str, Any]) -> str:
    if entry.get('case_id'):
        name = entry.get('case_name') or 'naziv nije zabeležen'
        return f'{name} ({entry.get("case_id")})'
    action = str(entry.get('action') or '')
    if action.startswith('test_'):
        return 'Testovi'
    if action == 'path_finding':
        return 'Graf'
    if action == 'activity_report_exported':
        return 'Izveštaj'
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


def _summary_cards(pdf: FPDF, font: str, cards: list[tuple[str, str]]) -> None:
    """A row of stat tiles - the shape of the period at a glance, before any raw rows."""
    usable = pdf.w - pdf.l_margin - pdf.r_margin
    gap = 4
    width = (usable - gap * (len(cards) - 1)) / len(cards)
    y0 = pdf.get_y()
    x = pdf.l_margin
    for value, label in cards:
        pdf.set_fill_color(*_CARD_BG)
        pdf.set_draw_color(*_ACCENT)
        pdf.set_line_width(0.3)
        pdf.rect(x, y0, width, 16, style='DF')
        pdf.set_xy(x, y0 + 2)
        pdf.set_font(font, 'B', 15)
        pdf.set_text_color(*_NAVY)
        pdf.cell(width, 7, value, align='C', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_xy(x, y0 + 9.5)
        pdf.set_font(font, '', 7.5)
        pdf.set_text_color(*_TEXT_GRAY)
        pdf.cell(width, 5, label, align='C', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        x += width + gap
    pdf.set_xy(pdf.l_margin, y0 + 16 + 5)
    pdf.set_text_color(*_TEXT_DARK)


def _tally_table(pdf: FPDF, font: str, rows: list[tuple[str, int, tuple[int, int, int] | None]], total: int) -> None:
    """Count + label, with a proportional bar so the dominant activity is obvious without
    comparing numbers."""
    label_width = 78
    bar_width = 60
    for label, count, colour in rows:
        pdf.set_font(font, '', 9)
        pdf.set_text_color(*_TEXT_DARK)
        if colour:
            pdf.set_fill_color(*colour)
            pdf.ellipse(pdf.l_margin, pdf.get_y() + 1.8, 2.2, 2.2, style='F')
        pdf.set_x(pdf.l_margin + (5 if colour else 0))
        pdf.cell(label_width, 5.5, _fit(pdf, label, label_width - 2), new_x=XPos.RIGHT, new_y=YPos.TOP)

        pdf.set_font(font, 'B', 9)
        pdf.cell(14, 5.5, str(count), align='R', new_x=XPos.RIGHT, new_y=YPos.TOP)

        bar_x = pdf.get_x() + 4
        bar_y = pdf.get_y() + 1.4
        pdf.set_fill_color(225, 232, 240)
        pdf.rect(bar_x, bar_y, bar_width, 2.6, style='F')
        if total > 0:
            pdf.set_fill_color(*(colour or _ACCENT))
            pdf.rect(bar_x, bar_y, max(0.6, bar_width * count / total), 2.6, style='F')
        pdf.ln(5.8)
    pdf.ln(1)


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
    font = register_unicode_font(pdf)
    pdf._font_family = font  # noqa: SLF001 - header/footer need the resolved family
    pdf.add_page()

    tz_label = format_tz_label(tz_offset_minutes)
    users_in_period = sorted({str(entry.get('user') or '') for entry in entries if entry.get('user')})
    days_in_period = {_local_stamp(entry.get('timestamp'), tz_offset_minutes)[:10] for entry in entries}
    cases_in_period = {str(entry.get('case_id')) for entry in entries if entry.get('case_id')}

    _section_title(pdf, font, 'Podaci o izveštaju')
    _kv_row(pdf, font, 'IZVEZAO', generated_by)
    _kv_row(pdf, font, 'GENERISANO', f'{_local_stamp(datetime.now(timezone.utc).isoformat(), tz_offset_minutes)} ({tz_label})')
    _kv_row(pdf, font, 'PERIOD', f'{format_period(date_from, date_to)} — vremenska zona {tz_label}')
    _kv_row(
        pdf, font, 'OBUHVAĆENI KORISNICI',
        ', '.join(selected_users) if selected_users else ('svi korisnici' if scope == 'all' else generated_by),
    )
    _kv_row(pdf, font, 'REDOSLED', 'Od najnovije ka najstarijoj akciji')
    pdf.ln(3)

    _summary_cards(pdf, font, [
        (str(len(entries)), 'Ukupno akcija'),
        (str(len(users_in_period)), 'Korisnika'),
        (str(len(days_in_period)), 'Dana sa aktivnošću'),
        (str(len(cases_in_period)), 'Slučajeva'),
    ])

    if not entries:
        _section_title(pdf, font, 'Hronologija akcija')
        pdf.set_font(font, '', 9.5)
        pdf.set_text_color(*_TEXT_GRAY)
        pdf.cell(0, 6, 'Nema zabeleženih akcija u izabranom periodu.', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        return bytes(pdf.output())

    tally: dict[str, tuple[int, tuple[int, int, int]]] = {}
    for entry in entries:
        action = str(entry.get('action') or '')
        label = action_label(action)
        count, colour = tally.get(label, (0, action_color(action)))
        tally[label] = (count + 1, colour)

    _section_title(pdf, font, 'Raspodela po tipu akcije')
    _tally_table(
        pdf, font,
        [(label, count, colour) for label, (count, colour) in sorted(tally.items(), key=lambda item: -item[1][0])],
        len(entries),
    )

    # Only meaningful once more than one account is covered - for a single analyst's own
    # report it would just repeat the total.
    if len(users_in_period) > 1:
        per_user: dict[str, int] = {}
        for entry in entries:
            key = str(entry.get('user') or '')
            per_user[key] = per_user.get(key, 0) + 1
        _section_title(pdf, font, 'Raspodela po korisniku')
        _tally_table(pdf, font, [(user, count, None) for user, count in sorted(per_user.items(), key=lambda i: -i[1])], len(entries))

    _section_title(pdf, font, 'Hronologija akcija')

    widths = [24, 26, 58, 62, 0]
    widths[4] = (pdf.w - pdf.l_margin - pdf.r_margin) - sum(widths[:4])
    headers = ['Vreme', 'Korisnik', 'Akcija', 'Slučaj / opseg', 'Detalji']

    def draw_header_row() -> None:
        pdf.set_font(font, 'B', 8.5)
        pdf.set_fill_color(*_NAVY)
        pdf.set_text_color(*_WHITE)
        for width, title in zip(widths, headers):
            pdf.cell(width, 7, f' {title}', border=0, fill=True, new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.ln(7)
        pdf.set_text_color(*_TEXT_DARK)

    def draw_day_heading(text: str, count: int) -> None:
        pdf.ln(1.5)
        pdf.set_font(font, 'B', 9)
        pdf.set_fill_color(226, 235, 245)
        pdf.set_text_color(*_NAVY)
        pdf.cell(sum(widths), 6.5, f'  {text}   ({count} {"akcija" if count != 1 else "akcija"})',
                 fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(*_TEXT_DARK)

    draw_header_row()

    # Grouped by day so a multi-day report can be scanned by date instead of read row by
    # row - the single biggest navigation aid in a long chronology.
    day_counts: dict[str, int] = {}
    for entry in entries:
        key = _local_day_heading(entry.get('timestamp'), tz_offset_minutes)
        day_counts[key] = day_counts.get(key, 0) + 1

    current_day: str | None = None
    row_index = 0
    for entry in entries:
        if pdf.get_y() > pdf.h - 24:
            pdf.add_page()
            draw_header_row()
            current_day = None

        day = _local_day_heading(entry.get('timestamp'), tz_offset_minutes)
        if day != current_day:
            draw_day_heading(day, day_counts[day])
            current_day = day
            row_index = 0

        action = str(entry.get('action') or '')
        local = _to_local(entry.get('timestamp'), tz_offset_minutes)
        shaded = row_index % 2 == 1
        row_y = pdf.get_y()

        if shaded:
            pdf.set_fill_color(*_LIGHT_ROW)
            pdf.rect(pdf.l_margin, row_y, sum(widths), 6, style='F')

        # Colour dot in front of the action, same coding as the on-screen log.
        pdf.set_fill_color(*action_color(action))
        pdf.ellipse(pdf.l_margin + widths[0] + widths[1] + 1.5, row_y + 2.1, 2.0, 2.0, style='F')

        pdf.set_xy(pdf.l_margin, row_y)
        pdf.set_font(font, '', 8)
        pdf.set_text_color(*_TEXT_DARK)
        row = [
            f' {local.strftime("%H:%M:%S")}' if local else '',
            f' {entry.get("user") or ""}',
            f'    {action_label(action)}',
            f' {_scope_text(entry)}',
            f' {summarize_details(entry)}',
        ]
        for width, value in zip(widths, row):
            pdf.cell(width, 6, _fit(pdf, value, width - 2), border=0, new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.ln(6)
        row_index += 1

    return bytes(pdf.output())


def _fit(pdf: FPDF, text: str, max_width: float) -> str:
    """Truncates with an ellipsis so a long value can never spill into the next column."""
    if pdf.get_string_width(text) <= max_width:
        return text
    ellipsis = '…'
    while text and pdf.get_string_width(text + ellipsis) > max_width:
        text = text[:-1]
    return text + ellipsis
