import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, of } from 'rxjs';

import { environment } from '../../../environments/environment';
import {
  ActivityLogResponse,
  ActivityReportPreview,
  AddressEnrichment,
  AnalyticsResponse,
  AuthUser,
  BehavioralAnalysisResult,
  Case,
  CasePathfindingResult,
  CaseReportContext,
  CaseStatus,
  CaseSummary,
  CreateCaseRequest,
  CreateUserRequest,
  CustodyChain,
  CustodyEvidenceChain,
  CustodyEvidenceSummary,
  CustodyFieldSuggestions,
  CustodyTransactionSummary,
  DexSwapAnalysisResult,
  FetchOnchainRequest,
  Investigation,
  InvestigatorLink,
  InvestigatorLinkConfidence,
  InvestigatorLinkListResponse,
  InvestigatorNote,
  InvestigatorNoteListResponse,
  KnownEntity,
  NodeLinkGraphResponse,
  OnchainNetwork,
  PathfindingDestinationMode,
  PathFindingRequest,
  PathFindingResponse,
  PinnedNode,
  PinnedNodeListResponse,
  ReportVerificationResult,
  ResetLinkResponse,
  ScenarioRequest,
  SeedSuggestionResponse,
  ScenarioRunResponse,
  SuiteListResponse,
  SuiteRunResponse,
  TestScenario,
  TransactionCustodyEntry,
  UploadCsvResponse,
  UserStatus,
} from '../../models/blockchain-forensics.models';

/** Filter for the activity report. Empty dates mean "everything since the system started
 * being used"; a single day is dateFrom === dateTo. */
