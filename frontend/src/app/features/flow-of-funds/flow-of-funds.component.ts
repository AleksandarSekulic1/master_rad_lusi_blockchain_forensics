import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, OnInit } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { distinctUntilChanged, map } from 'rxjs/operators';

import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { CaseDataApiService } from '../../core/services/case-data.api';
import { SettingsService } from '../../core/services/settings.service';
import { CaseSummary, EvidenceEntry, TransactionCustodyEntry } from '../../core/models/shared.models';
import { CustodyAccessDialogComponent } from '../custody-access-dialog/custody-access-dialog.component';
import { FlowOfFundsApiService } from './flow-of-funds.api';
import { AggregatedFlow, FlowAggregationLevel, FlowOfFundsDirection, FlowOfFundsNode, FlowOfFundsResult } from './flow-of-funds.models';
import { SankeyDiagramComponent, SankeyDiagramLink, SankeyDiagramNode } from './sankey-diagram/sankey-diagram.component';

const MIN_LEVELS = 1;
const MAX_LEVELS = 10;
const DEFAULT_LEVELS = 4;

/** Flow of Funds / Layering Analysis - traces aggregated flows outward (or backward) from
 * one or more seed addresses across several hops, rendered as an interactive Sankey
 * diagram. See backend/app/analytics/flow_of_funds.py for the algorithm; this page only
 * ever consumes its already-aggregated output (address/entity/category views), never raw
 * per-transaction rows, and reshapes NOTHING it fetches without also keeping a click-path
 * back to the concrete transactions behind it (see selectedFlow / the detail panel). */
@Component({
  selector: 'app-flow-of-funds',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, CustodyAccessDialogComponent, SankeyDiagramComponent],
  templateUrl: './flow-of-funds.component.html',
  styleUrl: './flow-of-funds.component.scss',
})
export class FlowOfFundsComponent implements OnInit {
  protected readonly minLevels = MIN_LEVELS;
  protected readonly maxLevelsCap = MAX_LEVELS;

  protected activeCase: CaseSummary | null = null;
  protected evidenceOptions: EvidenceEntry[] = [];
  protected selectedEvidence: string | null = null;
  /** Every address currently in the case's graph - used only for the seed input's
   * datalist/autocomplete and case-insensitive correction (see resolveAddress). This page
   * does not render that graph itself, it draws its own Sankey view instead. */
  protected caseAddresses: string[] = [];

  // --- search parameters ---
  protected seedAddresses: string[] = [];
  protected manualSeedInput = '';
  protected direction: FlowOfFundsDirection = 'forward';
  protected maxLevels = DEFAULT_LEVELS;
  protected minAmount: number | null = null;

  protected periodMode: 'all' | 'range' = 'all';
  protected startDate = '';
  protected endDate = '';

  // --- run state (custody-gated, same convention as every other case analysis) ---
  protected isRunning = false;
  protected runError: string | null = null;
  protected result: FlowOfFundsResult | null = null;
  protected isCustodyDialogOpen = false;
  protected custodyDialogError: string | null = null;

  // --- display-only controls: reshape the already-fetched result, no new request ---
  protected aggregationLevel: FlowAggregationLevel = 'address';
  protected assetFilter: string | null = null;
  protected unitMode: 'native' | 'usd' = 'native';

  protected selectedFlow: AggregatedFlow | null = null;

  constructor(
    private readonly state: AnalysisStateService,
    private readonly caseData: CaseDataApiService,
    private readonly flowApi: FlowOfFundsApiService,
    private readonly destroyRef: DestroyRef,
    public readonly settings: SettingsService,
  ) {}

