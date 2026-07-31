import { CommonModule } from '@angular/common';
import { Component, DestroyRef, ElementRef, HostListener, OnDestroy, OnInit, ViewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { map } from 'rxjs/operators';

import cytoscape, { Core, ElementDefinition } from 'cytoscape';

import { ensureCytoscapeExtensionsRegistered } from '../../core/cytoscape-setup';
import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { ApiService } from '../../core/services/api.service';
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
  TaintTimelineEntry,
  TaintTimelineEvent,
} from '../../models/blockchain-forensics.models';

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

  private cy: Core | null = null;
  private lastClickedHopKey: string | null = null;
  private lastHopClickWentToTarget = false;
  private timelinePlayTimer: ReturnType<typeof setInterval> | null = null;
  private static readonly TAINT_LOW_COLOR = '#1c2333';
  private static readonly TAINT_MID_COLOR = '#f59e0b';
  private static readonly TAINT_HIGH_COLOR = '#ff4d4d';
  private static readonly TAINT_MID_THRESHOLD = 50;
  private static readonly TOP_TAINTED_PREVIEW_LIMIT = 15;

  constructor(
    private readonly state: AnalysisStateService,
    private readonly api: ApiService,
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
    this.stopTimelinePlay();
    this.timelineEnabled = false;
    this.timelinePosition = 1;
    this.timelineMaxRank = 0;
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

  private get nonZeroTaintedNodes(): Array<{ address: string; taint_percentage: number; is_taint_seed: boolean }> {
    return (this.taintResult?.results ?? []).filter((item) => item.taint_percentage > 0);
  }

  get topTaintedNodes(): Array<{ address: string; taint_percentage: number; is_taint_seed: boolean }> {
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
    this.applyPathHighlight(String(node.id));
    this.loadAddressEnrichment();
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
    const hops = maxRank == null ? allHops : allHops.filter((hop) => hop.rank <= maxRank);

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

  /** Reveals nodes/edges as of the current timeline position AND recolors every visible
   * node to what its taint % actually was at that point - unlike the /graph page's
   * timeline (which only toggles visibility), the whole point here is watching the
   * percentage itself evolve hop by hop, so the color has to change too, not just what's
   * shown. Also applies the "hide non-tainted" filter, on or off the timeline alike -
   * hiding a node here hides its connected edges too (cytoscape does this automatically
   * for any node with display:none), so untainted edges never need separate handling. */
  private applyTaintTimeline(): void {
    if (!this.cy) {
      return;
    }

    if (!this.timelineEnabled) {
      this.cy.nodes().forEach((node) => {
        const finalPct = Number(node.data('finalTaintPercentage') ?? 0);
        node.data('taint_percentage', finalPct);
        node.data('displayLabel', this.hasRunTaint ? `${finalPct}%` : '');
        node.style('display', this.hideNonTaintedNodes && finalPct <= this.taintHideThreshold ? 'none' : 'element');
      });
      this.cy.edges().forEach((edge) => {
        edge.style('display', 'element');
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
        node.data('displayLabel', `${pct}%`);
      }
      const hiddenByFilter = this.hideNonTaintedNodes && pct <= this.taintHideThreshold;
      node.style('display', revealed && !hiddenByFilter ? 'element' : 'none');
    });
    this.cy.edges().forEach((edge) => {
      const rank = edge.data('chronoRank');
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
            // No address/hash text on canvas at all - on a few-hundred-node graph it's
            // pure clutter, and the full address is always one click away in the
            // inspector panel. Just the percentage (once analysis has run) stays, since
            // color alone isn't always a safe read for two nearby values (e.g. 66.67%
            // vs 100% looked too similar in testing). Kept in its own field (not baked
            // into a static label string) so the timeline scrubber can update it live.
            displayLabel: this.hasRunTaint ? `${taintPercentage}%` : '',
          },
          classes: isSeed ? 'taint-seed' : '',
        } as ElementDefinition;
      }),
      ...this.graph.links.map((link) => {
        const totalAmount = Number(link.total_amount ?? link.amount ?? 0);
        return {
          data: {
            ...link,
            id: `${link.source}__${link.target}__${Math.round(totalAmount * 1000)}`,
            totalAmount,
            chronoRank: this.taintResult?.edge_first_rank?.[`${link.source}__${link.target}`] ?? null,
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
            'arrow-scale': 0.55,
            'curve-style': 'bezier',
            opacity: 0.6,
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
      ],
    });

    this.cy.on('tap', 'node', (event) => {
      const nodeData = event.target.data() as GraphNodeData;
      if (this.isSeedSelectionMode) {
        this.toggleSeed(String(nodeData.id));
      } else {
        this.selectNode(nodeData);
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
}
