import { Observable } from 'rxjs';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';

import { environment } from '../../../environments/environment';
import { TransactionCustodyEntry } from '../../core/models/shared.models';
import { CasePathfindingResult, PathfindingDestinationMode } from '../../features/pathfinding/pathfinding.models';

@Injectable({
  providedIn: 'root',
})
export class PathfindingApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  /** Pathfinding Analysis (case-scoped, first version - plain BFS). Separate from
   * findPaths() above, which hits the older, unrelated standalone endpoint.
   *
   * `to` is required (and used) only for destinationMode 'specific_address' - for
   * 'nearest_cex' the backend resolves the destination itself from the case's own graph,
   * so `to` is simply omitted from the request body. */
  findCasePath(
    caseId: string,
    from: string,
    destinationMode: PathfindingDestinationMode,
    to: string | null,
    evidence?: string | null,
    custody?: TransactionCustodyEntry | null,
  ): Observable<CasePathfindingResult> {
    const params = evidence ? new HttpParams().set('evidence', evidence) : undefined;
    const body: Record<string, unknown> = { from, destination_mode: destinationMode, custody: custody ?? undefined };
    if (destinationMode === 'specific_address') {
      body['to'] = to;
    }
    return this.http.post<CasePathfindingResult>(`${this.apiUrl}/api/v1/cases/${caseId}/pathfinding`, body, { params });
  }
}
