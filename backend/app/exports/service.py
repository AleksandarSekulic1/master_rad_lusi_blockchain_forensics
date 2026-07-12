from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import pandas as pd

from app.analytics.graph_building.service import build_transaction_graph
from app.analytics.ingestion.csv_ingestion import clean_transaction_csv
from app.analytics.plugins.manager import run_plugin_pipeline


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
) -> dict[str, object]:
    return {
        'case': case,
        'rows': int(len(cleaned_frame)),
        'nodes': int(graph.number_of_nodes()),
        'edges': int(graph.number_of_edges()),
        'analytics': analytics,
        'audit_entries': audit_entries,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }


def build_case_csv_report(context: dict[str, object]) -> str:
    case = context['case']
    analytics = context['analytics']
    summary = analytics.get('summary', {}) if isinstance(analytics, dict) else {}
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


def build_case_pdf_report(context: dict[str, object]) -> bytes:
    case = context['case']
    analytics = context['analytics']
    summary = analytics.get('summary', {}) if isinstance(analytics, dict) else {}

    lines = [
        'Lusi v1.0 investigation report',
        f'Case: {_safe_text(case.get("name"))}',
        f'Case ID: {_safe_text(case.get("id"))}',
        f'Analyst: {_safe_text(case.get("analyst"))}',
        f'Created at: {_safe_text(case.get("created_at"))}',
        f'Updated at: {_safe_text(case.get("updated_at"))}',
        f'Evidence items: {len(case.get("evidence", []))}',
        f'Data rows: {context.get("rows")}',
        f'Nodes: {context.get("nodes")}',
        f'Edges: {context.get("edges")}',
        f'Blacklisted nodes: {_safe_text(summary.get("blacklisted_nodes", 0))}',
        f'High-risk nodes: {_safe_text(summary.get("high_risk_nodes", 0))}',
        f'Clusters: {_safe_text(summary.get("clusters", 0))}',
        '',
        'Evidence locker:',
    ]

    for entry in case.get('evidence', [])[:12]:
        if not isinstance(entry, dict):
            continue
        lines.append(
            f"- {entry.get('file_name')} | sha256={entry.get('sha256')} | size={entry.get('size_bytes')} | analyst={entry.get('analyst')} | imported={entry.get('imported_at')}"
        )

    lines.append('')
    lines.append('Audit log:')
    for entry in context['audit_entries'][:12]:
        lines.append(
            f"- {entry.get('timestamp')} | {entry.get('action')} | {entry.get('file_name')} | {entry.get('user')}"
        )

    return _build_simple_pdf(lines)


def build_graphml_export(graph: nx.MultiDiGraph) -> str:
    return '\n'.join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
        '<graph edgedefault="directed">',
        *[f'<node id="{_escape_xml(str(node_id))}" label="{_escape_xml(_safe_text(data.get("label", node_id)))}" />' for node_id, data in graph.nodes(data=True)],
        *[
            '<edge source="{source}" target="{target}" amount="{amount}" />'.format(
                source=_escape_xml(str(source)),
                target=_escape_xml(str(target)),
                amount=_escape_xml(_safe_text(data.get('total_amount', data.get('amount', 0)))),
            )
            for source, target, data in graph.edges(data=True)
        ],
        '</graph>',
        '</graphml>',
    ])


def build_gexf_export(graph: nx.MultiDiGraph) -> str:
    return '\n'.join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gexf xmlns="http://www.gexf.net/1.3" version="1.3">',
        '<graph mode="static" defaultedgetype="directed">',
        '<nodes>',
        *[f'<node id="{_escape_xml(str(node_id))}" label="{_escape_xml(_safe_text(data.get("label", node_id)))}" />' for node_id, data in graph.nodes(data=True)],
        '</nodes>',
        '<edges>',
        *[
            '<edge id="e{index}" source="{source}" target="{target}" weight="{weight}" />'.format(
                index=index,
                source=_escape_xml(str(source)),
                target=_escape_xml(str(target)),
                weight=_escape_xml(_safe_text(data.get('total_amount', data.get('amount', 0)))),
            )
            for index, (source, target, data) in enumerate(graph.edges(data=True))
        ],
        '</edges>',
        '</graph>',
        '</gexf>',
    ])


def build_case_artifacts(
    *,
    case: dict[str, object],
    cleaned_frame: pd.DataFrame,
    graph: nx.MultiDiGraph,
    audit_entries: list[dict[str, object]],
) -> dict[str, str | bytes]:
    analytics = run_plugin_pipeline(dataframe=cleaned_frame, graph=graph)
    context = build_case_export_context(
        case=case,
        cleaned_frame=cleaned_frame,
        graph=graph,
        analytics=analytics,
        audit_entries=audit_entries,
    )

    return {
        'csv': build_case_csv_report(context),
        'pdf': build_case_pdf_report(context),
        'graphml': build_graphml_export(graph),
        'gexf': build_gexf_export(graph),
    }


def build_case_artifacts_from_csv(*, case: dict[str, object], csv_path: Path, audit_entries: list[dict[str, object]]) -> dict[str, str | bytes]:
    cleaned_frame = clean_transaction_csv(csv_path)
    graph = build_transaction_graph(cleaned_frame)
    return build_case_artifacts(case=case, cleaned_frame=cleaned_frame, graph=graph, audit_entries=audit_entries)


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


def _build_simple_pdf(lines: list[str]) -> bytes:
    def escape_pdf(text: str) -> str:
        return text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')

    pdf_lines = ['BT', '/F1 11 Tf', '36 780 Td', '14 TL']
    for index, line in enumerate(lines):
        escaped = escape_pdf(line)
        pdf_lines.append(f'({escaped}) Tj' if index == 0 else f'T* ({escaped}) Tj')
    pdf_lines.append('ET')
    stream = '\n'.join(pdf_lines)

    objects = [
        '1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj',
        '2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj',
        '3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj',
        '4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj',
        f'5 0 obj << /Length {len(stream.encode("utf-8"))} >> stream\n{stream}\nendstream endobj',
    ]

    pdf = '%PDF-1.4\n'
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf += obj + '\n'

    startxref = len(pdf)
    pdf += f'xref\n0 {len(objects) + 1}\n'
    pdf += '0000000000 65535 f \n'
    for offset in offsets[1:]:
        pdf += f'{offset:010d} 00000 n \n'
    pdf += f'trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{startxref}\n%%EOF'
    return pdf.encode('utf-8')
