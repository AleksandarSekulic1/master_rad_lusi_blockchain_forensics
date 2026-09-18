import { Observable } from 'rxjs';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';

import { environment } from '../../../environments/environment';
import { DexSwapAnalysisResult, TransactionCustodyEntry } from '../../core/models/shared.models';

@Injectable({
  providedIn: 'root',
})
export class DexSwapAnalysisApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  /** Deliberate variant of getDexSwapAnalysis above (see DEX-SWAP-ANALIZA.md #12 /
   * LANAC-DOKAZA.md) - the DEX Swap Analysis page's own ANALYZE button calls this one,
   * always with a `custody` entry, since scanning the evidence for swap pairs is the same
   * kind of deliberate access as "Pokreni taint analizu"/"FIND PATH"/"Analiziraj graf".
   * `address` may be null - the backend then returns every swap event found anywhere in
   * the evidence (not scoped to one wallet), used by the page's "find all addresses"
   * scan (see dex_swap_analysis.py's target_address=None path). */
  runDexSwapAnalysis(
    caseId: string,
    address: string | null,
    evidence: string | null,
    custody: TransactionCustodyEntry,
  ): Observable<DexSwapAnalysisResult> {
    const params = evidence ? new HttpParams().set('evidence', evidence) : undefined;
    const body: Record<string, unknown> = { address, custody };
    return this.http.post<DexSwapAnalysisResult>(`${this.apiUrl}/api/v1/cases/${caseId}/dex-swap-analysis/run`, body, { params });
  }
}
