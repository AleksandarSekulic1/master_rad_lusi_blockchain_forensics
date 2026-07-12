import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';

import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { GraphLinkData, GraphNodeData, NodeLinkGraphResponse } from '../../models/blockchain-forensics.models';

@Component({
  selector: 'app-report-export',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './report-export.component.html',
  styleUrl: './report-export.component.scss',
})
export class ReportExportComponent {
  constructor(private readonly state: AnalysisStateService) {}

  get graph(): NodeLinkGraphResponse | null {
    return this.state.graphSnapshot;
  }

  get analyticsSummary(): Record<string, unknown> | null {
    return this.state.analyticsSnapshot?.summary ?? this.graph?.summary ?? null;
  }

  get sourceLabel(): string {
    return this.graph?.source_file ?? this.state.uploadSnapshot?.file_name ?? 'unknown-source.csv';
  }

  exportCsvReport(): void {
    const graph = this.requireGraph();
    if (!graph) {
      return;
    }

    const rows = this.buildReportRows(graph);
    this.downloadBlob(new Blob([this.toCsv(rows)], { type: 'text/csv;charset=utf-8' }), 'lusi-report.csv');
  }

  exportPdfReport(): void {
    const graph = this.requireGraph();
    if (!graph) {
      return;
    }

    const lines = this.buildReportLines(graph);
    this.downloadBlob(this.createPdfBlob(lines), 'lusi-report.pdf');
  }

  exportGraphMl(): void {
    const graph = this.requireGraph();
    if (!graph) {
      return;
    }

    this.downloadBlob(new Blob([this.buildGraphMl(graph)], { type: 'application/xml;charset=utf-8' }), 'lusi-graph.graphml');
  }

  exportGexf(): void {
    const graph = this.requireGraph();
    if (!graph) {
      return;
    }

    this.downloadBlob(new Blob([this.buildGexf(graph)], { type: 'application/xml;charset=utf-8' }), 'lusi-graph.gexf');
  }

  private requireGraph(): NodeLinkGraphResponse | null {
    return this.graph;
  }

  private buildReportRows(graph: NodeLinkGraphResponse): Array<Record<string, string | number>> {
    const analytics = this.state.analyticsSnapshot;
    const summary = analytics?.summary ?? graph.summary ?? {
      blacklisted_nodes: graph.nodes.filter((node) => Boolean(node.blacklist_flag)).length,
      high_risk_nodes: graph.nodes.filter((node) => Number(node.risk_score ?? 0) >= 70).length,
      clusters: new Set(graph.nodes.map((node) => node.cluster_id).filter(Boolean)).size,
    };

    return [
      { section: 'metadata', label: 'source_file', value: graph.source_file ?? this.sourceLabel },
      { section: 'metadata', label: 'generated_at', value: graph.generated_at ?? '' },
      { section: 'summary', label: 'nodes', value: graph.nodes.length },
      { section: 'summary', label: 'edges', value: graph.links.length },
      { section: 'summary', label: 'blacklisted_nodes', value: summary.blacklisted_nodes },
      { section: 'summary', label: 'high_risk_nodes', value: summary.high_risk_nodes },
      { section: 'summary', label: 'clusters', value: summary.clusters },
      ...graph.nodes.map((node) => this.serializeNodeRow(node)),
      ...graph.links.map((link) => this.serializeLinkRow(link)),
    ];
  }

  private buildReportLines(graph: NodeLinkGraphResponse): string[] {
    const summary = graph.summary ?? {
      blacklisted_nodes: graph.nodes.filter((node) => Boolean(node.blacklist_flag)).length,
      high_risk_nodes: graph.nodes.filter((node) => Number(node.risk_score ?? 0) >= 70).length,
      clusters: new Set(graph.nodes.map((node) => node.cluster_id).filter(Boolean)).size,
    };

    return [
      'Lusi v1.0 evidence report',
      `Source file: ${graph.source_file ?? this.sourceLabel}`,
      `Generated at: ${graph.generated_at ?? 'n/a'}`,
      `Nodes: ${graph.nodes.length}`,
      `Edges: ${graph.links.length}`,
      `Blacklisted nodes: ${summary.blacklisted_nodes}`,
      `High-risk nodes: ${summary.high_risk_nodes}`,
      `Clusters: ${summary.clusters}`,
      '',
      'Focused nodes:',
      ...graph.nodes.slice(0, 20).map((node) => this.formatNodeLine(node)),
      '',
      'Focused edges:',
      ...graph.links.slice(0, 20).map((link) => this.formatLinkLine(link)),
    ];
  }

