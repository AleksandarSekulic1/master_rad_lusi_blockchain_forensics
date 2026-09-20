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
import { CaseDataApiService } from '../../core/services/case-data.api';
import { AppLang, SettingsService } from '../../core/services/settings.service';
import { AuthService } from '../../core/services/auth.service';
import { CaseSummary, EvidenceEntry, TransactionCustodyEntry } from '../../core/models/shared.models';
import { CustodyAccessDialogComponent } from '../custody-access-dialog/custody-access-dialog.component';
import { FlowOfFundsApiService } from './flow-of-funds.api';
import {
  AggregatedFlow,
  FlowAggregationLevel,
  FlowOfFundsDirection,
  FlowOfFundsNode,
  FlowOfFundsResult,
  NodeAnnotationBadge,
  NodeAnnotationBuckets,
  SeedSuggestionResponse,
} from './flow-of-funds.models';
import { SankeyDiagramComponent, SankeyDiagramLink, SankeyDiagramNode } from './sankey-diagram/sankey-diagram.component';

/** One address involved in the currently selected flow, paired with whatever the rest of
 * the app already knows about it (see FlowOfFundsResult.node_annotations) - the detail
 * panel renders one of these per relevant address (source/target at address granularity,
 * every contributing address at entity/category granularity). */
interface FlowDetailAddress {
  address: string;
  role: 'source' | 'target';
  annotations: NodeAnnotationBuckets;
}

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
  imports: [CommonModule, FormsModule, RouterLink, CustodyAccessDialogComponent, SankeyDiagramComponent, SignaturePadComponent],
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
  /** Rule-based suggestions (see app/analytics/seed_suggestion.py) - opt-in panel next to the
   * manual seed input, same source data taint-analysis.component.ts already uses. Loaded on
   * demand, never automatically, since it scans the whole case rather than one address. */
  protected isSuggestingSeeds = false;
  protected seedSuggestions: SeedSuggestionResponse | null = null;
  protected suggestionsError: string | null = null;
  protected direction: FlowOfFundsDirection = 'forward';
  protected maxLevels = DEFAULT_LEVELS;
  protected minAmount: number | null = null;
  /** Levels/min. amount/period/Taint-Sybil live behind this toggle by default - same
   * "Napredna podešavanja" disclosure pattern as sybil-analysis.component.ts's own
   * showAdvancedFilters, so the primary form only ever shows what's needed to run a first,
   * sensible trace (address + direction). */
  protected showAdvanced = false;

  protected periodMode: 'all' | 'range' = 'all';
  protected startDate = '';
  protected endDate = '';

  // --- opt-in cross-references (both default off server-side - see
  // flow_of_funds_enrichment.enrich_flow_of_funds_nodes) ---
  protected includeTaint = false;
  protected includeSybil = false;

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

  // --- PDF izveštaj: potpis + pečat + kontrolni broj, ista struktura kao Sybil/Pathfinding
  // analiza (vidi ta dva buildXPdf-a) - samostalna kopija ovde po ustaljenoj konvenciji ove
  // aplikacije (mali PDF helperi se ne dele između stranica, vidi sybil-analysis.component
  // .ts), da se nijedna postojeća analiza ne bi ni posredno dirala. ---
  @ViewChild(SignaturePadComponent) private signaturePad?: SignaturePadComponent;
  protected isSignatureDialogOpen = false;
  protected signatureDeclarationAccepted = false;
  protected isExportingPdf = false;
  protected signatureError: string | null = null;
  /** Language the exported PDF is produced in - seeded from the app toggle when the signing
   * dialog opens, then confirmed by the analyst - own field per page convention (see
   * pathfindingPdfLang/sybilPdfLang), never shared across pages. */
  protected flowOfFundsPdfLang: AppLang = 'sr';

  constructor(
    private readonly state: AnalysisStateService,
    private readonly caseData: CaseDataApiService,
    private readonly flowApi: FlowOfFundsApiService,
    private readonly auth: AuthService,
    private readonly destroyRef: DestroyRef,
    private readonly router: Router,
    public readonly settings: SettingsService,
  ) {}

  /** PDF-string translator: SR or EN by the language chosen on the signing modal, then
   * ASCII-folded (harmless for English) since the PDF core font is Latin-1 only. */
  private lx(sr: string, en: string): string {
    return this.asciiSafe(this.flowOfFundsPdfLang === 'sr' ? sr : en);
  }

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
        this.seedSuggestions = null;
        this.suggestionsError = null;
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

  // --- seed-address suggestions (same GET /cases/{id}/seed-suggestions endpoint
  // taint-analysis.component.ts uses - rule-based, always explained with plain-language
  // reasons, never a silent auto-pick) ---

  get hasAnySuggestion(): boolean {
    const s = this.seedSuggestions;
    return !!s && (s.origin_candidates.length > 0 || s.laundering_points.length > 0);
  }

  isAlreadySeed(address: string): boolean {
    return this.seedAddresses.includes(address);
  }

  suggestSeeds(): void {
    const caseId = this.activeCase?.id;
    if (!caseId) {
      return;
    }
    if (this.seedSuggestions) {
      // toggle the already-loaded panel instead of refetching
      this.seedSuggestions = null;
      return;
    }
    this.isSuggestingSeeds = true;
    this.suggestionsError = null;
    this.flowApi.getSeedSuggestions(caseId, this.selectedEvidence).subscribe({
      next: (response) => {
        this.isSuggestingSeeds = false;
        this.seedSuggestions = response;
      },
      error: () => {
        this.isSuggestingSeeds = false;
        this.suggestionsError = this.t('Predlog adresa nije uspeo.', 'Suggesting addresses failed.');
      },
    });
  }

  addSuggestedSeed(address: string): void {
    const resolved = this.resolveAddress(address);
    if (!this.seedAddresses.includes(resolved)) {
      this.seedAddresses = [...this.seedAddresses, resolved];
    }
  }

  get originCandidatesFullyAdded(): boolean {
    const items = this.seedSuggestions?.origin_candidates ?? [];
    return items.length > 0 && items.every((item) => this.isAlreadySeed(this.resolveAddress(item.address)));
  }

  get launderingPointsFullyAdded(): boolean {
    const items = this.seedSuggestions?.laundering_points ?? [];
    return items.length > 0 && items.every((item) => this.isAlreadySeed(this.resolveAddress(item.address)));
  }

  /** Bulk-add every blacklist/OFAC candidate in one click - the only bucket defensible as a
   * seed on its own (see SeedSuggestionResponse.origin_candidates doc comment). */
  addAllOriginCandidates(): void {
    const additions = (this.seedSuggestions?.origin_candidates ?? [])
      .map((item) => this.resolveAddress(item.address))
      .filter((address) => !this.seedAddresses.includes(address));
    if (additions.length > 0) {
      this.seedAddresses = [...this.seedAddresses, ...Array.from(new Set(additions))];
    }
  }

  /** Bulk-add every heuristic laundering point in one click - still real findings, just not
   * a defensible origin on their own (see SeedSuggestionResponse.laundering_points). */
  addAllLaunderingPoints(): void {
    const additions = (this.seedSuggestions?.laundering_points ?? [])
      .map((item) => this.resolveAddress(item.address))
      .filter((address) => !this.seedAddresses.includes(address));
    if (additions.length > 0) {
      this.seedAddresses = [...this.seedAddresses, ...Array.from(new Set(additions))];
    }
  }

  dismissSuggestions(): void {
    this.seedSuggestions = null;
    this.suggestionsError = null;
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
          includeTaint: this.includeTaint,
          includeSybil: this.includeSybil,
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

  /** Direction toggle - before a first run, just local state; once a trace already exists,
   * switching it re-queries the SAME evidence "live" via a passive GET (no new custody
   * dialog, no new audit entry) - same "signed once, refine live afterward" precedent as
   * sybil-analysis.component.ts's own post-signature filter tweaks. Available both in the
   * search form and again in the results filter row, so it also works as a post-run filter
   * as requested, not just a pre-run setting. */
  setDirection(value: FlowOfFundsDirection): void {
    if (this.direction === value) {
      return;
    }
    this.direction = value;
    if (this.result) {
      this.refreshLiveWithCurrentParams();
    }
  }

  private refreshLiveWithCurrentParams(): void {
    const caseId = this.activeCase?.id;
    if (!caseId || this.seedAddresses.length === 0 || this.isRunning) {
      return;
    }
    this.isRunning = true;
    this.runError = null;
    this.flowApi
      .getFlowOfFunds(
        caseId,
        {
          sourceAddresses: this.seedAddresses,
          direction: this.direction,
          maxLevels: this.maxLevels,
          minAmount: this.minAmount ?? 0,
          startTime: this.startTimeIso,
          endTime: this.endTimeIso,
          includeTaint: this.includeTaint,
          includeSybil: this.includeSybil,
        },
        this.selectedEvidence,
      )
      .subscribe({
        next: (result) => {
          this.isRunning = false;
          this.result = result;
          this.selectedFlow = null;
          this.assetFilter = null;
        },
        error: () => {
          this.isRunning = false;
          this.runError = this.t('Osvežavanje smera nije uspelo.', 'Refreshing the direction failed.');
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

  /** True when the diagram is currently mixing more than one asset (filter = "Sve"/"All")
   * - link WIDTH is proportional to raw amount, so mixing e.g. ETH and BTC in one view
   * makes widths visually imply a direct magnitude comparison between incompatible units,
   * which is not true. Shown as an explicit warning rather than silently letting the
   * picture make a claim the numbers don't support - narrowing to one asset (the filter
   * chips above the diagram) is always the way to get comparable widths. */
  get showsMixedAssetWidths(): boolean {
    return this.assetFilter === null && this.availableAssets.length > 1;
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

  // --- Sankey view (nodes/links), MEMOIZED ------------------------------------------------
  // Angular re-evaluates template getters on every change-detection cycle - including ones
  // triggered by an event INSIDE the Sankey child itself (e.g. a zoom button click). A plain
  // getter that builds a fresh array every call would hand the child a NEW [nodes]/[links]
  // reference each time, which makes the child's own ngOnChanges fire and reset its pan/zoom
  // back to 100% on the very next tick - the zoom controls would appear to do nothing. Caching
  // the computed arrays, keyed by what they actually depend on, means the SAME reference is
  // returned (and ngOnChanges stays quiet) unless the trace result, aggregation level or
  // asset filter genuinely changed.
  private sankeyCache: {
    result: FlowOfFundsResult | null;
    aggregationLevel: FlowAggregationLevel;
    assetFilter: string | null;
    nodes: SankeyDiagramNode[];
    links: SankeyDiagramLink[];
  } | null = null;

  private get sankeyView(): { nodes: SankeyDiagramNode[]; links: SankeyDiagramLink[] } {
    const cache = this.sankeyCache;
    if (cache && cache.result === this.result && cache.aggregationLevel === this.aggregationLevel && cache.assetFilter === this.assetFilter) {
      return cache;
    }

    const flows = this.activeFlows;
    const links: SankeyDiagramLink[] = flows.map((flow) => ({
      source: flow.source,
      target: flow.target,
      value: flow.amount,
      asset: flow.asset,
      flow,
    }));

    let nodes: SankeyDiagramNode[] = [];
    if (this.result) {
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

      nodes = Array.from(bestLevel.entries()).map(([id, level]) => ({
        id,
        label: labels.get(id) ?? id,
        level: Math.max(0, level),
        isSeed: seedLabels.has(id),
        entityCategory: categoryByLabel.get(id) ?? null,
      }));
    }

    this.sankeyCache = { result: this.result, aggregationLevel: this.aggregationLevel, assetFilter: this.assetFilter, nodes, links };
    return this.sankeyCache;
  }

  get sankeyNodes(): SankeyDiagramNode[] {
    return this.sankeyView.nodes;
  }

  get sankeyLinks(): SankeyDiagramLink[] {
    return this.sankeyView.links;
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

  /** Same as assetLabel(), but follows the PDF's OWN chosen language (flowOfFundsPdfLang,
   * via lx()) instead of the app's current UI language (t()) - used everywhere inside
   * buildFlowOfFundsPdf/formatAnnotationLine, so a PDF exported in English never shows a
   * Serbian "Nepoznato" (or vice versa) just because the app happened to be in a different
   * language at export time. */
  private pdfAssetLabel(asset: string): string {
    return asset === 'UNKNOWN' ? this.lx('Nepoznato', 'Unknown') : asset;
  }

  protected formatAmount(value: number): string {
    return value.toLocaleString('en-US', { maximumFractionDigits: 8 });
  }

  /** A NodeAnnotationBadge's extra fields are typed `unknown` (different `type`s carry
   * different shapes - see flow_of_funds_enrichment.py) - these two narrow just enough for
   * the template to call .join()/toLocaleString() on them without an `any` cast. */
  protected asStringArray(value: unknown): string[] {
    return Array.isArray(value) ? value.map((item) => String(item)) : [];
  }

  protected asNumber(value: unknown): number {
    return typeof value === 'number' ? value : 0;
  }

  protected asString(value: unknown): string {
    return typeof value === 'string' ? value : '';
  }

  // --- cross-referenced findings for the currently selected flow (Graph/DEX/Token
  // Approval/Taint/Sybil - see backend/app/analytics/flow_of_funds_enrichment.py). Always
  // looked up by real address, never by an entity/category label - at address granularity
  // that's just [source, target]; at entity/category granularity it's every underlying
  // address the collapsed flow folds together (contributing_addresses), so a reader can
  // still see exactly what's known about each real wallet behind the aggregate. ---

  private static readonly EMPTY_ANNOTATIONS: NodeAnnotationBuckets = { facts: [], aggregated: [], heuristics: [] };

  private annotationsFor(address: string): NodeAnnotationBuckets {
    return this.result?.node_annotations?.[address] ?? FlowOfFundsComponent.EMPTY_ANNOTATIONS;
  }

  get selectedFlowAddresses(): FlowDetailAddress[] {
    const flow = this.selectedFlow;
    if (!flow) {
      return [];
    }
    const sourceAddresses = flow.contributing_addresses?.source ?? [flow.source];
    const targetAddresses = flow.contributing_addresses?.target ?? [flow.target];

    return [
      ...sourceAddresses.map((address): FlowDetailAddress => ({ address, role: 'source', annotations: this.annotationsFor(address) })),
      ...targetAddresses.map((address): FlowDetailAddress => ({ address, role: 'target', annotations: this.annotationsFor(address) })),
    ];
  }

  /** True when at least one address behind the current flow has ANY cross-referenced
   * finding at all - lets the template skip an empty "Povezani nalazi" section instead of
   * showing three empty headings. */
  get selectedFlowHasAnnotations(): boolean {
    return this.selectedFlowAddresses.some(
      (entry) => entry.annotations.facts.length + entry.annotations.aggregated.length + entry.annotations.heuristics.length > 0,
    );
  }

  // --- Chain of Evidence deep link: every transaction already carries the SAME tx_id its
  // custody entry (if any) is keyed by - see custody-log.component.ts's own ?tx= deep link,
  // reused as-is, nothing new added to that page. ---

  protected custodyLinkParams(txId: string): Record<string, string> {
    return this.activeCase ? { tx: txId, caseId: this.activeCase.id } : { tx: txId };
  }

  // --- handoff to Pathfinding/Taint Analysis, reusing the SAME one-shot mechanism
  // token-approval.component.ts already uses (AnalysisStateService.setPendingPathfindingSeed/
  // setPendingTaintSeeds) - neither of those pages is touched, they already know how to pick
  // this up on load. ---

  protected openFlowInPathfinding(): void {
    const flow = this.selectedFlow;
    if (!flow || this.aggregationLevel !== 'address') {
      return;
    }
    this.state.setPendingPathfindingSeed({ from: flow.source, to: flow.target });
    this.router.navigateByUrl('/pathfinding');
  }

  protected sendFlowAddressesToTaint(): void {
    const addresses = this.selectedFlowAddresses.map((entry) => entry.address);
    if (addresses.length === 0) {
      return;
    }
    this.state.setPendingTaintSeeds(Array.from(new Set(addresses)));
    this.router.navigateByUrl('/taint');
  }

  // ==========================================================================================
  // PDF izveštaj: potpis, pečat, kontrolni broj, provera verodostojnosti - ista struktura kao
  // Sybil/Pathfinding analiza (TAINT-ANALIZA.md §6.3), namerno pisana kao samostalna kopija
  // ovde (a ne izvučena u deljeni modul), po ustaljenoj konvenciji ove aplikacije. Koristi iste,
  // već postojeće, generičke backend rute - POST /api/v1/reports/register i
  // GET /api/v1/reports/verify (app/services/report_registry.py) - "Provera izveštaja" stranica
  // radi bez izmena, report_type='flow_of_funds' je samo još jedna slobodna vrednost.
  // ==========================================================================================

  get canExportPdf(): boolean {
    return !!this.result && !!this.activeCase && this.result.flow_count > 0;
  }

  openSignatureDialog(): void {
    if (!this.canExportPdf) {
      return;
    }
    this.isSignatureDialogOpen = true;
    this.signatureDeclarationAccepted = false;
    this.signatureError = null;
    this.flowOfFundsPdfLang = this.settings.lang();
    setTimeout(() => this.signaturePad?.clear());
  }

  closeSignatureDialog(): void {
    this.isSignatureDialogOpen = false;
  }

  get canSubmitSignature(): boolean {
    return (this.signaturePad?.hasStrokes ?? false) && this.signatureDeclarationAccepted && !this.isExportingPdf;
  }

  private signatureDeclaration(): string {
    return this.flowOfFundsPdfLang === 'sr'
      ? 'Potvrđujem da sam izradio ovaj izveštaj u okviru navedenog predmeta i da su u njemu prikazani rezultati '
        + 'onakvi kakve je aplikacija izračunala (agregacijom i, gde je naznačeno, heuristikom) nad navedenom evidencijom.'
      : 'I confirm that I produced this report within the stated case and that the results shown in it are those '
        + 'the application computed (by aggregation and, where noted, heuristically) over the stated evidence.';
  }

  /** The exact data the verification hash is computed over - kept to the figures a reader
   * could dispute (which flows, which addresses, which tx hashes), sorted so field order
   * never affects the hash. Descriptive prose is deliberately excluded, same discipline as
   * sybil-analysis/pathfinding.component.ts's own reportContentPayload. */
  private reportContentPayload(): Record<string, unknown> {
    const result = this.result!;
    // Total facts/aggregated/heuristics counts across every touched address - part of the
    // hash so a reader can tell whether the cross-referenced findings section was altered
    // after export, without hashing the full descriptive text (same "numbers, not prose"
    // discipline as sybil-analysis/pathfinding.component.ts's own payload).
    const annotationCounts = Object.values(result.node_annotations ?? {}).reduce(
      (totals, buckets) => ({
        facts: totals.facts + buckets.facts.length,
        aggregated: totals.aggregated + buckets.aggregated.length,
        heuristics: totals.heuristics + buckets.heuristics.length,
      }),
      { facts: 0, aggregated: 0, heuristics: 0 },
    );
    return {
      case_id: this.activeCase!.id,
      evidence: this.selectedEvidence ?? 'combined',
      source_addresses: [...result.source_addresses].sort(),
      direction: result.direction,
      max_levels: result.max_levels,
      levels_reached: result.levels_reached,
      start_time: result.start_time,
      end_time: result.end_time,
      truncated: result.truncated,
      flow_count: result.flow_count,
      // Scope of the optional cross-references included in THIS export - part of the hash
      // so "was taint/sybil included" is itself a disputable, verifiable fact.
      include_taint: this.includeTaint,
      include_sybil: this.includeSybil,
      node_annotation_counts: annotationCounts,
      address_flows: [...result.address_flows]
        .map((flow) => ({
          level: flow.level,
          source: flow.source,
          target: flow.target,
          asset: flow.asset,
          amount: flow.amount,
          transaction_count: flow.transaction_count,
          multi_output_same_tx: flow.multi_output_same_tx,
          tx_hashes: [...flow.tx_hashes].sort(),
        }))
        .sort((a, b) => a.level - b.level || a.source.localeCompare(b.source) || a.target.localeCompare(b.target) || a.asset.localeCompare(b.asset)),
      nodes: [...result.nodes]
        .map((node) => ({ id: node.id, level: node.level, type: node.type, entity_name: node.entity_name, entity_category: node.entity_category }))
        .sort((a, b) => a.id.localeCompare(b.id)),
    };
  }

  async confirmSignatureAndExport(): Promise<void> {
    if (!this.canSubmitSignature || !this.result || !this.activeCase) {
      return;
    }

    this.isExportingPdf = true;
    this.signatureError = null;
    try {
      const signatureImage = this.signaturePad!.getDataUrl();
      const declaration = this.signatureDeclaration();
      const result = this.result;

      // Registered BEFORE the document is built: the verification code has to be printed
      // inside the very report it identifies.
      const registration = await firstValueFrom(
        this.caseData.registerReport({
          case_id: this.activeCase.id,
          case_name: this.activeCase.name ?? '',
          declaration,
          content: this.reportContentPayload(),
          summary: {
            flow_count: result.flow_count,
            levels_reached: result.levels_reached,
            direction: result.direction,
            truncated: result.truncated,
            assets: this.availableAssets.join(', '),
          },
          report_type: 'flow_of_funds',
        }),
      );

      const catEmblem = await this.loadPdfImage('assets/cat_pdf.png').catch(() => null);
      const sealImage = await this.loadPdfImage('assets/seal.png').catch(() => null);
      this.buildFlowOfFundsPdf({ signatureImage, declaration, registration }, { catEmblem, sealImage });
      this.isSignatureDialogOpen = false;
    } catch {
      this.signatureError = this.t('Neuspešno generisanje PDF izveštaja.', 'Failed to generate the PDF report.');
    } finally {
      this.isExportingPdf = false;
    }
  }

  private static readonly PDF_NAVY: [number, number, number] = [13, 24, 40];
  private static readonly PDF_ACCENT: [number, number, number] = [43, 130, 191];
  private static readonly PDF_TEXT_GRAY: [number, number, number] = [100, 112, 128];
  private static readonly PDF_TEXT_DARK: [number, number, number] = [24, 28, 36];
  private static readonly PDF_WHITE: [number, number, number] = [255, 255, 255];
  private static readonly PDF_AMBER: [number, number, number] = [217, 119, 6];
  private static readonly PDF_GREEN: [number, number, number] = [22, 163, 74];

  private static readonly ASCII_MAP: Record<string, string> = {
    č: 'c', ć: 'c', š: 's', ž: 'z', đ: 'dj',
    Č: 'C', Ć: 'C', Š: 'S', Ž: 'Z', Đ: 'Dj',
  };

  /** Same reasoning as every other page's own asciiSafe: jsPDF's core fonts don't reliably
   * cover č/ć/š/ž/đ, so report text is transliterated rather than embedding a Unicode TTF
   * just for this document. Addresses/numbers/tx hashes are unaffected. */
  private asciiSafe(value: string | null | undefined): string {
    return (value ?? '').replace(/[čćšžđČĆŠŽĐ]/g, (match) => FlowOfFundsComponent.ASCII_MAP[match] ?? match);
  }

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
    const size = await FlowOfFundsComponent.loadImageSize(dataUrl);
    return { dataUrl, width: size.width, height: size.height };
  }

  /** Truncates an address for tight PDF table columns - NOT used for the on-screen Sankey
   * (that shows/omits full addresses on its own terms), only for this document's tables. */
  private static truncateAddress(value: string): string {
    if (value.length <= 16) {
      return value;
    }
    return `${value.slice(0, 8)}...${value.slice(-6)}`;
  }

  /** One plain-text line for a single cross-referenced badge, address-prefixed - shared by
   * the "Ključni nalazi" (facts+aggregated) and "Heuristički zaključci" (heuristics) PDF
   * sections, since a badge's `type` never appears in more than one bucket (see
   * flow_of_funds_enrichment.py's module docstring) so one switch safely covers all of them. */
  private formatAnnotationLine(address: string, badge: NodeAnnotationBadge, L: (sr: string, en: string) => string): string {
    const short = FlowOfFundsComponent.truncateAddress(address);
    switch (badge.type) {
      case 'known_entity':
        return L(`${short}: poznat entitet ${badge['name']} (${badge['category']})`, `${short}: known entity ${badge['name']} (${badge['category']})`);
      case 'blacklist':
        return L(
          `${short}: na crnoj listi (${this.asStringArray(badge['sources']).join(', ')}) - ${badge['label']}`,
          `${short}: blacklisted (${this.asStringArray(badge['sources']).join(', ')}) - ${badge['label']}`,
        );
      case 'flow_totals': {
        const asset = this.pdfAssetLabel(this.asString(badge['asset']));
        return L(
          `${short}: (${asset}) ukupno primljeno ${this.formatAmount(this.asNumber(badge['total_received']))}, poslato ${this.formatAmount(this.asNumber(badge['total_sent']))}`,
          `${short}: (${asset}) total received ${this.formatAmount(this.asNumber(badge['total_received']))}, sent ${this.formatAmount(this.asNumber(badge['total_sent']))}`,
        );
      }
      case 'dex_swap_detected':
        return L(`${short}: potvrđen DEX swap (${badge['dex_name'] ?? '?'}) - isti tx hash na oba kraka`, `${short}: confirmed DEX swap (${badge['dex_name'] ?? '?'}) - shared tx hash on both legs`);
      case 'token_approval':
        return L(`${short}: token approval (${badge['role']}, ${badge['status']})`, `${short}: token approval (${badge['role']}, ${badge['status']})`);
      case 'risk_score':
        return L(`${short}: risk scoring ${badge['score']}/100 (${badge['band']})`, `${short}: risk scoring ${badge['score']}/100 (${badge['band']})`);
      case 'peel_chain':
        return L(`${short}: peel chain uloga - ${badge['role']}`, `${short}: peel chain role - ${badge['role']}`);
      case 'chain_hop':
        return L(`${short}: chain hopping - ${badge['service_type']}`, `${short}: chain hopping - ${badge['service_type']}`);
      case 'wallet_cluster':
        return L(`${short}: wallet clustering - ${badge['cluster_id']}`, `${short}: wallet clustering - ${badge['cluster_id']}`);
      case 'dex_swap_potential':
        return L(`${short}: moguć DEX swap (samo vremenska podudarnost)`, `${short}: potential DEX swap (timing match only)`);
      case 'token_approval_risk':
        return L(`${short}: rizičan token approval - ${badge['risk_level']}`, `${short}: risky token approval - ${badge['risk_level']}`);
      case 'sybil_cluster':
        return L(`${short}: Sybil klaster ${badge['cluster_id']} (${badge['risk_level']})`, `${short}: Sybil cluster ${badge['cluster_id']} (${badge['risk_level']})`);
      case 'taint':
        return L(`${short}: taint procenat ${badge['percentage']}%`, `${short}: taint percentage ${badge['percentage']}%`);
      default:
        return `${short}: ${String(badge.type)}`;
    }
  }

  private buildFlowOfFundsPdf(
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
    const result = this.result!;
    const caseSummary = this.activeCase!;
    const NAVY = FlowOfFundsComponent.PDF_NAVY;
    const ACCENT = FlowOfFundsComponent.PDF_ACCENT;
    const TEXT_GRAY = FlowOfFundsComponent.PDF_TEXT_GRAY;
    const TEXT_DARK = FlowOfFundsComponent.PDF_TEXT_DARK;
    const WHITE = FlowOfFundsComponent.PDF_WHITE;

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
    doc.text(L('Lusi v1.0 - Izvestaj Flow of Funds / Layering analize', 'Lusi v1.0 - Flow of Funds / Layering analysis report'), titleX, 11);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(10);
    doc.text(`${L('Slucaj', 'Case')}: ${this.asciiSafe(caseSummary.name)}`, titleX, 19);
    doc.setTextColor(...TEXT_DARK);

    const kv = (label: string, value: string): void => {
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(9.5);
      doc.setTextColor(...TEXT_GRAY);
      doc.text(label, marginX, y);
      const valueX = marginX + Math.max(42, doc.getTextWidth(label) + 6);
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(10);
      doc.setTextColor(...TEXT_DARK);
      const lines: string[] = doc.splitTextToSize(value || 'n/a', usableWidth - (valueX - marginX));
      doc.text(lines, valueX, y);
      y += Math.max(6, lines.length * 5);
    };

    const periodText =
      result.start_time || result.end_time
        ? `${result.start_time ?? '...'} -> ${result.end_time ?? '...'}`
        : L('ceo period evidencije', 'entire evidence period');
    const directionText =
      result.direction === 'forward'
        ? L('unapred (kuda su sredstva otisla)', 'forward (where funds went)')
        : L('unazad (odakle su sredstva stigla)', 'backward (where funds came from)');

    kv('CASE ID', caseSummary.id);
    kv(L('IZVEZAO', 'EXPORTED BY'), this.asciiSafe(this.auth.currentUser?.username ?? caseSummary.analyst));
    kv(L('EVIDENCIJA', 'EVIDENCE'), this.selectedEvidence ? this.asciiSafe(this.selectedEvidence) : L('Sve transakcije (kombinovano)', 'All transactions (combined)'));
    kv(L('POLAZNE ADRESE', 'SOURCE ADDRESSES'), result.source_addresses.join(', '));
    kv(L('SMER', 'DIRECTION'), directionText);
    kv(L('VREMENSKI PERIOD', 'TIME PERIOD'), periodText);
    kv(L('NIVOI', 'LEVELS'), L(`dostignuto ${result.levels_reached} od trazenih ${result.max_levels}`, `reached ${result.levels_reached} of ${result.max_levels} requested`));
    kv(L('IMOVINA / BLOCKCHAIN', 'ASSET / BLOCKCHAIN'), this.availableAssets.map((asset) => this.pdfAssetLabel(asset)).join(', ') || 'n/a');
    kv(L('GENERISANO', 'GENERATED AT'), new Date().toLocaleString(this.flowOfFundsPdfLang === 'sr' ? 'sr-RS' : 'en-GB'));
    y += 2;

    // Short version of the disclaimer, placed BEFORE any result - never omitted: this is an
    // AGGREGATED, partly heuristic view (asset inference, entity/category collapsing, and -
    // when enabled - Taint/Sybil cross-references), not raw on-chain fact by itself.
    const methodologyNoteLines = doc.splitTextToSize(
      L(
        'Ovaj izvestaj prikazuje AGREGIRANE tokove sredstava (nikad pojedinacne transakcije bez konteksta) preko vise '
          + 'nivoa (hop-ova) od polaznih adresa - deo klasifikacija (pripadnost entitetu/kategoriji, procena imovine, '
          + 'taint/sybil kad su ukljuceni) je heuristicka. Detaljno objasnjenje i ogranicenja nalaze se na kraju izvestaja.',
        'This report shows AGGREGATED flows of funds (never individual transactions without context) across several '
          + 'hops from the source addresses - some of the classification (entity/category membership, asset inference, '
          + 'taint/sybil when enabled) is heuristic. A detailed explanation and limitations are at the end of the report.',
      ),
      usableWidth - 8,
    );
    const noteBoxHeight = methodologyNoteLines.length * 4.2 + 7;
    doc.setFillColor(253, 250, 240);
    doc.setDrawColor(...FlowOfFundsComponent.PDF_AMBER);
    doc.setLineWidth(0.4);
    doc.roundedRect(marginX, y - 4, usableWidth, noteBoxHeight, 2, 2, 'FD');
    doc.setFont('helvetica', 'italic');
    doc.setFontSize(8.5);
    doc.setTextColor(...TEXT_DARK);
    doc.text(methodologyNoteLines, marginX + 4, y + 1);
    y += noteBoxHeight + 3;
    doc.setFont('helvetica', 'normal');

    const sectionTitle = (title: string): void => {
      y += 3;
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

    const ensureSpace = (needed: number): void => {
      if (y + needed > pageHeight - 18) {
        doc.addPage();
        y = 16;
      }
    };

    const drawSummaryCards = (cards: Array<[string, string | number, [number, number, number]]>): void => {
      const gap = 3;
      const colWidth = (usableWidth - gap * (cards.length - 1)) / cards.length;
      const y0 = y;
      let x = marginX;
      for (const [label, value, color] of cards) {
        doc.setFillColor(...color);
        doc.rect(x, y0, colWidth, 15, 'F');
        doc.setTextColor(...WHITE);
        doc.setFont('helvetica', 'bold');
        doc.setFontSize(13);
        doc.text(String(value), x + colWidth / 2, y0 + 6.5, { align: 'center' });
        doc.setFont('helvetica', 'normal');
        doc.setFontSize(7);
        doc.text(doc.splitTextToSize(label, colWidth - 4), x + colWidth / 2, y0 + 11, { align: 'center' });
        x += colWidth + gap;
      }
      y = y0 + 15 + 6;
      doc.setTextColor(...TEXT_DARK);
    };

    // --- Rezime analize -------------------------------------------------------------------
    sectionTitle(L('Rezime analize', 'Analysis summary'));
    drawSummaryCards([
      [L('Agregiranih tokova', 'Aggregated flows'), result.flow_count, ACCENT],
      [L('Dostignutih nivoa', 'Levels reached'), result.levels_reached, ACCENT],
      [L('Relevantnih adresa', 'Relevant addresses'), result.nodes.length, ACCENT],
      [L('Skraceno', 'Truncated'), result.truncated ? L('DA', 'YES') : L('ne', 'no'), result.truncated ? FlowOfFundsComponent.PDF_AMBER : FlowOfFundsComponent.PDF_GREEN],
    ]);

    // --- Ukupan analizirani volumen, po imovini (nikad sabran preko razlicitih valuta) ----
    const volumeByAsset = new Map<string, { amount: number; count: number }>();
    for (const flow of result.address_flows) {
      const entry = volumeByAsset.get(flow.asset) ?? { amount: 0, count: 0 };
      entry.amount += flow.amount;
      entry.count += flow.transaction_count;
      volumeByAsset.set(flow.asset, entry);
    }
    sectionTitle(L('Ukupan analizirani volumen', 'Total analyzed volume'));
    doc.setFont('helvetica', 'italic');
    doc.setFontSize(8);
    doc.setTextColor(...TEXT_GRAY);
    doc.text(L('Zbir svih agregiranih tokova po imovini - nije neto vrednost, iste sredstva mogu proci kroz vise nivoa.', 'Sum of all aggregated flows per asset - not a net value, the same funds can pass through several levels.'), marginX, y);
    y += 5;
    doc.setFont('helvetica', 'normal');
    doc.setTextColor(...TEXT_DARK);
    autoTable(doc, {
      startY: y,
      margin: { left: marginX, right: marginX },
      head: [[L('Imovina', 'Asset'), L('Ukupan iznos', 'Total amount'), L('Broj transakcija', 'Transaction count')]],
      body: Array.from(volumeByAsset.entries()).map(([asset, entry]) => [this.pdfAssetLabel(asset), this.formatAmount(entry.amount), String(entry.count)]),
      styles: { fontSize: 8.5, cellPadding: 1.8, font: 'helvetica', textColor: TEXT_DARK },
      headStyles: { fillColor: NAVY, textColor: WHITE, font: 'helvetica', fontStyle: 'bold' },
      alternateRowStyles: { fillColor: [240, 245, 250] },
    });
    y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 8;

    // --- Glavni tokovi sredstava (top po iznosu) -------------------------------------------
    ensureSpace(30);
    const TOP_FLOWS_LIMIT = 20;
    const topFlows = [...result.address_flows].sort((a, b) => b.amount - a.amount).slice(0, TOP_FLOWS_LIMIT);
    sectionTitle(L('Glavni tokovi sredstava', 'Main flows of funds'));
    autoTable(doc, {
      startY: y,
      margin: { left: marginX, right: marginX },
      head: [[L('Nivo', 'Lvl'), L('Izvor', 'Source'), L('Odrediste', 'Target'), L('Imovina', 'Asset'), L('Iznos', 'Amount'), L('Tx', 'Tx'), 'UTXO']],
      body: topFlows.map((flow) => [
        String(flow.level),
        FlowOfFundsComponent.truncateAddress(flow.source),
        FlowOfFundsComponent.truncateAddress(flow.target),
        this.pdfAssetLabel(flow.asset),
        this.formatAmount(flow.amount),
        String(flow.transaction_count),
        flow.multi_output_same_tx ? L('da', 'yes') : '',
      ]),
      styles: { fontSize: 7.5, cellPadding: 1.5, font: 'courier', textColor: TEXT_DARK },
      headStyles: { fillColor: NAVY, textColor: WHITE, font: 'helvetica', fontStyle: 'bold' },
      alternateRowStyles: { fillColor: [240, 245, 250] },
      columnStyles: { 0: { font: 'helvetica' }, 3: { font: 'helvetica' }, 5: { font: 'helvetica' }, 6: { font: 'helvetica' } },
    });
    y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 4;
    if (result.address_flows.length > TOP_FLOWS_LIMIT) {
      doc.setFont('helvetica', 'italic');
      doc.setFontSize(7.5);
      doc.setTextColor(...TEXT_GRAY);
      doc.text(L(`+ ${result.address_flows.length - TOP_FLOWS_LIMIT} dodatnih tokova nije prikazano (puna lista u aplikaciji).`, `+ ${result.address_flows.length - TOP_FLOWS_LIMIT} more flows not shown (full list in the app).`), marginX, y);
      y += 6;
    }

    // --- Layering nivoi ---------------------------------------------------------------------
    ensureSpace(30);
    sectionTitle(L('Layering nivoi', 'Layering levels'));
    const levelStats = new Map<number, { flowCount: number; addresses: Set<string> }>();
    for (const flow of result.address_flows) {
      const stat = levelStats.get(flow.level) ?? { flowCount: 0, addresses: new Set<string>() };
      stat.flowCount += 1;
      stat.addresses.add(flow.target);
      levelStats.set(flow.level, stat);
    }
    autoTable(doc, {
      startY: y,
      margin: { left: marginX, right: marginX },
      head: [[L('Nivo', 'Level'), L('Broj tokova', 'Flow count'), L('Novih adresa na ovom nivou', 'New addresses at this level')]],
      body: Array.from(levelStats.entries())
        .sort((a, b) => a[0] - b[0])
        .map(([level, stat]) => [String(level), String(stat.flowCount), String(stat.addresses.size)]),
      styles: { fontSize: 8.5, cellPadding: 1.8, font: 'helvetica', textColor: TEXT_DARK },
      headStyles: { fillColor: NAVY, textColor: WHITE, font: 'helvetica', fontStyle: 'bold' },
      alternateRowStyles: { fillColor: [240, 245, 250] },
    });
    y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 8;

    // --- Relevantne adrese i entiteti --------------------------------------------------------
    ensureSpace(30);
    sectionTitle(L('Relevantne adrese i entiteti', 'Relevant addresses and entities'));
    autoTable(doc, {
      startY: y,
      margin: { left: marginX, right: marginX },
      head: [[L('Adresa', 'Address'), L('Nivo', 'Lvl'), L('Tip', 'Type'), L('Entitet', 'Entity'), L('Kategorija', 'Category')]],
      body: [...result.nodes]
        .sort((a, b) => a.level - b.level || a.id.localeCompare(b.id))
        .map((node) => [
          FlowOfFundsComponent.truncateAddress(node.id),
          String(node.level),
          node.type === 'seed' ? L('polazna', 'seed') : L('adresa', 'address'),
          node.entity_name ? this.asciiSafe(node.entity_name) : '-',
          node.entity_category ?? '-',
        ]),
      styles: { fontSize: 7.5, cellPadding: 1.4, font: 'courier', textColor: TEXT_DARK },
      headStyles: { fillColor: NAVY, textColor: WHITE, font: 'helvetica', fontStyle: 'bold' },
      alternateRowStyles: { fillColor: [240, 245, 250] },
      columnStyles: { 1: { font: 'helvetica' }, 2: { font: 'helvetica' }, 3: { font: 'helvetica' }, 4: { font: 'helvetica' } },
    });
    y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 8;

    // --- Kljucni forenzicki nalazi: cinjenice + agregati (jasno ODVOJENO od heuristike ispod,
    // ista disciplina kao Sybil izvestaj - "Kljucni dokazi" pa tek onda heuristicki zakljucak) --
    const factLines: string[] = [];
    const heuristicLines: string[] = [];
    for (const node of result.nodes) {
      const annotations = result.node_annotations[node.id];
      if (!annotations) {
        continue;
      }
      for (const badge of [...annotations.facts, ...annotations.aggregated]) {
        factLines.push(this.formatAnnotationLine(node.id, badge, L));
      }
      for (const badge of annotations.heuristics) {
        heuristicLines.push(this.formatAnnotationLine(node.id, badge, L));
      }
    }

    const FINDINGS_LIMIT = 30;
    const bullet = (text: string, color: [number, number, number]): void => {
      const lines: string[] = doc.splitTextToSize(this.asciiSafe(text), usableWidth - 6);
      ensureSpace(lines.length * 4.2 + 2);
      doc.setFillColor(...color);
      doc.circle(marginX + 1.2, y - 1.2, 0.7, 'F');
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(8.5);
      doc.setTextColor(...TEXT_DARK);
      doc.text(lines, marginX + 5, y);
      y += lines.length * 4.2 + 1.2;
    };

    ensureSpace(24);
    sectionTitle(L('Kljucni forenzicki nalazi (cinjenice i agregati)', 'Key forensic findings (facts and aggregates)'));
    if (factLines.length === 0) {
      doc.setFont('helvetica', 'italic');
      doc.setFontSize(8.5);
      doc.setTextColor(...TEXT_GRAY);
      doc.text(L('Nema poznatih entiteta, crne liste, DEX swap ili token approval nalaza za adrese u ovom toku.', 'No known entities, blacklist, DEX swap or token approval findings for the addresses in this trace.'), marginX, y);
      y += 6;
    } else {
      for (const line of factLines.slice(0, FINDINGS_LIMIT)) {
        bullet(line, FlowOfFundsComponent.PDF_GREEN);
      }
      if (factLines.length > FINDINGS_LIMIT) {
        doc.setFont('helvetica', 'italic');
        doc.setFontSize(7.5);
        doc.setTextColor(...TEXT_GRAY);
        doc.text(L(`+ ${factLines.length - FINDINGS_LIMIT} dodatnih nalaza nije prikazano.`, `+ ${factLines.length - FINDINGS_LIMIT} more findings not shown.`), marginX, y);
        y += 6;
      }
    }

    // --- Heuristicki zakljucci - EKSPLICITNO odvojeni naslov, boja i napomena da mogu biti
    // pogresni, ista disciplina kao Sybil izvestaj (heuristicki_zakljucak sekcija). ----------
    ensureSpace(24);
    sectionTitle(L('Heuristicki zakljucci (MOGU BITI POGRESNI)', 'Heuristic conclusions (MAY BE WRONG)'));
    doc.setFont('helvetica', 'italic');
    doc.setFontSize(8);
    doc.setTextColor(...FlowOfFundsComponent.PDF_AMBER);
    doc.text(L('Sledece stavke su zakljucci modela/heuristike (risk scoring, peel chain, chain hopping, wallet clustering, DEX swap "Potential", taint, sybil) - NIKADA blockchain cinjenica.', 'The following items are model/heuristic conclusions (risk scoring, peel chain, chain hopping, wallet clustering, "Potential" DEX swap, taint, sybil) - NEVER a blockchain fact.'), marginX, y, { maxWidth: usableWidth });
    y += 9;

    // Scope note - Taint/Sybil are opt-in per run (see flow_of_funds_enrichment.py), so
    // their ABSENCE below could otherwise look like "checked, found nothing" when it may
    // simply mean "not checked in this run" - the same incomplete-data-as-fact mistake this
    // whole report is built to avoid. Always state the scope explicitly, regardless of
    // whether any heuristic findings exist at all.
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(8);
    doc.setTextColor(...TEXT_GRAY);
    doc.text(
      L(
        `Taint analiza u ovom pokretanju: ${this.includeTaint ? 'UKLJUCENA' : 'NIJE ukljucena'}. Sybil analiza: ${this.includeSybil ? 'UKLJUCENA' : 'NIJE ukljucena'}.`,
        `Taint analysis in this run: ${this.includeTaint ? 'INCLUDED' : 'NOT included'}. Sybil analysis: ${this.includeSybil ? 'INCLUDED' : 'NOT included'}.`,
      ),
      marginX,
      y,
    );
    y += 6;
    doc.setTextColor(...TEXT_DARK);
    if (heuristicLines.length === 0) {
      doc.setFont('helvetica', 'italic');
      doc.setFontSize(8.5);
      doc.setTextColor(...TEXT_GRAY);
      doc.text(L('Nema heuristickih nalaza (risk/peel/hop/cluster i, ako su ukljuceni, taint/sybil) za adrese u ovom toku.', 'No heuristic findings (risk/peel/hop/cluster and, if included, taint/sybil) for the addresses in this trace.'), marginX, y);
      y += 6;
    } else {
      for (const line of heuristicLines.slice(0, FINDINGS_LIMIT)) {
        bullet(line, FlowOfFundsComponent.PDF_AMBER);
      }
      if (heuristicLines.length > FINDINGS_LIMIT) {
        doc.setFont('helvetica', 'italic');
        doc.setFontSize(7.5);
        doc.setTextColor(...TEXT_GRAY);
        doc.text(L(`+ ${heuristicLines.length - FINDINGS_LIMIT} dodatnih zakljucaka nije prikazano.`, `+ ${heuristicLines.length - FINDINGS_LIMIT} more conclusions not shown.`), marginX, y);
        y += 6;
      }
    }

    // --- Povezane transakcije i tx hash-evi -------------------------------------------------
    ensureSpace(30);
    sectionTitle(L('Povezane transakcije i tx hash-evi', 'Related transactions and tx hashes'));
    const TX_LIMIT = 40;
    const allTransactions = topFlows.flatMap((flow) =>
      flow.transactions.map((tx) => ({
        level: flow.level,
        route: `${FlowOfFundsComponent.truncateAddress(flow.source)} -> ${FlowOfFundsComponent.truncateAddress(flow.target)}`,
        amount: tx.amount,
        timestamp: tx.timestamp,
        txHash: tx.tx_hash,
      })),
    );
    autoTable(doc, {
      startY: y,
      margin: { left: marginX, right: marginX },
      head: [[L('Nivo', 'Lvl'), L('Ruta', 'Route'), L('Iznos', 'Amount'), L('Vreme', 'Time'), 'Tx hash']],
      body: allTransactions.slice(0, TX_LIMIT).map((tx) => [
        String(tx.level),
        tx.route,
        this.formatAmount(tx.amount),
        tx.timestamp ? new Date(tx.timestamp).toLocaleString(this.flowOfFundsPdfLang === 'sr' ? 'sr-RS' : 'en-GB') : 'n/a',
        this.asciiSafe(tx.txHash ?? 'n/a'),
      ]),
      styles: { fontSize: 7, cellPadding: 1.3, font: 'courier', textColor: TEXT_DARK },
      headStyles: { fillColor: NAVY, textColor: WHITE, font: 'helvetica', fontStyle: 'bold' },
      alternateRowStyles: { fillColor: [240, 245, 250] },
      columnStyles: { 0: { font: 'helvetica' }, 2: { font: 'helvetica' }, 3: { font: 'helvetica' } },
    });
    y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 4;
    if (allTransactions.length > TX_LIMIT) {
      doc.setFont('helvetica', 'italic');
      doc.setFontSize(7.5);
      doc.setTextColor(...TEXT_GRAY);
      doc.text(L(`+ ${allTransactions.length - TX_LIMIT} dodatnih transakcija nije prikazano (puna lista u aplikaciji / Lancu dokaza).`, `+ ${allTransactions.length - TX_LIMIT} more transactions not shown (full list in the app / Chain of custody).`), marginX, y);
      y += 6;
    }

    // --- Lanac dokaza (Chain of Evidence) - referenca, ne duplirana tabela - vidi Sybil
    // izvestaj za isti obrazac. ---------------------------------------------------------------
    ensureSpace(24);
    sectionTitle(L('Lanac dokaza (Chain of Evidence)', 'Chain of evidence'));
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9.5);
    doc.setTextColor(...TEXT_DARK);
    const custodyLines = doc.splitTextToSize(
      L(
        'Svaka transakcija u ovom izvestaju nosi isti tx_id koji njen zapis dobija u lancu dokaza (custody_log), ako je '
          + 'ova analiza pokrenuta sa potpisanim pristupom - videti stranicu "Lanac dokaza" i tx_id iz aplikacije za '
          + 'detalje po transakciji.',
        'Every transaction in this report carries the same tx_id its own chain-of-custody entry is keyed by, when this '
          + 'analysis was run with a signed access - see the "Chain of custody" page and the tx_id from the app for the '
          + 'per-transaction detail.',
      ),
      usableWidth,
    );
    doc.text(custodyLines, marginX, y);
    y += custodyLines.length * 4.6 + 4;

    // --- Metodologija i ogranicenja appendix -------------------------------------------------
    const paragraph = (text: string, options?: { bold?: boolean; size?: number; gap?: number }): void => {
      const size = options?.size ?? 9;
      doc.setFont('helvetica', options?.bold ? 'bold' : 'normal');
      doc.setFontSize(size);
      doc.setTextColor(...TEXT_DARK);
      const lines: string[] = doc.splitTextToSize(text, usableWidth);
      const lineHeight = size * 0.48;
      ensureSpace(lines.length * lineHeight);
      doc.text(lines, marginX, y);
      y += lines.length * lineHeight + (options?.gap ?? 3);
    };
    const appendixBullet = (text: string): void => {
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(9);
      doc.setTextColor(...TEXT_DARK);
      const lines: string[] = doc.splitTextToSize(text, usableWidth - 6);
      ensureSpace(lines.length * 4.4);
      doc.setFillColor(...ACCENT);
      doc.circle(marginX + 1.4, y - 1.2, 0.7, 'F');
      doc.text(lines, marginX + 6, y);
      y += lines.length * 4.4 + 1.6;
    };

    y += 6;
    ensureSpace(50);
    sectionTitle(L('Metodologija i ogranicenja', 'Methodology and limitations'));

    paragraph(L('Agregacija', 'Aggregation'), { bold: true, size: 10, gap: 2 });
    paragraph(
      L(
        'Tok sredstava se prati BFS pretragom preko stvarnih transakcija, nivo po nivo (hop po hop) od polaznih adresa. '
          + 'Svaka grana izmedju dva cvora, na istom nivou i u istoj imovini, agregira se u JEDAN tok - nikad pojedinacne '
          + 'transakcije bez konteksta - ali svaki agregat i dalje nosi puni spisak transakcija i tx hash-eva iza sebe.',
        'Funds are traced by a BFS search over real transactions, level by level (hop by hop) from the source addresses. '
          + 'Every edge between two nodes, at the same level and in the same asset, is aggregated into ONE flow - never '
          + 'individual transactions without context - but every aggregate still carries the full list of transactions '
          + 'and tx hashes behind it.',
      ),
      { gap: 5 },
    );

    paragraph(L('Sta je heuristika ovde', 'What is heuristic here'), { bold: true, size: 10, gap: 2 });
    appendixBullet(L('Imovina (ETH/BTC) se pretpostavlja iz oblika adrese kad evidencija ne deklarise valutu eksplicitno.', 'The asset (ETH/BTC) is inferred from address shape when the evidence does not explicitly declare a currency.'));
    appendixBullet(L('Spajanje u entitet/kategoriju koristi lokalni registar poznatih adresa - odsustvo iz registra ne znaci da adresa nije poznata, samo da nije u ovoj kuriranoj listi.', 'Entity/category collapsing uses the local known-address registry - absence from the registry does not mean the address is unknown, only that it is not in this curated list.'));
    appendixBullet(L('Risk scoring, peel chain, chain hopping i wallet clustering su isti plugin-ovi kao na Graf stranici - modeli, ne dokaz.', 'Risk scoring, peel chain, chain hopping and wallet clustering are the same plugins as on the Graph page - models, not proof.'));
    appendixBullet(L('Taint i Sybil nalazi (kad su ukljuceni) su zasebne heuristike sa sopstvenim ogranicenjima - videti njihove stranice za detalje.', 'Taint and Sybil findings (when enabled) are separate heuristics with their own limitations - see their own pages for detail.'));
    appendixBullet(L('Adrese se poklapaju tacnim poklapanjem (case-sensitive), ista konvencija kao Pathfinding/DEX Swap/Sybil.', 'Addresses are matched exactly (case-sensitive), the same convention as Pathfinding/DEX Swap/Sybil.'));

    // --- Potpis i overa -------------------------------------------------------------
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
      `${L('OVERENO', 'CERTIFIED')} ${new Date(signing.registration.registered_at).toLocaleDateString(this.flowOfFundsPdfLang === 'sr' ? 'sr-RS' : 'en-GB')}`,
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
        'Verodostojnost se proverava u aplikaciji Lusi, unosom gornjeg kontrolnog broja. Ako se otisak sadrzaja poklapa '
          + 'sa zabelezenim, podaci u izvestaju su isti kao u trenutku izvoza. Ako se ne poklapa, izvestaj je izmenjen '
          + 'posle izvoza.',
        'Authenticity is verified in the Lusi application by entering the verification code above. If the content hash '
          + 'matches the recorded one, the data in the report is the same as at export time. If it does not match, the '
          + 'report was altered after export.',
      ),
      usableWidth,
    );
    doc.text(verifyLines, marginX, y);
    y += verifyLines.length * 4.6 + 4;

    doc.setFont('helvetica', 'italic');
    doc.setFontSize(8);
    doc.setTextColor(...TEXT_GRAY);
    const limitLines = doc.splitTextToSize(
      L(
        'Ogranicenje: potpis iznad je izjava analiticara, a ne kriptografski dokaz - on ostaje netaknut i ako neko '
          + 'izmeni dokument. Izmena se otkriva iskljucivo poredjenjem otiska sadrzaja.',
        'Limitation: the signature above is the analyst\'s declaration, not a cryptographic proof - it stays intact '
          + 'even if someone edits the document. An alteration is detected solely by comparing the content hash.',
      ),
      usableWidth,
    );
    doc.text(limitLines, marginX, y);

    const pageCount = doc.getNumberOfPages();
    for (let page = 1; page <= pageCount; page++) {
      doc.setPage(page);
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(8);
      doc.setTextColor(...TEXT_GRAY);
      doc.text(`Lusi v1.0 forensic export | ${L('Strana', 'Page')} ${page}/${pageCount}`, pageWidth / 2, pageHeight - 8, { align: 'center' });
    }

    doc.save(`${caseSummary.id}_flow_of_funds_report.pdf`);
  }
}
