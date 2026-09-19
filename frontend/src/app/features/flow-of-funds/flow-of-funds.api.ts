import { Observable } from 'rxjs';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';

import { environment } from '../../../environments/environment';
import { TransactionCustodyEntry } from '../../core/models/shared.models';
import { FlowOfFundsRequestParams, FlowOfFundsResult } from './flow-of-funds.models';

@Injectable({
  providedIn: 'root',
})
export class FlowOfFundsApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  private buildParams(params: FlowOfFundsRequestParams, evidence: string | null): HttpParams {
    let httpParams = new HttpParams()
      .set('direction', params.direction)
      .set('max_levels', String(params.maxLevels));
    for (const address of params.sourceAddresses) {
      httpParams = httpParams.append('source', address);
    }
    if (params.minAmount != null) {
      httpParams = httpParams.set('min_amount', String(params.minAmount));
    }
    if (params.maxFlows != null) {
      httpParams = httpParams.set('max_flows', String(params.maxFlows));
    }
    if (params.startTime) {
      httpParams = httpParams.set('start_time', params.startTime);
    }
    if (params.endTime) {
      httpParams = httpParams.set('end_time', params.endTime);
    }
    if (evidence) {
      httpParams = httpParams.set('evidence', evidence);
    }
    return httpParams;
  }

  /** Passive preview (GET) - read-only, no custody dialog, no audit log entry. */
  getFlowOfFunds(caseId: string, params: FlowOfFundsRequestParams, evidence: string | null): Observable<FlowOfFundsResult> {
    return this.http.get<FlowOfFundsResult>(`${this.apiUrl}/api/v1/cases/${caseId}/flow-of-funds`, {
      params: this.buildParams(params, evidence),
    });
  }

  /** Deliberate variant (POST /run) - tracing across the case's evidence this way is treated
   * as a deliberate access, same as "Pokreni taint analizu"/"FIND PATH" - accepts a custody
   * entry and gets recorded in both chains of custody server-side. */
  runFlowOfFunds(
    caseId: string,
    params: FlowOfFundsRequestParams,
    evidence: string | null,
    custody: TransactionCustodyEntry,
  ): Observable<FlowOfFundsResult> {
    const httpParams = evidence ? new HttpParams().set('evidence', evidence) : undefined;
    const body = {
      source_addresses: params.sourceAddresses,
      direction: params.direction,
      max_levels: params.maxLevels,
      min_amount: params.minAmount ?? 0,
      max_flows: params.maxFlows ?? 500,
      start_time: params.startTime ?? null,
      end_time: params.endTime ?? null,
      custody,
    };
    return this.http.post<FlowOfFundsResult>(`${this.apiUrl}/api/v1/cases/${caseId}/flow-of-funds/run`, body, { params: httpParams });
  }
}
