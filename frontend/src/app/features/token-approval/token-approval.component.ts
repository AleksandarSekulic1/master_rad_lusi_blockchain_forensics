import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, OnInit } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { distinctUntilChanged, map } from 'rxjs/operators';

import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { ApiService } from '../../core/services/api.service';
import { SettingsService } from '../../core/services/settings.service';
import {
  CaseSummary,
  EvidenceEntry,
  NodeLinkGraphResponse,
  TokenApprovalCorrelationEntry,
  TokenApprovalCorrelationResult,
  TokenApprovalGroup,
  TokenApprovalRiskLevel,
} from '../../models/blockchain-forensics.models';
import { InvestigatorNodeDialogComponent } from '../investigator-node-dialog/investigator-node-dialog.component';

/** Same localStorage key graph-visualization.component.ts persists the investigator layer's
 * chosen investigation under - deliberately read, never written, from this page: the
 * investigation is picked once on the Graph page, and every other page that can open
 * InvestigatorNodeDialogComponent (see taint-analysis.component.ts's own
 * storedInvestigationId) just reuses whatever is already active there, rather than
 * duplicating a whole investigation picker on every analysis page. */
const SELECTED_INVESTIGATION_KEY = 'lusi_selected_investigation';

/** Token Approval / Ice Phishing Analysis - "for this address, what ERC-20 allowances did
 * it grant (or receive), and what happened to each one" (see
 * TOKEN-APPROVAL-IMPLEMENTATION.md #16). Deliberately separate page from Graph/Taint/
 * Pathfinding/Behavioral/DEX Swap, same reasoning as those pages' own headers.
 *
 * Backend-Phase-1 only (see TOKEN-APPROVAL-IMPLEMENTATION.md #12/#16.7): there is no
 * custody-gated "run" variant, no PDF report and no audit log entry yet for this analysis -
 * ANALYZE calls the read-only correlation endpoint directly, no access-reason dialog (that
 * is a deliberate, documented gap, not an oversight - see #16.7).
 *
 * Reuses the same case/evidence-picker shell as the sibling analysis pages
 * (AnalysisStateService, ApiService.getCase for the evidence list, ApiService.getCaseGraph
 * for the address autocomplete list - identical pattern to dex-swap-analysis.component.ts's
 * own loadCaseAddresses).
 */
@Component({
  selector: 'app-token-approval',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, InvestigatorNodeDialogComponent],
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

  constructor(
    private readonly state: AnalysisStateService,
    private readonly api: ApiService,
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
  }

  protected get canAnalyze(): boolean {
    return !!this.activeCase && this.address.trim().length > 0 && !this.isAnalyzing;
  }

  protected analyze(): void {
    if (!this.canAnalyze) {
      return;
    }
    const caseId = this.activeCase!.id;
    const address = this.address.trim();

    this.isAnalyzing = true;
    this.analysisError = null;
    this.selectedEntry = null;

    this.api.getTokenApprovalCorrelation(caseId, address, this.selectedEvidence).subscribe({
      next: (result) => {
        this.result = result;
        this.groupByKey = new Map(result.groups.map((group) => [this.groupKey(group.owner, group.spender, group.token_address), group]));
        this.isAnalyzing = false;
      },
      error: (error: HttpErrorResponse) => {
        this.result = null;
        this.groupByKey = new Map();
        this.isAnalyzing = false;
        this.analysisError =
          error.status === 404
            ? this.t('Adresa nije pronađena u evidenciji ovog slučaja.', 'The address was not found in this case’s evidence.')
            : this.t('Neuspešna Token Approval analiza.', 'The Token Approval analysis failed.');
      },
    });
  }

  /** File name of the currently scoped evidence - same pattern as the sibling analysis
   * pages, kept here only for the "Prikaz transakcija" label, since this page has no
   * custody dialog to feed it into. */
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
}
