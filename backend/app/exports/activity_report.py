"""Builds the activity-log report (PDF and CSV).

Generated on the server, straight from the append-only log file, rather than in the
browser from whatever the page happens to have loaded. For a document that claims "these
are all the actions in period X", the data has to come from the authoritative source.

The PDF is bilingual (SR/EN, chosen on the signing modal) and, since it leaves the app as
a standalone document, carries the same signed/verifiable shape as every other report in
this app (Taint/Pathfinding/DEX Swap/Behavioral, all built client-side): a cat emblem in
the header, the analyst's drawn signature, the Lusi seal, and a verification code + content
hash printed on the last page - see _draw_signature_section. The CSV export stays
unsigned and Serbian-only, same as this app's other raw-data CSV exports (e.g. the DEX
Swap/transactions CSV) - it is not itself a presentation document.
"""

from __future__ import annotations

import colorsys
import csv
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from PIL import Image

from app.exports.custody_pdf_common import decode_signature, draw_signature
from app.exports.pdf_fonts import register_unicode_font


Lang = Literal['sr', 'en']

ASSETS_DIR = Path(__file__).resolve().parent.parent / 'assets'

_NAVY = (13, 24, 40)
_ACCENT = (43, 130, 191)
_LIGHT_ROW = (240, 245, 250)
_TEXT_GRAY = (100, 112, 128)
_TEXT_DARK = (24, 28, 36)
_WHITE = (255, 255, 255)
_CARD_BG = (243, 247, 252)


def _L(lang: str, sr: str, en: str) -> str:
    return sr if lang == 'sr' else en


ACTION_LABELS: dict[str, tuple[str, str]] = {
    'csv_upload': ('Otpremljena CSV evidencija', 'Uploaded CSV evidence'),
    # Backend automatically splits a multi-currency upload into one evidence file per
    # currency (see upload.py's _split_and_store_by_currency) - kept in sync with the
    # frontend's own ACTION_PRESENTATION (activity-log.component.ts).
    'csv_upload_split': ('Razdvojena CSV evidencija (po valuti)', 'CSV evidence split (by currency)'),
    'evidence_removed': ('Uklonjena evidencija iz slučaja', 'Removed evidence from case'),
    'analytics_run': ('Pokrenuta analiza', 'Ran analysis'),
    'path_finding': ('Pretraga putanja', 'Pathfinding search'),
    'dex_swap_analysis_run': ('Pokrenuta DEX swap analiza', 'Ran DEX swap analysis'),
    'behavioral_analysis_run': ('Pokrenuta bihevioralna analiza', 'Ran behavioral analysis'),
    'token_approval_analysis_run': ('Pokrenuta Token Approval analiza', 'Ran Token Approval analysis'),
    'case_created': ('Kreiran slučaj', 'Case created'),
    'case_status_changed': ('Promenjen status slučaja', 'Case status changed'),
    'case_deleted': ('Obrisan slučaj', 'Case deleted'),
    'test_suite_run': ('Pokrenuti sistemski testovi', 'Ran system tests'),
    'test_scenarios_run': ('Pokrenuti validacioni scenariji', 'Ran validation scenarios'),
    'test_scenario_created': ('Kreiran validacioni scenario', 'Validation scenario created'),
    'test_scenario_updated': ('Izmenjen validacioni scenario', 'Validation scenario updated'),
    'test_scenario_deleted': ('Obrisan validacioni scenario', 'Validation scenario deleted'),
    'activity_report_exported': ('Izvezen izveštaj aktivnosti', 'Activity report exported'),
    'custody_pdf_exported': ('Izvezen lanac dokaza (PDF)', 'Chain of custody exported (PDF)'),
    'report_signed': ('Izvezen potpisan izveštaj (PDF)', 'Signed report exported (PDF)'),
}

