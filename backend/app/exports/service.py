from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import pandas as pd
from fpdf import FPDF
from fpdf.enums import XPos, YPos

from app.analytics.case_graph import clean_evidence_frames, combine_frames, graph_summary
from app.analytics.graph_building import build_transaction_graph
from app.analytics.plugins.manager import run_plugin_pipeline


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
    (Path('C:/Windows/Fonts/segoeui.ttf'), Path('C:/Windows/Fonts/segoeuib.ttf')),
)


def _register_report_font(pdf: FPDF) -> str:
    """Registers a Unicode-capable TTF font so diacritics (č, ć, š, ž, đ) render correctly.

    Falls back to the built-in Helvetica core font (ASCII-only) if no TTF is found on disk.
    """
    for regular_path, bold_path in _UNICODE_FONT_CANDIDATES:
        if not regular_path.exists():
            continue

        pdf.add_font('LusiSans', '', str(regular_path))
        pdf.add_font('LusiSans', 'B', str(bold_path if bold_path.exists() else regular_path))
        return 'LusiSans'

    return 'helvetica'


def _safe_text(value: object) -> str:
    if value is None:
        return ''
    return str(value)


def build_case_export_context(
    *,
    case: dict[str, object],
    cleaned_frame: pd.DataFrame,
    graph: nx.MultiDiGraph,
    analytics: dict[str, object],
    audit_entries: list[dict[str, object]],
    evidence_contributions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        'case': case,
        'rows': int(len(cleaned_frame)),
        'nodes': int(graph.number_of_nodes()),
        'edges': int(graph.number_of_edges()),
        'analytics': analytics,
        'summary': graph_summary(graph),
        'audit_entries': audit_entries,
        'evidence_contributions': evidence_contributions or [],
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }


def _frame_addresses(frame: pd.DataFrame) -> set[str]:
    addresses: set[str] = set()
    for column in ('sender_address', 'recipient_address'):
        if column in frame.columns:
            addresses.update(str(value) for value in frame[column].dropna().tolist())
    return addresses


def _evidence_contribution(entry: dict[str, object], frame: pd.DataFrame, graph: nx.MultiDiGraph) -> dict[str, object]:
    """Summarizes what a single evidence file added to the case's combined analysis.

    Row/volume counts come straight from that file's own cleaned data; risk and blacklist
    counts are read back from the COMBINED graph, since an address's risk score can depend
    on its proximity to blacklisted addresses introduced by other evidence in the same case.
    """
    addresses = _frame_addresses(frame)
    high_risk = 0
    blacklisted = 0
    for address in addresses:
        attrs = graph.nodes.get(address)
        if not attrs:
            continue
        if int(attrs.get('risk_score', 0) or 0) >= 70:
            high_risk += 1
        if bool(attrs.get('blacklist_flag')):
            blacklisted += 1

    total_amount = float(frame['amount'].sum()) if 'amount' in frame.columns and not frame.empty else 0.0

    return {
        'file_name': _safe_text(entry.get('file_name')),
        'rows': int(len(frame)),
        'total_amount': total_amount,
        'addresses_touched': len(addresses),
        'high_risk_addresses': high_risk,
        'blacklisted_addresses': blacklisted,
    }


