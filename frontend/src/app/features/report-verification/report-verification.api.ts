import { Observable } from 'rxjs';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';

import { environment } from '../../../environments/environment';
import { ReportVerificationResult } from '../../features/report-verification/report-verification.models';

@Injectable({
  providedIn: 'root',
})
export class ReportVerificationApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  /** Checks a report's verification code, and its content hash when one is supplied.
   * Omitting the hash is a valid use: the caller then reads the registered hash off the
   * response and compares it by eye with the one printed in the document. */
  verifyReport(code: string, contentHash?: string | null): Observable<ReportVerificationResult> {
    let params = new HttpParams().set('code', code);
    if (contentHash) {
      params = params.set('content_hash', contentHash);
    }
    return this.http.get<ReportVerificationResult>(`${this.apiUrl}/api/v1/reports/verify`, { params });
  }
}