# Report type (see reports.py's RegisterReportRequest.report_type) -> human label, shared
# between _report_signed_summary and the frontend's own copy (report-verification
# .component.ts's summaryLabels / activity-log.component.ts's REPORT_TYPE_LABELS).
_REPORT_TYPE_LABELS: dict[str, tuple[str, str]] = {
    'taint': ('Taint izveštaj', 'Taint report'),
    'pathfinding': ('Pathfinding izveštaj', 'Pathfinding report'),
    'dex_swap': ('DEX Swap izveštaj', 'DEX Swap report'),
    'behavioral': ('Bihevioralni izveštaj', 'Behavioral report'),
    'case_triage': ('Izveštaj za trijažu', 'Triage report'),
    'graph_analysis': ('Izveštaj analize grafa', 'Graph analysis report'),
    'activity_log': ('Izveštaj aktivnosti', 'Activity report'),
}


def action_label(action: str, lang: Lang = 'sr') -> str:
    """Human label for an action, matching what the Log aktivnosti page shows.

    Unknown actions fall back to their raw name rather than being relabelled or hidden - a
    report that quietly omitted an action it did not recognise would be worse than one
    showing a technical string.
    """
    pair = ACTION_LABELS.get(action)
    if pair:
        return _L(lang, *pair)
    if action.startswith('onchain_fetch'):
        return _L(lang, 'Povučene transakcije sa blockchain-a', 'Fetched transactions from the blockchain')
    return action


# --- Per-action colour, matching the on-screen log exactly (see activity-log.component
# .ts's ACTION_HUE) - one hue per action, evenly spaced, rather than a handful of group
# colours shared by unrelated actions. Fixed order (index * step, not a hash) so the same
# action always lands on the same hue in both the page and this PDF. ------------------
ACTION_HUE_ORDER: tuple[str, ...] = (
    'csv_upload',
    'csv_upload_split',
    'onchain_fetch',
    'analytics_run',
    'path_finding',
    'dex_swap_analysis_run',
    'behavioral_analysis_run',
    'token_approval_analysis_run',
    'case_created',
    'case_status_changed',
    'case_deleted',
    'test_suite_run',
    'test_scenarios_run',
    'test_scenario_created',
    'test_scenario_updated',
    'test_scenario_deleted',
    'activity_report_exported',
    'custody_pdf_exported',
    'report_signed',
    'other',
)

_ACTION_HUE: dict[str, int] = {
    key: round((200 + index * (360 / len(ACTION_HUE_ORDER))) % 360) for index, key in enumerate(ACTION_HUE_ORDER)
}


def _hsl_to_rgb(hue_deg: float, saturation: float, lightness: float) -> tuple[int, int, int]:
    # colorsys takes H/L/S in that order (not H/S/L) - easy to transpose by accident.
    red, green, blue = colorsys.hls_to_rgb(hue_deg / 360.0, lightness, saturation)
    return (round(red * 255), round(green * 255), round(blue * 255))


def action_color(action: str) -> tuple[int, int, int]:
    """Per-action colour - same hue as the on-screen tag, rendered darker/more saturated
    for print on white paper (the on-screen dark theme uses high lightness instead)."""
    if action in _ACTION_HUE:
        hue = _ACTION_HUE[action]
    elif action.startswith('onchain_fetch'):
        hue = _ACTION_HUE['onchain_fetch']
    else:
        hue = _ACTION_HUE['other']
    return _hsl_to_rgb(hue, 0.72, 0.38)


def format_tz_label(tz_offset_minutes: int) -> str:
    """-120 (JS convention for UTC+2) -> "UTC+02:00"."""
    total = -tz_offset_minutes
    sign = '+' if total >= 0 else '-'
    total = abs(total)
    return f'UTC{sign}{total // 60:02d}:{total % 60:02d}'


def format_period(date_from: str | None, date_to: str | None, lang: Lang = 'sr') -> str:
    if not date_from and not date_to:
        return _L(lang, 'Sve aktivnosti (od početka korišćenja sistema)', 'All activity (since the system was first used)')
    if date_from and date_to and date_from == date_to:
        return f'{_L(lang, "Jedan dan", "One day")}: {_dmy(date_from)}'
    if date_from and date_to:
        return f'{_L(lang, "Od", "From")} {_dmy(date_from)} {_L(lang, "do", "to")} {_dmy(date_to)}'
    if date_from:
        return f'{_L(lang, "Od", "From")} {_dmy(date_from)} {_L(lang, "do danas", "to today")}'
    return f'{_L(lang, "Do", "To")} {_dmy(date_to)}'


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