  private serializeNodeRow(node: GraphNodeData): Record<string, string | number> {
    return {
      section: 'node',
      label: node.address ?? node.id,
      value: Number(node.risk_score ?? 0),
    };
  }

  private serializeLinkRow(link: GraphLinkData): Record<string, string | number> {
    return {
      section: 'edge',
      label: `${link.source} -> ${link.target}`,
      value: Number(link.total_amount ?? link.amount ?? 0),
    };
  }

  private formatNodeLine(node: GraphNodeData): string {
    const flags = [
      node.blacklist_flag ? 'blacklisted' : null,
      node.anomaly_flag ? 'anomaly' : null,
      node.peel_chain_flag ? 'peel' : null,
      node.chain_hop_flag ? 'hop' : null,
    ].filter(Boolean);

    return `${node.address ?? node.id} | risk=${node.risk_score ?? 0} | cluster=${node.cluster_id ?? 'n/a'} | ${flags.join(', ') || 'normal'}`;
  }

  private formatLinkLine(link: GraphLinkData): string {
    return `${link.source} -> ${link.target} | total=${Number(link.total_amount ?? link.amount ?? 0).toFixed(4)} | count=${link.transaction_count ?? 1}`;
  }

  private toCsv(rows: Array<Record<string, string | number>>): string {
    const headers = Array.from(new Set(rows.flatMap((row) => Object.keys(row))));
    const escapedRows = rows.map((row) => headers.map((header) => this.escapeCsvValue(row[header] ?? '')));
    return [headers.join(','), ...escapedRows.map((row) => row.join(','))].join('\n');
  }

