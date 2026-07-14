import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';
import { Observable } from 'rxjs';

import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { ApiService } from '../../core/services/api.service';
import { NodeLinkGraphResponse } from '../../models/blockchain-forensics.models';

@Component({
  selector: 'app-report-export',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './report-export.component.html',
  styleUrl: './report-export.component.scss',
})
export class ReportExportComponent {
  protected isExporting = false;
  protected exportError: string | null = null;

  constructor(
    private readonly api: ApiService,
    private readonly state: AnalysisStateService,
  ) {}

  get graph(): NodeLinkGraphResponse | null {
    return this.state.graphSnapshot;
  }

  get analyticsSummary(): Record<string, unknown> | null {
    return this.state.analyticsSnapshot?.summary ?? this.graph?.summary ?? null;
  }

  get sourceLabel(): string {
    return this.graph?.source_file ?? this.state.uploadSnapshot?.file_name ?? 'unknown-source.csv';
  }

  get activeCaseId(): string | null {
    return this.state.selectedCaseSnapshot?.id ?? null;
  }

  exportCsvReport(): void {
    this.runExport((caseId) => this.api.exportCaseReportCsv(caseId), 'report.csv');
  }

  exportPdfReport(): void {
    this.runExport((caseId) => this.api.exportCaseReportPdf(caseId), 'report.pdf');
  }

  exportGraphMl(): void {
    this.runExport((caseId) => this.api.exportCaseGraphml(caseId), 'graph.graphml');
  }

  exportGexf(): void {
    this.runExport((caseId) => this.api.exportCaseGexf(caseId), 'graph.gexf');
  }

  private runExport(request: (caseId: string) => Observable<Blob>, suffix: string): void {
    const caseId = this.activeCaseId;
    if (!caseId) {
      this.exportError = 'Select a case on the Cases page before exporting.';
      return;
    }

    this.isExporting = true;
    this.exportError = null;

    request(caseId).subscribe({
      next: (blob) => {
        this.isExporting = false;
        this.downloadBlob(blob, `${caseId}_${suffix}`);
      },
      error: () => {
        this.isExporting = false;
        this.exportError = `Export failed for case ${caseId}.`;
      },
    });
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