def build_case_csv_report(context: dict[str, object]) -> str:
    case = context['case']
    summary = context.get('summary', {}) or {}
    audit_entries = context['audit_entries'] if isinstance(context['audit_entries'], list) else []

    rows = [
        ['section', 'field', 'value'],
        ['case', 'id', _safe_text(case.get('id'))],
        ['case', 'name', _safe_text(case.get('name'))],
        ['case', 'analyst', _safe_text(case.get('analyst'))],
        ['case', 'created_at', _safe_text(case.get('created_at'))],
        ['case', 'updated_at', _safe_text(case.get('updated_at'))],
        ['summary', 'rows', _safe_text(context.get('rows'))],
        ['summary', 'nodes', _safe_text(context.get('nodes'))],
        ['summary', 'edges', _safe_text(context.get('edges'))],
        ['summary', 'blacklisted_nodes', _safe_text(summary.get('blacklisted_nodes', 0))],
        ['summary', 'high_risk_nodes', _safe_text(summary.get('high_risk_nodes', 0))],
        ['summary', 'clusters', _safe_text(summary.get('clusters', 0))],
    ]

    for entry in case.get('evidence', []):
        if not isinstance(entry, dict):
            continue
        rows.extend([
            ['evidence', 'file_name', _safe_text(entry.get('file_name'))],
            ['evidence', 'stored_name', _safe_text(entry.get('stored_name'))],
            ['evidence', 'imported_at', _safe_text(entry.get('imported_at'))],
            ['evidence', 'size_bytes', _safe_text(entry.get('size_bytes'))],
            ['evidence', 'sha256', _safe_text(entry.get('sha256'))],
            ['evidence', 'analyst', _safe_text(entry.get('analyst'))],
        ])

    for contribution in context.get('evidence_contributions', []) or []:
        rows.extend([
            ['evidence_contribution', 'file_name', _safe_text(contribution.get('file_name'))],
            ['evidence_contribution', 'rows', _safe_text(contribution.get('rows'))],
            ['evidence_contribution', 'total_amount', _safe_text(contribution.get('total_amount'))],
            ['evidence_contribution', 'addresses_touched', _safe_text(contribution.get('addresses_touched'))],
            ['evidence_contribution', 'high_risk_addresses', _safe_text(contribution.get('high_risk_addresses'))],
            ['evidence_contribution', 'blacklisted_addresses', _safe_text(contribution.get('blacklisted_addresses'))],
        ])

    for entry in audit_entries:
        rows.extend([
            ['audit_log', 'timestamp', _safe_text(entry.get('timestamp'))],
            ['audit_log', 'file_name', _safe_text(entry.get('file_name'))],
            ['audit_log', 'case_id', _safe_text(entry.get('case_id'))],
            ['audit_log', 'action', _safe_text(entry.get('action'))],
            ['audit_log', 'user', _safe_text(entry.get('user'))],
            ['audit_log', 'sha256', _safe_text(entry.get('sha256'))],
        ])

    return '\n'.join(','.join(_escape_csv_cell(value) for value in row) for row in rows)