  private escapeCsvValue(value: string | number): string {
    const text = String(value ?? '');
    if (/[",\n]/.test(text)) {
      return `"${text.replace(/"/g, '""')}"`;
    }
    return text;
  }

  private createPdfBlob(lines: string[]): Blob {
    const sanitizedLines = lines.map((line) => this.escapePdfText(line));
    const contentLines = [
      'BT',
      '/F1 11 Tf',
      '36 780 Td',
      '14 TL',
      ...sanitizedLines.flatMap((line, index) => [index === 0 ? `(${line}) Tj` : `T* (${line}) Tj`]),
      'ET',
    ].join('\n');

    const objects = [
      '1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj',
      '2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj',
      '3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj',
      '4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj',
      `5 0 obj << /Length ${contentLines.length} >> stream\n${contentLines}\nendstream endobj`,
    ];

    let pdf = '%PDF-1.4\n';
    const offsets: number[] = [0];

    for (const object of objects) {
      offsets.push(pdf.length);
      pdf += `${object}\n`;
    }

    const xrefStart = pdf.length;
    pdf += `xref\n0 ${objects.length + 1}\n`;
    pdf += '0000000000 65535 f \n';
    for (let index = 1; index < offsets.length; index += 1) {
      pdf += `${offsets[index].toString().padStart(10, '0')} 00000 n \n`;
    }
    pdf += `trailer << /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefStart}\n%%EOF`;

    return new Blob([pdf], { type: 'application/pdf' });
  }

  private escapePdfText(text: string): string {
    return text.replace(/\\/g, '\\\\').replace(/\(/g, '\\(').replace(/\)/g, '\\)');
  }

  private buildGraphMl(graph: NodeLinkGraphResponse): string {
    const attributeNames = this.collectAttributeNames(graph);
    const nodeKeys = attributeNames.nodes.map((name, index) => ({ name, id: `n${index}` }));
    const edgeKeys = attributeNames.edges.map((name, index) => ({ name, id: `e${index}` }));

    return [
      '<?xml version="1.0" encoding="UTF-8"?>',
      '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
      ...nodeKeys.map((entry) => `<key id="${entry.id}" for="node" attr.name="${entry.name}" attr.type="string" />`),
      ...edgeKeys.map((entry) => `<key id="${entry.id}" for="edge" attr.name="${entry.name}" attr.type="string" />`),
      '<graph id="LusiGraph" edgedefault="directed">',
      ...graph.nodes.map((node) => this.graphMlNode(node, nodeKeys)),
      ...graph.links.map((link, index) => this.graphMlEdge(link, edgeKeys, index)),
      '</graph>',
      '</graphml>',
    ].join('\n');
  }

  private buildGexf(graph: NodeLinkGraphResponse): string {
    const attributeNames = this.collectAttributeNames(graph);
    const nodeKeys = attributeNames.nodes.map((name, index) => ({ name, id: `n${index}` }));
    const edgeKeys = attributeNames.edges.map((name, index) => ({ name, id: `e${index}` }));

    return [
      '<?xml version="1.0" encoding="UTF-8"?>',
      '<gexf xmlns="http://www.gexf.net/1.3" version="1.3">',
      '<graph mode="static" defaultedgetype="directed">',
      '<attributes class="node">',
      ...nodeKeys.map((entry) => `<attribute id="${entry.id}" title="${entry.name}" type="string" />`),
      '</attributes>',
      '<attributes class="edge">',
      ...edgeKeys.map((entry) => `<attribute id="${entry.id}" title="${entry.name}" type="string" />`),
      '</attributes>',
      '<nodes>',
      ...graph.nodes.map((node) => this.gexfNode(node, nodeKeys)),
      '</nodes>',
      '<edges>',
      ...graph.links.map((link, index) => this.gexfEdge(link, edgeKeys, index)),
      '</edges>',
      '</graph>',
      '</gexf>',
    ].join('\n');
  }

  private collectAttributeNames(graph: NodeLinkGraphResponse): { nodes: string[]; edges: string[] } {
    const nodeNames = new Set<string>();
    const edgeNames = new Set<string>();

    graph.nodes.forEach((node) => Object.keys(node).forEach((key) => nodeNames.add(key)));
    graph.links.forEach((link) => Object.keys(link).forEach((key) => edgeNames.add(key)));

    return {
      nodes: Array.from(nodeNames).sort(),
      edges: Array.from(edgeNames).sort(),
    };
  }

  private graphMlNode(node: GraphNodeData, keys: Array<{ name: string; id: string }>): string {
    const values = keys
      .map((entry) => this.graphMlData(entry.id, node[entry.name]))
      .filter(Boolean)
      .join('');

    return `<node id="${this.escapeXml(node.id)}" label="${this.escapeXml(node.label ?? node.address ?? node.id)}">${values}</node>`;
  }

  private graphMlEdge(link: GraphLinkData, keys: Array<{ name: string; id: string }>, index: number): string {
    const values = keys
      .map((entry) => this.graphMlData(entry.id, link[entry.name]))
      .filter(Boolean)
      .join('');

    return `<edge id="e${index}" source="${this.escapeXml(link.source)}" target="${this.escapeXml(link.target)}">${values}</edge>`;
  }

  private graphMlData(key: string, value: unknown): string {
    if (value === undefined || value === null || value === '') {
      return '';
    }

    return `<data key="${key}">${this.escapeXml(String(value))}</data>`;
  }

  private gexfNode(node: GraphNodeData, keys: Array<{ name: string; id: string }>): string {
    const values = keys
      .map((entry) => this.gexfAttValue(entry.id, node[entry.name]))
      .filter(Boolean)
      .join('');

    return `<node id="${this.escapeXml(node.id)}" label="${this.escapeXml(node.label ?? node.address ?? node.id)}"><attvalues>${values}</attvalues></node>`;
  }

  private gexfEdge(link: GraphLinkData, keys: Array<{ name: string; id: string }>, index: number): string {
    const values = keys
      .map((entry) => this.gexfAttValue(entry.id, link[entry.name]))
      .filter(Boolean)
      .join('');

    return `<edge id="e${index}" source="${this.escapeXml(link.source)}" target="${this.escapeXml(link.target)}"><attvalues>${values}</attvalues></edge>`;
  }

  private gexfAttValue(key: string, value: unknown): string {
    if (value === undefined || value === null || value === '') {
      return '';
    }

    return `<attvalue for="${key}" value="${this.escapeXml(String(value))}" />`;
  }

  private escapeXml(value: string): string {
    return value
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&apos;');
  }

  private downloadBlob(blob: Blob, fileName: string): void {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = fileName;
    anchor.click();
    URL.revokeObjectURL(url);
  }
}