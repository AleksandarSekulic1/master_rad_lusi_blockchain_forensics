import { Observable } from 'rxjs';
import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';

import { environment } from '../../../environments/environment';
import { InvestigatorLink, InvestigatorLinkConfidence, InvestigatorNote } from '../../core/models/shared.models';

@Injectable({
  providedIn: 'root',
})
export class InvestigatorNodeDialogApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  addInvestigatorLink(
    investigationId: string,
    body: {
      source_address: string;
      target_address: string;
      reason: string;
      evidence: string;
      confidence: InvestigatorLinkConfidence;
    },
  ): Observable<InvestigatorLink> {
    return this.http.post<InvestigatorLink>(`${this.apiUrl}/api/v1/investigations/${investigationId}/links`, body);
  }

  addInvestigatorNote(investigationId: string, body: { address: string; text: string }): Observable<InvestigatorNote> {
    return this.http.post<InvestigatorNote>(`${this.apiUrl}/api/v1/investigations/${investigationId}/notes`, body);
  }

  updateInvestigatorNote(investigationId: string, noteId: string, body: { text: string }): Observable<InvestigatorNote> {
    return this.http.patch<InvestigatorNote>(
      `${this.apiUrl}/api/v1/investigations/${investigationId}/notes/${noteId}`,
      body,
    );
  }

  deleteInvestigatorNote(investigationId: string, noteId: string): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/api/v1/investigations/${investigationId}/notes/${noteId}`);
  }
}