_WEEKDAYS_SR = ('ponedeljak', 'utorak', 'sreda', 'četvrtak', 'petak', 'subota', 'nedelja')
_WEEKDAYS_EN = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')


def _local_day_heading(timestamp: str | None, tz_offset_minutes: int, lang: Lang = 'sr') -> str:
    local = _to_local(timestamp, tz_offset_minutes)
    if not local:
        return _L(lang, 'Nepoznat datum', 'Unknown date')
    weekday = (_WEEKDAYS_SR if lang == 'sr' else _WEEKDAYS_EN)[local.weekday()]
    return f'{local.strftime("%d.%m.%Y.")} — {weekday}'


def summarize_details(entry: dict[str, Any], lang: Lang = 'sr') -> str:
    """One-line "what exactly happened", mirroring the summary shown on the page (see
    activity-log.component.ts's own summary())."""
    action = str(entry.get('action') or '')
    details = entry.get('details') or {}
    L = lambda sr, en: _L(lang, sr, en)  # noqa: E731

    if action == 'analytics_run':
        seed_count = details.get('seed_count', 0)
        scope = details.get('evidence_scope', 'combined')
        scope_text = L('sva evidencija (kombinovano)', 'all evidence (combined)') if scope == 'combined' else str(scope)
        summary = f'{seed_count} {L("izvora (seed)", "seed addresses")} · {scope_text}'
        if details.get('custody_recorded'):
            tx_rows = details.get('custody_transaction_rows', 0)
            evidence_files = details.get('custody_evidence_files', 0)
            summary += f' · {L("lanac dokaza", "chain of custody")}: {tx_rows} {L("transakcija", "transactions")}, {evidence_files} {L("fajl(ova)", "file(s)")}'
        return summary
    if action == 'dex_swap_analysis_run':
        address = details.get('address') or L('sve adrese', 'all addresses')
        scope = details.get('evidence_scope', 'combined')
        scope_text = L('sva evidencija (kombinovano)', 'all evidence (combined)') if scope == 'combined' else str(scope)
        summary = f'{address} · {scope_text} · {details.get("total_events", 0)} {L("dogadjaja", "events")}'
        if details.get('custody_recorded'):
            tx_rows = details.get('custody_transaction_rows', 0)
            evidence_files = details.get('custody_evidence_files', 0)
            summary += f' · {L("lanac dokaza", "chain of custody")}: {tx_rows} {L("transakcija", "transactions")}, {evidence_files} {L("fajl(ova)", "file(s)")}'
        return summary
    if action == 'token_approval_analysis_run':
        address = details.get('address') or L('sve adrese', 'all addresses')
        scope = details.get('evidence_scope', 'combined')
        scope_text = L('sva evidencija (kombinovano)', 'all evidence (combined)') if scope == 'combined' else str(scope)
        summary = f'{address} · {scope_text} · {details.get("correlation_count", 0)} {L("odobrenja", "grants")}'
        if details.get('custody_recorded'):
            tx_rows = details.get('custody_transaction_rows', 0)
            evidence_files = details.get('custody_evidence_files', 0)
            findings = details.get('token_approval_findings_recorded', 0)
            summary += (
                f' · {L("lanac dokaza", "chain of custody")}: {tx_rows} {L("transakcija", "transactions")}, '
                f'{evidence_files} {L("fajl(ova)", "file(s)")}, {findings} {L("TOKEN_APPROVAL nalaza", "TOKEN_APPROVAL findings")}'
            )
        return summary
    if action == 'test_suite_run':
        return f'{details.get("passed", 0)}/{details.get("total", 0)} {L("testova prošlo", "tests passed")}'
    if action == 'test_scenarios_run':
        return f'{details.get("passed", 0)}/{details.get("total", 0)} {L("scenarija prošlo", "scenarios passed")}'
    if action in ('test_scenario_created', 'test_scenario_updated', 'test_scenario_deleted'):
        return str(details.get('name') or details.get('scenario_id') or '')
    if action == 'path_finding':
        return f'{details.get("source_address", "?")} -> {details.get("target_address", "?")}'
    if action == 'case_status_changed':
        return f'{details.get("from", "?")} -> {details.get("to", "?")}'
    if action == 'csv_upload':
        return str(details.get('original_name') or entry.get('file_name') or '')
    if action == 'csv_upload_split':
        currency = str(details.get('currency') or '') or L('bez valute', 'no currency')
        return f'{details.get("source_file") or entry.get("file_name") or ""} · {currency}'
    if action.startswith('onchain_fetch'):
        rows = details.get('rows_fetched')
        query = details.get('query', '')
        return f'{query} · {rows} {L("transakcija", "transactions")}' if rows is not None else str(query)
    if action == 'activity_report_exported':
        period = _short_period(details.get('date_from'), details.get('date_to'), lang)
        users = details.get('users')
        users_text = f' · {", ".join(users)}' if isinstance(users, list) and users else ''
        return f'{str(details.get("format", "")).upper()} · {details.get("entry_count", 0)} {L("zapisa", "entries")} · {period}{users_text}'
    if action == 'custody_pdf_exported':
        scope = details.get('scope')
        target = details.get('tx_id') or details.get('evidence_stored_name') or '?'
        scope_text = L('transakcija', 'transaction') if scope == 'transaction' else L('dokazni fajl', 'evidence file')
        return f'{scope_text}: {target} · {details.get("entry_count", 0)} {L("zapisa", "entries")}'
    if action == 'report_signed':
        return _report_signed_summary(details, lang)
    return str(entry.get('file_name') or '')


