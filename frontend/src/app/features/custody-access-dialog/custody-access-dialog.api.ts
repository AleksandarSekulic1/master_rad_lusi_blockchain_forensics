import { Observable } from 'rxjs';
import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';

import { environment } from '../../../environments/environment';
import { CustodyFieldSuggestions } from '../../features/custody-access-dialog/custody-access-dialog.models';

@Injectable({
  providedIn: 'root',
})
export class CustodyAccessDialogApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  /** Prior values typed for this case's editable identification fields, so re-accessing
   * the same evidence (or the same physical device) can be offered back instead of
   * retyped identically. */
  getCustodySuggestions(caseId: string): Observable<CustodyFieldSuggestions> {
    return this.http.get<CustodyFieldSuggestions>(`${this.apiUrl}/api/v1/cases/${caseId}/custody/suggestions`);
  }
}
