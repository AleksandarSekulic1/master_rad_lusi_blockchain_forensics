import { Observable } from 'rxjs';
import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';

import { environment } from '../../../environments/environment';
import { UploadCsvResponse } from '../../core/models/shared.models';
import { FetchBitcoinRequest, FetchOnchainRequest, PreviewOnchainRequest, PreviewOnchainResult } from '../../features/dashboard/dashboard.models';

@Injectable({
  providedIn: 'root',
})
export class DashboardApiService {
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

  previewOnchainTransactions(request: PreviewOnchainRequest): Observable<PreviewOnchainResult> {
    return this.http.post<PreviewOnchainResult>(`${this.apiUrl}/api/v1/onchain/preview`, request);
  }

  fetchBitcoinTransactions(address: string, caseId: string): Observable<UploadCsvResponse> {
    const request: FetchBitcoinRequest = { address, case_id: caseId };
    return this.http.post<UploadCsvResponse>(`${this.apiUrl}/api/v1/bitcoin/fetch`, request);
  }
}
