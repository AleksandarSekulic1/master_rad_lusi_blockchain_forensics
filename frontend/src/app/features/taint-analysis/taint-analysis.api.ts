import { Observable, of } from 'rxjs';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';

import { environment } from '../../../environments/environment';
import { KnownEntity, SeedSuggestionResponse } from '../../features/taint-analysis/taint-analysis.models';

@Injectable({
  providedIn: 'root',
})
export class TaintAnalysisApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  /** Batch, local-only exchange/mixer/sanctioned lookup for a whole list of addresses at
   * once (see backend known_entities.json) - no Etherscan calls involved, so it's safe to
   * check every cash-out candidate in one request instead of one enrichAddress() call per
   * address just for this single field. */
  getKnownEntities(addresses: string[]): Observable<Record<string, KnownEntity | null>> {
    if (addresses.length === 0) {
      return of({});
    }
    const params = new HttpParams().set('addresses', addresses.join(','));
    return this.http.get<Record<string, KnownEntity | null>>(`${this.apiUrl}/api/v1/addresses/known-entities`, { params });
  }

  /** Rule-based, explained seed suggestions - replaces the old "run analytics and take
   * everything the outlier detector flagged" approach. */
  getSeedSuggestions(caseId: string, evidence?: string | null): Observable<SeedSuggestionResponse> {
    const params = evidence ? new HttpParams().set('evidence', evidence) : undefined;
    return this.http.get<SeedSuggestionResponse>(`${this.apiUrl}/api/v1/cases/${caseId}/seed-suggestions`, { params });
  }
}
