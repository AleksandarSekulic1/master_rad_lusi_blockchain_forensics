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

  /** Passive variant (GET .../sybil-analysis, read-only - see SYBIL-ANALIZA.md §6/§9) - used
   * to let the analyst tweak the time window/address threshold/address/contract filter
   * AFTER an initial signed run and instantly see how the cluster count changes, without a
   * new custody dialog for every tweak. Same detection, same evidence already deliberately
   * accessed by the first ANALYZE click - re-reading it to preview a different parameter
   * combination is not itself a new deliberate access (identical treatment to the DEX Swap
   * Graph overlay's own passive GET call). */
  getSybilAnalysis(
    caseId: string,
    address: string | null,
    contract: string | null,
    evidence: string | null,
    timeWindowSeconds: number,
    minAddresses: number,
  ): Observable<SybilAnalysisResult> {
    let params = new HttpParams()
      .set('time_window_seconds', String(timeWindowSeconds))
      .set('min_addresses', String(minAddresses));
    if (address) {
      params = params.set('address', address);
    }
    if (contract) {
      params = params.set('contract', contract);
    }
    if (evidence) {
      params = params.set('evidence', evidence);
    }
    return this.http.get<SybilAnalysisResult>(`${this.apiUrl}/api/v1/cases/${caseId}/sybil-analysis`, { params });
  }
}
