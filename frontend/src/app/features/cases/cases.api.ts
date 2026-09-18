import { Observable } from 'rxjs';
import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';

import { environment } from '../../../environments/environment';
import { Case, CaseStatus } from '../../core/models/shared.models';
import { CreateCaseRequest } from '../../features/cases/cases.models';

@Injectable({
  providedIn: 'root',
})
export class CasesApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  createCase(request: CreateCaseRequest): Observable<Case> {
    return this.http.post<Case>(`${this.apiUrl}/api/v1/cases`, request);
  }

  deleteCase(caseId: string): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/api/v1/cases/${caseId}`);
  }

  /** Removes one evidence file from a case (inverse of a CSV upload). Returns the updated
   * case so the evidence locker can refresh in place. */
  removeCaseEvidence(caseId: string, storedName: string): Observable<Case> {
    return this.http.delete<Case>(`${this.apiUrl}/api/v1/cases/${caseId}/evidence/${encodeURIComponent(storedName)}`);
  }

  setCaseStatus(caseId: string, status: CaseStatus): Observable<Case> {
    return this.http.patch<Case>(`${this.apiUrl}/api/v1/cases/${caseId}/status`, { status });
  }
}
