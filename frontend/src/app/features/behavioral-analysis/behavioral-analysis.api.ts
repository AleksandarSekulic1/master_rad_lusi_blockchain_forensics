import { Observable } from 'rxjs';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';

import { environment } from '../../../environments/environment';
import { TransactionCustodyEntry } from '../../core/models/shared.models';
import { BehavioralAnalysisResult } from '../../features/behavioral-analysis/behavioral-analysis.models';

@Injectable({
  providedIn: 'root',
})
export class BehavioralAnalysisApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  /** Deliberate-access counterpart of getBehavioralAnalysis: same result, but this POST
   * carries a `custody` entry and the backend records it in both chains of custody before
   * returning - so running the analysis leaves the same audit trail as Taint/Pathfinding/
   * DEX Swaps. `custody` is optional at the API level (direct/test callers), but the
   * Behavioral Analysis page always supplies one. */
  runBehavioralAnalysis(
    caseId: string,
    address: string,
    evidence?: string | null,
    custody?: TransactionCustodyEntry | null,
  ): Observable<BehavioralAnalysisResult> {
    const params = evidence ? new HttpParams().set('evidence', evidence) : undefined;
    return this.http.post<BehavioralAnalysisResult>(
      `${this.apiUrl}/api/v1/cases/${caseId}/behavioral-analysis/run`,
      { address, custody: custody ?? undefined },
      { params },
    );
  }
}
