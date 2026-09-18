import { Observable } from 'rxjs';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';

import { environment } from '../../../environments/environment';
import { SybilAnalysisResult, TransactionCustodyEntry } from '../../core/models/shared.models';

@Injectable({
  providedIn: 'root',
})
export class SybilAnalysisApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  /** Deliberate variant (see SYBIL-ANALIZA.md / LANAC-DOKAZA.md) - the Sybil Analysis
   * page's own ANALYZE button calls this one, always with a `custody` entry, since scanning
   * the evidence for synchronized-address clusters is the same kind of deliberate access as
   * "Pokreni taint analizu"/"FIND PATH"/"Analiziraj graf"/DEX Swaps' ANALYZE. `address` and
   * `contract` may both be null - the backend then returns every cluster found anywhere in
   * the evidence. */
  runSybilAnalysis(
    caseId: string,
    address: string | null,
    contract: string | null,
    evidence: string | null,
    timeWindowSeconds: number,
    minAddresses: number,
    custody: TransactionCustodyEntry,
  ): Observable<SybilAnalysisResult> {
    const params = evidence ? new HttpParams().set('evidence', evidence) : undefined;
    const body: Record<string, unknown> = {
      address,
      contract,
      time_window_seconds: timeWindowSeconds,
      min_addresses: minAddresses,
      custody,
    };
    return this.http.post<SybilAnalysisResult>(`${this.apiUrl}/api/v1/cases/${caseId}/sybil-analysis/run`, body, { params });
  }
}