  /** Tiny inline translator: picks the Serbian or English string for the active language. */
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
        this.caseAddresses = [];
        this.seedAddresses = [];
        this.manualSeedInput = '';
        this.resetSearchState();
        if (this.activeCase) {
          this.loadEvidenceOptions(this.activeCase.id);
          this.loadCaseAddresses(this.activeCase.id);
        }
      });
  }

  private loadEvidenceOptions(caseId: string): void {
    this.caseData.getCase(caseId).subscribe({
      next: (caseDetail) => (this.evidenceOptions = caseDetail.evidence),
      error: () => (this.evidenceOptions = []),
    });
  }

  private loadCaseAddresses(caseId: string): void {
    if (!this.activeCase?.evidence_count) {
      this.caseAddresses = [];
      return;
    }
    this.caseData.getCaseGraph(caseId, null).subscribe({
      next: (graph) => {
        this.caseAddresses = graph.nodes.map((node) => String(node.id)).sort();
      },
      error: () => (this.caseAddresses = []),
    });
  }

  onEvidenceSelected(storedName: string): void {
    this.selectedEvidence = storedName || null;
    this.resetSearchState();
  }

  private resetSearchState(): void {
    this.result = null;
    this.runError = null;
    this.selectedFlow = null;
    this.custodyDialogError = null;
    this.assetFilter = null;
  }

  // --- seed chip input, same shape as taint-analysis.component.ts's own seed chips ---

  private resolveAddress(address: string): string {
    const match = this.caseAddresses.find((candidate) => candidate.toLowerCase() === address.toLowerCase());
    return match ?? address;
  }

  addManualSeed(): void {
    const address = this.manualSeedInput.trim();
    this.manualSeedInput = '';
    if (!address) {
      return;
    }
    const resolved = this.resolveAddress(address);
    if (!this.seedAddresses.includes(resolved)) {
      this.seedAddresses = [...this.seedAddresses, resolved];
    }
  }

  removeSeed(address: string): void {
    this.seedAddresses = this.seedAddresses.filter((candidate) => candidate !== address);
  }

  get selectedEvidenceFileName(): string | null {
    if (!this.selectedEvidence) {
      return null;
    }
    return this.evidenceOptions.find((entry) => entry.stored_name === this.selectedEvidence)?.file_name ?? null;
  }

  // --- time period filter (two-mode version of activity-log's own period picker: 'all' or
  // 'from - to', a single day rarely matters for a multi-hop trace) ---

  setPeriodMode(mode: 'all' | 'range'): void {
    this.periodMode = mode;
    if (mode === 'all') {
      this.startDate = '';
      this.endDate = '';
    }
  }

  get isRangeInverted(): boolean {
    return this.periodMode === 'range' && !!this.startDate && !!this.endDate && this.startDate > this.endDate;
  }

  private get startTimeIso(): string | null {
    return this.periodMode === 'range' && this.startDate ? `${this.startDate}T00:00:00Z` : null;
  }

  private get endTimeIso(): string | null {
    return this.periodMode === 'range' && this.endDate ? `${this.endDate}T23:59:59Z` : null;
  }

  // --- running the trace ---

  get canRun(): boolean {
    return !!this.activeCase && this.seedAddresses.length > 0 && !this.isRunning && !this.isRangeInverted;
  }

  openCustodyDialog(): void {
    if (!this.canRun) {
      return;
    }
    this.custodyDialogError = null;
    this.isCustodyDialogOpen = true;
  }

  closeCustodyDialog(): void {
    this.isCustodyDialogOpen = false;
  }

  confirmCustodyAndRun(custody: TransactionCustodyEntry): void {
    const caseId = this.activeCase?.id;
    if (!caseId || !this.canRun) {
      return;
    }

    this.isRunning = true;
    this.runError = null;
    this.custodyDialogError = null;

    this.flowApi
      .runFlowOfFunds(
        caseId,
        {
          sourceAddresses: this.seedAddresses,
          direction: this.direction,
          maxLevels: this.maxLevels,
          minAmount: this.minAmount ?? 0,
          startTime: this.startTimeIso,
          endTime: this.endTimeIso,
        },
        this.selectedEvidence,
        custody,
      )
      .subscribe({
        next: (result) => {
          this.isRunning = false;
          this.result = result;
          this.isCustodyDialogOpen = false;
          this.selectedFlow = null;
          this.assetFilter = null;
        },
        error: (error: HttpErrorResponse) => {
          this.isRunning = false;
          const message =
            error.status === 404
              ? this.t(
                  'Jedna ili više polaznih adresa nisu pronađene u evidenciji.',
                  'One or more source addresses were not found in the evidence.',
                )
              : this.t('Praćenje toka sredstava nije uspelo.', 'Tracing the flow of funds failed.');
          this.custodyDialogError = message;
          this.runError = message;
        },
      });
  }

  // --- display-only reshaping of an already-fetched result (no network call) ---

  get availableAssets(): string[] {
    if (!this.result) {
      return [];
    }
    return Array.from(new Set(this.result.address_flows.map((flow) => flow.asset))).sort();
  }

  setAggregationLevel(level: FlowAggregationLevel): void {
    this.aggregationLevel = level;
    this.selectedFlow = null;
  }

  setAssetFilter(asset: string | null): void {
    this.assetFilter = asset;
    this.selectedFlow = null;
  }

  private get activeFlows(): AggregatedFlow[] {
    if (!this.result) {
      return [];
    }
    const source =
      this.aggregationLevel === 'entity'
        ? this.result.entity_flows
        : this.aggregationLevel === 'category'
          ? this.result.category_flows
          : this.result.address_flows;
    return this.assetFilter ? source.filter((flow) => flow.asset === this.assetFilter) : source;
  }

  get hasActiveFlows(): boolean {
    return this.activeFlows.length > 0;
  }

  /** Label a node carries at the currently selected aggregation level, mirroring the
   * backend's own fallback order (entity name, else category, else the raw address - see
   * app/analytics/flow_of_funds.py's _entity_key/_category_key). Used only to identify
   * which Sankey node ids correspond to the ORIGINAL seed addresses, so seed nodes still
   * highlight correctly even once the diagram is collapsed to entities/categories. */
  private nodeLabelAtLevel(node: FlowOfFundsNode): string {
    if (this.aggregationLevel === 'entity') {
      return node.entity_name || node.id;
    }
    if (this.aggregationLevel === 'category') {
      return node.entity_category || node.id;
    }
    return node.id;
  }

  get sankeyNodes(): SankeyDiagramNode[] {
    if (!this.result) {
      return [];
    }
    const flows = this.activeFlows;
    const seedLabels = new Set(
      this.result.nodes.filter((node) => node.type === 'seed').map((node) => this.nodeLabelAtLevel(node)),
    );
    const categoryByLabel = new Map<string, string | null>();
    for (const node of this.result.nodes) {
      categoryByLabel.set(this.nodeLabelAtLevel(node), node.entity_category);
    }

    // Same "smallest level a node was first reached at" reduction the backend computes for
    // its own address-level `nodes` list (see _node_records) - reimplemented here because
    // at entity/category granularity the node ids are collapsed labels the backend's
    // `nodes` array doesn't directly index.
    const bestLevel = new Map<string, number>();
    for (const label of seedLabels) {
      bestLevel.set(label, 0);
    }
    for (const flow of flows) {
      const sourceCandidate = flow.level - 1;
      bestLevel.set(flow.source, bestLevel.has(flow.source) ? Math.min(bestLevel.get(flow.source)!, sourceCandidate) : sourceCandidate);
      bestLevel.set(flow.target, bestLevel.has(flow.target) ? Math.min(bestLevel.get(flow.target)!, flow.level) : flow.level);
    }

    const labels = new Map<string, string>();
    for (const flow of flows) {
      labels.set(flow.source, flow.source_label);
      labels.set(flow.target, flow.target_label);
    }

    return Array.from(bestLevel.entries()).map(([id, level]) => ({
      id,
      label: labels.get(id) ?? id,
      level: Math.max(0, level),
      isSeed: seedLabels.has(id),
      entityCategory: categoryByLabel.get(id) ?? null,
    }));
  }

  get sankeyLinks(): SankeyDiagramLink[] {
    return this.activeFlows.map((flow) => ({
      source: flow.source,
      target: flow.target,
      value: flow.amount,
      asset: flow.asset,
      flow,
    }));
  }

  onFlowSelected(link: SankeyDiagramLink | null): void {
    this.selectedFlow = (link?.flow as AggregatedFlow) ?? null;
  }

  closeFlowDetail(): void {
    this.selectedFlow = null;
  }

  protected assetLabel(asset: string): string {
    return asset === 'UNKNOWN' ? this.t('Nepoznato', 'Unknown') : asset;
  }

  protected formatAmount(value: number): string {
    return value.toLocaleString('en-US', { maximumFractionDigits: 8 });
  }
}