export interface ActivityReportOptions {
  dateFrom?: string | null;
  dateTo?: string | null;
  /** Admin-only; the backend ignores it for everyone else and returns their own entries. */
  users?: string[];
}

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

  // --- Investigator layer: investigations + investigator links (see
  // CASE-MANAGEMENT-IMPLEMENTATION.md). Separate from the evidence-Case endpoints above;
  // links are an additional forensic layer, never a blockchain fact. ---

  listInvestigations(): Observable<{ investigations: Investigation[] }> {
    return this.http.get<{ investigations: Investigation[] }>(`${this.apiUrl}/api/v1/investigations`);
  }

  createInvestigation(body: { name: string; description?: string | null }): Observable<Investigation> {
    return this.http.post<Investigation>(`${this.apiUrl}/api/v1/investigations`, body);
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

  deleteInvestigatorLink(investigationId: string, linkId: string): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/api/v1/investigations/${investigationId}/links/${linkId}`);
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

  /** Pinned nodes for an investigation (persisted - step 10). */
  getInvestigatorPins(investigationId: string): Observable<PinnedNodeListResponse> {
    return this.http.get<PinnedNodeListResponse>(`${this.apiUrl}/api/v1/investigations/${investigationId}/pins`);
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

  exportCaseReportCsv(caseId: string): Observable<Blob> {
    return this.http.get(`${this.apiUrl}/api/v1/exports/cases/${caseId}/report.csv`, { responseType: 'blob' });
  }

  exportCaseReportPdf(caseId: string): Observable<Blob> {
    return this.http.get(`${this.apiUrl}/api/v1/exports/cases/${caseId}/report.pdf`, { responseType: 'blob' });
  }

  /** Full case analysis context (case metadata, summary, evidence locker + contribution
   * breakdown, audit log). The dashboard's "Izvoz izveštaja" panel renders its own signed,
   * bilingual triage PDF from this on the client. */
  getCaseReportContext(caseId: string): Observable<CaseReportContext> {
    return this.http.get<CaseReportContext>(`${this.apiUrl}/api/v1/exports/cases/${caseId}/report-context`);
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

  /** Rule-based, explained seed suggestions - replaces the old "run analytics and take
   * everything the outlier detector flagged" approach. */
  getSeedSuggestions(caseId: string, evidence?: string | null): Observable<SeedSuggestionResponse> {
    const params = evidence ? new HttpParams().set('evidence', evidence) : undefined;
    return this.http.get<SeedSuggestionResponse>(`${this.apiUrl}/api/v1/cases/${caseId}/seed-suggestions`, { params });
  }

  enrichAddress(address: string, network: OnchainNetwork = 'mainnet'): Observable<AddressEnrichment> {
    const params = new HttpParams().set('network', network);
    return this.http.get<AddressEnrichment>(`${this.apiUrl}/api/v1/addresses/${address}/enrich`, { params });
  }

  /** Batch, local-only exchange/mixer/sanctioned lookup for a whole list of addresses at
   * once (see backend known_entities.json) - no Etherscan calls involved, so it's safe to
   * check every cash-out candidate in one request instead of one enrichAddress() call per
   * address just for this single field. */
  getKnownEntities(addresses: string[]): Observable<Record<string, KnownEntity | null>> {
    if (addresses.length === 0) {
      return of({});
    }
    const params = new HttpParams().set('addresses', addresses.join(','));
    return this.http.get<Record<string, KnownEntity | null>>(`${this.apiUrl}/api/v1/addresses/known-entities`, { params });
  }

  findPaths(request: PathFindingRequest): Observable<PathFindingResponse> {
    return this.http.post<PathFindingResponse>(`${this.apiUrl}/api/v1/graph/path-finding`, request);
  }

  /** Pathfinding Analysis (case-scoped, first version - plain BFS). Separate from
   * findPaths() above, which hits the older, unrelated standalone endpoint.
   *
   * `to` is required (and used) only for destinationMode 'specific_address' - for
   * 'nearest_cex' the backend resolves the destination itself from the case's own graph,
   * so `to` is simply omitted from the request body. */
  findCasePath(
    caseId: string,
    from: string,
    destinationMode: PathfindingDestinationMode,
    to: string | null,
    evidence?: string | null,
    custody?: TransactionCustodyEntry | null,
  ): Observable<CasePathfindingResult> {
    const params = evidence ? new HttpParams().set('evidence', evidence) : undefined;
    const body: Record<string, unknown> = { from, destination_mode: destinationMode, custody: custody ?? undefined };
    if (destinationMode === 'specific_address') {
      body['to'] = to;
    }
    return this.http.post<CasePathfindingResult>(`${this.apiUrl}/api/v1/cases/${caseId}/pathfinding`, body, { params });
  }

  /** Behavioral / Time-of-Day Analysis (case-scoped, first version - UTC only, no
   * timezone/continent inference). Read-only, like getCaseGraph/getSeedSuggestions - no
   * custody entry, since it only re-reads the case's own already-built graph. */
  getBehavioralAnalysis(caseId: string, address: string, evidence?: string | null): Observable<BehavioralAnalysisResult> {
    let params = new HttpParams().set('address', address);
    if (evidence) {
      params = params.set('evidence', evidence);
    }
    return this.http.get<BehavioralAnalysisResult>(`${this.apiUrl}/api/v1/cases/${caseId}/behavioral-analysis`, { params });
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

  /** Deliberate variant of getDexSwapAnalysis above (see DEX-SWAP-ANALIZA.md #12 /
   * LANAC-DOKAZA.md) - the DEX Swap Analysis page's own ANALYZE button calls this one,
   * always with a `custody` entry, since scanning the evidence for swap pairs is the same
   * kind of deliberate access as "Pokreni taint analizu"/"FIND PATH"/"Analiziraj graf". */
  runDexSwapAnalysis(
    caseId: string,
    address: string,
    evidence: string | null,
    custody: TransactionCustodyEntry,
  ): Observable<DexSwapAnalysisResult> {
    const params = evidence ? new HttpParams().set('evidence', evidence) : undefined;
    const body: Record<string, unknown> = { address, custody };
    return this.http.post<DexSwapAnalysisResult>(`${this.apiUrl}/api/v1/cases/${caseId}/dex-swap-analysis/run`, body, { params });
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

  // --- Correctness tests (admin only; the backend enforces that, not these methods) ---

  listSuiteTests(): Observable<SuiteListResponse> {
    return this.http.get<SuiteListResponse>(`${this.apiUrl}/api/v1/tests/suite`);
  }

  runSuite(): Observable<SuiteRunResponse> {
    return this.http.post<SuiteRunResponse>(`${this.apiUrl}/api/v1/tests/suite/run`, {});
  }

  listScenarios(): Observable<{ scenarios: TestScenario[] }> {
    return this.http.get<{ scenarios: TestScenario[] }>(`${this.apiUrl}/api/v1/tests/scenarios`);
  }

  createScenario(request: ScenarioRequest): Observable<TestScenario> {
    return this.http.post<TestScenario>(`${this.apiUrl}/api/v1/tests/scenarios`, request);
  }

  updateScenario(scenarioId: string, request: ScenarioRequest): Observable<TestScenario> {
    return this.http.put<TestScenario>(`${this.apiUrl}/api/v1/tests/scenarios/${scenarioId}`, request);
  }

  deleteScenario(scenarioId: string): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/api/v1/tests/scenarios/${scenarioId}`);
  }

  runScenarios(scenarioId?: string | null): Observable<ScenarioRunResponse> {
    const params = scenarioId ? new HttpParams().set('scenario_id', scenarioId) : undefined;
    return this.http.post<ScenarioRunResponse>(`${this.apiUrl}/api/v1/tests/scenarios/run`, {}, { params });
  }

  /** How many records the chosen report filter would produce - drives the disabled state
   * of the generate buttons so an empty report is prevented before the click. */
  getActivityReportPreview(options: ActivityReportOptions): Observable<ActivityReportPreview> {
    return this.http.get<ActivityReportPreview>(`${this.apiUrl}/api/v1/activity-log/report/preview`, {
      params: this.activityReportParams(options),
    });
  }

  downloadActivityReport(options: ActivityReportOptions, format: 'pdf' | 'csv'): Observable<Blob> {
    return this.http.get(`${this.apiUrl}/api/v1/activity-log/report.${format}`, {
      params: this.activityReportParams(options),
      responseType: 'blob',
    });
  }

  /** The timezone offset travels with every report request: the server stores UTC but the
   * user picks days as they see them on screen, and the two only line up if the server
   * knows which zone to convert against. */
  private activityReportParams(options: ActivityReportOptions): HttpParams {
    let params = new HttpParams().set('tz_offset_minutes', String(new Date().getTimezoneOffset()));
    if (options.dateFrom) {
      params = params.set('date_from', options.dateFrom);
    }
    if (options.dateTo) {
      params = params.set('date_to', options.dateTo);
    }
    if (options.users?.length) {
      params = params.set('users', options.users.join(','));
    }
    return params;
  }

  /** Recorded analyst actions, newest first. `user` is only honoured for admins - the
   * backend narrows everyone else to their own entries regardless of what's passed, so
   * this parameter is a convenience for the admin filter, not an access control. */
  getActivityLog(options?: { user?: string | null; caseId?: string | null; limit?: number }): Observable<ActivityLogResponse> {
    let params = new HttpParams().set('limit', String(options?.limit ?? 200));
    if (options?.user) {
      params = params.set('user', options.user);
    }
    if (options?.caseId) {
      params = params.set('case_id', options.caseId);
    }
    return this.http.get<ActivityLogResponse>(`${this.apiUrl}/api/v1/activity-log`, { params });
  }

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

  /** Prior values typed for this case's editable identification fields, so re-accessing
   * the same evidence (or the same physical device) can be offered back instead of
   * retyped identically. */
  getCustodySuggestions(caseId: string): Observable<CustodyFieldSuggestions> {
    return this.http.get<CustodyFieldSuggestions>(`${this.apiUrl}/api/v1/cases/${caseId}/custody/suggestions`);
  }

  exportCustodyPdf(caseId: string, txId: string): Observable<Blob> {
    return this.http.get(`${this.apiUrl}/api/v1/cases/${caseId}/custody/transactions/${encodeURIComponent(txId)}/export.pdf`, {
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

  exportCustodyEvidencePdf(caseId: string, evidenceStoredName: string): Observable<Blob> {
    return this.http.get(`${this.apiUrl}/api/v1/cases/${caseId}/custody/evidence/${encodeURIComponent(evidenceStoredName)}/export.pdf`, {
      responseType: 'blob',
    });
  }
}