import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, OnInit, ViewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { distinctUntilChanged, map } from 'rxjs/operators';

import { jsPDF } from 'jspdf';
import autoTable from 'jspdf-autotable';

import { SignaturePadComponent } from '../../core/components/signature-pad/signature-pad.component';
import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { ApiService } from '../../core/services/api.service';
import { AuthService } from '../../core/services/auth.service';
import { AppLang, SettingsService } from '../../core/services/settings.service';
import {
  CaseSummary,
  EvidenceEntry,
  NodeLinkGraphResponse,
  TokenApprovalCorrelationEntry,
  TokenApprovalCorrelationResult,
  TokenApprovalGroup,
  TokenApprovalRiskLevel,
  TransactionCustodyEntry,
} from '../../models/blockchain-forensics.models';
import { CustodyAccessDialogComponent } from '../custody-access-dialog/custody-access-dialog.component';
import { InvestigatorNodeDialogComponent } from '../investigator-node-dialog/investigator-node-dialog.component';

/** Same localStorage key graph-visualization.component.ts persists the investigator layer's
 * chosen investigation under - deliberately read, never written, from this page: the
 * investigation is picked once on the Graph page, and every other page that can open
 * InvestigatorNodeDialogComponent (see taint-analysis.component.ts's own
 * storedInvestigationId) just reuses whatever is already active there, rather than
 * duplicating a whole investigation picker on every analysis page. */
const SELECTED_INVESTIGATION_KEY = 'lusi_selected_investigation';

/** One row of the "Predlog za dalju analizu"/"Predloži adrese" panels (see
 * rankRiskySpenders in the component below) - a spender address ranked by the highest
 * risk_score any of its (owner, spender, token) groups reached. */
interface SuggestedRiskyAddress {
  address: string;
  owner: string;
  riskLevel: TokenApprovalRiskLevel;
  riskScore: number;
  topReasonLabel: string | null;
}

/** Token Approval / Ice Phishing Analysis - "for this address, what ERC-20 allowances did
 * it grant (or receive), and what happened to each one" (see
 * TOKEN-APPROVAL-IMPLEMENTATION.md #16-19). A full peer of Graph/Taint/Pathfinding/
 * Behavioral/DEX Swap: custody-gated ANALYZE (§18), a signed PDF report through the same
 * registry as those pages (§19), and cross-references to DEX Swap/Pathfinding/Graph where
 * the data actually supports one (§19.3) - never a fabricated link.
 *
 * Reuses the same case/evidence-picker shell as the sibling analysis pages
 * (AnalysisStateService, ApiService.getCase for the evidence list, ApiService.getCaseGraph
 * for the address autocomplete list - identical pattern to dex-swap-analysis.component.ts's
 * own loadCaseAddresses).
 */
@Component({
  selector: 'app-token-approval',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, InvestigatorNodeDialogComponent, CustodyAccessDialogComponent, SignaturePadComponent],
  templateUrl: './token-approval.component.html',
  styleUrl: './token-approval.component.scss',
})
export class TokenApprovalComponent implements OnInit {
  protected activeCase: CaseSummary | null = null;
  protected evidenceOptions: EvidenceEntry[] = [];
  protected selectedEvidence: string | null = null;

  protected address = '';
  protected isAnalyzing = false;
  protected analysisError: string | null = null;
  protected result: TokenApprovalCorrelationResult | null = null;

  // --- Case address autocomplete (see dex-swap-analysis.component.ts's own
  // loadCaseAddresses) - a plain <datalist>, not a visible extra dropdown, so the address
  // field on screen stays exactly the single input the mockup asks for. ---
  protected caseAddresses: string[] = [];

  /** (owner, spender, token) -> its group's risk_level/risk_indicators/spender_known - see
   * groupKey(). Correlation entries and groups are two views of the same backend data
   * (TOKEN-APPROVAL-IMPLEMENTATION.md #14.2), so this is a lookup, not a second fetch. */
  private groupByKey = new Map<string, TokenApprovalGroup>();

  /** The correlation entry currently shown in the detail modal - null means the modal is
   * closed. Kept as the entry itself (not just an index) so the modal keeps working even
   * if `result` is later replaced by a fresh ANALYZE. */
  protected selectedEntry: TokenApprovalCorrelationEntry | null = null;

  // --- "Add Investigator Note" (see InvestigatorNodeDialogComponent's own header) - opens
  // the SAME shared dialog Graph/Taint/Pathfinding use, for whichever investigation is
  // currently active on the Graph page. Attached to the SPENDER of the selected entry (the
  // newly-surfaced entity a finding is actually about), not the owner (already known - it
  // is the address that was searched) - see TOKEN-APPROVAL-IMPLEMENTATION.md #16.5. ---
  protected isNoteDialogOpen = false;
  protected noteDialogAddress: string | null = null;

  // --- Lanac dokaza (see TOKEN-APPROVAL-IMPLEMENTATION.md #18) - correlating the case's
  // evidence for approve/permit <-> transferFrom pairs is a deliberate access to it, same
  // as "Pokreni taint analizu"/"FIND PATH"/"Analiziraj graf"/DEX Swaps' ANALYZE, so ANALYZE
  // here opens the same shared custody-access dialog BEFORE calling the backend - same
  // pattern as dex-swap-analysis.component.ts's openCustodyDialog/confirmCustodyAndAnalyze,
  // simplified since this page only ever analyses one address at a time (no queue). ---
  protected isCustodyDialogOpen = false;
  protected custodyDialogError: string | null = null;

  // --- Cross-reference: DEX Swap Analysis (see TOKEN-APPROVAL-IMPLEMENTATION.md #19.3) -
  // "did funds pulled via transferFrom later show up in a detected DEX swap" is only ever
  // STATED when this set genuinely contains the address - loaded once per successful
  // ANALYZE, from the case's own already-existing DEX Swap Analysis endpoint (read-only,
  // no new backend route). A failed/empty load just means no cross-reference is shown,
  // never an error on this page. ---
  private dexSwapAddresses = new Set<string>();

  // --- PDF export (see TOKEN-APPROVAL-IMPLEMENTATION.md #19) - same signed-report
  // mechanism as Taint/Pathfinding/DEX Swap: a control number is registered server-side
  // BEFORE the document is built, the analyst draws a signature declaring they produced
  // it, and the PDF itself is assembled client-side. ---
  @ViewChild(SignaturePadComponent) private signaturePad?: SignaturePadComponent;
  protected isSignatureDialogOpen = false;
  protected signatureDeclarationAccepted = false;
  protected signatureError: string | null = null;
  protected isExportingPdf = false;
  protected tokenApprovalPdfLang: AppLang = 'sr';

  constructor(
    private readonly state: AnalysisStateService,
    private readonly api: ApiService,
    private readonly auth: AuthService,
    private readonly router: Router,
    private readonly destroyRef: DestroyRef,
    public readonly settings: SettingsService,
  ) {}

  /** Tiny inline translator: picks the Serbian or English string for the active language
   * (same pattern as every other analysis page's own t()). */
  protected t(sr: string, en: string): string {
    return this.settings.lang() === 'sr' ? sr : en;
  }

