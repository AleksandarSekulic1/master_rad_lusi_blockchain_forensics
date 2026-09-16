import { Observable } from 'rxjs';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';

import { environment } from '../../../environments/environment';
import { AddressEnrichment, AnalyticsResponse, Case, CaseSummary, DexSwapAnalysisResult, InvestigatorLinkListResponse, InvestigatorNoteListResponse, NodeLinkGraphResponse, OnchainNetwork, PinnedNodeListResponse, TokenApprovalCorrelationResult, TransactionCustodyEntry } from '../../core/models/shared.models';

@Injectable({
  providedIn: 'root',
})
export class CaseDataApiService {
  private readonly apiUrl = environment.apiUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  getCase(caseId: string): Observable<Case> {
    return this.http.get<Case>(`${this.apiUrl}/api/v1/cases/${caseId}`);
  }

  getCaseGraph(caseId: string, evidence?: string | null): Observable<NodeLinkGraphResponse> {
    const params = evidence ? new HttpParams().set('evidence', evidence) : undefined;
    return this.http.get<NodeLinkGraphResponse>(`${this.apiUrl}/api/v1/cases/${caseId}/graph`, { params });
  }

  listCases(search?: string | null): Observable<{ cases: CaseSummary[] }> {
    const trimmed = search?.trim();
    const params = trimmed ? new HttpParams().set('search', trimmed) : undefined;
    return this.http.get<{ cases: CaseSummary[] }>(`${this.apiUrl}/api/v1/cases`, { params });
  }

  /** `custody` is omitted by passive callers (Dashboard, Graf) that just want an
   * annotated graph. The Taint Analysis page's "Pokreni taint analizu" button always
   * supplies one (see the custody-access-dialog, opened before this is ever called for
   * that deliberate action) - see LANAC-DOKAZA.md for why the split exists. */
  runCaseAnalytics(
    caseId: string,
    evidence?: string | null,
    seedAddresses?: string[] | null,
    custody?: TransactionCustodyEntry | null,
  ): Observable<AnalyticsResponse> {
    const params = evidence ? new HttpParams().set('evidence', evidence) : undefined;
    const body = {
      seed_addresses: seedAddresses?.length ? seedAddresses : undefined,
      custody: custody ?? undefined,
    };
    return this.http.post<AnalyticsResponse>(`${this.apiUrl}/api/v1/cases/${caseId}/analytics/run`, body, { params });
  }

  /** Registers a report before the PDF is built, returning the verification code that
   * gets printed into it. The code has to exist first - it cannot be derived from a
   * document it is itself part of. */
  registerReport(request: {
    case_id: string;
    case_name: string;
    declaration: string;
    content: Record<string, unknown>;
    summary: Record<string, unknown>;
    /** 'taint' | 'pathfinding' | 'dex_swap' so far - purely descriptive, lets Log
     * aktivnosti/izveštaj aktivnosti tell one signed report apart from another (see
     * activity_report.py's _REPORT_TYPE_LABELS). Optional so this method's existing
     * callers keep compiling even before each one is updated to pass it. */
    report_type?: string;
  }): Observable<{ verification_code: string; content_hash: string; registered_at: string; analyst: string }> {
    return this.http.post<{ verification_code: string; content_hash: string; registered_at: string; analyst: string }>(
      `${this.apiUrl}/api/v1/reports/register`,
      request,
    );
  }

  enrichAddress(address: string, network: OnchainNetwork = 'mainnet'): Observable<AddressEnrichment> {
    const params = new HttpParams().set('network', network);
    return this.http.get<AddressEnrichment>(`${this.apiUrl}/api/v1/addresses/${address}/enrich`, { params });
  }

  /** `address` is optional (unlike getBehavioralAnalysis above) - omitted, the backend
   * returns every candidate swap in the case's evidence, which is what the Graph page's
   * overlay needs (see graph-visualization.component.ts's loadDexSwapOverlay); the DEX
   * Swap Analysis page itself always supplies one. */
  getDexSwapAnalysis(caseId: string, address?: string | null, evidence?: string | null): Observable<DexSwapAnalysisResult> {
    let params = new HttpParams();
    if (address) {
      params = params.set('address', address);
    }
    if (evidence) {
      params = params.set('evidence', evidence);
    }
    return this.http.get<DexSwapAnalysisResult>(`${this.apiUrl}/api/v1/cases/${caseId}/dex-swap-analysis`, { params });
  }

  /** Token Approval / Ice Phishing Analysis - correlates each approve()/permit() grant for
   * `address` with its later transferFrom() usage (see
   * analytics/token_approval_analysis.correlate_approval_usage and
   * TOKEN-APPROVAL-IMPLEMENTATION.md #14/#16). Read-only - Token Approval's backend is
   * Phase 1 (extraction/correlation/risk indicators only): there is no deliberate/
   * custody-gated "run" variant yet, so unlike getDexSwapAnalysis/runDexSwapAnalysis above
   * there is only ONE method here, not a passive/deliberate pair (see
   * TOKEN-APPROVAL-IMPLEMENTATION.md #12.6/#16.7 for what is deliberately out of scope).
   *
   * `address` is optional (unlike token-approval.component.ts's own usage, which always
   * supplies one) - omitted, the backend returns every grant in the case's scoped
   * evidence, which is what the Graph page's APPROVAL overlay needs (see
   * graph-visualization.component.ts's loadTokenApprovalOverlay, same pattern as
   * getDexSwapAnalysis's own optional `address` above). */
  getTokenApprovalCorrelation(caseId: string, address?: string | null, evidence?: string | null): Observable<TokenApprovalCorrelationResult> {
    let params = new HttpParams();
    if (address) {
      params = params.set('address', address);
    }
    if (evidence) {
      params = params.set('evidence', evidence);
    }
    return this.http.get<TokenApprovalCorrelationResult>(`${this.apiUrl}/api/v1/cases/${caseId}/token-approval-correlation`, { params });
  }

  /** Investigator notes in an investigation. With `address`, only that address's
   * node notes (backend scopes ?address= to address/node notes - see step 4); without it,
   * every note in the investigation (nodes + transactions). */
  getInvestigatorNotes(investigationId: string, address?: string | null): Observable<InvestigatorNoteListResponse> {
    const params = address ? new HttpParams().set('address', address) : undefined;
    return this.http.get<InvestigatorNoteListResponse>(
      `${this.apiUrl}/api/v1/investigations/${investigationId}/notes`,
      { params },
    );
  }

  /** Every investigator link in an investigation (optionally only those touching one
   * address - either endpoint, the association is undirected). */
  getInvestigatorLinks(investigationId: string, address?: string | null): Observable<InvestigatorLinkListResponse> {
    const params = address ? new HttpParams().set('address', address) : undefined;
    return this.http.get<InvestigatorLinkListResponse>(
      `${this.apiUrl}/api/v1/investigations/${investigationId}/links`,
      { params },
    );
  }

  /** Pinned nodes for an investigation (persisted - step 10). */
  getInvestigatorPins(investigationId: string): Observable<PinnedNodeListResponse> {
    return this.http.get<PinnedNodeListResponse>(`${this.apiUrl}/api/v1/investigations/${investigationId}/pins`);
  }
}
