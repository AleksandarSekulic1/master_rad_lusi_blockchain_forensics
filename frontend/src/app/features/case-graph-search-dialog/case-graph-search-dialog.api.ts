import { Observable } from 'rxjs';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';

import { environment } from '../../../environments/environment';
import { CaseGraphNeighborhoodResult } from '../../features/case-graph-search-dialog/case-graph-search-dialog.models';

@Injectable({
  providedIn: 'root',
})
export class CaseGraphSearchDialogApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  /** Graph-db pilot (Neo4j) - "every address connected to `address` within `maxHops`
   * steps", answered by a Cypher query rather than a hand-rolled bounded BFS. Optional/
   * additive: the backend returns HTTP 503 when Neo4j isn't running - see
   * PREDLOG-GRAF-SUBP.md. Read-only, like getCaseGraph - no custody entry. */
  getCaseGraphNeighborhood(
    caseId: string,
    address: string,
    maxHops: number,
    evidence?: string | null,
  ): Observable<CaseGraphNeighborhoodResult> {
    let params = new HttpParams().set('address', address).set('max_hops', String(maxHops));
    if (evidence) {
      params = params.set('evidence', evidence);
    }
    return this.http.get<CaseGraphNeighborhoodResult>(
      `${this.apiUrl}/api/v1/cases/${caseId}/graph-search/neighborhood`,
      { params },
    );
  }
}
