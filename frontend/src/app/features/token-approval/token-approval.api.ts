import { Observable } from 'rxjs';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';

import { environment } from '../../../environments/environment';
import { TokenApprovalCorrelationResult, TransactionCustodyEntry } from '../../core/models/shared.models';

@Injectable({
  providedIn: 'root',
})
export class TokenApprovalApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  /** Deliberate variant of getTokenApprovalCorrelation above (see
   * TOKEN-APPROVAL-IMPLEMENTATION.md #18) - the Token Approval Analysis page's own
   * ANALYZE button calls this one, always with a `custody` entry, since correlating the
   * case's evidence for approve/permit <-> transferFrom pairs is the same kind of
   * deliberate access as "Pokreni taint analizu"/"FIND PATH"/"Analiziraj graf"/DEX Swaps'
   * ANALYZE. Beyond the standard per-transaction/per-evidence-file chain of custody entry
   * every deliberate analysis writes, each individual approval transaction's own row
   * additionally carries a structured TOKEN_APPROVAL evidence item on the backend - see
   * that section for the full field list. `address` may be null, same as
   * runDexSwapAnalysis - the backend then correlates every grant found anywhere in the
   * evidence, not scoped to one wallet. */
  runTokenApprovalAnalysis(
    caseId: string,
    address: string | null,
    evidence: string | null,
    custody: TransactionCustodyEntry,
  ): Observable<TokenApprovalCorrelationResult> {
    const params = evidence ? new HttpParams().set('evidence', evidence) : undefined;
    const body: Record<string, unknown> = { address, custody };
    return this.http.post<TokenApprovalCorrelationResult>(`${this.apiUrl}/api/v1/cases/${caseId}/token-approval-analysis/run`, body, { params });
  }
}