def _report_signed_summary(details: dict[str, Any], lang: Lang = 'sr') -> str:
    """Every registerReport() caller (Taint/Pathfinding/DEX Swap/...) sends its own
    free-form `summary` dict alongside report_type - this picks out the one or two numbers
    that actually distinguish one signed report from another of the same kind, the same
    way each page's own on-screen summary does. Falls back to just the type + code for an
    unrecognized/missing report_type, rather than guessing at unfamiliar summary keys."""
    L = lambda sr, en: _L(lang, sr, en)  # noqa: E731
    report_type = str(details.get('report_type') or '')
    type_pair = _REPORT_TYPE_LABELS.get(report_type)
    type_label = L(*type_pair) if type_pair else L('Izveštaj', 'Report')
    code = str(details.get('verification_code') or '?')

    extra = ''
    if report_type == 'taint':
        extra = f' · {details.get("tainted_addresses", 0)} {L("zaprljanih adresa", "tainted addresses")}, {details.get("cash_out_points", 0)} {L("tačaka unovčavanja", "cash-out points")}'
    elif report_type == 'pathfinding':
        extra = f' · {details.get("hops", 0)} {L("skokova", "hops")}'
    elif report_type == 'dex_swap':
        extra = f' · {details.get("total_events", 0)} {L("događaja", "events")}'
    elif report_type in ('case_triage', 'graph_analysis'):
        extra = f' · {details.get("nodes", 0)} {L("čvorova", "nodes")}, {details.get("edges", 0)} {L("veza", "edges")}, {details.get("blacklisted", 0)} {L("na crnoj listi", "blacklisted")}'

    return f'{type_label} · {code}{extra}'


def _short_period(date_from: Any, date_to: Any, lang: Lang = 'sr') -> str:
    """Compact form of the window a report covered, for one table cell."""
    L = lambda sr, en: _L(lang, sr, en)  # noqa: E731
    if not date_from and not date_to:
        return L('sve aktivnosti', 'all activity')
    if date_from and date_to and date_from == date_to:
        return f'{L("jedan dan", "one day")}: {_dmy(str(date_from))}'
    if date_from and date_to:
        return f'{_dmy(str(date_from))} – {_dmy(str(date_to))}'
    return f'{L("od", "from")} {_dmy(str(date_from))}' if date_from else f'{L("do", "to")} {_dmy(str(date_to))}'


