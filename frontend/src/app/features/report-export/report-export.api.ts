import { Observable } from 'rxjs';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';

import { environment } from '../../../environments/environment';
import { CaseReportContext } from '../../features/report-export/report-export.models';

@Injectable({
  providedIn: 'root',
})
export class ReportExportApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  /** Full case analysis context (case metadata, summary, evidence locker + contribution
   * breakdown, audit log). The dashboard's "Izvoz izveštaja" panel renders its own signed,
   * bilingual triage PDF from this on the client. */
  getCaseReportContext(caseId: string): Observable<CaseReportContext> {
    return this.http.get<CaseReportContext>(`${this.apiUrl}/api/v1/exports/cases/${caseId}/report-context`);
  }

  exportCaseGraphml(caseId: string): Observable<Blob> {
    return this.http.get(`${this.apiUrl}/api/v1/exports/cases/${caseId}/graph.graphml`, { responseType: 'blob' });
  }

  exportCaseGexf(caseId: string): Observable<Blob> {
    return this.http.get(`${this.apiUrl}/api/v1/exports/cases/${caseId}/graph.gexf`, { responseType: 'blob' });
  }

  /** Raw, cleaned per-transaction CSV of the case's combined evidence (or one evidence
   * file when `evidence` is given) - the same rows every analysis page reads from, not
   * the section/field/value summary in exportCaseReportCsv's report.csv. Plain, unsigned
   * download: no custody dialog, no audit log entry, same as getCaseGraph. */
  exportCaseTransactionsCsv(caseId: string, evidence?: string | null): Observable<Blob> {
    const params = evidence ? new HttpParams().set('evidence', evidence) : undefined;
    return this.http.get(`${this.apiUrl}/api/v1/cases/${caseId}/transactions/export`, { params, responseType: 'blob' });
  }
}