  ngOnInit(): void {
    this.state.selectedCase$
      .pipe(
        map((caseSummary) => caseSummary?.id ?? null),
        distinctUntilChanged(),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(() => {
        this.activeCase = this.state.selectedCaseSnapshot;
        this.selectedEvidence = null;
        this.evidenceOptions = [];
        this.clearResult();
        if (this.activeCase) {
          this.loadEvidenceOptions(this.activeCase.id);
          this.loadCaseAddresses();
        } else {
          this.caseAddresses = [];
        }
      });
  }

  private loadEvidenceOptions(caseId: string): void {
    this.api.getCase(caseId).subscribe({
      next: (caseDetail) => {
        this.evidenceOptions = caseDetail.evidence;
      },
      error: () => {
        this.evidenceOptions = [];
      },
    });
  }

  private loadCaseAddresses(): void {
    const caseId = this.activeCase?.id;
    if (!caseId) {
      this.caseAddresses = [];
      return;
    }
    this.api.getCaseGraph(caseId, this.selectedEvidence).subscribe({
      next: (graph: NodeLinkGraphResponse) => {
        this.caseAddresses = [...new Set(graph.nodes.map((node) => String(node.id)))].sort((a, b) => a.localeCompare(b));
      },
      error: () => {
        this.caseAddresses = [];
      },
    });
  }

  protected onEvidenceSelected(storedName: string): void {
    this.selectedEvidence = storedName || null;
    this.clearResult();
    this.loadCaseAddresses();
  }

  private clearResult(): void {
    this.result = null;
    this.groupByKey = new Map();
    this.analysisError = null;
    this.selectedEntry = null;
    this.isCustodyDialogOpen = false;
    this.custodyDialogError = null;
    this.dexSwapAddresses = new Set();
    this.selectedSuggestions = new Set();
    // Case-wide suggestions (§7.2) are scoped to a case+evidence combination too - a
    // stale list from before a case/evidence switch would suggest addresses that don't
    // even exist in the newly scoped evidence.
    this.caseSuggestions = null;
    this.caseSuggestionError = null;
    this.selectedCaseSuggestions = new Set();
  }

  protected get canAnalyze(): boolean {
    return !!this.activeCase && this.address.trim().length > 0 && !this.isAnalyzing;
  }

  /** Opens the access-reason dialog before actually running the analysis - see
   * isCustodyDialogOpen's own comment for why ANALYZE needs this now. */
  protected openCustodyDialog(): void {
    if (!this.canAnalyze) {
      return;
    }
    this.custodyDialogError = null;
    this.isCustodyDialogOpen = true;
  }

  protected closeCustodyDialog(): void {
    this.isCustodyDialogOpen = false;
  }

  protected confirmCustodyAndAnalyze(custody: TransactionCustodyEntry): void {
    if (!this.canAnalyze) {
      return;
    }
    const caseId = this.activeCase!.id;
    const address = this.address.trim();

    this.isAnalyzing = true;
    this.analysisError = null;
    this.custodyDialogError = null;
    this.selectedEntry = null;

    this.api.runTokenApprovalAnalysis(caseId, address, this.selectedEvidence, custody).subscribe({
      next: (result) => {
        this.result = result;
        this.groupByKey = new Map(result.groups.map((group) => [this.groupKey(group.owner, group.spender, group.token_address), group]));
        this.isAnalyzing = false;
        this.isCustodyDialogOpen = false;
        this.loadDexSwapCrossReference(caseId);
      },
      error: (error: HttpErrorResponse) => {
        this.isAnalyzing = false;
        const message =
          error.status === 404
            ? this.t('Adresa nije pronađena u evidenciji ovog slučaja.', 'The address was not found in this case’s evidence.')
            : this.t('Neuspešna Token Approval analiza.', 'The Token Approval analysis failed.');
        // Failure stays INSIDE the dialog (nothing typed is lost), same pattern as every
        // other custody-gated analysis page - the dialog is dismissed only on success.
        this.custodyDialogError = message;
        this.analysisError = message;
      },
    });
  }

  /** File name of the currently scoped evidence - fed into the custody dialog's default
   * "identifikator dokaznog materijala", same pattern as the sibling analysis pages. */
  protected get selectedEvidenceFileName(): string | null {
    if (!this.selectedEvidence) {
      return null;
    }
    return this.evidenceOptions.find((entry) => entry.stored_name === this.selectedEvidence)?.file_name ?? null;
  }

  // --- Risk lookup (correlation entry -> its group's risk fields) --------------------

  private groupKey(owner: string, spender: string, token: string | null): string {
    return `${owner.toLowerCase()}|${spender.toLowerCase()}|${(token ?? '').toLowerCase()}`;
  }

  private groupFor(entry: TokenApprovalCorrelationEntry): TokenApprovalGroup | null {
    return this.groupByKey.get(this.groupKey(entry.owner, entry.spender, entry.token_address)) ?? null;
  }

  protected riskLevelFor(entry: TokenApprovalCorrelationEntry): TokenApprovalRiskLevel {
    return this.groupFor(entry)?.risk_level ?? 'LOW';
  }

  protected riskIndicatorsFor(entry: TokenApprovalCorrelationEntry) {
    return this.groupFor(entry)?.risk_indicators ?? [];
  }

  protected spenderKnownFor(entry: TokenApprovalCorrelationEntry): boolean {
    return this.groupFor(entry)?.spender_known ?? false;
  }

  protected spenderMultiOwnerFor(entry: TokenApprovalCorrelationEntry): number | null {
    return this.groupFor(entry)?.spender_multi_owner?.distinct_owner_count ?? null;
  }

  // --- Suggested addresses for further analysis (Taint Analysis handoff) --------------
  // "Which addresses are most worth investigating further" is never a NEW heuristic here -
  // it's just the risk scoring §15 already computed, read back and ranked. One row per
  // distinct SPENDER that owns at least one MEDIUM/HIGH group (the side that actually
  // received/could use an allowance - the more useful seed for tracing where funds moved
  // to), keeping whichever of its groups scored highest when a spender shows up more than
  // once (e.g. approved by several owners - see spender_multi_owner). Two independent
  // sources feed the SAME ranking logic (rankRiskySpenders below):
  //  (a) suggestedTaintAddresses - from the ONE address just analyzed (this.result), and
  //  (b) caseSuggestions - from a CASE-WIDE scan (§7.2's own "Predloži adrese" button,
  //      same idea as Taint Analysis's "Predloži seed adrese": no address typed in first).
  // The analyst picks some or all from either list and hands them to Taint Analysis via
  // the same one-shot AnalysisStateService mechanism "Otvori u Pathfinding" already uses.
  private rankRiskySpenders(groups: TokenApprovalGroup[]): SuggestedRiskyAddress[] {
    const bySpender = new Map<string, SuggestedRiskyAddress>();
    for (const group of groups) {
      if (group.risk_level === 'LOW') {
        continue;
      }
      const existing = bySpender.get(group.spender);
      if (existing && existing.riskScore >= group.risk_score) {
        continue;
      }
      bySpender.set(group.spender, {
        address: group.spender,
        owner: group.owner,
        riskLevel: group.risk_level,
        riskScore: group.risk_score,
        topReasonLabel: group.risk_indicators[0]?.label ?? null,
      });
    }
    return [...bySpender.values()].sort((a, b) => b.riskScore - a.riskScore);
  }

  protected selectedSuggestions = new Set<string>();

  protected get suggestedTaintAddresses(): SuggestedRiskyAddress[] {
    return this.result ? this.rankRiskySpenders(this.result.groups) : [];
  }

  protected isSuggestionSelected(address: string): boolean {
    return this.selectedSuggestions.has(address);
  }

  protected toggleSuggestion(address: string): void {
    if (this.selectedSuggestions.has(address)) {
      this.selectedSuggestions.delete(address);
    } else {
      this.selectedSuggestions.add(address);
    }
  }

  protected get allSuggestionsSelected(): boolean {
    const list = this.suggestedTaintAddresses;
    return list.length > 0 && list.every((item) => this.selectedSuggestions.has(item.address));
  }

  protected toggleAllSuggestions(): void {
    const list = this.suggestedTaintAddresses;
    this.selectedSuggestions = this.allSuggestionsSelected ? new Set() : new Set(list.map((item) => item.address));
  }

  /** Hands the selected spender(s) to Taint Analysis as ready-made seeds - Taint Analysis
   * decides for itself whether/how to run (this page never runs a taint analysis itself),
   * same division of responsibility as openInPathfinding below. */
  protected sendSelectedToTaintAnalysis(): void {
    if (this.selectedSuggestions.size === 0) {
      return;
    }
    this.state.setPendingTaintSeeds([...this.selectedSuggestions]);
    this.router.navigateByUrl('/taint-analysis');
  }

  // --- "Predloži adrese" - CASE-WIDE, no address typed in first (see rankRiskySpenders
  // comment above). Reuses the existing PASSIVE `getTokenApprovalCorrelation` call
  // (address omitted -> every approval group in the scoped evidence, same endpoint the
  // Graph page's own Token Approval overlay already calls) - no new backend route, no
  // custody dialog (this only re-reads already-cleaned evidence, exactly like Taint
  // Analysis's own "Predloži seed adrese" never gates itself behind custody either; the
  // custody-gated moment stays "ANALIZIRAJ"/"Pošalji izabrane..." below, which touch a
  // SPECIFIC address's findings). ---
  protected isSuggestingCaseAddresses = false;
  protected caseSuggestionError: string | null = null;
  protected caseSuggestions: SuggestedRiskyAddress[] | null = null;
  protected selectedCaseSuggestions = new Set<string>();

  protected get canSuggestCaseAddresses(): boolean {
    return !!this.activeCase && !this.isSuggestingCaseAddresses;
  }

  protected suggestCaseAddresses(): void {
    if (!this.canSuggestCaseAddresses) {
      return;
    }
    const caseId = this.activeCase!.id;
    this.isSuggestingCaseAddresses = true;
    this.caseSuggestionError = null;
    this.caseSuggestions = null;
    this.selectedCaseSuggestions = new Set();

    this.api.getTokenApprovalCorrelation(caseId, null, this.selectedEvidence).subscribe({
      next: (result) => {
        this.isSuggestingCaseAddresses = false;
        this.caseSuggestions = this.rankRiskySpenders(result.groups);
      },
      error: () => {
        this.isSuggestingCaseAddresses = false;
        this.caseSuggestionError = this.t('Neuspešno predlaganje adresa.', 'Failed to suggest addresses.');
      },
    });
  }

  protected dismissCaseSuggestions(): void {
    this.caseSuggestions = null;
    this.selectedCaseSuggestions = new Set();
  }

  protected isCaseSuggestionSelected(address: string): boolean {
    return this.selectedCaseSuggestions.has(address);
  }

  protected toggleCaseSuggestion(address: string): void {
    if (this.selectedCaseSuggestions.has(address)) {
      this.selectedCaseSuggestions.delete(address);
    } else {
      this.selectedCaseSuggestions.add(address);
    }
  }

  protected get allCaseSuggestionsSelected(): boolean {
    const list = this.caseSuggestions ?? [];
    return list.length > 0 && list.every((item) => this.selectedCaseSuggestions.has(item.address));
  }

  protected toggleAllCaseSuggestions(): void {
    const list = this.caseSuggestions ?? [];
    this.selectedCaseSuggestions = this.allCaseSuggestionsSelected ? new Set() : new Set(list.map((item) => item.address));
  }

  protected sendSelectedCaseSuggestionsToTaintAnalysis(): void {
    if (this.selectedCaseSuggestions.size === 0) {
      return;
    }
    this.state.setPendingTaintSeeds([...this.selectedCaseSuggestions]);
    this.router.navigateByUrl('/taint-analysis');
  }

  /** Fills the Address field with a suggested address so the analyst can run the normal,
   * custody-gated single-address Token Approval analysis on it - never skips the custody
   * dialog itself, same division of responsibility as every other custody-gated action on
   * this page. */
  protected analyzeSuggestedCaseAddress(address: string): void {
    this.address = address;
    this.dismissCaseSuggestions();
  }

  // --- Summary counters (shown only once a result exists - see the template) ---------

  protected get totalApprovals(): number {
    return this.result?.correlations.length ?? 0;
  }

  protected get unlimitedApprovalsCount(): number {
    return this.result?.correlations.filter((entry) => entry.unlimited_basis !== null).length ?? 0;
  }

  protected get activeApprovalsCount(): number {
    return this.result?.correlations.filter((entry) => !entry.revoked).length ?? 0;
  }

  protected get revokedApprovalsCount(): number {
    return this.result?.correlations.filter((entry) => entry.revoked).length ?? 0;
  }

  protected get usedApprovalsCount(): number {
    return this.result?.correlations.filter((entry) => entry.used === true).length ?? 0;
  }

  /** `used === false` specifically - NOT the same as "total minus used", since `used` is
   * tri-state (§13.3/§14.3: `null` means "cannot be ruled out", never counted as either
   * used or unused). */
  protected get unusedApprovalsCount(): number {
    return this.result?.correlations.filter((entry) => entry.used === false).length ?? 0;
  }

  /** "Potentially risky" = the owning group's risk_level is MEDIUM or HIGH - i.e. NOT the
   * absence of risk (LOW). Matches the table's own Risk column/badge exactly, so this
   * count and what the analyst sees highlighted in the table never disagree. */
  protected get riskyApprovalsCount(): number {
    if (!this.result) {
      return 0;
    }
    return this.result.correlations.filter((entry) => this.riskLevelFor(entry) !== 'LOW').length;
  }

  // --- Table/detail display helpers ---------------------------------------------------

  protected statusClass(status: string): string {
    if (status === 'UNKNOWN') {
      return 'status-unknown';
    }
    const used = status.includes('USED');
    const revoked = status.includes('REVOKED');
    if (used && revoked) {
      return 'status-used-revoked';
    }
    if (revoked) {
      return 'status-revoked';
    }
    if (used) {
      return 'status-used';
    }
    return 'status-approved';
  }

  protected riskClass(level: TokenApprovalRiskLevel): string {
    return `risk-${level.toLowerCase()}`;
  }

  protected unlimitedBasisLabel(entry: TokenApprovalCorrelationEntry): string | null {
    if (entry.unlimited_basis === 'declared') {
      return this.t('Deklarisano u evidenciji', 'Declared in the evidence');
    }
    if (entry.unlimited_basis === 'potential_by_magnitude') {
      return this.t('Heuristika po veličini iznosa (nije potvrđeno)', 'Heuristic by amount size (not confirmed)');
    }
    return null;
  }

  /** Table cell: an astronomically large raw number is never a useful thing to read at a
   * glance, so an unlimited grant shows as a badge instead (the exact raw figure is still
   * available in the detail panel) - same "don't show false precision" discipline as the
   * backend's own unlimited_basis distinction (TOKEN-APPROVAL-IMPLEMENTATION.md #7.4). */
  protected allowanceLabel(entry: TokenApprovalCorrelationEntry): string {
    if (entry.unlimited_basis) {
      return this.t('NEOGRANIČENO', 'UNLIMITED');
    }
    return entry.approval_amount.toLocaleString('en-US', { maximumFractionDigits: 6 });
  }

  protected formatAmount(value: number | null): string {
    if (value === null || value === undefined) {
      return this.t('n/d', 'n/a');
    }
    return value.toLocaleString('en-US', { maximumFractionDigits: 6 });
  }

  /** Compact "how long" reading for a seconds figure (time to first use, time to
   * revocation, ...) - a plain seconds count is unreadable past a few minutes, and the
   * detail panel needs several of these at once. */
  protected formatDuration(seconds: number | null): string {
    if (seconds === null || seconds === undefined) {
      return this.t('n/d', 'n/a');
    }
    const abs = Math.abs(seconds);
    if (abs < 90) {
      return `${Math.round(seconds)}s`;
    }
    const minutes = seconds / 60;
    if (Math.abs(minutes) < 90) {
      return `${minutes.toFixed(1)} min`;
    }
    const hours = minutes / 60;
    if (Math.abs(hours) < 48) {
      return `${hours.toFixed(1)} h`;
    }
    return `${(hours / 24).toFixed(1)} d`;
  }

  protected formatUsed(used: boolean | null): string {
    if (used === true) {
      return this.t('Da', 'Yes');
    }
    if (used === false) {
      return this.t('Ne', 'No');
    }
    return this.t('Nepoznato (neatribuiran transfer postoji)', 'Unknown (an unattributed transfer exists)');
  }

  // --- Detail modal --------------------------------------------------------------------

  protected openDetail(entry: TokenApprovalCorrelationEntry): void {
    this.selectedEntry = entry;
  }

  protected closeDetail(): void {
    this.selectedEntry = null;
  }

  // --- "Show on Graph" -------------------------------------------------------------------
  // Preselects the spender as the Graph page's inspected node (same mechanism the Graph
  // page itself uses for a clicked node - AnalysisStateService.setSelectedNode) before
  // navigating there. The approve()/permit()/transferFrom row that produced this finding
  // reuses the base evidence schema's sender_address/recipient_address (see
  // TOKEN-APPROVAL-IMPLEMENTATION.md #8.1), so both owner and spender are genuine nodes in
  // the case's shared transaction graph, not a Token-Approval-only concept.

  protected showOnGraph(address: string): void {
    this.state.setSelectedNode({ id: address, address, label: address });
    this.router.navigateByUrl('/graph');
  }

  // --- "Add Investigator Note" ---------------------------------------------------------

  protected get storedInvestigationId(): string | null {
    try {
      return localStorage.getItem(SELECTED_INVESTIGATION_KEY);
    } catch {
      return null;
    }
  }

  protected openNoteDialog(address: string): void {
    if (!this.storedInvestigationId) {
      return;
    }
    this.noteDialogAddress = address;
    this.isNoteDialogOpen = true;
  }

  protected closeNoteDialog(): void {
    this.isNoteDialogOpen = false;
  }

  // --- Cross-reference: DEX Swap Analysis (TOKEN-APPROVAL-IMPLEMENTATION.md #19.3) -----

  /** One case-wide, address-unscoped call (same call shape the Graph page's own DEX swap
   * overlay already uses) - cheap, read-only, and independent of the result above: a
   * failure here just means no cross-reference badge is shown, it never blocks or errors
   * the Token Approval result already on screen. */
  private loadDexSwapCrossReference(caseId: string): void {
    this.api.getDexSwapAnalysis(caseId, null, this.selectedEvidence).subscribe({
      next: (swapResult) => {
        this.dexSwapAddresses = new Set(swapResult.events.map((event) => event.user_address));
      },
      error: () => {
        this.dexSwapAddresses = new Set();
      },
    });
  }

  /** True when the SPENDER of this finding also appears as the wallet side of a detected
   * DEX swap somewhere in this case's evidence - i.e. funds this spender controls (or the
   * spender itself) were also observed swapping tokens through a DEX. Never claims the
   * SAME funds moved - only that both facts are independently true of the same address,
   * which is what the underlying data can actually support (see TOKEN-APPROVAL-
   * IMPLEMENTATION.md #19.3 for why this stops short of a stronger claim). */
  protected hasDexSwapCrossReference(entry: TokenApprovalCorrelationEntry): boolean {
    if (this.dexSwapAddresses.has(entry.spender)) {
      return true;
    }
    return entry.receiving_destinations.some((address) => this.dexSwapAddresses.has(address));
  }

  // --- "Otvori u Pathfinding" (TOKEN-APPROVAL-IMPLEMENTATION.md #19.3) -----------------
  // Hands the owner/spender pair to Pathfinding via the same one-shot mechanism
  // AnalysisStateService already offers - Pathfinding decides for itself whether/how to
  // search (this page never runs a path search itself).

  protected openInPathfinding(entry: TokenApprovalCorrelationEntry): void {
    this.state.setPendingPathfindingSeed({ from: entry.owner, to: entry.spender });
    this.router.navigateByUrl('/pathfinding');
  }

  // --- PDF export (TOKEN-APPROVAL-IMPLEMENTATION.md #19) -------------------------------

  private static readonly PDF_NAVY: [number, number, number] = [13, 24, 40];
  private static readonly PDF_ACCENT: [number, number, number] = [43, 130, 191];
  private static readonly PDF_TEXT_GRAY: [number, number, number] = [100, 112, 128];
  private static readonly PDF_TEXT_DARK: [number, number, number] = [24, 28, 36];
  private static readonly PDF_WHITE: [number, number, number] = [255, 255, 255];
  private static readonly PDF_AMBER: [number, number, number] = [217, 119, 6];
  private static readonly PDF_HIGH: [number, number, number] = [220, 38, 38];
  private static readonly PDF_MEDIUM: [number, number, number] = [217, 119, 6];
  private static readonly PDF_LOW: [number, number, number] = [5, 150, 105];

  private static riskColor(level: TokenApprovalRiskLevel): [number, number, number] {
    if (level === 'HIGH') {
      return TokenApprovalComponent.PDF_HIGH;
    }
    if (level === 'MEDIUM') {
      return TokenApprovalComponent.PDF_MEDIUM;
    }
    return TokenApprovalComponent.PDF_LOW;
  }

  /** jsPDF's core fonts don't cover č/ć/š/ž/đ reliably - same fixed transliteration table
   * as every other report-producing page in this app (e.g. dex-swap-analysis.component
   * .ts's own asciiSafe) - duplicated, not shared, same convention as those pages. */
  private static readonly ASCII_MAP: Record<string, string> = {
    č: 'c', ć: 'c', š: 's', ž: 'z', đ: 'dj',
    Č: 'C', Ć: 'C', Š: 'S', Ž: 'Z', Đ: 'Dj',
  };

  private asciiSafe(value: string | null | undefined): string {
    return (value ?? '').replace(/[čćšžđČĆŠŽĐ]/g, (match) => TokenApprovalComponent.ASCII_MAP[match] ?? match);
  }

  /** PDF-string translator: SR or EN by the language chosen on the signing modal, then
   * ASCII-folded - same pattern as taint-analysis.component.ts's lx()/dex-swap-analysis
   * .component.ts's lx(). */
  private lx(sr: string, en: string): string {
    return this.asciiSafe(this.tokenApprovalPdfLang === 'sr' ? sr : en);
  }

  private static formatPdfAmount(value: number): string {
    return value.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 6 });
  }

  private formatPdfAllowance(entry: TokenApprovalCorrelationEntry): string {
    return entry.unlimited_basis ? this.lx('NEOGRANICENO', 'UNLIMITED') : TokenApprovalComponent.formatPdfAmount(entry.approval_amount);
  }

  /** Same loadPdfImage/loadImageSize helper duplicated across every report-producing page
   * in this app (taint-analysis/pathfinding/behavioral-analysis/dex-swap-analysis
   * .component.ts) - not centralized, same convention as the rest of this file. */
  private static loadImageSize(dataUrl: string): Promise<{ width: number; height: number }> {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
      image.onerror = () => reject(new Error('image load failed'));
      image.src = dataUrl;
    });
  }

  private async loadPdfImage(path: string): Promise<{ dataUrl: string; width: number; height: number }> {
    const response = await fetch(path);
    if (!response.ok) {
      throw new Error(`asset not found: ${path}`);
    }
    const blob = await response.blob();
    const dataUrl = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = () => reject(new Error('asset read failed'));
      reader.readAsDataURL(blob);
    });
    const size = await TokenApprovalComponent.loadImageSize(dataUrl);
    return { dataUrl, width: size.width, height: size.height };
  }

  protected get canExportPdf(): boolean {
    return !!this.result && this.result.correlations.length > 0 && !this.isAnalyzing && !this.isExportingPdf;
  }

  openSignatureDialog(): void {
    if (!this.canExportPdf) {
      return;
    }
    this.isSignatureDialogOpen = true;
    this.signatureDeclarationAccepted = false;
    this.signatureError = null;
    this.tokenApprovalPdfLang = this.settings.lang();
    setTimeout(() => this.signaturePad?.clear());
  }

  closeSignatureDialog(): void {
    this.isSignatureDialogOpen = false;
  }

  get canSubmitSignature(): boolean {
    return (this.signaturePad?.hasStrokes ?? false) && this.signatureDeclarationAccepted && !this.isExportingPdf;
  }

  private signatureDeclaration(): string {
    return this.tokenApprovalPdfLang === 'sr'
      ? 'Potvrđujem da sam izradio ovaj izveštaj u okviru navedenog predmeta i da su u njemu prikazani rezultati '
        + 'onakvi kakve je aplikacija izračunala (heuristički, gde je naznačeno) nad navedenom evidencijom.'
      : 'I confirm that I produced this report within the stated case and that the results shown in it are those '
        + 'the application computed (heuristically, where indicated) over the stated evidence.';
  }

  /** The exact data the verification hash is computed over - kept to the figures a reader
   * could dispute (which grants, which status/risk, which amounts/addresses), sorted so
   * field order never affects the hash - same discipline as every other report on this
   * page's reportContentPayload(). */
  private reportContentPayload(): Record<string, unknown> {
    const result = this.result!;
    return {
      case_id: this.activeCase!.id,
      evidence: this.selectedEvidence ?? 'combined',
      address: result.address,
      unlimited_threshold: result.unlimited_threshold,
      rapid_use_seconds: result.rapid_use_seconds,
      correlations: [...result.correlations]
        .map((entry) => ({
          owner: entry.owner,
          spender: entry.spender,
          token_address: entry.token_address,
          approval_amount: entry.approval_amount,
          unlimited_basis: entry.unlimited_basis,
          approval_timestamp: entry.approval_timestamp,
          approval_transaction_hash: entry.approval_transaction_hash,
          status: entry.status,
          used: entry.used,
          revoked: entry.revoked,
          transfer_from_count: entry.transfer_from_count,
          total_amount_transferred: entry.total_amount_transferred,
          risk_level: this.riskLevelFor(entry),
        }))
        .sort((a, b) => a.approval_timestamp.localeCompare(b.approval_timestamp) || a.spender.localeCompare(b.spender)),
    };
  }

  async confirmSignatureAndExport(): Promise<void> {
    if (!this.canSubmitSignature || !this.canExportPdf || !this.activeCase || !this.result) {
      return;
    }

    this.isExportingPdf = true;
    this.signatureError = null;
    try {
      const signatureImage = this.signaturePad!.getDataUrl();
      const declaration = this.signatureDeclaration();
      const result = this.result;

      const registration = await firstValueFrom(
        this.api.registerReport({
          case_id: this.activeCase.id,
          case_name: this.activeCase.name ?? '',
          declaration,
          content: this.reportContentPayload(),
          summary: {
            address: result.address,
            total_approvals: this.totalApprovals,
            unlimited_approvals: this.unlimitedApprovalsCount,
            active_approvals: this.activeApprovalsCount,
            revoked_approvals: this.revokedApprovalsCount,
            used_approvals: this.usedApprovalsCount,
            unused_approvals: this.unusedApprovalsCount,
            potentially_risky_approvals: this.riskyApprovalsCount,
          },
          report_type: 'token_approval',
        }),
      );

      const catEmblem = await this.loadPdfImage('assets/cat_pdf.png').catch(() => null);
      const sealImage = await this.loadPdfImage('assets/seal.png').catch(() => null);
      this.buildTokenApprovalPdf({ signatureImage, declaration, registration }, { catEmblem, sealImage });
      this.isSignatureDialogOpen = false;
    } catch {
      this.signatureError = this.t('Neuspešno generisanje PDF izveštaja.', 'Failed to generate the PDF report.');
    } finally {
      this.isExportingPdf = false;
    }
  }

  /** Groups correlations by (owner, spender, token) - the same grant relationship §12/§14
   * already group by - so the APPROVAL HISTORY section can render each relationship's own
   * chronological chain (APPROVE -> [change] -> [transferFrom] -> [REVOKED]) instead of one
   * flat, unordered list. Reuses `result.correlations` exactly as already fetched - no new
   * data, no re-derivation of what §14 already computed. */
  private historyGroups(): Array<{ owner: string; spender: string; token: string | null; entries: TokenApprovalCorrelationEntry[] }> {
    const byKey = new Map<string, { owner: string; spender: string; token: string | null; entries: TokenApprovalCorrelationEntry[] }>();
    for (const entry of this.result?.correlations ?? []) {
      const key = this.groupKey(entry.owner, entry.spender, entry.token_address);
      const bucket = byKey.get(key) ?? { owner: entry.owner, spender: entry.spender, token: entry.token_address, entries: [] };
      bucket.entries.push(entry);
      byKey.set(key, bucket);
    }
    for (const bucket of byKey.values()) {
      bucket.entries.sort((a, b) => a.approval_timestamp.localeCompare(b.approval_timestamp));
    }
    return [...byKey.values()];
  }

  private buildTokenApprovalPdf(
    signing: {
      signatureImage: string;
      declaration: string;
      registration: { verification_code: string; content_hash: string; registered_at: string; analyst: string };
    },
    assets: {
      catEmblem: { dataUrl: string; width: number; height: number } | null;
      sealImage: { dataUrl: string; width: number; height: number } | null;
    },
  ): void {
    const L = (sr: string, en: string): string => this.lx(sr, en);
    const caseSummary = this.activeCase!;
    const result = this.result!;
    const NAVY = TokenApprovalComponent.PDF_NAVY;
    const ACCENT = TokenApprovalComponent.PDF_ACCENT;
    const TEXT_GRAY = TokenApprovalComponent.PDF_TEXT_GRAY;
    const TEXT_DARK = TokenApprovalComponent.PDF_TEXT_DARK;
    const WHITE = TokenApprovalComponent.PDF_WHITE;

    const doc = new jsPDF({ unit: 'mm', format: 'a4' });
    const pageWidth = doc.internal.pageSize.getWidth();
    const pageHeight = doc.internal.pageSize.getHeight();
    const marginX = 14;
    const usableWidth = pageWidth - marginX * 2;
    let y = 32;

    const barHeight = 24;
    doc.setFillColor(...NAVY);
    doc.rect(0, 0, pageWidth, barHeight, 'F');
    let titleX = marginX;
    if (assets.catEmblem) {
      const emblem = 19;
      const emblemW = (assets.catEmblem.width / assets.catEmblem.height) * emblem;
      doc.addImage(assets.catEmblem.dataUrl, 'PNG', marginX, (barHeight - emblem) / 2, emblemW, emblem);
      titleX = marginX + emblemW + 5;
    }
    doc.setTextColor(...WHITE);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(15);
    doc.text(L('Lusi v1.0 - Izvestaj Token Approval / Ice Phishing analize', 'Lusi v1.0 - Token Approval / Ice Phishing analysis report'), titleX, 11);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(10);
    doc.text(`${L('Slucaj', 'Case')}: ${this.asciiSafe(caseSummary.name)}`, titleX, 19);
    doc.setTextColor(...TEXT_DARK);

    const kv = (label: string, value: string): void => {
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(9.5);
      doc.setTextColor(...TEXT_GRAY);
      doc.text(label, marginX, y);
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(10);
      doc.setTextColor(...TEXT_DARK);
      const lines: string[] = doc.splitTextToSize(value || 'n/a', usableWidth - 42);
      doc.text(lines, marginX + 42, y);
      y += Math.max(6, lines.length * 5);
    };

    kv('CASE ID', caseSummary.id);
    kv(L('ANALIZIRANA ADRESA', 'ANALYZED ADDRESS'), this.asciiSafe(result.address ?? L('(sve adrese)', '(all addresses)')));
    kv(L('IZVEZAO', 'EXPORTED BY'), this.asciiSafe(this.auth.currentUser?.username ?? caseSummary.analyst));
    kv(
      L('EVIDENCIJA', 'EVIDENCE'),
      this.selectedEvidence ? this.asciiSafe(this.selectedEvidence) : L('Sve transakcije (kombinovano)', 'All transactions (combined)'),
    );
    kv(L('GENERISANO', 'GENERATED AT'), new Date().toLocaleString(this.tokenApprovalPdfLang === 'sr' ? 'sr-RS' : 'en-GB'));
    y += 2;

    // Short version of the disclaimer, BEFORE any result - full methodology appendix at
    // the end. Composed here (not read from result.disclaimer) so it follows the chosen
    // report language - the backend field is Serbian-only.
    const disclaimerLines = doc.splitTextToSize(
      L(
        'Token Approval Analysis cita iskljucivo polja koja evidencija stvarno deklarise - nista nije dekodirano sa '
          + 'lanca. Risk nivo i risk indikatori su OZNACENE HEURISTIKE za prioritizaciju pregleda - NIKAD tvrdnja da '
          + 'je adresa ili ugovor zlonameran/kriminalan, i nikad dokaz.',
        'Token Approval Analysis reads only fields the evidence actually declares - nothing is decoded from the '
          + 'chain. The risk level and risk indicators are LABELLED HEURISTICS for prioritising review - NEVER a claim '
          + 'that an address or contract is malicious/criminal, and never proof.',
      )
        + ' '
        + L('Detaljno objasnjenje se nalazi na kraju ovog izvestaja.', 'A detailed explanation is at the end of this report.'),
      usableWidth - 8,
    );
    const noteBoxHeight = disclaimerLines.length * 4.2 + 7;
    doc.setFillColor(253, 250, 240);
    doc.setDrawColor(...TokenApprovalComponent.PDF_AMBER);
    doc.setLineWidth(0.4);
    doc.roundedRect(marginX, y - 4, usableWidth, noteBoxHeight, 2, 2, 'FD');
    doc.setFont('helvetica', 'italic');
    doc.setFontSize(8.5);
    doc.setTextColor(...TEXT_DARK);
    doc.text(disclaimerLines, marginX + 4, y + 1);
    y += noteBoxHeight + 3;
    doc.setFont('helvetica', 'normal');

    const sectionTitle = (title: string): void => {
      y += 3;
      if (y > pageHeight - 30) {
        doc.addPage();
        y = 16;
      }
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(12);
      doc.setTextColor(...NAVY);
      doc.text(title, marginX, y);
      doc.setDrawColor(...ACCENT);
      doc.setLineWidth(0.6);
      doc.line(marginX, y + 2, pageWidth - marginX, y + 2);
      y += 8;
      doc.setTextColor(...TEXT_DARK);
    };

    // --- SUMMARY -----------------------------------------------------------------------
    sectionTitle(L('Rezime (Summary)', 'Summary'));
    const summaryCards: Array<[string, string | number, [number, number, number]]> = [
      [L('Ukupno', 'Total'), this.totalApprovals, ACCENT],
      [L('Neogranicenih', 'Unlimited'), this.unlimitedApprovalsCount, TokenApprovalComponent.PDF_HIGH],
      [L('Aktivnih', 'Active'), this.activeApprovalsCount, TEXT_GRAY],
      [L('Opozvanih', 'Revoked'), this.revokedApprovalsCount, TEXT_GRAY],
      [L('Koriscenih', 'Used'), this.usedApprovalsCount, ACCENT],
      [L('Nekoriscenih', 'Unused'), this.unusedApprovalsCount, TEXT_GRAY],
      [L('Rizicnih', 'Risky'), this.riskyApprovalsCount, TokenApprovalComponent.PDF_MEDIUM],
    ];
    const gap = 3;
    const colWidth = (usableWidth - gap * (summaryCards.length - 1)) / summaryCards.length;
    const cardsY = y;
    let cardX = marginX;
    for (const [label, value, color] of summaryCards) {
      doc.setFillColor(...color);
      doc.rect(cardX, cardsY, colWidth, 16, 'F');
      doc.setTextColor(...WHITE);
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(13);
      doc.text(String(value), cardX + colWidth / 2, cardsY + 7, { align: 'center' });
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(6.5);
      doc.text(doc.splitTextToSize(label, colWidth - 3), cardX + colWidth / 2, cardsY + 12, { align: 'center' });
      cardX += colWidth + gap;
    }
    y = cardsY + 16 + 6;
    doc.setTextColor(...TEXT_DARK);

    if (!result.data_completeness.event_type_declared) {
      doc.setFont('helvetica', 'italic');
      doc.setFontSize(8.5);
      doc.setTextColor(...TEXT_GRAY);
      const lines = doc.splitTextToSize(
        L(
          'Evidencija nema kolonu "event_type" - nijedan approve/permit red nije prepoznat (vidi Ogranicenja).',
          'The evidence has no "event_type" column - no approve/permit row was recognised (see Limitations).',
        ),
        usableWidth,
      );
      doc.text(lines, marginX, y);
      y += lines.length * 4.2 + 4;
      doc.setTextColor(...TEXT_DARK);
    }

    // --- APPROVAL FINDINGS ---------------------------------------------------------------
    sectionTitle(L('Pronadjena odobrenja (Approval Findings)', 'Approval Findings'));
    const sortedEntries = [...result.correlations].sort((a, b) => a.approval_timestamp.localeCompare(b.approval_timestamp));
    autoTable(doc, {
      startY: y,
      margin: { left: marginX, right: marginX },
      head: [[L('Owner', 'Owner'), 'Spender', L('Token', 'Token'), L('Allowance', 'Allowance'), L('Status', 'Status'), L('Rizik', 'Risk'), L('Datum', 'Date'), L('Tx hash (odobrenje)', 'Tx hash (approval)')]],
      body: sortedEntries.map((entry) => [
        entry.owner,
        entry.spender,
        entry.token_address ?? '?',
        this.formatPdfAllowance(entry),
        entry.status,
        this.riskLevelFor(entry),
        new Date(entry.approval_timestamp).toLocaleString(this.tokenApprovalPdfLang === 'sr' ? 'sr-RS' : 'en-GB'),
        entry.approval_transaction_hash ?? 'n/a',
      ]),
      styles: { fontSize: 6.8, cellPadding: 1.3, font: 'courier', textColor: TEXT_DARK },
      headStyles: { fillColor: NAVY, textColor: WHITE, font: 'helvetica', fontStyle: 'bold', fontSize: 7 },
      alternateRowStyles: { fillColor: [240, 245, 250] },
      columnStyles: { 5: { cellWidth: 14, font: 'helvetica' } },
      didParseCell: (data) => {
        if (data.section === 'body' && data.column.index === 5) {
          const level = sortedEntries[data.row.index] ? this.riskLevelFor(sortedEntries[data.row.index]) : 'LOW';
          data.cell.styles.textColor = TokenApprovalComponent.riskColor(level);
          data.cell.styles.fontStyle = 'bold';
        }
      },
    });
    y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 4;

    // --- Usage & revocation detail table (second table, same section - keeps the first
    // table's columns from becoming unreadably cramped, see TOKEN-APPROVAL-
    // IMPLEMENTATION.md #19.1) ---
    const withActivity = sortedEntries.filter((entry) => entry.transfer_from_count > 0 || entry.revoked);
    if (withActivity.length > 0) {
      if (y > pageHeight - 40) {
        doc.addPage();
        y = 16;
      }
      sectionTitle(L('Koriscenje i opoziv (Usage & Revocation)', 'Usage & Revocation'));
      autoTable(doc, {
        startY: y,
        margin: { left: marginX, right: marginX },
        head: [[
          'Spender', L('Prva upotreba', 'First use'), L('Vreme do prve upotrebe', 'Time to first use'),
          L('# transferFrom', '# transferFrom'), L('Ukupno povuceno', 'Total withdrawn'),
          L('Odrediste(a)', 'Destination(s)'), L('Opoziv', 'Revocation'),
        ]],
        body: withActivity.map((entry) => [
          entry.spender,
          entry.first_use_timestamp ? new Date(entry.first_use_timestamp).toLocaleString(this.tokenApprovalPdfLang === 'sr' ? 'sr-RS' : 'en-GB') : L('nikad', 'never'),
          this.formatDuration(entry.time_to_first_use_seconds),
          String(entry.transfer_from_count),
          TokenApprovalComponent.formatPdfAmount(entry.total_amount_transferred),
          entry.receiving_destinations.join(', ') || 'n/a',
          entry.revoked
            ? `${entry.revocation_timestamp ? new Date(entry.revocation_timestamp).toLocaleDateString(this.tokenApprovalPdfLang === 'sr' ? 'sr-RS' : 'en-GB') : ''} (${this.formatDuration(entry.seconds_to_revocation)} ${L('posle odobrenja', 'after approval')})`
            : L('nije opozvano', 'not revoked'),
        ]),
        styles: { fontSize: 6.8, cellPadding: 1.3, font: 'courier', textColor: TEXT_DARK },
        headStyles: { fillColor: NAVY, textColor: WHITE, font: 'helvetica', fontStyle: 'bold', fontSize: 6.8 },
        alternateRowStyles: { fillColor: [240, 245, 250] },
      });
      y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 4;
    }

    // --- APPROVAL HISTORY ----------------------------------------------------------------
    const groups = this.historyGroups().filter((group) => group.entries.length > 0);
    if (groups.length > 0) {
      if (y > pageHeight - 40) {
        doc.addPage();
        y = 16;
      }
      sectionTitle(L('Istorija odobrenja (Approval History)', 'Approval History'));
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(8.5);
      for (const group of groups) {
        if (y > pageHeight - 24) {
          doc.addPage();
          y = 16;
        }
        doc.setFont('helvetica', 'bold');
        doc.setFontSize(9);
        doc.setTextColor(...NAVY);
        doc.text(`${group.owner}  ->  ${group.spender}  (${group.token ?? '?'})`, marginX, y);
        y += 5;
        doc.setFont('courier', 'normal');
        doc.setFontSize(7.5);
        doc.setTextColor(...TEXT_DARK);
        const steps: string[] = [];
        group.entries.forEach((entry, index) => {
          const when = new Date(entry.approval_timestamp).toLocaleDateString(this.tokenApprovalPdfLang === 'sr' ? 'sr-RS' : 'en-GB');
          steps.push(`${index === 0 ? 'APPROVE' : L('PROMENA', 'CHANGE')} ${this.formatPdfAllowance(entry)} @ ${when}`);
          if (entry.transfer_from_count > 0) {
            steps.push(`  -> TRANSFERFROM x${entry.transfer_from_count} (${TokenApprovalComponent.formatPdfAmount(entry.total_amount_transferred)})`);
          }
          if (entry.revoked) {
            steps.push(`  -> REVOKED${entry.revocation_timestamp ? ' @ ' + new Date(entry.revocation_timestamp).toLocaleDateString(this.tokenApprovalPdfLang === 'sr' ? 'sr-RS' : 'en-GB') : ''}`);
          }
        });
        const lines = doc.splitTextToSize(steps.join('   >>   '), usableWidth - 4);
        if (y + lines.length * 3.6 > pageHeight - 18) {
          doc.addPage();
          y = 16;
        }
        doc.text(lines, marginX + 2, y);
        y += lines.length * 3.6 + 4;
      }
      doc.setFont('helvetica', 'normal');
    }

    // --- RISK ASSESSMENT -------------------------------------------------------------------
    sectionTitle(L('Procena rizika (Risk Assessment)', 'Risk Assessment'));
    doc.setFont('helvetica', 'italic');
    doc.setFontSize(8.5);
    doc.setTextColor(...TEXT_GRAY);
    const riskIntro = doc.splitTextToSize(
      L(
        'Ovde navedene ocene NISU dokaz da je spender zlonameran. Formulacije nize opisuju OBRAZAC u podacima koji '
          + 'zasluzuje dodatnu proveru ("potencijalno rizicno odobrenje", "moguca zloupotreba odobrenja", "sumnjiv '
          + 'obrazac odobrenja") - nikad tvrdnju o identitetu ili nameri.',
        'The ratings below are NOT proof that the spender is malicious. The wording describes a PATTERN in the data '
          + 'that deserves further review ("potentially risky approval", "potential approval abuse", "suspicious '
          + 'approval pattern") - never a claim about identity or intent.',
      ),
      usableWidth,
    );
    doc.text(riskIntro, marginX, y);
    y += riskIntro.length * 4.2 + 4;
    doc.setTextColor(...TEXT_DARK);

    const riskyEntries = sortedEntries.filter((entry) => this.riskLevelFor(entry) !== 'LOW');
    if (riskyEntries.length > 0) {
      autoTable(doc, {
        startY: y,
        margin: { left: marginX, right: marginX },
        head: [['Spender', L('Rizik', 'Risk'), L('Indikatori', 'Indicators')]],
        body: riskyEntries.map((entry) => [
          entry.spender,
          this.riskLevelFor(entry),
          this.riskIndicatorsFor(entry).map((indicator) => indicator.label).join('; '),
        ]),
        styles: { fontSize: 7, cellPadding: 1.4, font: 'helvetica', textColor: TEXT_DARK },
        headStyles: { fillColor: NAVY, textColor: WHITE, fontStyle: 'bold' },
        alternateRowStyles: { fillColor: [253, 246, 227] },
        columnStyles: { 0: { font: 'courier' }, 1: { cellWidth: 16 } },
        didParseCell: (data) => {
          if (data.section === 'body' && data.column.index === 1) {
            const level = riskyEntries[data.row.index] ? this.riskLevelFor(riskyEntries[data.row.index]) : 'LOW';
            data.cell.styles.textColor = TokenApprovalComponent.riskColor(level);
            data.cell.styles.fontStyle = 'bold';
          }
        },
      });
      y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 4;

      // Full reasons, one small block per risky finding - the table above is the index,
      // this is the "why" a reader would actually need before acting on it.
      for (const entry of riskyEntries) {
        const indicators = this.riskIndicatorsFor(entry);
        if (indicators.length === 0) {
          continue;
        }
        if (y > pageHeight - 30) {
          doc.addPage();
          y = 16;
        }
        doc.setFont('helvetica', 'bold');
        doc.setFontSize(8);
        doc.setTextColor(...NAVY);
        doc.text(`${entry.spender} (${this.riskLevelFor(entry)})`, marginX, y);
        y += 4.5;
        for (const indicator of indicators) {
          doc.setFont('helvetica', 'bold');
          doc.setFontSize(7.5);
          doc.setTextColor(...TEXT_DARK);
          const labelLines = doc.splitTextToSize(`- ${indicator.label}`, usableWidth - 4);
          doc.text(labelLines, marginX + 2, y);
          y += labelLines.length * 3.6;
          doc.setFont('helvetica', 'normal');
          doc.setFontSize(7);
          doc.setTextColor(...TEXT_GRAY);
          for (const reason of indicator.reasons) {
            const reasonLines = doc.splitTextToSize(`  ${reason}`, usableWidth - 8);
            if (y + reasonLines.length * 3.4 > pageHeight - 18) {
              doc.addPage();
              y = 16;
            }
            doc.text(reasonLines, marginX + 4, y);
            y += reasonLines.length * 3.4;
          }
          y += 1;
        }
        y += 2;
      }
    } else {
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(9);
      doc.text(L('Nijedno odobrenje nije oznaceno kao rizicno (LOW rizik za sve).', 'No approval is flagged as risky (LOW risk for all).'), marginX, y);
      y += 6;
    }

    // --- RELATED ACTIVITY ------------------------------------------------------------------
    sectionTitle(L('Povezana aktivnost (Related Activity)', 'Related Activity'));
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9);
    doc.setTextColor(...TEXT_DARK);
    const crossReferenced = sortedEntries.filter((entry) => this.hasDexSwapCrossReference(entry));
    const relatedLines: string[] = [
      L(
        `${sortedEntries.filter((e) => e.transfer_from_count > 0).length} od ${sortedEntries.length} odobrenja ima bar jednu povezanu transferFrom transakciju (vidi "Koriscenje i opoziv" iznad).`,
        `${sortedEntries.filter((e) => e.transfer_from_count > 0).length} of ${sortedEntries.length} approvals have at least one linked transferFrom transaction (see "Usage & Revocation" above).`,
      ),
    ];
    if (crossReferenced.length > 0) {
      relatedLines.push(
        L(
          `${crossReferenced.length} spender/odrediste adresa se takodje pojavljuje u detektovanim DEX Swap dogadjajima ovog slucaja: ${crossReferenced.map((e) => e.spender).join(', ')}.`,
          `${crossReferenced.length} spender/destination address(es) also appear in this case's detected DEX Swap events: ${crossReferenced.map((e) => e.spender).join(', ')}.`,
        ),
      );
    } else {
      relatedLines.push(
        L(
          'Nijedna spender/odrediste adresa se ne poklapa sa detektovanim DEX Swap dogadjajima u ovoj evidenciji.',
          'No spender/destination address matches a detected DEX Swap event in this evidence.',
        ),
      );
    }
    relatedLines.push(
      L(
        'Taint analiza (da li su povucena sredstva deo vec zaprljanog toka) se ne pokrece iz ove analize - proveri '
          + 'na stranici Graf, preko isprekidane APPROVAL veze, POSLE pokretanja "Analiziraj graf".',
        'Taint analysis (whether the withdrawn funds are part of an already-tainted flow) is not run from this '
          + 'analysis - check it on the Graph page, via the dashed APPROVAL edge, AFTER running "Analyze graph".',
      ),
    );
    relatedLines.push(
      L(
        'Dalje pracenje kretanja sredstava (Pathfinding) je dostupno preko dugmeta "Otvori u Pathfinding" na svakom nalazu.',
        'Further tracing of fund movement (Pathfinding) is available via the "Open in Pathfinding" button on each finding.',
      ),
    );
    for (const line of relatedLines) {
      const wrapped = doc.splitTextToSize(`- ${line}`, usableWidth - 4);
      if (y + wrapped.length * 4.4 > pageHeight - 18) {
        doc.addPage();
        y = 16;
      }
      doc.text(wrapped, marginX, y);
      y += wrapped.length * 4.4 + 1.5;
    }

    // --- CHAIN OF EVIDENCE -----------------------------------------------------------------
    sectionTitle(L('Lanac dokaza (Chain of Evidence)', 'Chain of Evidence'));
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9);
    doc.setTextColor(...TEXT_DARK);
    const custodyLines = doc.splitTextToSize(
      L(
        'Ova analiza je pokrenuta uz potpisan razlog pristupa - svaka pojedinacna approve/permit transakcija u '
          + 'obuhvacenoj evidenciji je upisana u lanac dokaza (Custody Log), sa strukturiranim TOKEN_APPROVAL '
          + 'dokazom (blockchain cinjenice / izracunati indikatori / heuristicki zakljucci, odvojeno). Puna hronologija '
          + 'pristupa po transakciji je dostupna na stranici "Lanac dokaza".',
        'This analysis was run with a signed access reason - every individual approve/permit transaction in the '
          + 'covered evidence was written into the chain of evidence (Custody Log), carrying a structured '
          + 'TOKEN_APPROVAL evidence item (blockchain facts / computed indicators / heuristic conclusions, kept '
          + 'separate). The full per-transaction access history is available on the "Chain of Evidence" page.',
      ),
      usableWidth,
    );
    doc.text(custodyLines, marginX, y);
    y += custodyLines.length * 4.6 + 4;

    // --- Methodology / limitations appendix ------------------------------------------------
    const paragraph = (text: string, options?: { bold?: boolean; size?: number; gap?: number }): void => {
      const size = options?.size ?? 9;
      doc.setFont('helvetica', options?.bold ? 'bold' : 'normal');
      doc.setFontSize(size);
      doc.setTextColor(...TEXT_DARK);
      const lines: string[] = doc.splitTextToSize(text, usableWidth);
      const lineHeight = size * 0.48;
      if (y + lines.length * lineHeight > pageHeight - 18) {
        doc.addPage();
        y = 16;
      }
      doc.text(lines, marginX, y);
      y += lines.length * lineHeight + (options?.gap ?? 3);
    };
    const bullet = (text: string): void => {
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(9);
      doc.setTextColor(...TEXT_DARK);
      const lines: string[] = doc.splitTextToSize(text, usableWidth - 6);
      if (y + lines.length * 4.4 > pageHeight - 18) {
        doc.addPage();
        y = 16;
      }
      doc.setFillColor(...ACCENT);
      doc.circle(marginX + 1.4, y - 1.2, 0.7, 'F');
      doc.text(lines, marginX + 6, y);
      y += lines.length * 4.4 + 1.6;
    };

    if (y > pageHeight - 60) {
      doc.addPage();
      y = 16;
    }
    sectionTitle(L('Metodologija i ogranicenja', 'Methodology and limitations'));
    paragraph(L('Sta ovo NIJE', 'What this is NOT'), { bold: true, size: 10, gap: 2 });
    paragraph(
      L(
        'Ovo nije dokaz krivicnog dela niti potvrda da je spender adresa zlonamerna. "Rizik" je heuristicki '
          + 'indikator za prioritizaciju rucnog pregleda, izveden iz obrazaca u samoj evidenciji (neogranicen iznos, '
          + 'brzo povlacenje, nepoznat spender, ...) - nikad iz spoljasnjeg izvora reputacije.',
        'This is not proof of a crime nor confirmation that the spender address is malicious. "Risk" is a heuristic '
          + 'indicator for prioritising manual review, derived from patterns in the evidence itself (unlimited amount, '
          + 'rapid draining, unknown spender, ...) - never from an external reputation source.',
      ),
      { gap: 5 },
    );
    paragraph(L('Ogranicenja podataka', 'Data limitations'), { bold: true, size: 10, gap: 2 });
    bullet(
      L(
        'Projekat danas ne povlaci Approval/permit evente automatski sa lanca (nema tokentx/getLogs poziva) - svi '
          + 'podaci u ovom izvestaju poticu iz evidencije koju je analiticar uneo/uvezao sa event_type/token_address/'
          + 'spender_address/is_unlimited kolonama.',
        'The project does not currently pull Approval/permit events automatically from the chain (no tokentx/getLogs '
          + 'call) - every figure in this report comes from evidence the analyst entered/imported with event_type/'
          + 'token_address/spender_address/is_unlimited columns.',
      ),
    );
    bullet(
      L(
        '"Neograniceno" (UNLIMITED) je ili direktno deklarisano u evidenciji, ili heuristika po velicini iznosa - '
          + 'nikad potvrdjeno poredjenjem sa tacnom uint256 max vrednoscu (iznos stize kao float64, bez registra '
          + 'decimala po tokenu).',
        '"UNLIMITED" is either directly declared in the evidence, or a heuristic based on amount size - never '
          + 'confirmed against the exact uint256 max value (the amount arrives as a float64, with no per-token '
          + 'decimals registry).',
      ),
    );
    bullet(
      L(
        'transferFrom transakcija bez spender_address kolone ostaje neatribuirana - ne pretpostavlja se kom '
          + 'odobrenju pripada.',
        'A transferFrom transaction without a spender_address column stays unattributed - it is never assumed to '
          + 'belong to a specific grant.',
      ),
    );
    bullet(
      L(
        'Nema registra USD vrednosti/cene tokena - "veliki iznos" je heuristika po velicini broja, ne po '
          + 'novcanoj vrednosti.',
        'There is no token price/USD value registry - "large amount" is a heuristic by number size, not monetary '
          + 'value.',
      ),
    );
    bullet(
      L(
        'DEX Swap unakrsna provera (Related Activity) potvrdjuje samo da se ISTA adresa pojavljuje u oba nalaza - '
          + 'ne da su ISTA sredstva presla iz jedne aktivnosti u drugu.',
        'The DEX Swap cross-reference (Related Activity) only confirms the SAME address appears in both findings - '
          + 'not that the SAME funds moved from one activity into the other.',
      ),
    );

    // --- Signature and seal ---------------------------------------------------------
    doc.addPage();
    y = 16;
    sectionTitle(L('Potpis i overa', 'Signature and certification'));
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9);
    doc.setTextColor(...TEXT_DARK);
    const declarationLines = doc.splitTextToSize(this.asciiSafe(signing.declaration), usableWidth);
    doc.text(declarationLines, marginX, y);
    y += declarationLines.length * 4.6 + 6;

    const signatureBoxWidth = usableWidth * 0.52;
    const signatureBoxHeight = 34;
    doc.setDrawColor(...TEXT_GRAY);
    doc.setLineWidth(0.3);
    doc.rect(marginX, y, signatureBoxWidth, signatureBoxHeight);
    doc.addImage(signing.signatureImage, 'PNG', marginX + 2, y + 2, signatureBoxWidth - 4, signatureBoxHeight - 4);

    doc.setFontSize(8);
    doc.setTextColor(...TEXT_GRAY);
    doc.text(L('Potpis analiticara', 'Analyst signature'), marginX, y + signatureBoxHeight + 4);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(9.5);
    doc.setTextColor(...TEXT_DARK);
    doc.text(this.asciiSafe(signing.registration.analyst), marginX, y + signatureBoxHeight + 9);

    const sealCenterX = marginX + signatureBoxWidth + (usableWidth - signatureBoxWidth) / 2;
    const sealCenterY = y + signatureBoxHeight / 2;
    if (assets.sealImage) {
      const sealHeight = 34;
      const sealWidth = (assets.sealImage.width / assets.sealImage.height) * sealHeight;
      doc.addImage(assets.sealImage.dataUrl, 'PNG', sealCenterX - sealWidth / 2, sealCenterY - sealHeight / 2, sealWidth, sealHeight);
    } else {
      const sealRadius = 19;
      doc.setDrawColor(...NAVY);
      doc.setLineWidth(1.1);
      doc.circle(sealCenterX, sealCenterY, sealRadius);
      doc.setLineWidth(0.4);
      doc.circle(sealCenterX, sealCenterY, sealRadius - 2.5);
      doc.setTextColor(...NAVY);
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(11);
      doc.text('LUSI', sealCenterX, sealCenterY - 3, { align: 'center' });
      doc.setFontSize(6.5);
      doc.setFont('helvetica', 'normal');
      doc.text(L('DIGITALNA FORENZIKA', 'DIGITAL FORENSICS'), sealCenterX, sealCenterY + 2, { align: 'center' });
    }
    doc.setTextColor(...NAVY);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(7);
    doc.text(
      `${L('OVERENO', 'CERTIFIED')} ${new Date(signing.registration.registered_at).toLocaleDateString(this.tokenApprovalPdfLang === 'sr' ? 'sr-RS' : 'en-GB')}`,
      sealCenterX,
      y + signatureBoxHeight + 4,
      { align: 'center' },
    );
    doc.setFont('helvetica', 'normal');
    y += signatureBoxHeight + 16;

    sectionTitle(L('Provera verodostojnosti', 'Authenticity check'));
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(9);
    doc.setTextColor(...TEXT_GRAY);
    doc.text(L('KONTROLNI BROJ', 'VERIFICATION CODE'), marginX, y);
    doc.setFont('courier', 'bold');
    doc.setFontSize(13);
    doc.setTextColor(...NAVY);
    doc.text(signing.registration.verification_code, marginX + 45, y + 0.5);
    y += 8;

    doc.setFont('helvetica', 'bold');
    doc.setFontSize(9);
    doc.setTextColor(...TEXT_GRAY);
    doc.text(L('OTISAK SADRZAJA', 'CONTENT HASH'), marginX, y);
    doc.setFont('courier', 'normal');
    doc.setFontSize(7.5);
    doc.setTextColor(...TEXT_DARK);
    doc.text(doc.splitTextToSize(signing.registration.content_hash, usableWidth - 45), marginX + 45, y);
    y += 9;

    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9);
    doc.setTextColor(...TEXT_DARK);
    const verifyLines = doc.splitTextToSize(
      L(
        'Verodostojnost se proverava u aplikaciji Lusi, unosom gornjeg kontrolnog broja. Ako se otisak sadrzaja '
          + 'poklapa sa zabelezenim, podaci u izvestaju su isti kao u trenutku izvoza.',
        'Authenticity is verified in the Lusi application by entering the verification code above. If the content '
          + 'hash matches the recorded one, the data in the report is the same as at export time.',
      ),
      usableWidth,
    );
    doc.text(verifyLines, marginX, y);

    const pageCount = doc.getNumberOfPages();
    for (let page = 1; page <= pageCount; page++) {
      doc.setPage(page);
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(8);
      doc.setTextColor(...TEXT_GRAY);
      doc.text(`Lusi v1.0 forensic export | ${L('Strana', 'Page')} ${page}/${pageCount}`, pageWidth / 2, pageHeight - 8, { align: 'center' });
    }

    doc.save(`${caseSummary.id}_token_approval_report.pdf`);
  }
}
