import { Observable } from 'rxjs';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';

import { environment } from '../../../environments/environment';
import { Case, Investigation, PinnedNode } from '../../core/models/shared.models';

@Injectable({
  providedIn: 'root',
})
export class GraphVisualizationApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  // --- Investigator layer: investigations + investigator links (see
  // CASE-MANAGEMENT-IMPLEMENTATION.md). Separate from the evidence-Case endpoints above;
  // links are an additional forensic layer, never a blockchain fact. ---

  listInvestigations(): Observable<{ investigations: Investigation[] }> {
    return this.http.get<{ investigations: Investigation[] }>(`${this.apiUrl}/api/v1/investigations`);
  }

  createInvestigation(body: { name: string; description?: string | null }): Observable<Investigation> {
    return this.http.post<Investigation>(`${this.apiUrl}/api/v1/investigations`, body);
  }

  /** Permanently removes an investigation and everything in it (notes, pinned nodes,
   * investigator links). Does not touch the evidence case. */
  deleteInvestigation(investigationId: string): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/api/v1/investigations/${investigationId}`);
  }

  deleteInvestigatorLink(investigationId: string, linkId: string): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/api/v1/investigations/${investigationId}/links/${linkId}`);
  }

  /** Pin an address (upsert - re-sending updates the stored position). */
  pinInvestigatorNode(
    investigationId: string,
    body: { address: string; x?: number | null; y?: number | null },
  ): Observable<PinnedNode> {
    return this.http.put<PinnedNode>(`${this.apiUrl}/api/v1/investigations/${investigationId}/pins`, body);
  }

  unpinInvestigatorNode(investigationId: string, address: string): Observable<void> {
    const params = new HttpParams().set('address', address);
    return this.http.delete<void>(`${this.apiUrl}/api/v1/investigations/${investigationId}/pins`, { params });
  }
}
