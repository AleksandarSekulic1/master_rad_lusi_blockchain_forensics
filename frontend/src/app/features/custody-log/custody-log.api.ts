import { Observable } from 'rxjs';
import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';

import { environment } from '../../../environments/environment';
import { CustodyChain, CustodyEvidenceChain, CustodyEvidenceSummary, CustodyTransactionSummary } from '../../features/custody-log/custody-log.models';

@Injectable({
  providedIn: 'root',
})
export class CustodyLogApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  // --- Lanac dokaza po transakciji (open to any authenticated user, admin included) ---

  /** Every transaction accessed at least once in this case, most recently accessed first. */
  getCustodyTransactions(caseId: string): Observable<{ case_id: string; transactions: CustodyTransactionSummary[] }> {
    return this.http.get<{ case_id: string; transactions: CustodyTransactionSummary[] }>(
      `${this.apiUrl}/api/v1/cases/${caseId}/custody/transactions`,
    );
  }

  getCustodyChain(caseId: string, txId: string): Observable<CustodyChain> {
    return this.http.get<CustodyChain>(`${this.apiUrl}/api/v1/cases/${caseId}/custody/transactions/${encodeURIComponent(txId)}`);
  }

  exportCustodyPdf(caseId: string, txId: string, lang: 'sr' | 'en' = 'sr'): Observable<Blob> {
    return this.http.get(`${this.apiUrl}/api/v1/cases/${caseId}/custody/transactions/${encodeURIComponent(txId)}/export.pdf`, {
      params: { lang },
      responseType: 'blob',
    });
  }

  // --- Lanac dokaza po dokaznom fajlu (coarser sibling - see LANAC-DOKAZA.md) ---

  getCustodyEvidenceList(caseId: string): Observable<{ case_id: string; evidence: CustodyEvidenceSummary[] }> {
    return this.http.get<{ case_id: string; evidence: CustodyEvidenceSummary[] }>(
      `${this.apiUrl}/api/v1/cases/${caseId}/custody/evidence`,
    );
  }

  getCustodyEvidenceChain(caseId: string, evidenceStoredName: string): Observable<CustodyEvidenceChain> {
    return this.http.get<CustodyEvidenceChain>(
      `${this.apiUrl}/api/v1/cases/${caseId}/custody/evidence/${encodeURIComponent(evidenceStoredName)}`,
    );
  }

  exportCustodyEvidencePdf(caseId: string, evidenceStoredName: string, lang: 'sr' | 'en' = 'sr'): Observable<Blob> {
    return this.http.get(`${this.apiUrl}/api/v1/cases/${caseId}/custody/evidence/${encodeURIComponent(evidenceStoredName)}/export.pdf`, {
      params: { lang },
      responseType: 'blob',
    });
  }
}