class _CaseReportPDF(FPDF):
    def __init__(self, *, case_name: str, font_family: str) -> None:
        super().__init__(format='A4')
        self._case_name = case_name
        self._font_family = font_family
        self.set_auto_page_break(auto=True, margin=20)
        self.set_top_margin(30)

    def header(self) -> None:  # noqa: D102 - fpdf2 lifecycle hook
        self.set_fill_color(*_NAVY)
        self.rect(0, 0, self.w, 24, style='F')
        self.set_text_color(*_WHITE)
        self.set_font(self._font_family, 'B', 15)
        self.set_xy(12, 5)
        self.cell(0, 8, 'Lusi v1.0 - Investigation Report', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font(self._font_family, '', 10)
        self.set_xy(12, 14)
        self.cell(0, 6, f'Case: {self._case_name}', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(*_TEXT_DARK)
        self.set_xy(self.l_margin, 30)

    def footer(self) -> None:  # noqa: D102 - fpdf2 lifecycle hook
        self.set_y(-14)
        self.set_font(self._font_family, '', 8)
        self.set_text_color(*_TEXT_GRAY)
        self.cell(0, 8, f'Lusi v1.0 forensic export | Page {self.page_no()}', align='C')


def _section_title(pdf: FPDF, font_family: str, title: str) -> None:
    pdf.ln(3)
    pdf.set_font(font_family, 'B', 12)
    pdf.set_text_color(*_NAVY)
    pdf.cell(0, 8, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    y = pdf.get_y()
    pdf.set_draw_color(*_ACCENT)
    pdf.set_line_width(0.6)
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(3)
    pdf.set_text_color(*_TEXT_DARK)


def _kv_row(pdf: FPDF, font_family: str, label: str, value: str) -> None:
    pdf.set_font(font_family, 'B', 9.5)
    pdf.set_text_color(*_TEXT_GRAY)
    pdf.cell(38, 6, label, new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.set_font(font_family, '', 10)
    pdf.set_text_color(*_TEXT_DARK)
    pdf.multi_cell(0, 6, value or 'n/a', new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _draw_summary_cards(pdf: FPDF, font_family: str, cards: list[tuple[str, object, tuple[int, int, int]]]) -> None:
    usable_width = pdf.w - pdf.l_margin - pdf.r_margin
    gap = 3.0
    col_width = (usable_width - gap * (len(cards) - 1)) / len(cards)
    y0 = pdf.get_y()
    x = pdf.l_margin

    for label, value, color in cards:
        pdf.set_xy(x, y0)
        pdf.set_fill_color(*color)
        pdf.set_text_color(*_WHITE)
        pdf.set_font(font_family, 'B', 14)
        pdf.multi_cell(col_width, 9, _safe_text(value), align='C', fill=True, new_x=XPos.LEFT, new_y=YPos.TOP)
        pdf.set_xy(x, y0 + 9)
        pdf.set_font(font_family, '', 7.5)
        pdf.multi_cell(col_width, 6, label, align='C', fill=True, new_x=XPos.LEFT, new_y=YPos.TOP)
        x += col_width + gap

    pdf.set_xy(pdf.l_margin, y0 + 9 + 6 + 4)


def _draw_table(
    pdf: FPDF,
    font_family: str,
    *,
    headers: list[str],
    column_widths: list[float],
    rows: list[list[str]],
    empty_message: str,
) -> None:
    if not rows:
        pdf.set_font(font_family, '', 9.5)
        pdf.set_text_color(*_TEXT_GRAY)
        pdf.cell(0, 6, empty_message, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(*_TEXT_DARK)
        return

    line_height = 5.0
    pdf.set_font(font_family, 'B', 8.5)
    pdf.set_fill_color(*_NAVY)
    pdf.set_text_color(*_WHITE)
    for header, width in zip(headers, column_widths):
        pdf.cell(width, 7, header, border=0, fill=True, align='L', new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.ln(7)

    pdf.set_font(font_family, '', 8)
    for row_index, row in enumerate(rows):
        cell_line_counts = [
            max(1, len(pdf.multi_cell(width, line_height, cell_text, dry_run=True, output='LINES')))
            for cell_text, width in zip(row, column_widths)
        ]
        row_height = max(cell_line_counts) * line_height

        if pdf.get_y() + row_height > pdf.page_break_trigger:
            pdf.add_page()

        y0 = pdf.get_y()
        x0 = pdf.get_x()
        pdf.set_fill_color(*(_LIGHT_ROW if row_index % 2 == 0 else _WHITE))
        pdf.set_text_color(*_TEXT_DARK)

        x = x0
        for cell_text, width in zip(row, column_widths):
            pdf.set_xy(x, y0)
            pdf.multi_cell(width, line_height, cell_text, border=0, fill=True, align='L')
            x += width

        pdf.set_xy(x0, y0 + row_height)


def build_case_pdf_report(context: dict[str, object]) -> bytes:
    case = context['case']
    summary = context.get('summary', {}) or {}
    audit_entries = context['audit_entries'] if isinstance(context['audit_entries'], list) else []
    evidence_entries = [entry for entry in case.get('evidence', []) if isinstance(entry, dict)]

    pdf = _CaseReportPDF(case_name=_safe_text(case.get('name')), font_family='helvetica')
    font_family = _register_report_font(pdf)
    pdf._font_family = font_family  # header()/footer() read this on every page
    pdf.add_page()

    _kv_row(pdf, font_family, 'CASE ID', _safe_text(case.get('id')))
    _kv_row(pdf, font_family, 'ANALYST', _safe_text(case.get('analyst')))
    _kv_row(pdf, font_family, 'STATUS', _safe_text(case.get('status', 'open')))
    _kv_row(pdf, font_family, 'DESCRIPTION', _safe_text(case.get('description')) or 'n/a')
    _kv_row(pdf, font_family, 'CREATED AT', _safe_text(case.get('created_at')))
    _kv_row(pdf, font_family, 'UPDATED AT', _safe_text(case.get('updated_at')))
    _kv_row(pdf, font_family, 'GENERATED AT', _safe_text(context.get('generated_at')))

    _section_title(pdf, font_family, 'Analysis summary')
    _draw_summary_cards(
        pdf,
        font_family,
        [
            ('Data rows', context.get('rows', 0), _ACCENT),
            ('Nodes', context.get('nodes', 0), _ACCENT),
            ('Edges', context.get('edges', 0), _ACCENT),
            ('Blacklisted', summary.get('blacklisted_nodes', 0), (198, 40, 40) if summary.get('blacklisted_nodes', 0) else _TEXT_GRAY),
            ('High-risk', summary.get('high_risk_nodes', 0), (230, 126, 34) if summary.get('high_risk_nodes', 0) else _TEXT_GRAY),
            ('Clusters', summary.get('clusters', 0), _ACCENT),
        ],
    )

    _section_title(pdf, font_family, 'Evidence locker')
    _draw_table(
        pdf,
        font_family,
        headers=['File name', 'SHA-256', 'Size', 'Analyst', 'Imported at'],
        column_widths=[44, 62, 18, 26, 36],
        rows=[
            [
                _safe_text(entry.get('file_name')),
                _safe_text(entry.get('sha256')),
                _format_bytes(entry.get('size_bytes')),
                _safe_text(entry.get('analyst')),
                _safe_text(entry.get('imported_at')),
            ]
            for entry in evidence_entries
        ],
        empty_message='No evidence has been recorded for this case yet.',
    )

    evidence_contributions = context.get('evidence_contributions', []) or []
    _section_title(pdf, font_family, 'Evidence contribution breakdown')
    _draw_table(
        pdf,
        font_family,
        headers=['File name', 'Rows', 'Total amount', 'Addresses', 'High-risk', 'Blacklisted'],
        column_widths=[66, 16, 28, 24, 26, 26],
        rows=[
            [
                _safe_text(contribution.get('file_name')),
                _safe_text(contribution.get('rows')),
                f'{float(contribution.get("total_amount", 0) or 0):.4f}',
                _safe_text(contribution.get('addresses_touched')),
                _safe_text(contribution.get('high_risk_addresses')),
                _safe_text(contribution.get('blacklisted_addresses')),
            ]
            for contribution in evidence_contributions
        ],
        empty_message='No evidence contribution data available.',
    )

    _section_title(pdf, font_family, 'Audit log')
    _draw_table(
        pdf,
        font_family,
        headers=['Timestamp', 'Action', 'File', 'User'],
        column_widths=[42, 26, 66, 52],
        rows=[
            [
                _safe_text(entry.get('timestamp')),
                _safe_text(entry.get('action')),
                _safe_text(entry.get('file_name')),
                _safe_text(entry.get('user')),
            ]
            for entry in audit_entries
        ],
        empty_message='No audit log entries recorded for this case yet.',
    )

    return bytes(pdf.output())


def _format_bytes(value: object) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return 'n/a'

    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.2f} {unit}'
        size /= 1024
    return f'{size:.2f} GB'


_NODE_REPORT_ATTRIBUTES: tuple[tuple[str, str, str], ...] = (
    # (report_key, graphml/gexf attr.type, gexf attr.type)
    ('risk_score', 'int', 'integer'),
    ('risk_band', 'string', 'string'),
    ('cluster', 'string', 'string'),
    ('blacklisted', 'boolean', 'boolean'),
    ('flags', 'string', 'string'),
)


def _risk_color(risk_score: int, blacklisted: bool) -> tuple[int, int, int]:
    if blacklisted:
        return (211, 47, 47)
    if risk_score >= 80:
        return (244, 81, 30)
    if risk_score >= 60:
        return (251, 140, 0)
    if risk_score >= 35:
        return (253, 216, 53)
    if risk_score > 0:
        return (156, 204, 101)
    return (144, 164, 174)


def _node_report_attributes(node_id: object, data: dict[str, object]) -> dict[str, object]:
    risk_score = int(data.get('risk_score', 0) or 0)
    blacklisted = bool(data.get('blacklist_flag'))
    flags = [
        name
        for name, present in (
            ('blacklisted', blacklisted),
            ('peel_chain', bool(data.get('peel_chain_flag'))),
            ('chain_hop', bool(data.get('chain_hop_flag'))),
            ('anomaly', bool(data.get('anomaly_flag'))),
        )
        if present
    ]

    return {
        'label': _safe_text(data.get('label', node_id)),
        'risk_score': risk_score,
        'risk_band': _safe_text(data.get('risk_band') or 'none'),
        'cluster': _safe_text(data.get('cluster_id') or 'unclustered'),
        'blacklisted': blacklisted,
        'flags': '|'.join(flags) if flags else 'none',
        'color': _risk_color(risk_score, blacklisted),
    }


def _typed_value(value: object) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return _safe_text(value)


def build_graphml_export(graph: nx.MultiDiGraph) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:y="http://www.yworks.com/xml/graphml" '
        'xsi:schemaLocation="http://graphml.graphdrawing.org/xmlns '
        'http://graphml.graphdrawing.org/xmlns/1.0/graphml.xsd">',
    ]

    for index, (name, attr_type, _) in enumerate(_NODE_REPORT_ATTRIBUTES):
        lines.append(f'<key id="n{index}" for="node" attr.name="{name}" attr.type="{attr_type}" />')
    lines.append('<key id="ngfx" for="node" yfiles.type="nodegraphics" />')
    lines.append('<key id="e0" for="edge" attr.name="total_amount" attr.type="double" />')
    lines.append('<key id="e1" for="edge" attr.name="transaction_count" attr.type="int" />')

    lines.append('<graph edgedefault="directed">')

    for node_id, data in graph.nodes(data=True):
        report = _node_report_attributes(node_id, data)
        color_hex = '#{:02X}{:02X}{:02X}'.format(*report['color'])
        data_entries = ''.join(
            f'<data key="n{index}">{_escape_xml(_typed_value(report[name]))}</data>'
            for index, (name, _, _) in enumerate(_NODE_REPORT_ATTRIBUTES)
        )
        graphics = (
            f'<data key="ngfx"><y:ShapeNode>'
            f'<y:Fill color="{color_hex}" transparent="false" />'
            f'<y:NodeLabel>{_escape_xml(report["label"])}</y:NodeLabel>'
            f'</y:ShapeNode></data>'
        )
        lines.append(f'<node id="{_escape_xml(str(node_id))}">{data_entries}{graphics}</node>')

    for index, (source, target, data) in enumerate(graph.edges(data=True)):
        total_amount = _safe_text(data.get('total_amount', data.get('amount', 0)))
        transaction_count = _safe_text(data.get('transaction_count', 1))
        lines.append(
            f'<edge id="e{index}" source="{_escape_xml(str(source))}" target="{_escape_xml(str(target))}">'
            f'<data key="e0">{_escape_xml(total_amount)}</data>'
            f'<data key="e1">{_escape_xml(transaction_count)}</data>'
            f'</edge>'
        )

    lines.append('</graph>')
    lines.append('</graphml>')
    return '\n'.join(lines)


def build_gexf_export(graph: nx.MultiDiGraph) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gexf xmlns="http://www.gexf.net/1.3" xmlns:viz="http://www.gexf.net/1.3/viz" version="1.3">',
        '<graph mode="static" defaultedgetype="directed">',
        '<attributes class="node">',
    ]

    for index, (name, _, attr_type) in enumerate(_NODE_REPORT_ATTRIBUTES):
        lines.append(f'<attribute id="{index}" title="{name}" type="{attr_type}" />')
    lines.append('</attributes>')

    lines.append('<nodes>')
    for node_id, data in graph.nodes(data=True):
        report = _node_report_attributes(node_id, data)
        red, green, blue = report['color']
        attvalues = ''.join(
            f'<attvalue for="{index}" value="{_escape_xml(_typed_value(report[name]))}" />'
            for index, (name, _, _) in enumerate(_NODE_REPORT_ATTRIBUTES)
        )
        lines.append(
            f'<node id="{_escape_xml(str(node_id))}" label="{_escape_xml(report["label"])}">'
            f'<viz:color r="{red}" g="{green}" b="{blue}" />'
            f'<attvalues>{attvalues}</attvalues>'
            f'</node>'
        )
    lines.append('</nodes>')

    lines.append('<edges>')
    for index, (source, target, data) in enumerate(graph.edges(data=True)):
        weight = _safe_text(data.get('total_amount', data.get('amount', 0)))
        lines.append(
            f'<edge id="e{index}" source="{_escape_xml(str(source))}" target="{_escape_xml(str(target))}" weight="{_escape_xml(weight)}" />'
        )
    lines.append('</edges>')

    lines.append('</graph>')
    lines.append('</gexf>')
    return '\n'.join(lines)


def build_case_artifacts_from_evidence(
    *,
    case: dict[str, object],
    evidence_paths: list[tuple[dict[str, object], Path]],
    audit_entries: list[dict[str, object]],
) -> dict[str, str | bytes]:
    """Builds every export artifact from ALL evidence in the case combined into one graph.

    Analyzing evidence files together (rather than only the most recently imported one)
    means later imports no longer hide the analysis of earlier ones, and relationships
    between addresses pulled in from different evidence files become visible too.
    """
    per_evidence_frames = clean_evidence_frames(evidence_paths)
    combined_frame = combine_frames(per_evidence_frames)
    graph = build_transaction_graph(combined_frame)
    analytics = run_plugin_pipeline(dataframe=combined_frame, graph=graph)

    evidence_contributions = [_evidence_contribution(entry, frame, graph) for entry, frame in per_evidence_frames]

    context = build_case_export_context(
        case=case,
        cleaned_frame=combined_frame,
        graph=graph,
        analytics=analytics,
        audit_entries=audit_entries,
        evidence_contributions=evidence_contributions,
    )

    return {
        'csv': build_case_csv_report(context),
        'pdf': build_case_pdf_report(context),
        'graphml': build_graphml_export(graph),
        'gexf': build_gexf_export(graph),
    }


def _escape_csv_cell(value: object) -> str:
    text = _safe_text(value)
    if any(character in text for character in [',', '"', '\n', '\r']):
        return '"' + text.replace('"', '""') + '"'
    return text


def _escape_xml(value: str) -> str:
    return (
        value.replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
        .replace("'", '&apos;')
    )