def _scope_text(entry: dict[str, Any], lang: Lang = 'sr') -> str:
    L = lambda sr, en: _L(lang, sr, en)  # noqa: E731
    if entry.get('case_id'):
        name = entry.get('case_name') or L('naziv nije zabeležen', 'name not recorded')
        return f'{name} ({entry.get("case_id")})'
    action = str(entry.get('action') or '')
    if action.startswith('test_'):
        return L('Testovi', 'Tests')
    if action == 'path_finding':
        return L('Graf', 'Graph')
    if action == 'activity_report_exported':
        return L('Izveštaj', 'Report')
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
    def __init__(self, *, font_family: str, lang: Lang) -> None:
        super().__init__(format='A4', orientation='L')
        self._font_family = font_family
        self._lang = lang
        self.set_auto_page_break(auto=True, margin=18)
        self.set_top_margin(30)

    def header(self) -> None:  # noqa: D102 - fpdf2 lifecycle hook
        self.set_fill_color(*_NAVY)
        self.rect(0, 0, self.w, 22, style='F')

        title_x = 12.0
        cat_path = ASSETS_DIR / 'cat_pdf.png'
        if cat_path.exists():
            emblem = 16.0
            with Image.open(cat_path) as cat_img:
                emblem_w = emblem * (cat_img.width / cat_img.height)
            self.image(str(cat_path), x=12, y=(22 - emblem) / 2, w=emblem_w, h=emblem)
            title_x = 12 + emblem_w + 4

        self.set_text_color(*_WHITE)
        self.set_font(self._font_family, 'B', 14)
        self.set_xy(title_x, 5)
        self.cell(0, 8, _L(self._lang, 'Lusi v1.0 - Izveštaj aktivnosti', 'Lusi v1.0 - Activity report'), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font(self._font_family, '', 9)
        self.set_xy(title_x, 13)
        self.cell(0, 6, _L(self._lang, 'Chain of custody - zapis radnji analitičara', 'Chain of custody - a record of the analyst’s actions'), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(*_TEXT_DARK)
        self.set_xy(self.l_margin, 28)

    def footer(self) -> None:  # noqa: D102 - fpdf2 lifecycle hook
        self.set_y(-12)
        self.set_font(self._font_family, '', 8)
        self.set_text_color(*_TEXT_GRAY)
        page_word = _L(self._lang, 'Strana', 'Page')
        self.cell(0, 8, f'Lusi v1.0 forensic export | {page_word} {self.page_no()}', align='C')


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


def _draw_signature_section(
    pdf: FPDF,
    font: str,
    *,
    lang: Lang,
    declaration: str,
    signature_image: str | None,
    registration: dict[str, Any],
) -> None:
    """"Potpis i overa" - the same shape as every client-built signed report in this app
    (see e.g. dex-swap-analysis.component.ts's buildDexSwapPdf): declaration, drawn
    signature, seal, verification code + content hash, and the same limitation note."""
    L = lambda sr, en: _L(lang, sr, en)  # noqa: E731

    pdf.add_page()
    _section_title(pdf, font, L('Potpis i overa', 'Signature and certification'))

    pdf.set_font(font, '', 9.5)
    pdf.set_text_color(*_TEXT_DARK)
    pdf.multi_cell(0, 5.5, declaration, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    usable = pdf.w - pdf.l_margin - pdf.r_margin
    sig_box_w = usable * 0.42
    sig_box_h = 32
    sig_x = pdf.l_margin
    sig_y = pdf.get_y()
    pdf.set_draw_color(*_TEXT_GRAY)
    pdf.set_line_width(0.3)
    pdf.rect(sig_x, sig_y, sig_box_w, sig_box_h)

    signature = decode_signature(signature_image)
    if signature is not None:
        draw_signature(pdf, signature, x=sig_x + 2, y=sig_y + 2, box_w=sig_box_w - 4, box_h=sig_box_h - 4)

    pdf.set_xy(sig_x, sig_y + sig_box_h + 3)
    pdf.set_font(font, '', 8)
    pdf.set_text_color(*_TEXT_GRAY)
    pdf.cell(sig_box_w, 5, L('Potpis analitičara', 'Analyst signature'), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_x(sig_x)
    pdf.set_font(font, 'B', 9.5)
    pdf.set_text_color(*_TEXT_DARK)
    pdf.cell(sig_box_w, 5.5, str(registration.get('analyst') or ''), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    seal_path = ASSETS_DIR / 'seal.png'
    if seal_path.exists():
        seal_h = 30.0
        with Image.open(seal_path) as seal_img:
            seal_w = seal_h * (seal_img.width / seal_img.height)
        seal_cx = sig_x + sig_box_w + (usable - sig_box_w) / 2
        seal_cy = sig_y + sig_box_h / 2
        pdf.image(str(seal_path), x=seal_cx - seal_w / 2, y=seal_cy - seal_h / 2, w=seal_w, h=seal_h)

    pdf.set_y(sig_y + sig_box_h + 16)
    _section_title(pdf, font, L('Provera verodostojnosti', 'Authenticity check'))

    pdf.set_font(font, 'B', 9)
    pdf.set_text_color(*_TEXT_GRAY)
    pdf.cell(45, 6, L('KONTROLNI BROJ', 'VERIFICATION CODE'), new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.set_font('courier', 'B', 13)
    pdf.set_text_color(*_NAVY)
    pdf.cell(0, 6, str(registration.get('verification_code') or ''), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    pdf.set_font(font, 'B', 9)
    pdf.set_text_color(*_TEXT_GRAY)
    pdf.cell(45, 6, L('OTISAK SADRŽAJA', 'CONTENT HASH'), new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.set_font('courier', '', 7.5)
    pdf.set_text_color(*_TEXT_DARK)
    pdf.multi_cell(0, 5, str(registration.get('content_hash') or ''), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)

    pdf.set_font(font, '', 9)
    pdf.set_text_color(*_TEXT_DARK)
    pdf.multi_cell(
        0, 5.2,
        L(
            'Verodostojnost se proverava u aplikaciji Lusi, unosom gornjeg kontrolnog broja. Ako se otisak sadržaja '
            'poklapa sa zabeleženim, podaci u izveštaju su isti kao u trenutku izvoza. Ako se ne poklapa, izveštaj je '
            'izmenjen posle izvoza.',
            'Authenticity is verified in the Lusi application by entering the verification code above. If the content '
            'hash matches the recorded one, the data in the report is the same as at export time. If it does not '
            'match, the report was altered after export.',
        ),
        new_x=XPos.LMARGIN, new_y=YPos.NEXT,
    )
    pdf.ln(2)

    # No italic face is registered for the embedded Unicode font (see pdf_fonts.py's
    # register_unicode_font - only regular/bold), so this stays normal weight rather than
    # requesting a style that would raise FPDFException for that family.
    pdf.set_font(font, '', 8)
    pdf.set_text_color(*_TEXT_GRAY)
    pdf.multi_cell(
        0, 4.6,
        L(
            'Ograničenje: potpis iznad je izjava analitičara, a ne kriptografski dokaz — ostaje netaknut i ako neko '
            'izmeni dokument. Izmena se otkriva isključivo poređenjem otiska sadržaja. Provera potvrđuje da se PODACI '
            'poklapaju sa registrovanim, ne da je PDF fajl bajt-po-bajt isti.',
            'Limitation: the signature above is the analyst\'s declaration, not a cryptographic proof — it stays '
            'intact even if someone edits the document. An alteration is detected only by comparing the content hash. '
            'The check confirms the DATA matches what was registered, not that the PDF file is byte-for-byte '
            'identical.',
        ),
        new_x=XPos.LMARGIN, new_y=YPos.NEXT,
    )


def build_activity_pdf(
    entries: list[dict[str, Any]],
    *,
    generated_by: str,
    date_from: str | None,
    date_to: str | None,
    tz_offset_minutes: int,
    selected_users: list[str],
    scope: str,
    lang: Lang = 'sr',
    signing: dict[str, Any],
) -> bytes:
    L = lambda sr, en: _L(lang, sr, en)  # noqa: E731
    pdf = _ActivityReportPDF(font_family='helvetica', lang=lang)
    font = register_unicode_font(pdf)
    pdf._font_family = font  # noqa: SLF001 - header/footer need the resolved family
    pdf.add_page()

    tz_label = format_tz_label(tz_offset_minutes)
    users_in_period = sorted({str(entry.get('user') or '') for entry in entries if entry.get('user')})
    days_in_period = {_local_stamp(entry.get('timestamp'), tz_offset_minutes)[:10] for entry in entries}
    cases_in_period = {str(entry.get('case_id')) for entry in entries if entry.get('case_id')}

    _section_title(pdf, font, L('Podaci o izveštaju', 'Report details'))
    _kv_row(pdf, font, L('IZVEZAO', 'EXPORTED BY'), generated_by)
    _kv_row(pdf, font, L('GENERISANO', 'GENERATED AT'), f'{_local_stamp(datetime.now(timezone.utc).isoformat(), tz_offset_minutes)} ({tz_label})')
    _kv_row(pdf, font, L('PERIOD', 'PERIOD'), f'{format_period(date_from, date_to, lang)} — {L("vremenska zona", "time zone")} {tz_label}')
    _kv_row(
        pdf, font, L('OBUHVAĆENI KORISNICI', 'USERS COVERED'),
        ', '.join(selected_users) if selected_users else (L('svi korisnici', 'all users') if scope == 'all' else generated_by),
    )
    _kv_row(pdf, font, L('REDOSLED', 'ORDER'), L('Od najnovije ka najstarijoj akciji', 'Newest action first'))
    pdf.ln(3)

    _summary_cards(pdf, font, [
        (str(len(entries)), L('Ukupno akcija', 'Total actions')),
        (str(len(users_in_period)), L('Korisnika', 'Users')),
        (str(len(days_in_period)), L('Dana sa aktivnošću', 'Days with activity')),
        (str(len(cases_in_period)), L('Slučajeva', 'Cases')),
    ])

    if not entries:
        _section_title(pdf, font, L('Hronologija akcija', 'Action timeline'))
        pdf.set_font(font, '', 9.5)
        pdf.set_text_color(*_TEXT_GRAY)
        pdf.cell(0, 6, L('Nema zabeleženih akcija u izabranom periodu.', 'No recorded actions in the selected period.'), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        _draw_signature_section(
            pdf, font, lang=lang,
            declaration=str(signing.get('declaration') or ''),
            signature_image=signing.get('signature_image'),
            registration=signing.get('registration') or {},
        )
        return bytes(pdf.output())

    tally: dict[str, tuple[int, tuple[int, int, int]]] = {}
    for entry in entries:
        action = str(entry.get('action') or '')
        label = action_label(action, lang)
        count, colour = tally.get(label, (0, action_color(action)))
        tally[label] = (count + 1, colour)

    _section_title(pdf, font, L('Raspodela po tipu akcije', 'Distribution by action type'))
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
        _section_title(pdf, font, L('Raspodela po korisniku', 'Distribution by user'))
        _tally_table(pdf, font, [(user, count, None) for user, count in sorted(per_user.items(), key=lambda i: -i[1])], len(entries))

    _section_title(pdf, font, L('Hronologija akcija', 'Action timeline'))

    # Vreme/Korisnik/Akcija stay compact (measured against real content - short
    # timestamps, short usernames, the longest bilingual action label) so the
    # freed-up space can go to Slučaj/opseg and Detalji, which carry the longest,
    # most-often-truncated free text. Those two also render a size smaller (see
    # `detail_font_size` below) - still legible, but enough headroom that the
    # worst measured real-world strings fit without an ellipsis.
    widths = [14, 18, 54, 83, 0]
    widths[4] = (pdf.w - pdf.l_margin - pdf.r_margin) - sum(widths[:4])
    headers = [L('Vreme', 'Time'), L('Korisnik', 'User'), L('Akcija', 'Action'), L('Slučaj / opseg', 'Case / scope'), L('Detalji', 'Details')]
    row_font_sizes = [8, 8, 8, 7, 7]

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
        action_word = L('akcija', 'actions') if count != 1 else L('akcija', 'action')
        pdf.cell(sum(widths), 6.5, f'  {text}   ({count} {action_word})',
                 fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(*_TEXT_DARK)

    draw_header_row()

    # Grouped by day so a multi-day report can be scanned by date instead of read row by
    # row - the single biggest navigation aid in a long chronology.
    day_counts: dict[str, int] = {}
    for entry in entries:
        key = _local_day_heading(entry.get('timestamp'), tz_offset_minutes, lang)
        day_counts[key] = day_counts.get(key, 0) + 1

    # Row height is per-row, not fixed: Slučaj/opseg and Detalji wrap onto extra lines
    # instead of being cut off with "…" (see _cell_lines below), so a row with a long
    # detail string grows taller rather than hiding part of it - the same trade-off the
    # on-screen log makes in .summary-text (activity-log.component.scss).
    detail_font_size = row_font_sizes[4]
    line_h = detail_font_size * 0.62
    min_row_height = 6.0
    day_header_height = 8.0

    def _cell_lines(text: str, width: float) -> list[str]:
        pdf.set_font(font, '', detail_font_size)
        return pdf.multi_cell(max(width, 1.0), h=line_h, text=text, align='L', dry_run=True, output='LINES') or ['']

    current_day: str | None = None
    row_index = 0
    for entry in entries:
        action = str(entry.get('action') or '')
        local = _to_local(entry.get('timestamp'), tz_offset_minutes)

        scope_text = f' {_scope_text(entry, lang)}'
        detail_text = f' {summarize_details(entry, lang)}'
        n_lines = max(1, len(_cell_lines(scope_text, widths[3] - 2)), len(_cell_lines(detail_text, widths[4] - 2)))
        row_height = max(min_row_height, n_lines * line_h + 1.6)

        day = _local_day_heading(entry.get('timestamp'), tz_offset_minutes, lang)
        needed = row_height + (0 if day == current_day else day_header_height)
        if pdf.get_y() + needed > pdf.h - 24:
            pdf.add_page()
            draw_header_row()
            current_day = None

        if day != current_day:
            draw_day_heading(day, day_counts[day])
            current_day = day
            row_index = 0

        shaded = row_index % 2 == 1
        row_y = pdf.get_y()

        if shaded:
            pdf.set_fill_color(*_LIGHT_ROW)
            pdf.rect(pdf.l_margin, row_y, sum(widths), row_height, style='F')

        # Colour dot in front of the action, same coding as the on-screen log.
        pdf.set_fill_color(*action_color(action))
        pdf.ellipse(pdf.l_margin + widths[0] + widths[1] + 1.5, row_y + 2.1, 2.0, 2.0, style='F')

        pdf.set_xy(pdf.l_margin, row_y)
        pdf.set_text_color(*_TEXT_DARK)
        fixed_row = [
            f' {local.strftime("%H:%M:%S")}' if local else '',
            f' {entry.get("user") or ""}',
            f'    {action_label(action, lang)}',
        ]
        for width, value, size in zip(widths[:3], fixed_row, row_font_sizes[:3]):
            pdf.set_font(font, '', size)
            pdf.cell(width, row_height, _fit(pdf, value, width - 2), border=0, new_x=XPos.RIGHT, new_y=YPos.TOP)

        pdf.set_font(font, '', detail_font_size)
        pdf.set_xy(pdf.l_margin + sum(widths[:3]), row_y)
        pdf.multi_cell(widths[3] - 2, h=line_h, text=scope_text, border=0, align='L', new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.set_xy(pdf.l_margin + sum(widths[:4]), row_y)
        pdf.multi_cell(widths[4] - 2, h=line_h, text=detail_text, border=0, align='L', new_x=XPos.RIGHT, new_y=YPos.TOP)

        pdf.set_xy(pdf.l_margin, row_y + row_height)
        row_index += 1

    _draw_signature_section(
        pdf, font, lang=lang,
        declaration=str(signing.get('declaration') or ''),
        signature_image=signing.get('signature_image'),
        registration=signing.get('registration') or {},
    )

    return bytes(pdf.output())


def _fit(pdf: FPDF, text: str, max_width: float) -> str:
    """Truncates with an ellipsis so a long value can never spill into the next column."""
    if pdf.get_string_width(text) <= max_width:
        return text
    ellipsis = '…'
    while text and pdf.get_string_width(text + ellipsis) > max_width:
        text = text[:-1]
    return text + ellipsis
