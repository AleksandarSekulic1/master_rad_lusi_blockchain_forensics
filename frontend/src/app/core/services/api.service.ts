import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import {
  AddressEnrichment,
  AnalyticsResponse,
  AuthUser,
  Case,
  CaseStatus,
  CaseSummary,
  CreateCaseRequest,
  CreateUserRequest,
  FetchOnchainRequest,
  NodeLinkGraphResponse,
  OnchainNetwork,
  PathFindingRequest,
  PathFindingResponse,
  ResetLinkResponse,
  UploadCsvResponse,
  UserStatus,
} from '../../models/blockchain-forensics.models';

@Injectable({
  providedIn: 'root',
})
export class ApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  uploadCsv(file: File, caseId: string): Observable<UploadCsvResponse> {
    const formData = new FormData();
    formData.append('file', file, file.name);
    formData.append('case_id', caseId);

    return this.http.post<UploadCsvResponse>(`${this.apiUrl}/api/v1/upload/csv`, formData);
  }

  fetchOnchainTransactions(request: FetchOnchainRequest): Observable<UploadCsvResponse> {
    return this.http.post<UploadCsvResponse>(`${this.apiUrl}/api/v1/onchain/fetch`, request);
  }

  listCases(): Observable<{ cases: CaseSummary[] }> {
    return this.http.get<{ cases: CaseSummary[] }>(`${this.apiUrl}/api/v1/cases`);
  }

  createCase(request: CreateCaseRequest): Observable<Case> {
    return this.http.post<Case>(`${this.apiUrl}/api/v1/cases`, request);
  }

  getCase(caseId: string): Observable<Case> {
    return this.http.get<Case>(`${this.apiUrl}/api/v1/cases/${caseId}`);
  }

  setCaseStatus(caseId: string, status: CaseStatus): Observable<Case> {
    return this.http.patch<Case>(`${this.apiUrl}/api/v1/cases/${caseId}/status`, { status });
  }

  deleteCase(caseId: string): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/api/v1/cases/${caseId}`);
  }

  exportCaseReportCsv(caseId: string): Observable<Blob> {
    return this.http.get(`${this.apiUrl}/api/v1/exports/cases/${caseId}/report.csv`, { responseType: 'blob' });
  }

  exportCaseReportPdf(caseId: string): Observable<Blob> {
    return this.http.get(`${this.apiUrl}/api/v1/exports/cases/${caseId}/report.pdf`, { responseType: 'blob' });
  }

  exportCaseGraphml(caseId: string): Observable<Blob> {
    return this.http.get(`${this.apiUrl}/api/v1/exports/cases/${caseId}/graph.graphml`, { responseType: 'blob' });
  }

  exportCaseGexf(caseId: string): Observable<Blob> {
    return this.http.get(`${this.apiUrl}/api/v1/exports/cases/${caseId}/graph.gexf`, { responseType: 'blob' });
  }

  getCaseGraph(caseId: string, evidence?: string | null): Observable<NodeLinkGraphResponse> {
    const params = evidence ? new HttpParams().set('evidence', evidence) : undefined;
    return this.http.get<NodeLinkGraphResponse>(`${this.apiUrl}/api/v1/cases/${caseId}/graph`, { params });
  }

  runCaseAnalytics(caseId: string, evidence?: string | null, seedAddresses?: string[] | null): Observable<AnalyticsResponse> {
    const params = evidence ? new HttpParams().set('evidence', evidence) : undefined;
    const body = seedAddresses?.length ? { seed_addresses: seedAddresses } : {};
    return this.http.post<AnalyticsResponse>(`${this.apiUrl}/api/v1/cases/${caseId}/analytics/run`, body, { params });
  }

  enrichAddress(address: string, network: OnchainNetwork = 'mainnet'): Observable<AddressEnrichment> {
    const params = new HttpParams().set('network', network);
    return this.http.get<AddressEnrichment>(`${this.apiUrl}/api/v1/addresses/${address}/enrich`, { params });
  }

  findPaths(request: PathFindingRequest): Observable<PathFindingResponse> {
    return this.http.post<PathFindingResponse>(`${this.apiUrl}/api/v1/graph/path-finding`, request);
  }

  listUsers(): Observable<{ users: AuthUser[] }> {
    return this.http.get<{ users: AuthUser[] }>(`${this.apiUrl}/api/v1/users`);
  }

  createUser(request: CreateUserRequest): Observable<AuthUser> {
    return this.http.post<AuthUser>(`${this.apiUrl}/api/v1/users`, request);
  }

  setUserStatus(userId: string, status: UserStatus): Observable<AuthUser> {
    return this.http.patch<AuthUser>(`${this.apiUrl}/api/v1/users/${userId}/status`, { status });
  }

  generateResetLink(userId: string): Observable<ResetLinkResponse> {
    return this.http.post<ResetLinkResponse>(`${this.apiUrl}/api/v1/users/${userId}/reset-link`, {});
  }
}