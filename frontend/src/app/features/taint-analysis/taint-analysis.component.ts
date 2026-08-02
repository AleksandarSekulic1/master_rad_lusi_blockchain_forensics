import { CommonModule } from '@angular/common';
import { Component, DestroyRef, ElementRef, HostListener, OnDestroy, OnInit, ViewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { map } from 'rxjs/operators';

import cytoscape, { Core, EdgeSingular, ElementDefinition, NodeSingular } from 'cytoscape';
import { jsPDF } from 'jspdf';
import autoTable from 'jspdf-autotable';

import { ensureCytoscapeExtensionsRegistered } from '../../core/cytoscape-setup';
import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { ApiService } from '../../core/services/api.service';
import { AuthService } from '../../core/services/auth.service';
import {
  AddressEnrichment,
  AddressType,
  CaseSummary,
  EvidenceEntry,
  GraphNodeData,
  KnownEntityCategory,
  NodeLinkGraphResponse,
  TaintAnalysisResult,
  TaintedHop,
  TaintNodeResult,
  TaintTimelineEntry,
  TaintTimelineEvent,
} from '../../models/blockchain-forensics.models';

/** One individual transaction on a clicked edge, joined from the raw per-transaction data
 * cytoscape already carries (amount/timestamp/metadata) with whatever taint contribution
 * that exact transaction had (null when it never carried any tainted value at all). */
interface EdgeTransactionDetail {
  amount: number;
  timestamp: string;
  metadata: string | null;
  taintedAmount: number | null;
  taintPctAtHop: number | null;
  taintBySource: Record<string, number> | null;
}

/** One entry in a node's complete transaction ledger, with the % right before and after
 * it - the "why is my percentage what it is" explanation, including the clean (or merely
 * less-tainted-than-average) inflows that pure tainted_hops leaves invisible. */
interface NodeEventLogEntry {
  rank: number;
  direction: 'in' | 'out';
  counterparty: string;
  amount: number;
  taintedAmount: number;
  timestamp: string;
  pctBefore: number;
  pctAfter: number;
  delta: number;
}

@Component({
  selector: 'app-taint-analysis',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './taint-analysis.component.html',
  styleUrl: './taint-analysis.component.scss',
})
export class TaintAnalysisComponent implements OnInit, OnDestroy {
  @ViewChild('taintCanvas', { static: true })
  protected taintCanvas!: ElementRef<HTMLDivElement>;

  protected activeCase: CaseSummary | null = null;
  protected evidenceOptions: EvidenceEntry[] = [];
  protected selectedEvidence: string | null = null;
  protected isLoadingGraph = false;
  protected graphError: string | null = null;
  protected graph: NodeLinkGraphResponse | null = null;

  protected seedAddresses: string[] = [];
  protected manualSeedInput = '';
  protected isRunningTaint = false;
  protected isSuggestingSeeds = false;
  protected taintError: string | null = null;
  protected hasRunTaint = false;
  protected taintResult: TaintAnalysisResult | null = null;
  protected selectedNode: GraphNodeData | null = null;
  protected showAllTopTainted = false;
  protected showAllEventLog = false;
  protected addressEnrichment: AddressEnrichment | null = null;
  protected isEnrichingAddress = false;

  protected timelineEnabled = false;
  protected timelinePosition = 1;
  protected timelineMaxRank = 0;
  protected isTimelinePlaying = false;
  protected timelineSpeed = 1;
  protected readonly timelineSpeedOptions = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
  protected timelineSubtitlesEnabled = true;
  protected timelineFollowEnabled = true;
  protected hideNonTaintedNodes = false;
  /** "Hide nodes at or below this %" - defaults to 0 (the old fixed behavior: only
   * exactly-untainted nodes). Raising it matters on graphs with many seeds, where almost
   * nothing sits at exactly 0% but plenty of nodes carry only a negligible sliver. */
  protected taintHideThreshold = 0;
  protected isExportingPdf = false;
  /** Which seed(s) currently count towards displayed %/color/labels - starts as "all seeds"
   * right after a run (the familiar combined view). Deselecting one doesn't remove it as a
   * seed, it just excludes its share from what's drawn, so you can isolate one source's own
   * spread when several independent seeds happen to converge on the same addresses. */
  protected activeSeeds = new Set<string>();

  protected selectedEdgeDetails: {
    source: string;
    target: string;
    totalAmount: number;
    transactions: EdgeTransactionDetail[];
  } | null = null;

  private cy: Core | null = null;
  private lastClickedHopKey: string | null = null;
  private lastHopClickWentToTarget = false;
  private selectedEdgeKey: string | null = null;
  private timelinePlayTimer: ReturnType<typeof setInterval> | null = null;
  private static readonly TAINT_LOW_COLOR = '#1c2333';
  private static readonly TAINT_MID_COLOR = '#f59e0b';
  private static readonly TAINT_HIGH_COLOR = '#ff4d4d';
  private static readonly TAINT_MID_THRESHOLD = 50;
  private static readonly TOP_TAINTED_PREVIEW_LIMIT = 15;
  private static readonly EVENT_LOG_PREVIEW_LIMIT = 10;

  constructor(
    private readonly state: AnalysisStateService,
    private readonly api: ApiService,
    private readonly auth: AuthService,
    private readonly destroyRef: DestroyRef,
  ) {
    ensureCytoscapeExtensionsRegistered();
  }

  ngOnInit(): void {
    this.state.selectedCase$
      .pipe(
        map((caseSummary) => caseSummary?.id ?? null),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(() => {
        this.activeCase = this.state.selectedCaseSnapshot;
        this.selectedEvidence = null;
        this.evidenceOptions = [];
        this.resetAnalysisState();
        if (this.activeCase) {
          this.loadEvidenceOptions(this.activeCase.id);
          this.loadCaseGraph();
        }
      });
  }

  ngOnDestroy(): void {
    this.cy?.destroy();
    this.cy = null;
    this.stopTimelinePlay();
  }

  /** The seed chip list (or the seed-panel disappearing once analysis runs) changes the
   * height of content ABOVE the canvas, which shifts the canvas down/up on the page -
   * cytoscape caches its container's on-screen position and only re-measures it on an
   * explicit resize(), so without this, clicks on nodes drift further out of alignment
   * with the cursor as more seeds get added (or the layout otherwise changes). */
  @HostListener('window:resize')
  onWindowResize(): void {
    this.cy?.resize();
  }

  private scheduleCyResize(): void {
    requestAnimationFrame(() => this.cy?.resize());
  }

  loadEvidenceOptions(caseId: string): void {
    this.api.getCase(caseId).subscribe({
      next: (caseDetail) => {
        this.evidenceOptions = caseDetail.evidence;
      },
      error: () => {
        this.evidenceOptions = [];
      },
    });
  }

  onEvidenceSelected(storedName: string): void {
    this.selectedEvidence = storedName || null;
    this.loadCaseGraph();
  }

  /** Loads the plain (un-analyzed) case graph so an investigator can pick seed nodes by
   * clicking on them - running the full analytics pipeline isn't needed until "Pokreni
   * taint analizu" is pressed. */
  loadCaseGraph(): void {
    const caseId = this.activeCase?.id;
    if (!caseId) {
      return;
    }

    if (!this.activeCase?.evidence_count) {
      this.graph = null;
      this.resetAnalysisState();
      this.graphError = null;
      return;
    }

    this.isLoadingGraph = true;
    this.graphError = null;

    this.api.getCaseGraph(caseId, this.selectedEvidence).subscribe({
      next: (graph) => {
        this.graph = graph;
        this.resetAnalysisState();
        this.isLoadingGraph = false;
        this.renderGraph();
      },
      error: () => {
        this.isLoadingGraph = false;
        this.graphError = 'Neuspešno učitavanje grafa za izabrani slučaj.';
      },
    });
  }

  /** Clears any previous taint run (seeds, results, selection) without touching which
   * graph is loaded - used both when switching case/evidence and when starting a fresh
   * taint pass ("Nova analiza") on the same graph. */
  private resetAnalysisState(): void {
    this.seedAddresses = [];
    this.manualSeedInput = '';
    this.hasRunTaint = false;
    this.taintResult = null;
    this.taintError = null;
    this.selectedNode = null;
    this.addressEnrichment = null;
    this.showAllTopTainted = false;
    this.lastClickedHopKey = null;
    this.selectedEdgeKey = null;
    this.selectedEdgeDetails = null;
    this.stopTimelinePlay();
    this.timelineEnabled = false;
    this.timelinePosition = 1;
    this.timelineMaxRank = 0;
    this.activeSeeds.clear();
  }

  startNewAnalysis(): void {
    this.resetAnalysisState();
    this.renderGraph();
  }

  /** Re-opens the seed panel on top of the just-computed results, without clearing
   * anything - the current seed chips and the graph's existing colors/markers stay
   * exactly as they are, so you can add or remove a few addresses and re-run instead of
   * starting over from an empty seed list every time. */
  editSeeds(): void {
    this.stopTimelinePlay();
    this.hasRunTaint = false;
  }

  get isSeedSelectionMode(): boolean {
    return !this.hasRunTaint;
  }

  toggleSeed(nodeId: string): void {
    if (this.hasRunTaint) {
      return;
    }
    if (this.seedAddresses.includes(nodeId)) {
      this.removeSeedAddress(nodeId);
    } else {
      this.addSeedAddress(nodeId);
    }
  }

  removeSeed(address: string): void {
    this.removeSeedAddress(address);
  }

  addManualSeed(): void {
    const address = this.manualSeedInput.trim();
    this.manualSeedInput = '';
    if (!address) {
      return;
    }
    this.addSeedAddress(this.resolveNodeId(address));
  }

  /** Mirror of addManualSeed - lets you paste/type an address to remove it directly,
   * instead of having to visually find its chip among dozens of others to click its ×. */
  removeManualSeed(): void {
    const address = this.manualSeedInput.trim();
    this.manualSeedInput = '';
    if (!address) {
      return;
    }
    this.removeSeedAddress(this.resolveNodeId(address));
  }

  /** Ethereum addresses are case-insensitive in practice (checksummed mixed-case is just
   * a typo-check convention, not a different address) - typing one in a different case
   * than the graph's own would otherwise silently fail to match the actual node, so this
   * looks up the graph's real, correctly-cased id before it's ever added/removed/sent to
   * the backend. Falls back to the raw input unchanged if no matching node exists (still
   * a harmless no-op seed, same as before). */
  private resolveNodeId(address: string): string {
    const match = this.graph?.nodes.find((node) => String(node.id).toLowerCase() === address.toLowerCase());
    return match ? String(match.id) : address;
  }

  /** Runs the existing heuristics (blacklist, risk score, anomaly, peel-chain, chain-hop
   * - the same plugins already used to color the main Graf page) purely to find good
   * starting candidates, and adds whatever they flag to the current seed selection. This
   * doesn't run or change the taint analysis itself - it's just a shortcut so you don't
   * have to manually click through a few-hundred-node graph looking for the suspicious
   * ones; anything suggested can still be removed same as a manually picked seed. */
  suggestSeeds(): void {
    const caseId = this.activeCase?.id;
    if (!caseId || !this.graph || this.isSuggestingSeeds) {
      return;
    }

    this.isSuggestingSeeds = true;
    this.taintError = null;

    this.api.runCaseAnalytics(caseId, this.selectedEvidence).subscribe({
      next: (response) => {
        this.isSuggestingSeeds = false;
        const suggested = response.nodes.filter(
          (node) =>
            Boolean(node.blacklist_flag) ||
            Boolean(node.peel_chain_flag) ||
            Boolean(node.chain_hop_flag) ||
            Boolean(node.anomaly_flag) ||
            Number(node.risk_score ?? 0) >= 70,
        );
        for (const node of suggested) {
          this.addSeedAddress(String(node.id));
        }
      },
      error: () => {
        this.isSuggestingSeeds = false;
        this.taintError = 'Neuspešno predlaganje čvorova.';
      },
    });
  }

  private addSeedAddress(address: string): void {
    if (this.seedAddresses.includes(address)) {
      return;
    }
    this.seedAddresses.push(address);
    this.cy?.getElementById(address).addClass('taint-seed');
    this.scheduleCyResize();
  }

  private removeSeedAddress(address: string): void {
    const index = this.seedAddresses.indexOf(address);
    if (index < 0) {
      return;
    }
    this.seedAddresses.splice(index, 1);
    this.cy?.getElementById(address).removeClass('taint-seed');
    this.scheduleCyResize();
  }

  runTaintAnalysis(): void {
    const caseId = this.activeCase?.id;
    if (!caseId || !this.graph) {
      return;
    }

    this.isRunningTaint = true;
    this.taintError = null;

    this.api.runCaseAnalytics(caseId, this.selectedEvidence, this.seedAddresses).subscribe({
      next: (response) => {
        this.graph = response;
        this.taintResult = (response.analytics?.['taint_analysis'] as TaintAnalysisResult | undefined) ?? null;
        // Otherwise a node selected in an earlier run (e.g. before "Izmeni izvore") would
        // carry over and show up immediately in the inspector on the new run, with no
        // click having happened yet in this run at all.
        this.selectedNode = null;
        this.addressEnrichment = null;
        // The backend may have auto-seeded extra addresses from the blacklist that we
        // never explicitly added (e.g. an empty-seed "crna lista" run) - without this,
        // seedAddresses would still be empty while those nodes show a gold seed ring on
        // canvas, so clicking one to "remove" it would silently ADD it instead (it
        // wasn't tracked as present), looking like the click did nothing.
        this.seedAddresses = [...(this.taintResult?.seed_addresses ?? [])];
        this.activeSeeds = new Set(this.seedAddresses);
        this.hasRunTaint = true;
        this.isRunningTaint = false;
        this.timelineMaxRank = this.taintResult?.timeline_max_rank ?? 0;
        // Starts fully revealed at the final state (matches what you'd see with the
        // timeline off) - scrubbing backwards from there to watch it unfold is the
        // natural direction, rather than starting blank and having to press play first.
        this.timelinePosition = this.timelineMaxRank || 1;
        this.timelineEnabled = false;
        this.renderGraph();
      },
      error: () => {
        this.isRunningTaint = false;
        this.taintError = 'Neuspešno pokretanje taint analize.';
      },
    });
  }

  private get nonZeroTaintedNodes(): TaintNodeResult[] {
    return (this.taintResult?.results ?? []).filter((item) => item.taint_percentage > 0);
  }

  /** Nodes that received tainted funds but never sent anything onward in this evidence -
   * the natural candidates for "where did the money leave the traceable network" (a real
   * cash-out, or simply outside this case's evidence window). Seeds are excluded even if
   * they also never send anything, since they're the origin of the trail, not its end. */
  get cashOutCandidates(): TaintNodeResult[] {
    if (!this.graph) {
      return [];
    }
    const outDegree = new Map<string, number>();
    for (const link of this.graph.links) {
      const source = String(link.source);
      outDegree.set(source, (outDegree.get(source) ?? 0) + 1);
    }
    return this.nonZeroTaintedNodes
      .filter((item) => !item.is_taint_seed && (outDegree.get(item.address) ?? 0) === 0)
      .sort((a, b) => b.taint_percentage - a.taint_percentage);
  }

  get topTaintedNodes(): TaintNodeResult[] {
    const all = this.nonZeroTaintedNodes;
    return this.showAllTopTainted ? all : all.slice(0, TaintAnalysisComponent.TOP_TAINTED_PREVIEW_LIMIT);
  }

  get hiddenTopTaintedCount(): number {
    return Math.max(0, this.nonZeroTaintedNodes.length - TaintAnalysisComponent.TOP_TAINTED_PREVIEW_LIMIT);
  }

  toggleShowAllTopTainted(): void {
    this.showAllTopTainted = !this.showAllTopTainted;
  }

  /** The node's real, final taint % - always the complete result, regardless of where
   * the timeline scrubber happens to be. Used whenever the timeline isn't actively
   * driving the display (see selectedNodeTaintPercentage below). */
  get selectedNodeFinalTaintPercentage(): number {
    return Number(this.selectedNode?.taint_percentage ?? 0);
  }

  /** While the timeline is OFF, the inspector always shows the complete, final picture -
   * full percentage, full hop history - same as before this feature existed. Only while
   * it's actively turned ON does either one reflect "as of this scrub position" instead,
   * since that's the one case where showing partial/in-progress state is the actual
   * point (watching a value change), not a regression. */
  get selectedNodeTaintPercentage(): number {
    if (!this.selectedNode) {
      return 0;
    }
    if (!this.timelineEnabled) {
      return this.selectedNodeFinalTaintPercentage;
    }
    return this.getNodeTaintAtRank(String(this.selectedNode.id), this.timelinePosition);
  }

  get selectedNodeHops(): TaintedHop[] {
    if (!this.selectedNode || !this.taintResult) {
      return [];
    }
    const id = String(this.selectedNode.id);
    const hops = this.taintResult.tainted_hops.filter((hop) => hop.source === id || hop.target === id);
    if (!this.timelineEnabled) {
      return hops;
    }
    return hops.filter((hop) => hop.rank <= this.timelinePosition);
  }

  /** Every transaction that ever touched the selected node's balance, tainted or not, each
   * paired with the % right before and right after it - the full "why is my percentage
   * what it is" audit trail. tainted_hops only records transfers that carried SOME tainted
   * value, so a clean inflow that diluted the balance (grew it without growing the tainted
   * share) would otherwise never show up anywhere in the inspector at all. Respects the
   * same "full vs as-of-now" rule as the hops list above. */
  get selectedNodeEventLog(): NodeEventLogEntry[] {
    if (!this.selectedNode || !this.taintResult) {
      return [];
    }
    const id = String(this.selectedNode.id);
    const series = this.taintResult.node_taint_series?.[id] ?? [];
    const visible = this.timelineEnabled ? series.filter((entry) => entry.rank <= this.timelinePosition) : series;

    let previousPct = 0;
    return visible.map((entry) => {
      const row: NodeEventLogEntry = {
        rank: entry.rank,
        direction: entry.direction,
        counterparty: entry.counterparty,
        amount: entry.amount,
        taintedAmount: entry.tainted_amount,
        timestamp: entry.timestamp,
        pctBefore: previousPct,
        pctAfter: entry.taint_percentage,
        delta: Math.round((entry.taint_percentage - previousPct) * 100) / 100,
      };
      previousPct = entry.taint_percentage;
      return row;
    });
  }

  get selectedNodeEventLogPreview(): NodeEventLogEntry[] {
    const all = this.selectedNodeEventLog;
    return this.showAllEventLog ? all : all.slice(0, TaintAnalysisComponent.EVENT_LOG_PREVIEW_LIMIT);
  }

  get hiddenEventLogCount(): number {
    return Math.max(0, this.selectedNodeEventLog.length - TaintAnalysisComponent.EVENT_LOG_PREVIEW_LIMIT);
  }

  toggleShowAllEventLog(): void {
    this.showAllEventLog = !this.showAllEventLog;
  }

  /** Full (uncapped) per-seed breakdown of the selected node's own taint % - "" for a
   * single-seed run, where the aggregate % already tells the whole story. Always the
   * final result, same as the ranked lists elsewhere in this panel. */
  get selectedNodeSourceBreakdown(): Array<{ address: string; pct: number }> {
    const bySource = this.selectedNode?.taint_by_source;
    if (!bySource) {
      return [];
    }
    return Object.entries(bySource)
      .map(([address, pct]) => ({ address, pct }))
      .sort((a, b) => b.pct - a.pct);
  }

  /** Same idea as selectedNodeSourceBreakdown but for one specific hop - "" when only one
   * seed's money made up that transfer. */
  hopSourceBreakdown(hop: TaintedHop): Array<{ address: string; pct: number }> {
    return Object.entries(hop.taint_by_source ?? {})
      .map(([address, pct]) => ({ address, pct }))
      .sort((a, b) => b.pct - a.pct);
  }

  edgeTransactionSourceBreakdown(tx: EdgeTransactionDetail): Array<{ address: string; pct: number }> {
    return Object.entries(tx.taintBySource ?? {})
      .map(([address, pct]) => ({ address, pct }))
      .sort((a, b) => b.pct - a.pct);
  }

  /** Joins the edge's own raw transaction list (every transfer between this pair, tainted
   * or not - cytoscape already carries it verbatim from the graph payload) against
   * tainted_hops to attach taint data to whichever of those transactions actually carried
   * some. Matched by (timestamp, amount) since that's the only thing both records share -
   * tainted_hops has no transaction id of its own to join on directly. */
  private buildEdgeDetails(
    edge: EdgeSingular,
  ): { source: string; target: string; totalAmount: number; transactions: EdgeTransactionDetail[] } {
    const source = String(edge.data('source'));
    const target = String(edge.data('target'));
    const rawTransactions = (edge.data('transactions') as Array<Record<string, unknown>> | undefined) ?? [];
    const hops = (this.taintResult?.tainted_hops ?? []).filter((hop) => hop.source === source && hop.target === target);

    const transactions: EdgeTransactionDetail[] = rawTransactions.map((tx) => {
      const amount = Number(tx['amount'] ?? 0);
      const timestamp = String(tx['timestamp'] ?? '');
      const metadataValue = tx['metadata'];
      const matchingHop = hops.find((hop) => hop.timestamp === timestamp && Math.abs(hop.amount - amount) < 1e-9);
      return {
        amount,
        timestamp,
        metadata: metadataValue == null || metadataValue === '' ? null : String(metadataValue),
        taintedAmount: matchingHop?.tainted_amount ?? null,
        taintPctAtHop: matchingHop?.taint_pct_at_hop ?? null,
        taintBySource: matchingHop?.taint_by_source ?? null,
      };
    });

    transactions.sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
    // Same aggregate the arrow label is derived from - reused directly rather than
    // re-summed here, so this total can never drift from what's already shown on canvas.
    const totalAmount = Number(edge.data('totalAmount') ?? 0);
    return { source, target, totalAmount, transactions };
  }

  /** True once every known seed is switched on - the normal, unfiltered "combined" view.
   * Used to skip the filtering math entirely in the common case and fall back to the
   * backend's own aggregate fields, rather than re-deriving a sum that should equal them
   * anyway but could drift by a rounding hair since each per-seed share is independently
   * rounded to 2 decimals server-side. */
  get isAllSeedsActive(): boolean {
    return this.activeSeeds.size >= (this.taintResult?.seed_addresses.length ?? 0);
  }

  toggleSeedFilter(seed: string): void {
    if (this.activeSeeds.has(seed)) {
      this.activeSeeds.delete(seed);
    } else {
      this.activeSeeds.add(seed);
    }
    this.applyTaintTimeline();
  }

  showAllSeedSources(): void {
    this.activeSeeds = new Set(this.taintResult?.seed_addresses ?? []);
    this.applyTaintTimeline();
  }

  /** Restricts a per-seed breakdown down to only the currently active seeds - the one
   * piece of math this whole feature boils down to, reused for node %, edge %, and path
   * tracing alike so "isolate this source" means the same thing everywhere on the page. */
  private filteredBreakdown(bySource: Record<string, number> | undefined): Record<string, number> {
    if (!bySource) {
      return {};
    }
    if (this.isAllSeedsActive) {
      return bySource;
    }
    return Object.fromEntries(Object.entries(bySource).filter(([seed]) => this.activeSeeds.has(seed)));
  }

  private filteredSum(bySource: Record<string, number> | undefined): number {
    return Object.values(this.filteredBreakdown(bySource)).reduce((sum, pct) => sum + pct, 0);
  }

  /** The node's displayed % once the active-seed filter is applied - the real aggregate
   * field when every seed is active (avoids rounding drift, see isAllSeedsActive), or the
   * summed share of just the selected seed(s) otherwise. */
  private nodeFilteredPct(fullPct: number, bySource: Record<string, number> | undefined): number {
    return this.isAllSeedsActive ? fullPct : Math.round(this.filteredSum(bySource) * 100) / 100;
  }

  /** First click on a hop jumps to who RECEIVED the funds; clicking the exact same hop
   * again jumps back to who SENT them, then back to the recipient, and so on - lets you
   * step back and forth along one hop without losing your place. Clicking a different
   * hop always starts fresh at its recipient. */
  toggleHopNode(hop: TaintedHop): void {
    const key = `${hop.source}__${hop.target}__${hop.timestamp}`;
    const goToTarget = this.lastClickedHopKey === key ? !this.lastHopClickWentToTarget : true;
    this.lastClickedHopKey = key;
    this.lastHopClickWentToTarget = goToTarget;
    this.selectNodeFromList(goToTarget ? hop.target : hop.source);
  }

  /** Single choke point for "this node is now being inspected" - used by every selection
   * entry point (canvas tap, ranked-list click, hop click) so path highlighting and
   * on-chain enrichment always stay in sync with whatever's currently selected, without
   * duplicating the same three calls at every call site. */
  private selectNode(node: GraphNodeData): void {
    this.selectedNode = node;
    this.showAllEventLog = false;
    this.applyPathHighlight(String(node.id));
    this.loadAddressEnrichment();
  }

  /** Clears the current node selection (inspector panel + path highlight + enrichment) -
   * the counterpart to selectNode, reached by tapping the already-selected node again or
   * tapping empty canvas. */
  private deselectNode(): void {
    this.selectedNode = null;
    this.addressEnrichment = null;
    this.showAllEventLog = false;
    this.applyPathHighlight(null);
  }

  /** Real on-chain identity/provenance for the selected address (ENS name, known entity,
   * balance, and - most relevant for "whose money is this really" - its own funding
   * source: whoever sent it its very first transaction ever, on-chain, independent of
   * this case's own evidence). A seed node's taint % has no "parent" inside the taint
   * model by definition, so this is the only way to see where ITS money actually came
   * from in real life. */
  loadAddressEnrichment(): void {
    this.addressEnrichment = null;
    const address = this.selectedNode?.address ?? this.selectedNode?.id;
    if (!address) {
      return;
    }

    this.isEnrichingAddress = true;
    this.api.enrichAddress(String(address)).subscribe({
      next: (result) => {
        this.addressEnrichment = result;
        this.isEnrichingAddress = false;
      },
      error: () => {
        this.isEnrichingAddress = false;
      },
    });
  }

  get addressTypeLabel(): string {
    return this.typeLabel(this.addressEnrichment?.address_type);
  }

  get fundingSourceTypeLabel(): string {
    return this.typeLabel(this.addressEnrichment?.funding_source_type);
  }

  private typeLabel(type: AddressType | null | undefined): string {
    switch (type) {
      case 'contract':
        return 'Pametni ugovor';
      case 'eoa':
        return 'Obična adresa (EOA)';
      default:
        return 'Nepoznato';
    }
  }

  entityCategoryLabel(category: KnownEntityCategory): string {
    switch (category) {
      case 'exchange':
        return 'berza';
      case 'mixer':
        return 'mikser za prikrivanje sredstava';
      case 'sanctioned':
        return 'OFAC sankcionisano';
      default:
        return category;
    }
  }

  /** Below this, a funding transfer looks like "just enough for gas" rather than a real
   * transfer of value - a classic pattern for activating a fresh mule/burner wallet before
   * routing the real illicit funds through it. */
  private static readonly DUST_FUNDING_THRESHOLD_ETH = 0.02;

  get isDustFunding(): boolean {
    const amount = this.addressEnrichment?.funding_amount_eth;
    return amount != null && amount > 0 && amount < TaintAnalysisComponent.DUST_FUNDING_THRESHOLD_ETH;
  }

  /** Is the funding source itself a blacklisted address in this graph? A direct hit means
   * the selected node is one hop away from an entity already flagged. */
  get fundingSourceBlacklistMatch(): GraphNodeData | null {
    const fundingAddress = this.addressEnrichment?.funding_source;
    const nodes = this.graph?.nodes;
    if (!fundingAddress || !nodes) {
      return null;
    }

    const normalized = fundingAddress.toLowerCase();
    return (
      nodes.find(
        (candidate) => String(candidate.address ?? candidate.id ?? '').toLowerCase() === normalized && candidate.blacklist_flag,
      ) ?? null
    );
  }

  /** Clicking a ranked address in the sidebar both opens its inspector details (same as
   * clicking it on canvas) and pans/zooms the graph to it - on a few-hundred-node graph,
   * finding a specific ranked node by eye in the hairball isn't realistic otherwise. */
  selectNodeFromList(address: string): void {
    const node = this.graph?.nodes.find((candidate) => String(candidate.id) === address);
    if (!node) {
      return;
    }
    this.selectNode(node);

    const element = this.cy?.getElementById(address);
    if (!element || element.empty()) {
      return;
    }
    this.cy?.elements().unselect();
    element.select();
    this.cy?.animate({ fit: { eles: element, padding: 200 } }, { duration: 300 });
  }

  /** Traces the actual tainted-money path touching a node: backward through
   * tainted_hops to find where its dirty funds came from, and forward to find
   * everywhere they eventually went - not just its immediate neighbors, the whole
   * connected lineage. Respects the same "full vs as-of-now" rule as the inspector
   * panel: the complete path when the timeline is off, only hops up to the current
   * scrub position while it's on. */
  private getTaintedPathElements(nodeId: string, maxRank: number | null): { nodeIds: Set<string>; edgeKeys: Set<string> } {
    const allHops = this.taintResult?.tainted_hops ?? [];
    // Seed filter only applies to the full/final view (maxRank === null) - see the
    // scoping note on applyTaintTimeline.
    const applySeedFilter = maxRank == null && !this.isAllSeedsActive;
    const hops = allHops.filter(
      (hop) => (maxRank == null || hop.rank <= maxRank) && (!applySeedFilter || this.filteredSum(hop.taint_by_source) > 1e-9),
    );

    const outgoing = new Map<string, TaintedHop[]>();
    const incoming = new Map<string, TaintedHop[]>();
    for (const hop of hops) {
      if (!outgoing.has(hop.source)) {
        outgoing.set(hop.source, []);
      }
      outgoing.get(hop.source)!.push(hop);
      if (!incoming.has(hop.target)) {
        incoming.set(hop.target, []);
      }
      incoming.get(hop.target)!.push(hop);
    }

    const nodeIds = new Set<string>([nodeId]);
    const edgeKeys = new Set<string>();

    const forwardQueue = [nodeId];
    while (forwardQueue.length > 0) {
      const current = forwardQueue.pop()!;
      for (const hop of outgoing.get(current) ?? []) {
        edgeKeys.add(`${hop.source}__${hop.target}`);
        if (!nodeIds.has(hop.target)) {
          nodeIds.add(hop.target);
          forwardQueue.push(hop.target);
        }
      }
    }

    const backwardQueue = [nodeId];
    while (backwardQueue.length > 0) {
      const current = backwardQueue.pop()!;
      for (const hop of incoming.get(current) ?? []) {
        edgeKeys.add(`${hop.source}__${hop.target}`);
        if (!nodeIds.has(hop.source)) {
          nodeIds.add(hop.source);
          backwardQueue.push(hop.source);
        }
      }
    }

    return { nodeIds, edgeKeys };
  }

  /** Lights up the tainted path touching nodeId and fades everything else - clears back
   * to a normal, fully-lit view when nodeId is null (nothing selected) or when the node
   * has no tainted hops at all (yet), rather than dimming the whole graph for nothing. */
  private applyPathHighlight(nodeId: string | null): void {
    if (!this.cy) {
      return;
    }

    this.cy.elements().removeClass('path-highlighted path-dimmed');
    if (!nodeId) {
      return;
    }

    const maxRank = this.timelineEnabled ? this.timelinePosition : null;
    const { nodeIds, edgeKeys } = this.getTaintedPathElements(nodeId, maxRank);
    if (edgeKeys.size === 0) {
      return;
    }

    this.cy.nodes().forEach((node) => {
      node.addClass(nodeIds.has(node.id()) ? 'path-highlighted' : 'path-dimmed');
    });
    this.cy.edges().forEach((edge) => {
      const key = `${edge.data('source')}__${edge.data('target')}`;
      edge.addClass(edgeKeys.has(key) ? 'path-highlighted' : 'path-dimmed');
    });
  }

  private get currentTimelineEvent(): TaintTimelineEvent | null {
    const events = this.taintResult?.timeline_events ?? [];
    if (this.timelinePosition < 1 || this.timelinePosition > events.length) {
      return null;
    }
    return events[this.timelinePosition - 1];
  }

  get timelineCurrentDate(): string | null {
    return this.currentTimelineEvent?.timestamp ?? null;
  }

  /** "pošiljalac → primalac · iznos · datum" - same caption format as the /graph page's
   * own timeline, for a consistent feel between the two replay features. */
  get timelineCurrentCaption(): string | null {
    const event = this.currentTimelineEvent;
    if (!event) {
      return null;
    }
    return `${event.source} → ${event.target} · ${event.amount.toFixed(2)}`;
  }

  toggleTimelineSubtitles(): void {
    this.timelineSubtitlesEnabled = !this.timelineSubtitlesEnabled;
  }

  toggleTimelineFollow(): void {
    this.timelineFollowEnabled = !this.timelineFollowEnabled;
  }

  /** Hides every node at or below the current threshold - both during static viewing and
   * mid-playback - since on a few-hundred-node graph the vast majority of nodes are
   * usually untouched (or barely touched) by the seed(s), and seeing only the ones that
   * actually matter is the whole point of "which addresses does this money actually
   * reach". Defaults to a 0% threshold (hide only the untouched ones); raising it also
   * hides negligible slivers, which matters most when many seeds are selected at once
   * and almost nothing sits at exactly 0% anymore. */
  toggleHideNonTaintedNodes(): void {
    this.hideNonTaintedNodes = !this.hideNonTaintedNodes;
    this.applyTaintTimeline();
  }

  onTaintThresholdChange(value: number): void {
    this.taintHideThreshold = Math.min(100, Math.max(0, Number(value) || 0));
    this.applyTaintTimeline();
  }

  toggleTimeline(): void {
    this.timelineEnabled = !this.timelineEnabled;
    if (!this.timelineEnabled) {
      this.stopTimelinePlay();
    }
    this.applyTaintTimeline();
  }

  onTimelinePositionChange(value: number): void {
    this.timelinePosition = value;
    this.applyTaintTimeline();
  }

  toggleTimelinePlay(): void {
    if (this.isTimelinePlaying) {
      this.stopTimelinePlay();
      return;
    }
    if (this.timelinePosition >= this.timelineMaxRank) {
      this.timelinePosition = 1;
    }
    this.isTimelinePlaying = true;
    this.startTimelinePlayInterval();
  }

  onTimelineSpeedChange(speed: number): void {
    this.timelineSpeed = speed;
    if (this.isTimelinePlaying) {
      this.startTimelinePlayInterval();
    }
  }

  /** tainted_hops is already in ascending rank order (the backend appends to it in the
   * same chronological pass it ranks events in), so "next" is just the first one past
   * the current position - no separate sort needed. */
  get hasNextTaintedTransaction(): boolean {
    return (this.taintResult?.tainted_hops ?? []).some((hop) => hop.rank > this.timelinePosition);
  }

  skipToNextTaintedTransaction(): void {
    const next = (this.taintResult?.tainted_hops ?? []).find((hop) => hop.rank > this.timelinePosition);
    if (!next) {
      return;
    }
    this.timelinePosition = next.rank;
    this.applyTaintTimeline();
  }

  private startTimelinePlayInterval(): void {
    if (this.timelinePlayTimer !== null) {
      clearInterval(this.timelinePlayTimer);
    }
    const baseIntervalMs = 500;
    this.timelinePlayTimer = setInterval(() => {
      if (this.timelinePosition >= this.timelineMaxRank) {
        this.stopTimelinePlay();
        return;
      }
      this.timelinePosition += 1;
      this.applyTaintTimeline();
    }, baseIntervalMs / this.timelineSpeed);
  }

  private stopTimelinePlay(): void {
    if (this.timelinePlayTimer !== null) {
      clearInterval(this.timelinePlayTimer);
      this.timelinePlayTimer = null;
    }
    this.isTimelinePlaying = false;
  }

  /** Looks up what a node's taint % actually was right after the Nth chronological event
   * that touched it - reconstructed purely from the backend's own per-node history
   * (node_taint_series), never re-simulated in JS, so the replay can't drift from what
   * the real algorithm computed. */
  private getNodeTaintAtRank(nodeId: string, rank: number): number {
    const series: TaintTimelineEntry[] | undefined = this.taintResult?.node_taint_series?.[nodeId];
    if (!series || series.length === 0) {
      const isSeed = this.taintResult?.seed_addresses.includes(nodeId) ?? false;
      const firstRank = this.taintResult?.node_first_rank?.[nodeId];
      return isSeed && firstRank != null && firstRank <= rank ? 100 : 0;
    }
    let pct = 0;
    for (const entry of series) {
      if (entry.rank > rank) {
        break;
      }
      pct = entry.taint_percentage;
    }
    return pct;
  }

  /** What % of THIS specific transfer (not the node's overall balance) was tainted -
   * looked up from tainted_hops for the given (source, target) pair, as of maxRank (or
   * the final state if null). Returns '' when the pair never carried any tainted value,
   * so plain/clean edges stay unlabeled instead of cluttering the graph with "0%"
   * everywhere. tainted_hops is already chronologically ordered, so the last matching
   * entry is simply the most recent one. */
  /** The seed filter only applies in the full/final view (timeline off) - see the
   * scoping note on applyTaintTimeline - so timeline playback always passes
   * ignoreSeedFilter=true and shows every seed's contribution regardless of what's
   * currently toggled, rather than mixing "as-of-position" with "as-of-filter" in a way
   * that's hard to reason about while scrubbing. */
  private getEdgeTaintLabel(source: string, target: string, maxRank: number | null, ignoreSeedFilter = false): string {
    const hops = (this.taintResult?.tainted_hops ?? []).filter(
      (hop) => hop.source === source && hop.target === target && (maxRank == null || hop.rank <= maxRank),
    );
    if (hops.length === 0) {
      return '';
    }
    const latest = hops[hops.length - 1];
    if (ignoreSeedFilter || this.isAllSeedsActive) {
      // Capped at 2 shares (+ a "how many more" count) so the arrow label stays readable
      // even when a dozen+ seeds all feed the same edge - the uncapped, full breakdown is
      // available in the node inspector once you click either end of this edge.
      const breakdown = TaintAnalysisComponent.formatBreakdown(latest.taint_by_source, 2);
      return breakdown || `${latest.taint_pct_at_hop}%`;
    }
    const filteredBySource = this.filteredBreakdown(latest.taint_by_source);
    const filteredPct = Object.values(filteredBySource).reduce((sum, pct) => sum + pct, 0);
    if (filteredPct <= 1e-9) {
      return '';
    }
    const breakdown = TaintAnalysisComponent.formatBreakdown(filteredBySource, 2);
    return breakdown || `${Math.round(filteredPct * 100) / 100}%`;
  }

  /** "" when there's 0 or 1 contributing source (the plain aggregate % already covers
   * that case) - otherwise each source's share, largest first, e.g. "60%+40%", capped at
   * maxEntries with a "+N" tail for how many more beyond that weren't shown. */
  private static formatBreakdown(bySource: Record<string, number> | undefined, maxEntries: number): string {
    const entries = Object.entries(bySource ?? {}).sort((a, b) => b[1] - a[1]);
    if (entries.length <= 1) {
      return '';
    }
    const shown = entries
      .slice(0, maxEntries)
      .map(([, pct]) => `${pct}%`)
      .join('+');
    const remaining = entries.length - maxEntries;
    return remaining > 0 ? `${shown}+${remaining}` : shown;
  }

  /** Full addresses on canvas were pure clutter on a few-hundred-node graph (see earlier
   * cleanup), but with real result numbers now flying around it's genuinely hard to tell
   * "which circle was that 100% one again" without ANY label - a short, still-unique-
   * enough prefix/suffix strikes the balance. */
  private truncateAddress(value: string): string {
    if (value.length <= 14) {
      return value;
    }
    return `${value.slice(0, 6)}…${value.slice(-4)}`;
  }

  /** "0x7c33…45ac" - the truncated address a cytoscape node element already carries in
   * its data, falling back to its id if address was never set. */
  private nodeAddressLabel(node: NodeSingular): string {
    return this.truncateAddress(String(node.data('address') ?? node.id()));
  }

  /** Reveals nodes/edges as of the current timeline position AND recolors every visible
   * node to what its taint % actually was at that point - unlike the /graph page's
   * timeline (which only toggles visibility), the whole point here is watching the
   * percentage itself evolve hop by hop, so the color has to change too, not just what's
   * shown. Also applies the "hide non-tainted" filter, on or off the timeline alike -
   * hiding a node here hides its connected edges too (cytoscape does this automatically
   * for any node with display:none), so untainted edges never need separate handling.
   *
   * The per-seed source filter ("Filter po izvoru") only applies in this OFF branch. The
   * backend only stores a per-node taint_by_source snapshot of the CURRENT/final state,
   * not a full per-seed history at every past rank the way node_taint_series does for the
   * aggregate - so there's no historically-accurate way to isolate one seed's share while
   * scrubbing. Rather than show a filtered view that's silently wrong mid-scrub, the
   * timeline branch below always ignores the filter and shows every seed's contribution. */
  private applyTaintTimeline(): void {
    if (!this.cy) {
      return;
    }

    if (!this.timelineEnabled) {
      this.cy.nodes().forEach((node) => {
        const finalPct = Number(node.data('finalTaintPercentage') ?? 0);
        const bySource = node.data('taint_by_source') as Record<string, number> | undefined;
        const displayPct = this.nodeFilteredPct(finalPct, bySource);
        node.data('taint_percentage', displayPct);
        node.data('displayLabel', this.hasRunTaint ? `${this.nodeAddressLabel(node)}\n${displayPct}%` : '');
        const hiddenByThreshold = this.hideNonTaintedNodes && displayPct <= this.taintHideThreshold;
        const hiddenBySeedFilter = !this.isAllSeedsActive && displayPct <= 1e-9;
        node.style('display', hiddenByThreshold || hiddenBySeedFilter ? 'none' : 'element');
      });
      this.cy.edges().forEach((edge) => {
        const label = this.getEdgeTaintLabel(String(edge.data('source')), String(edge.data('target')), null);
        edge.data('edgeLabel', label);
        const hiddenBySeedFilter = !this.isAllSeedsActive && label === '';
        edge.style('display', hiddenBySeedFilter ? 'none' : 'element');
      });
      this.applyPathHighlight(this.selectedNode ? String(this.selectedNode.id) : null);
      return;
    }

    const position = this.timelinePosition;
    this.cy.nodes().forEach((node) => {
      const rank = node.data('chronoRank');
      const revealed = rank == null || rank <= position;
      const pct = revealed ? this.getNodeTaintAtRank(node.id(), position) : 0;
      if (revealed) {
        node.data('taint_percentage', pct);
        node.data('displayLabel', `${this.nodeAddressLabel(node)}\n${pct}%`);
      }
      const hiddenByFilter = this.hideNonTaintedNodes && pct <= this.taintHideThreshold;
      node.style('display', revealed && !hiddenByFilter ? 'element' : 'none');
    });
    this.cy.edges().forEach((edge) => {
      const rank = edge.data('chronoRank');
      edge.data('edgeLabel', this.getEdgeTaintLabel(String(edge.data('source')), String(edge.data('target')), position, true));
      edge.style('display', rank != null && rank <= position ? 'element' : 'none');
    });

    this.applyPathHighlight(this.selectedNode ? String(this.selectedNode.id) : null);
    this.focusOnCurrentTransaction();
  }

  /** Pans/zooms to the edge that just got revealed, but only when it's actually outside
   * the current view - if it's already visible, the camera stays put so following the
   * trail doesn't feel jumpy, especially at faster playback speeds. Looks the edge up by
   * its actual source/target for the current rank (not by chronoRank, which is only an
   * edge's FIRST occurrence) so a repeat transfer on an already-revealed edge still gets
   * followed correctly. */
  private focusOnCurrentTransaction(): void {
    if (!this.cy || !this.timelineFollowEnabled) {
      return;
    }

    const event = this.currentTimelineEvent;
    if (!event) {
      return;
    }

    const currentEdge = this.cy
      .edges()
      .filter((edge) => edge.data('source') === event.source && edge.data('target') === event.target);
    if (currentEdge.empty()) {
      return;
    }

    const extent = this.cy.extent();
    const box = currentEdge.boundingBox();
    const isVisible = box.x1 >= extent.x1 && box.x2 <= extent.x2 && box.y1 >= extent.y1 && box.y2 <= extent.y2;
    if (isVisible) {
      return;
    }

    this.cy.animate({ fit: { eles: currentEdge, padding: 150 } }, { duration: 350 });
  }

  private renderGraph(): void {
    if (!this.taintCanvas) {
      return;
    }

    this.cy?.destroy();
    this.cy = null;

    if (!this.graph) {
      return;
    }

    const elements: ElementDefinition[] = [
      ...this.graph.nodes.map((node) => {
        const id = String(node.id);
        const isSeed = Boolean(node.is_taint_seed) || this.seedAddresses.includes(id);
        const taintPercentage = node.taint_percentage ?? 0;
        return {
          data: {
            ...node,
            id,
            taint_percentage: taintPercentage,
            finalTaintPercentage: taintPercentage,
            chronoRank: this.taintResult?.node_first_rank?.[id] ?? null,
            // No label at all before a run (pure clutter while just picking seeds on a
            // few-hundred-node graph). Once results exist, show a short address prefix
            // alongside the % - color alone isn't always a safe read for two nearby
            // values (e.g. 66.67% vs 100% looked too similar in testing), and with real
            // results on screen it's genuinely hard to tell which circle is which
            // without some address hint. Kept in its own field (not baked into a static
            // label string) so the timeline scrubber can update it live.
            displayLabel: this.hasRunTaint ? `${this.truncateAddress(String(node.address ?? node.id))}\n${taintPercentage}%` : '',
          },
          classes: isSeed ? 'taint-seed' : '',
        } as ElementDefinition;
      }),
      ...this.graph.links.map((link) => {
        const totalAmount = Number(link.total_amount ?? link.amount ?? 0);
        const source = String(link.source);
        const target = String(link.target);
        return {
          data: {
            ...link,
            id: `${link.source}__${link.target}__${Math.round(totalAmount * 1000)}`,
            totalAmount,
            chronoRank: this.taintResult?.edge_first_rank?.[`${source}__${target}`] ?? null,
            // What % of THIS transfer was tainted, e.g. "66.67%" - blank for edges that
            // never carried any tainted value, so clean edges don't clutter the graph.
            edgeLabel: this.getEdgeTaintLabel(source, target, null),
          },
        } as ElementDefinition;
      }),
    ];
    const maxLinkAmount = Math.max(1, ...this.graph.links.map((link) => Number(link.total_amount ?? link.amount ?? 0)));

    // Fixed node size and spacing looks fine for a handful of nodes but crams a few
    // hundred into a single overlapping blob - scale both down/up with node count, same
    // approach as the main graph page.
    const nodeCount = this.graph.nodes.length;
    const sizeScale = nodeCount > 150 ? 0.45 : nodeCount > 60 ? 0.7 : 1;
    const nodeSize = Math.round(34 * sizeScale);

    this.cy = cytoscape({
      container: this.taintCanvas.nativeElement,
      elements,
      wheelSensitivity: 0.2,
      layout: {
        name: 'fcose',
        quality: 'default',
        randomize: true,
        animate: false,
        fit: true,
        padding: 60,
        nodeSeparation: nodeCount > 60 ? 150 : 100,
        nodeRepulsion: nodeCount > 60 ? 10000 : 6000,
        idealEdgeLength: nodeCount > 60 ? 100 : 80,
      } as any,
      style: [
        {
          selector: 'node',
          style: {
            label: 'data(displayLabel)',
            'text-valign': 'center',
            'text-halign': 'center',
            'text-wrap': 'wrap',
            'font-size': 9,
            'min-zoomed-font-size': 8,
            color: '#ecf2ff',
            'text-outline-width': 2,
            'text-outline-color': '#07111f',
            // Two-stop ramp (dark -> amber -> red) instead of one dark-to-red span:
            // a single 0-100 red interpolation compresses visually - 66% and 100% both
            // read as "red" at a glance - splitting the range gives the mid-tier its own
            // hue step so a 66% node doesn't look the same as a 100% node.
            'background-color': `mapData(taint_percentage, 0, ${TaintAnalysisComponent.TAINT_MID_THRESHOLD}, ${TaintAnalysisComponent.TAINT_LOW_COLOR}, ${TaintAnalysisComponent.TAINT_MID_COLOR})`,
            'border-width': 2,
            'border-color': '#3a4a63',
            width: nodeSize,
            height: nodeSize,
          },
        },
        {
          selector: `node[taint_percentage > ${TaintAnalysisComponent.TAINT_MID_THRESHOLD}]`,
          style: {
            'background-color': `mapData(taint_percentage, ${TaintAnalysisComponent.TAINT_MID_THRESHOLD}, 100, ${TaintAnalysisComponent.TAINT_MID_COLOR}, ${TaintAnalysisComponent.TAINT_HIGH_COLOR})`,
          },
        },
        {
          selector: 'node.taint-seed',
          style: {
            'border-width': 4,
            'border-color': '#fbbf24',
            'border-style': 'double',
          },
        },
        {
          selector: 'node:selected',
          style: {
            'overlay-opacity': 0.16,
            'overlay-color': '#7dd3fc',
          },
        },
        {
          selector: 'edge',
          style: {
            width: `mapData(totalAmount, 0, ${maxLinkAmount}, 1, 3.5)`,
            'line-color': '#3a4a63',
            'target-arrow-color': '#3a4a63',
            'target-arrow-shape': 'triangle',
            // Bigger than the /graph page's - on a graph this sparse, direction is the
            // whole point of the arrow, so it needs to actually read as one at a glance.
            'arrow-scale': 1,
            'curve-style': 'bezier',
            opacity: 0.6,
            // % of THIS transfer that was tainted (blank on clean edges - see
            // getEdgeTaintLabel) rotated to sit along the line, not fighting for space
            // with the percentage already shown on each node.
            label: 'data(edgeLabel)',
            'font-size': 8,
            'min-zoomed-font-size': 7,
            color: '#7dd6ff',
            'text-outline-width': 2,
            'text-outline-color': '#07111f',
            'text-rotation': 'autorotate',
            'text-margin-y': -8,
          },
        },
        // Path highlight uses outline-* (drawn outside the border) rather than
        // border-*, so it layers independently on top of the seed's gold border
        // instead of fighting it for the same property.
        {
          selector: 'node.path-highlighted',
          style: {
            'outline-width': 4,
            'outline-color': '#7dd3fc',
            'outline-style': 'solid',
            'outline-offset': 2,
          },
        },
        {
          selector: 'edge.path-highlighted',
          style: {
            'line-color': '#7dd3fc',
            'target-arrow-color': '#7dd3fc',
            opacity: 1,
            width: 3,
          },
        },
        {
          selector: 'node.path-dimmed',
          style: {
            opacity: 0.15,
          },
        },
        {
          selector: 'edge.path-dimmed',
          style: {
            opacity: 0.06,
          },
        },
        // Tap-to-focus on a single edge (separate from path highlighting). Gold AND
        // dashed - not just a different color - so it's unmistakable even when this
        // same edge is already cyan from a selected node's path highlight (color alone
        // wasn't enough there: toggling this off still leaves the cyan path-highlight
        // showing, which read as "nothing happened" until the dash pattern made the
        // on/off states visibly distinct regardless of what's layered underneath).
        {
          selector: 'edge.edge-focused',
          style: {
            'line-color': '#fbbf24',
            'target-arrow-color': '#fbbf24',
            'line-style': 'dashed',
            opacity: 1,
            width: 4,
            'font-size': 10,
          },
        },
      ],
    });

    this.cy.on('tap', 'node', (event) => {
      const nodeData = event.target.data() as GraphNodeData;
      if (this.isSeedSelectionMode) {
        this.toggleSeed(String(nodeData.id));
      } else if (this.selectedNode && String(this.selectedNode.id) === String(nodeData.id)) {
        // Tapping the already-selected node again deselects it - same toggle rule as
        // edges below, so there's always a click-based way back to "nothing selected"
        // without having to pick a different node first.
        this.deselectNode();
      } else {
        this.selectNode(nodeData);
      }
    });

    // Tapping an edge toggles a highlight on it - tap the SAME edge again to clear it,
    // rather than it staying stuck highlighted with no way back short of picking a node.
    this.cy.on('tap', 'edge', (event) => {
      const edge = event.target;
      const key = `${edge.data('source')}__${edge.data('target')}`;
      this.cy?.edges().removeClass('edge-focused');
      if (this.selectedEdgeKey === key) {
        this.selectedEdgeKey = null;
        this.selectedEdgeDetails = null;
      } else {
        this.selectedEdgeKey = key;
        edge.addClass('edge-focused');
        this.selectedEdgeDetails = this.buildEdgeDetails(edge);
      }
    });

    // Tapping empty canvas (not any node/edge) clears the current selection - the
    // standard "click elsewhere to deselect" escape hatch, since without it the only
    // way to clear a selected node's path highlight was to pick a different node.
    this.cy.on('tap', (event) => {
      if (event.target === this.cy) {
        this.deselectNode();
      }
    });

    // The seed-panel above the canvas just appeared/disappeared (or changed) as part of
    // this same render pass - give the DOM a frame to settle into its final layout, then
    // make sure cytoscape's container measurement matches it exactly.
    this.scheduleCyResize();
    this.applyTaintTimeline();
  }

  fitWholeGraph(): void {
    this.cy?.fit(undefined, 60);
  }

  private static readonly PDF_NAVY: [number, number, number] = [13, 24, 40];
  private static readonly PDF_ACCENT: [number, number, number] = [43, 130, 191];
  private static readonly PDF_TEXT_GRAY: [number, number, number] = [100, 112, 128];
  private static readonly PDF_TEXT_DARK: [number, number, number] = [24, 28, 36];
  private static readonly PDF_WHITE: [number, number, number] = [255, 255, 255];
  private static readonly PDF_AMBER: [number, number, number] = [217, 119, 6];
  private static readonly PDF_RISK_HIGH: [number, number, number] = [220, 38, 38];
  private static readonly PDF_RISK_MEDIUM: [number, number, number] = [217, 119, 6];
  private static readonly PDF_RISK_LOW: [number, number, number] = [22, 163, 74];
  private static readonly PDF_CANVAS_BG = '#070d1a';

  /** Same 3-tier split the on-canvas color ramp already uses at 50% (dark/amber pivot) -
   * reusing it here means a node reads as the same risk tier in the PDF table as it does
   * by color on screen, instead of inventing a second, inconsistent scale. */
  private static riskLabel(pct: number): string {
    if (pct >= 50) {
      return 'VISOK';
    }
    if (pct >= 10) {
      return 'SREDNJI';
    }
    return 'NIZAK';
  }

  private static riskColor(pct: number): [number, number, number] {
    if (pct >= 50) {
      return TaintAnalysisComponent.PDF_RISK_HIGH;
    }
    if (pct >= 10) {
      return TaintAnalysisComponent.PDF_RISK_MEDIUM;
    }
    return TaintAnalysisComponent.PDF_RISK_LOW;
  }

  /** jsPDF's built-in core fonts (helvetica/courier) only cover WinAnsi/Latin-1, which is
   * missing č/ć/đ (and renders š/ž unreliably) - embedding a Unicode TTF just for this
   * report isn't worth the bundle size, so report text is transliterated to plain ASCII
   * instead. Addresses/numbers are unaffected since they never contain these characters. */
  private static readonly ASCII_MAP: Record<string, string> = {
    č: 'c', ć: 'c', š: 's', ž: 'z', đ: 'dj',
    Č: 'C', Ć: 'C', Š: 'S', Ž: 'Z', Đ: 'Dj',
  };

  private asciiSafe(value: string | null | undefined): string {
    return (value ?? '').replace(/[čćšžđČĆŠŽĐ]/g, (match) => TaintAnalysisComponent.ASCII_MAP[match] ?? match);
  }

  private static loadImageSize(dataUrl: string): Promise<{ width: number; height: number }> {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
      image.onerror = () => reject(new Error('image load failed'));
      image.src = dataUrl;
    });
  }

  /** Snapshots the graph EXACTLY as currently rendered - same colors, same arrows, and
   * critically, the same set of nodes/edges that are actually visible right now. Nodes
   * hidden by the taint-threshold filter (display:none) are excluded from cytoscape's own
   * bounding box and never rendered into the PNG, so the exported picture automatically
   * matches whatever "Sakrij ispod praga" is currently set to, without any extra logic
   * here - same goes for the timeline's reveal state if it happens to be scrubbed mid-way. */
  async exportPdfReport(): Promise<void> {
    if (!this.cy || !this.taintResult || !this.activeCase || this.isExportingPdf) {
      return;
    }

    this.isExportingPdf = true;
    this.taintError = null;
    try {
      const graphImage = this.cy.png({ full: true, scale: 2, bg: TaintAnalysisComponent.PDF_CANVAS_BG });
      const imageSize = await TaintAnalysisComponent.loadImageSize(graphImage);
      this.buildTaintPdf(graphImage, imageSize);
    } catch {
      this.taintError = 'Neuspesno generisanje PDF izvestaja.';
    } finally {
      this.isExportingPdf = false;
    }
  }

  /** "" for a single contributing source (the plain % column already covers that case) -
   * otherwise the top few sources by share, truncated, with a "+N drugih" tail. A real
   * case can easily have dozens of seeds feeding one address - printing all of them
   * uncapped (as this used to) blew up a single table cell into a wall of full-length
   * hex addresses that dwarfed the rest of the row, so this is capped the same way the
   * on-canvas edge label already is, just with a slightly higher limit since a report
   * page has more room to work with than an arrow label. */
  private formatBreakdownForPdf(
    bySource: Record<string, number> | undefined,
    options?: { maxEntries?: number; separator?: string; truncateAddresses?: boolean },
  ): string {
    const maxEntries = options?.maxEntries ?? 3;
    const separator = options?.separator ?? ', ';
    const truncate = options?.truncateAddresses ?? true;
    const entries = Object.entries(bySource ?? {}).sort((a, b) => b[1] - a[1]);
    if (entries.length <= 1) {
      return '';
    }
    const shown = entries
      .slice(0, maxEntries)
      .map(([address, pct]) => `${truncate ? this.truncateAddress(address) : address}: ${pct}%`)
      .join(separator);
    const remaining = entries.length - maxEntries;
    return remaining > 0 ? `${shown}${separator}+${remaining} drugih` : shown;
  }

  /** Auto-composed plain-language wrap-up for the very end of the report - the same facts
   * as the summary cards/key-findings box up top, but read as prose rather than numbers, so
   * the report doesn't force a reader to reconstruct the story themselves from raw tables. */
  private buildConclusionParagraph(): string {
    const tainted = this.nonZeroTaintedNodes;
    const totalNodes = this.graph?.nodes.length ?? 0;
    const cashOut = this.cashOutCandidates;
    const maxPct = tainted.reduce((max, item) => Math.max(max, item.taint_percentage), 0);
    const seedCount = this.taintResult?.seed_addresses.length ?? 0;
    const multiSourceNode = tainted.find((item) => Object.keys(item.taint_by_source ?? {}).length > 1);

    const sentences: string[] = [
      `Analiza je identifikovala ${tainted.length} adresa koje sadrze zaprljana sredstva, od ukupno ${totalNodes} adresa obuhvacenih ovom evidencijom.`,
    ];

    if (cashOut.length === 0) {
      sentences.push('Nije detektovana nijedna verovatna tacka unovcavanja u ovoj evidenciji.');
    } else if (cashOut.length === 1) {
      sentences.push(
        `Detektovana je jedna verovatna tacka unovcavanja (adresa ${cashOut[0].address}) sa konacnim procentom zaprljanosti od ${cashOut[0].taint_percentage}%.`,
      );
    } else {
      const pcts = cashOut.map((item) => item.taint_percentage);
      sentences.push(
        `Detektovano je ${cashOut.length} verovatnih tacaka unovcavanja, sa procentom zaprljanosti u rasponu od ${Math.min(...pcts)}% do ${Math.max(...pcts)}%.`,
      );
    }

    sentences.push(`Koriscen je ${seedCount} ${seedCount === 1 ? 'pocetni izvor' : 'pocetnih izvora'} (seed adresa).`);
    sentences.push(`Najveci zabelezeni procenat zaprljanosti iznosio je ${maxPct}% (nivo rizika: ${TaintAnalysisComponent.riskLabel(maxPct)}).`);

    if (multiSourceNode) {
      sentences.push(
        `Pracena adresa ${multiSourceNode.address} sadrzala je sredstva poreklom iz vise razlicitih izvora (${this.formatBreakdownForPdf(multiSourceNode.taint_by_source)}).`,
      );
    }

    return sentences.join(' ');
  }

  private buildTaintPdf(graphImage: string, imageSize: { width: number; height: number }): void {
    const result = this.taintResult!;
    const caseSummary = this.activeCase!;
    const NAVY = TaintAnalysisComponent.PDF_NAVY;
    const ACCENT = TaintAnalysisComponent.PDF_ACCENT;
    const TEXT_GRAY = TaintAnalysisComponent.PDF_TEXT_GRAY;
    const TEXT_DARK = TaintAnalysisComponent.PDF_TEXT_DARK;
    const WHITE = TaintAnalysisComponent.PDF_WHITE;

    const doc = new jsPDF({ unit: 'mm', format: 'a4' });
    const pageWidth = doc.internal.pageSize.getWidth();
    const pageHeight = doc.internal.pageSize.getHeight();
    const marginX = 14;
    const usableWidth = pageWidth - marginX * 2;
    let y = 32;

    doc.setFillColor(...NAVY);
    doc.rect(0, 0, pageWidth, 24, 'F');
    doc.setTextColor(...WHITE);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(15);
    doc.text('Lusi v1.0 - Izvestaj taint analize', marginX, 11);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(10);
    doc.text(`Slucaj: ${this.asciiSafe(caseSummary.name)}`, marginX, 19);
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
    kv('IZVEZAO', this.asciiSafe(this.auth.currentUser?.username ?? caseSummary.analyst));
    kv('EVIDENCIJA', this.selectedEvidence ? this.asciiSafe(this.selectedEvidence) : 'Sve transakcije (kombinovano)');
    kv('GENERISANO', new Date().toLocaleString());
    y += 2;

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
        doc.setFontSize(14);
        doc.text(String(value), x + colWidth / 2, y0 + 6.5, { align: 'center' });
        doc.setFont('helvetica', 'normal');
        doc.setFontSize(7);
        doc.text(doc.splitTextToSize(label, colWidth - 4), x + colWidth / 2, y0 + 11, { align: 'center' });
        x += colWidth + gap;
      }
      y = y0 + 15 + 6;
      doc.setTextColor(...TEXT_DARK);
    };

    sectionTitle('Rezime analize');
    const maxTaintPct = this.nonZeroTaintedNodes.reduce((max, item) => Math.max(max, item.taint_percentage), 0);
    const hasMultiSourceOverall = this.nonZeroTaintedNodes.some((item) => Object.keys(item.taint_by_source ?? {}).length > 1);
    drawSummaryCards([
      ['Ukupno adresa', this.graph?.nodes.length ?? 0, ACCENT],
      ['Zaprljanih adresa', this.nonZeroTaintedNodes.length, ACCENT],
      ['Izvora (seed)', result.seed_addresses.length, ACCENT],
      ['Tacaka unovcavanja', this.cashOutCandidates.length, this.cashOutCandidates.length > 0 ? TaintAnalysisComponent.PDF_AMBER : TEXT_GRAY],
    ]);

    const findingLines: string[] = [`${this.nonZeroTaintedNodes.length} adresa sa zaprljanim sredstvima identifikovano`];
    if (this.cashOutCandidates.length > 0) {
      findingLines.push(
        `${this.cashOutCandidates.length} ${this.cashOutCandidates.length === 1 ? 'verovatna tacka unovcavanja' : 'verovatnih tacaka unovcavanja'} detektovano`,
      );
    }
    findingLines.push(`Najveci procenat zaprljanosti: ${maxTaintPct}%`);
    if (hasMultiSourceOverall) {
      findingLines.push('Otkriveno mesanje sredstava iz vise razlicitih izvora');
    }

    sectionTitle('Kljucni nalazi');
    const findingsLineHeight = 6.5;
    const findingsBoxTop = y - 5;
    const findingsBoxHeight = (findingLines.length + 1) * findingsLineHeight + 6;
    doc.setFillColor(240, 247, 253);
    doc.setDrawColor(...ACCENT);
    doc.setLineWidth(0.4);
    doc.roundedRect(marginX, findingsBoxTop, usableWidth, findingsBoxHeight, 2, 2, 'FD');

    const drawCheckmark = (checkX: number, baseline: number): void => {
      doc.setDrawColor(...ACCENT);
      doc.setLineWidth(0.6);
      doc.lines(
        [
          [1.1, 1.5],
          [2.0, -2.9],
        ],
        checkX,
        baseline - 1.4,
        [1, 1],
        'S',
        false,
      );
    };

    let findingY = findingsBoxTop + 7;
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(10);
    doc.setTextColor(...TEXT_DARK);
    for (const line of findingLines) {
      drawCheckmark(marginX + 4, findingY);
      doc.text(line, marginX + 11, findingY);
      findingY += findingsLineHeight;
    }
    drawCheckmark(marginX + 4, findingY);
    doc.setFont('helvetica', 'bold');
    doc.text('Nivo rizika: ', marginX + 11, findingY);
    const riskPrefixWidth = doc.getTextWidth('Nivo rizika: ');
    doc.setTextColor(...TaintAnalysisComponent.riskColor(maxTaintPct));
    doc.text(TaintAnalysisComponent.riskLabel(maxTaintPct), marginX + 11 + riskPrefixWidth, findingY);
    doc.setFont('helvetica', 'normal');
    doc.setTextColor(...TEXT_DARK);
    y = findingsBoxTop + findingsBoxHeight + 9;

    sectionTitle('Izvori (seed adrese)');
    autoTable(doc, {
      startY: y,
      margin: { left: marginX, right: marginX },
      head: [['#', 'Seed adresa']],
      body:
        result.seed_addresses.length > 0
          ? result.seed_addresses.map((address, index) => [`Seed #${index + 1}`, address])
          : [['-', '(automatski, sa crne liste)']],
      styles: { fontSize: 8.5, cellPadding: 1.8, font: 'courier', textColor: TEXT_DARK },
      headStyles: { fillColor: NAVY, textColor: WHITE, font: 'helvetica', fontStyle: 'bold' },
      alternateRowStyles: { fillColor: [240, 245, 250] },
      columnStyles: { 0: { cellWidth: 26, font: 'helvetica' } },
    });
    y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 4;

    sectionTitle('Evidencija (SHA-256)');
    const relevantEvidence = this.selectedEvidence
      ? this.evidenceOptions.filter((entry) => entry.stored_name === this.selectedEvidence)
      : this.evidenceOptions;
    if (relevantEvidence.length === 0) {
      doc.setFont('helvetica', 'italic');
      doc.setFontSize(9);
      doc.setTextColor(...TEXT_GRAY);
      doc.text('Podaci o evidenciji nisu dostupni.', marginX, y);
      y += 6;
      doc.setTextColor(...TEXT_DARK);
    } else {
      for (const entry of relevantEvidence) {
        doc.setFont('helvetica', 'bold');
        doc.setFontSize(8.5);
        doc.setTextColor(...TEXT_DARK);
        doc.text(this.asciiSafe(entry.file_name), marginX, y);
        y += 4;
        doc.setFont('courier', 'normal');
        doc.setFontSize(7.5);
        doc.setTextColor(...TEXT_GRAY);
        doc.text(entry.sha256, marginX, y);
        y += 5.5;
      }
      doc.setTextColor(...TEXT_DARK);
    }
    y += 1;

    sectionTitle('Podesavanja prikaza u trenutku izvoza');
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9.5);
    const settingsRows: Array<[string, string]> = [
      [
        'Filter praga',
        this.hideNonTaintedNodes
          ? `Ukljucen - sakriveni cvorovi sa taint % <= ${this.taintHideThreshold}%`
          : 'Iskljucen - prikazani svi cvorovi',
      ],
      [
        'Vremenska traka',
        this.timelineEnabled
          ? `Ukljucena - snimak stanja na transakciji ${this.timelinePosition} / ${this.timelineMaxRank}`
          : 'Iskljucena - prikazan konacan, potpun rezultat analize',
      ],
    ];
    if (this.selectedNode) {
      settingsRows.push(['Istaknuta putanja', `Cvor ${this.asciiSafe(String(this.selectedNode.address ?? this.selectedNode.id))}`]);
    }
    for (const [label, value] of settingsRows) {
      doc.setFont('helvetica', 'bold');
      doc.text(`${label}:`, marginX, y);
      doc.setFont('helvetica', 'normal');
      const lines: string[] = doc.splitTextToSize(value, usableWidth - 42);
      doc.text(lines, marginX + 42, y);
      y += Math.max(5.5, lines.length * 5);
    }
    y += 3;

    const maxImgHeight = 145;
    let renderWidth = usableWidth;
    let renderHeight = (imageSize.height / imageSize.width) * renderWidth;
    if (renderHeight > maxImgHeight) {
      renderHeight = maxImgHeight;
      renderWidth = (imageSize.width / imageSize.height) * renderHeight;
    }
    // Checked BEFORE printing the heading (not after) - otherwise a too-tall image pushes
    // itself to a fresh page while "Graficki prikaz mreze" is left stranded alone at the
    // bottom of the previous one, with nothing under it until the page break.
    const sectionTitleHeight = 11;
    if (y + sectionTitleHeight + renderHeight > pageHeight - 20) {
      doc.addPage();
      y = 16;
    }
    sectionTitle('Graficki prikaz mreze');
    const imgX = marginX + (usableWidth - renderWidth) / 2;
    doc.setFillColor(7, 13, 26);
    doc.rect(imgX, y, renderWidth, renderHeight, 'F');
    doc.addImage(graphImage, 'PNG', imgX, y, renderWidth, renderHeight);
    y += renderHeight + 5;

    doc.setFont('helvetica', 'italic');
    doc.setFontSize(8);
    doc.setTextColor(...TEXT_GRAY);
    const legendText =
      'Boja cvora: tamno (0%) -> zuto/narandzasto (50%) -> crveno (100%). Duplo zlatna ivica = izvor (seed adresa). ' +
      'Slika prikazuje tacno ono sto je bilo vidljivo na ekranu u trenutku izvoza (ukljucujuci aktivni filter praga).';
    doc.text(doc.splitTextToSize(legendText, usableWidth), marginX, y);
    doc.setTextColor(...TEXT_DARK);

    const topRows = this.nonZeroTaintedNodes.map((item, index) => [
      String(index + 1),
      item.address,
      `${item.taint_percentage}% (${TaintAnalysisComponent.riskLabel(item.taint_percentage)})`,
      item.is_taint_seed ? 'Da' : 'Ne',
    ]);

    doc.addPage();
    y = 16;
    sectionTitle('Najvise zaprljane adrese');
    autoTable(doc, {
      startY: y,
      margin: { left: marginX, right: marginX },
      head: [['#', 'Adresa', 'Taint %', 'Izvor?']],
      body: topRows,
      styles: { fontSize: 8, cellPadding: 1.6, font: 'courier', textColor: TEXT_DARK },
      headStyles: { fillColor: NAVY, textColor: WHITE, font: 'helvetica', fontStyle: 'bold' },
      alternateRowStyles: { fillColor: [240, 245, 250] },
      columnStyles: { 0: { cellWidth: 8, font: 'helvetica' }, 2: { cellWidth: 26 }, 3: { cellWidth: 14, font: 'helvetica' } },
    });
    y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY;

    // A ranked overview table (one line per address) and a full per-source breakdown don't
    // fit the same row: with dozens of contributing seeds on a real case, cramming every
    // "address: pct%" pair into one cell of an already-narrow column wrapped and overflowed
    // the page (see the mangled last page this replaced). Giving each multi-source address
    // its own full-width mini-table below keeps the ranked list compact while still listing
    // every single source at full, untruncated length, sorted highest share first.
    const multiSourceNodes = this.nonZeroTaintedNodes.filter((item) => Object.keys(item.taint_by_source ?? {}).length > 1);
    if (multiSourceNodes.length > 0) {
      y += 4;
      if (y > pageHeight - 40) {
        doc.addPage();
        y = 16;
      }
      sectionTitle('Detaljna raspodela po izvoru');
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(8.5);
      doc.setTextColor(...TEXT_GRAY);
      const breakdownNote = doc.splitTextToSize(
        'Pun spisak doprinosa svakog izvora za adrese cija su sredstva poreklom iz vise razlicitih seed adresa, sortiran od najveceg ka najmanjem procentu.',
        usableWidth,
      );
      doc.text(breakdownNote, marginX, y);
      y += breakdownNote.length * 4 + 4;
      doc.setTextColor(...TEXT_DARK);

      for (const node of multiSourceNodes) {
        const entries = Object.entries(node.taint_by_source ?? {}).sort((a, b) => b[1] - a[1]);
        if (y > pageHeight - 35) {
          doc.addPage();
          y = 16;
        }
        doc.setFont('courier', 'bold');
        doc.setFontSize(9);
        doc.setTextColor(...NAVY);
        doc.text(
          `${node.address}  -  ${node.taint_percentage}% (${TaintAnalysisComponent.riskLabel(node.taint_percentage)})`,
          marginX,
          y,
        );
        y += 5;
        doc.setTextColor(...TEXT_DARK);

        autoTable(doc, {
          startY: y,
          margin: { left: marginX, right: marginX },
          head: [['Izvor (seed adresa)', 'Procenat']],
          body: entries.map(([address, pct]) => [address, `${pct}%`]),
          styles: { fontSize: 8, cellPadding: 1.4, font: 'courier', textColor: TEXT_DARK },
          headStyles: { fillColor: ACCENT, textColor: WHITE, font: 'helvetica', fontStyle: 'bold' },
          alternateRowStyles: { fillColor: [240, 245, 250] },
          columnStyles: { 1: { cellWidth: 26, font: 'helvetica' } },
        });
        y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 6;
      }
    }

    if (this.cashOutCandidates.length > 0) {
      y += 4;
      if (y > pageHeight - 40) {
        doc.addPage();
        y = 16;
      }
      sectionTitle('Verovatne tacke unovcavanja');
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(8.5);
      doc.setTextColor(...TEXT_GRAY);
      const note = doc.splitTextToSize(
        'Primili su zaprljana sredstva i nikad ih dalje nisu poslali u ovoj evidenciji - verovatno mesto gde je novac ' +
          'napustio pracenu mrezu.',
        usableWidth,
      );
      doc.text(note, marginX, y);
      y += note.length * 4 + 3;
      doc.setTextColor(...TEXT_DARK);

      autoTable(doc, {
        startY: y,
        margin: { left: marginX, right: marginX },
        head: [['#', 'Adresa', 'Taint %']],
        body: this.cashOutCandidates.map((item, index) => [String(index + 1), item.address, `${item.taint_percentage}%`]),
        styles: { fontSize: 8, cellPadding: 1.6, font: 'courier', textColor: TEXT_DARK },
        headStyles: { fillColor: TaintAnalysisComponent.PDF_AMBER, textColor: WHITE, font: 'helvetica', fontStyle: 'bold' },
        alternateRowStyles: { fillColor: [253, 246, 227] },
        columnStyles: { 0: { cellWidth: 8, font: 'helvetica' } },
      });
      y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY;
    }

    y += 6;
    if (y > pageHeight - 45) {
      doc.addPage();
      y = 16;
    }
    sectionTitle('Zakljucak analize');
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9.5);
    doc.setTextColor(...TEXT_DARK);
    const conclusionLines = doc.splitTextToSize(this.buildConclusionParagraph(), usableWidth);
    doc.text(conclusionLines, marginX, y);

    const pageCount = doc.getNumberOfPages();
    for (let page = 1; page <= pageCount; page++) {
      doc.setPage(page);
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(8);
      doc.setTextColor(...TEXT_GRAY);
      doc.text(`Lusi v1.0 forensic export | Strana ${page}/${pageCount}`, pageWidth / 2, pageHeight - 8, { align: 'center' });
    }

    doc.save(`${caseSummary.id}_taint_report.pdf`);
  }
}
