import { CommonModule } from '@angular/common';
import { Component, DestroyRef, ElementRef, HostListener, OnDestroy, OnInit, ViewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';
import { distinctUntilChanged, map } from 'rxjs/operators';

import cytoscape, { Core, ElementDefinition } from 'cytoscape';
import fcose from 'cytoscape-fcose';
import layoutUtilities from 'cytoscape-layout-utilities';

cytoscape.use(fcose);
cytoscape.use(layoutUtilities);

import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { ApiService } from '../../core/services/api.service';
import {
  AddressEnrichment,
  AddressType,
  CaseSummary,
  KnownEntityCategory,
  EvidenceEntry,
  GraphLinkData,
  GraphNodeData,
  NodeLinkGraphResponse,
} from '../../models/blockchain-forensics.models';

@Component({
  selector: 'app-graph-visualization',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './graph-visualization.component.html',
  styleUrl: './graph-visualization.component.scss',
})
export class GraphVisualizationComponent implements OnInit, OnDestroy {
  @ViewChild('graphCanvas', { static: true })
  protected graphCanvas!: ElementRef<HTMLDivElement>;

  protected graph: NodeLinkGraphResponse | null = null;
  protected selectedNode: GraphNodeData | null = null;
  protected hasLoadedGraph = false;
  protected activeCase: CaseSummary | null = null;
  protected isLoadingCaseGraph = false;
  protected caseGraphError: string | null = null;
  protected evidenceOptions: EvidenceEntry[] = [];
  protected selectedEvidence: string | null = null;
  protected addressEnrichment: AddressEnrichment | null = null;
  protected isLayoutRunning = false;
  protected isEnrichingAddress = false;
  protected timelineEnabled = false;
  protected timelinePosition = 1;
  protected timelineMaxRank = 0;
  protected isTimelinePlaying = false;
  protected timelineSpeed = 1;
  protected readonly timelineSpeedOptions = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
  protected timelineSubtitlesEnabled = true;
  protected timelineFollowEnabled = true;
  protected deadEndFilterEnabled = false;
  protected fundingSourceFilterEnabled = false;
  protected showAllSenders = false;
  protected showAllRecipients = false;

  /** A node with hundreds of counterparties (e.g. a deposit hub) would otherwise render
   * hundreds of <dd> rows in the inspector panel, forcing the whole page to scroll past
   * the graph canvas just to reach the rest of a single node's details - previewing the
   * top few and letting the investigator expand on demand keeps the panel usable. */
  private static readonly COUNTERPARTY_PREVIEW_LIMIT = 15;

  private cy: Core | null = null;
  /** Ids of nodes that only ever received funds within this case's evidence (out-degree
   * = 0) and aren't already flagged as suspicious - recomputed whenever the graph loads,
   * so the "Sakrij slepe ulice" toggle can hide/show them without recomputation on every
   * click. */
  private deadEndNodeIds = new Set<string>();
  /** The mirror image of deadEndNodeIds: nodes that only ever sent funds (in-degree = 0)
   * and aren't flagged as suspicious - common in single-address "deposits only" evidence,
   * where every counterparty is a one-way funding source rather than a forwarding hop. */
  private fundingSourceNodeIds = new Set<string>();
  private layoutIndicatorTimer: ReturnType<typeof setTimeout> | null = null;
  private timelinePlayTimer: ReturnType<typeof setInterval> | null = null;
  /** Index 0 = the label for rank 1, etc. - so the slider can show "do transakcije #N
   * (datum)" without re-deriving it from the graph on every drag tick. */
  private timelineDates: string[] = [];
  /** Index 0 = the "pošiljalac → primalac · iznos · datum" caption for rank 1, etc. -
   * the subtitle text shown over the canvas during playback. */
  private timelineCaptions: string[] = [];
  /** Ranks whose transaction touches a flagged node (crna lista/visok rizik/anomalija/
   * peel lanac/skok lanca) - lets "Sledeća sumnjiva transakcija" jump straight there
   * instead of stepping through every single transaction on a large graph. */
  private timelineSuspiciousRanks = new Set<number>();
  /** Index 0 = a "⏱ pauza od ..." note for rank 1, or null if the gap since the
   * previous transaction wasn't notably large - a dormant wallet suddenly reactivating
   * is itself a forensic signal, easy to miss when every step looks equally spaced. */
  private timelineGapNotes: (string | null)[] = [];
  /** Index 0 = running total transferred up to and including rank 1, etc. - shows how
   * much value has moved through this trail by the current point in the playback. */
  private timelineCumulativeTotals: number[] = [];

  constructor(
    private readonly state: AnalysisStateService,
    private readonly api: ApiService,
    private readonly destroyRef: DestroyRef,
  ) {}

  ngOnInit(): void {
    // Rendered from analytics$, not graph$: the plain /graph response has no
    // blacklist/risk/anomaly/peel-chain data at all (that's only computed by the
    // analytics pipeline), so coloring nodes from it would never show any warnings.
    this.state.analytics$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((graph) => {
      this.graph = graph;
      this.hasLoadedGraph = Boolean(graph);
      this.renderGraph();
    });

    this.state.selectedNode$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((node) => {
      this.selectedNode = node;
      this.showAllSenders = false;
      this.showAllRecipients = false;
      this.syncSelection();
      this.loadAddressEnrichment();
    });

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
        if (this.activeCase) {
          this.loadEvidenceOptions(this.activeCase.id);
          this.loadActiveCaseGraph();
        }
      });
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
    this.loadActiveCaseGraph();
  }

  loadActiveCaseGraph(): void {
    const caseId = this.activeCase?.id;
    if (!caseId) {
      return;
    }

    if (!this.activeCase?.evidence_count) {
      this.graph = null;
      this.state.setGraph(null);
      this.state.setAnalytics(null);
      this.caseGraphError = null;
      return;
    }

    this.isLoadingCaseGraph = true;
    this.caseGraphError = null;

    forkJoin({
      graph: this.api.getCaseGraph(caseId, this.selectedEvidence),
      analytics: this.api.runCaseAnalytics(caseId, this.selectedEvidence),
    }).subscribe({
      next: ({ graph, analytics }) => {
        this.state.setGraph(graph);
        this.state.setAnalytics(analytics);
        this.state.ensureValidSelectedNode(analytics.nodes);
        this.isLoadingCaseGraph = false;
      },
      error: () => {
        this.isLoadingCaseGraph = false;
        this.caseGraphError = 'Neuspešno učitavanje grafa za izabrani slučaj.';
      },
    });
  }

  ngOnDestroy(): void {
    this.cy?.destroy();
    this.cy = null;
    if (this.layoutIndicatorTimer !== null) {
      clearTimeout(this.layoutIndicatorTimer);
      this.layoutIndicatorTimer = null;
    }
    this.stopTimelinePlay();
  }

  @HostListener('window:resize')
  onResize(): void {
    this.cy?.resize();
    this.cy?.fit(undefined, 80);
  }

  fitWholeGraph(): void {
    this.cy?.fit(undefined, 60);
  }

  get timelineCurrentDate(): string | null {
    if (this.timelinePosition < 1 || this.timelinePosition > this.timelineDates.length) {
      return null;
    }
    return this.timelineDates[this.timelinePosition - 1];
  }

  get timelineCurrentCaption(): string | null {
    if (this.timelinePosition < 1 || this.timelinePosition > this.timelineCaptions.length) {
      return null;
    }
    return this.timelineCaptions[this.timelinePosition - 1];
  }

  get timelineCurrentGapNote(): string | null {
    if (this.timelinePosition < 1 || this.timelinePosition > this.timelineGapNotes.length) {
      return null;
    }
    return this.timelineGapNotes[this.timelinePosition - 1];
  }

  get timelineCurrentCumulativeTotal(): number | null {
    if (this.timelinePosition < 1 || this.timelinePosition > this.timelineCumulativeTotals.length) {
      return null;
    }
    return this.timelineCumulativeTotals[this.timelinePosition - 1];
  }

  toggleTimelineSubtitles(): void {
    this.timelineSubtitlesEnabled = !this.timelineSubtitlesEnabled;
  }

  toggleTimelineFollow(): void {
    this.timelineFollowEnabled = !this.timelineFollowEnabled;
  }

  get hasNextSuspiciousTransaction(): boolean {
    for (let rank = this.timelinePosition + 1; rank <= this.timelineMaxRank; rank++) {
      if (this.timelineSuspiciousRanks.has(rank)) {
        return true;
      }
    }
    return false;
  }

  skipToNextSuspiciousTransaction(): void {
    for (let rank = this.timelinePosition + 1; rank <= this.timelineMaxRank; rank++) {
      if (this.timelineSuspiciousRanks.has(rank)) {
        this.timelinePosition = rank;
        this.applyVisibilityFilters();
        return;
      }
    }
  }

  toggleTimeline(): void {
    this.timelineEnabled = !this.timelineEnabled;
    if (!this.timelineEnabled) {
      this.stopTimelinePlay();
    }
    this.applyVisibilityFilters();
  }

  onTimelinePositionChange(value: number): void {
    this.timelinePosition = value;
    this.applyVisibilityFilters();
  }

  /** Number of "dead-end" nodes in the currently loaded graph - shown on the toggle
   * button so an investigator knows upfront whether the filter would do anything. */
  get deadEndNodeCount(): number {
    return this.deadEndNodeIds.size;
  }

  toggleDeadEndFilter(): void {
    this.deadEndFilterEnabled = !this.deadEndFilterEnabled;
    this.applyVisibilityFilters();
  }

  /** Number of one-way funding-source nodes in the currently loaded graph - the mirror
   * stat to deadEndNodeCount, shown on the funding-source toggle button. */
  get fundingSourceNodeCount(): number {
    return this.fundingSourceNodeIds.size;
  }

  toggleFundingSourceFilter(): void {
    this.fundingSourceFilterEnabled = !this.fundingSourceFilterEnabled;
    this.applyVisibilityFilters();
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

  /** Changing speed mid-playback restarts the interval at the new rate rather than
   * waiting for the current tick to elapse - it should feel immediate, like YouTube. */
  onTimelineSpeedChange(speed: number): void {
    this.timelineSpeed = speed;
    if (this.isTimelinePlaying) {
      this.startTimelinePlayInterval();
    }
  }

  private startTimelinePlayInterval(): void {
    if (this.timelinePlayTimer !== null) {
      clearInterval(this.timelinePlayTimer);
    }
    const baseIntervalMs = 700;
    this.timelinePlayTimer = setInterval(() => {
      if (this.timelinePosition >= this.timelineMaxRank) {
        this.stopTimelinePlay();
        return;
      }
      this.timelinePosition += 1;
      this.applyVisibilityFilters();
    }, baseIntervalMs / this.timelineSpeed);
  }

  private stopTimelinePlay(): void {
    if (this.timelinePlayTimer !== null) {
      clearInterval(this.timelinePlayTimer);
      this.timelinePlayTimer = null;
    }
    this.isTimelinePlaying = false;
  }

  /** Reveals the graph as of the current timeline position, and separately hides any
   * "dead-end" nodes when that filter is on - without re-running the layout, so node/edge
   * positions stay put and only visibility toggles (hiding a node's display also hides its
   * connected edges automatically, so dead-end nodes disappear cleanly). */
  private applyVisibilityFilters(): void {
    if (!this.cy) {
      return;
    }

    const position = this.timelinePosition;
    this.cy.nodes().forEach((node) => {
      const rank = node.data('chronoRank');
      const timelineVisible = !this.timelineEnabled || rank == null || rank <= position;
      const hiddenAsDeadEnd = this.deadEndFilterEnabled && this.deadEndNodeIds.has(node.id());
      const hiddenAsFundingSource = this.fundingSourceFilterEnabled && this.fundingSourceNodeIds.has(node.id());
      node.style('display', timelineVisible && !hiddenAsDeadEnd && !hiddenAsFundingSource ? 'element' : 'none');
    });
    this.cy.edges().forEach((edge) => {
      const rank = edge.data('chronoRank');
      const timelineVisible = !this.timelineEnabled || (rank != null && rank <= position);
      edge.style('display', timelineVisible ? 'element' : 'none');
    });

    this.focusOnCurrentTransaction();
  }

  /** Pans/zooms to the just-revealed transaction, but only when it's actually outside
   * the current view - if it's already visible, the camera stays put so following the
   * trail doesn't feel jumpy, especially at faster playback speeds. */
  private focusOnCurrentTransaction(): void {
    if (!this.cy || !this.timelineFollowEnabled) {
      return;
    }

    const currentEdge = this.cy.edges().filter((edge) => edge.data('chronoRank') === this.timelinePosition);
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

  /** Below this, a funding transfer looks like "just enough for gas" rather than a real
   * transfer of value - a classic pattern for activating a fresh mule/burner wallet before
   * routing the real illicit funds through it. */
  private static readonly DUST_FUNDING_THRESHOLD_ETH = 0.02;

  get isDustFunding(): boolean {
    const amount = this.addressEnrichment?.funding_amount_eth;
    return amount != null && amount > 0 && amount < GraphVisualizationComponent.DUST_FUNDING_THRESHOLD_ETH;
  }

  /** Is the funding source itself a blacklisted address in this case's own graph? A direct
   * hit means the selected node is one hop away from an entity we already flagged. */
  get fundingSourceBlacklistMatch(): GraphNodeData | null {
    const fundingAddress = this.addressEnrichment?.funding_source;
    const nodes = this.state.analyticsSnapshot?.nodes;
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

  /** Senders that funded the selected node *within this case's own evidence* - distinct
   * from "Izvor sredstava" (that address's first-ever on-chain transaction, its whole
   * history). A node can show received funds here from several senders even when the
   * on-chain funding source is n/a, because that first-ever transaction predates or
   * otherwise falls outside what this case's evidence actually captured. */
  get caseIncomingSenders(): Array<{ address: string; amount: number }> {
    return this.aggregateCaseCounterparties('incoming');
  }

  /** Everyone the selected node sent funds to within this case's own evidence - the
   * outgoing mirror of caseIncomingSenders, for "where did this node's money go". */
  get caseOutgoingRecipients(): Array<{ address: string; amount: number }> {
    return this.aggregateCaseCounterparties('outgoing');
  }

  get visibleIncomingSenders(): Array<{ address: string; amount: number }> {
    const senders = this.caseIncomingSenders;
    return this.showAllSenders ? senders : senders.slice(0, GraphVisualizationComponent.COUNTERPARTY_PREVIEW_LIMIT);
  }

  get hiddenSendersCount(): number {
    return Math.max(0, this.caseIncomingSenders.length - GraphVisualizationComponent.COUNTERPARTY_PREVIEW_LIMIT);
  }

  toggleShowAllSenders(): void {
    this.showAllSenders = !this.showAllSenders;
  }

  get visibleOutgoingRecipients(): Array<{ address: string; amount: number }> {
    const recipients = this.caseOutgoingRecipients;
    return this.showAllRecipients ? recipients : recipients.slice(0, GraphVisualizationComponent.COUNTERPARTY_PREVIEW_LIMIT);
  }

  get hiddenRecipientsCount(): number {
    return Math.max(0, this.caseOutgoingRecipients.length - GraphVisualizationComponent.COUNTERPARTY_PREVIEW_LIMIT);
  }

  toggleShowAllRecipients(): void {
    this.showAllRecipients = !this.showAllRecipients;
  }

  private aggregateCaseCounterparties(direction: 'incoming' | 'outgoing'): Array<{ address: string; amount: number }> {
    if (!this.selectedNode) {
      return [];
    }

    const nodeId = String(this.selectedNode.id);
    const links = this.graph?.links ?? [];
    const totals = new Map<string, number>();

    for (const link of links) {
      const matches = direction === 'incoming' ? String(link.target) === nodeId : String(link.source) === nodeId;
      if (!matches) {
        continue;
      }
      const counterparty = String(direction === 'incoming' ? link.source : link.target);
      const amount = Number(link.total_amount ?? link.amount ?? 0);
      totals.set(counterparty, (totals.get(counterparty) ?? 0) + amount);
    }

    return Array.from(totals.entries())
      .map(([address, amount]) => ({ address, amount }))
      .sort((a, b) => b.amount - a.amount);
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

  get graphSummary(): string {
    if (!this.graph) {
      return 'Čekanje na podatke grafa sa servera.';
    }

    return `${this.graph.nodes.length} čvorova, ${this.graph.links.length} veza, generisano ${this.graph.generated_at ?? 'n/a'}`;
  }

  get nodeCount(): number {
    return this.graph?.nodes.length ?? 0;
  }

  get edgeCount(): number {
    return this.graph?.links.length ?? 0;
  }

  get selectedNodeFlags(): string[] {
    if (!this.selectedNode) {
      return [];
    }

    const flags: string[] = [];
    if (this.selectedNode.blacklist_flag) {
      flags.push('Crna lista');
    }
    if (this.selectedNode.peel_chain_flag) {
      flags.push('Peel lanac');
    }
    if (Number(this.selectedNode.risk_score ?? 0) >= 70) {
      flags.push('Visok rizik');
    }
    if (this.selectedNode.chain_hop_flag) {
      flags.push('Skok lanca');
    }
    if (this.selectedNode.anomaly_flag) {
      flags.push('Anomalija');
    }
    return flags;
  }

  /** Same criteria as selectedNodeFlags (crna lista/visok rizik/anomalija/peel lanac/
   * skok lanca) but as a plain predicate, for scanning every node while building the
   * timeline's suspicious-rank index rather than just the currently selected one. */
  private isNodeSuspicious(node: GraphNodeData | undefined): boolean {
    if (!node) {
      return false;
    }
    return (
      Boolean(node.blacklist_flag) ||
      Boolean(node.peel_chain_flag) ||
      Number(node.risk_score ?? 0) >= 70 ||
      Boolean(node.chain_hop_flag) ||
      Boolean(node.anomaly_flag)
    );
  }

  /** In/out-degree per node within this graph's own links, shared by the dead-end and
   * funding-source computations below so both can be derived from a single pass. */
  private computeNodeDegrees(graph: NodeLinkGraphResponse): { inDegree: Map<string, number>; outDegree: Map<string, number> } {
    const inDegree = new Map<string, number>();
    const outDegree = new Map<string, number>();
    for (const link of graph.links) {
      const source = String(link.source);
      const target = String(link.target);
      outDegree.set(source, (outDegree.get(source) ?? 0) + 1);
      inDegree.set(target, (inDegree.get(target) ?? 0) + 1);
    }
    return { inDegree, outDegree };
  }

  /** "Slepa ulica": čvor koji je u okviru dokaza ovog slučaja primio sredstva bar
   * jednom (in-degree > 0) ali ih nikada nije prosledio dalje (out-degree = 0), i koji
   * pritom nije već označen kao sumnjiv (crna lista/visok rizik/anomalija/peel
   * lanac/skok lanca) - takvi čvorovi retko nose dodatnu forenzičku vrednost i na
   * velikim grafovima najviše doprinose "hairball" efektu. */
  private computeDeadEndNodeIds(graph: NodeLinkGraphResponse): Set<string> {
    const { inDegree, outDegree } = this.computeNodeDegrees(graph);

    const deadEnds = new Set<string>();
    for (const node of graph.nodes) {
      const id = String(node.id);
      const hasIncoming = (inDegree.get(id) ?? 0) > 0;
      const hasOutgoing = (outDegree.get(id) ?? 0) > 0;
      if (hasIncoming && !hasOutgoing && !this.isNodeSuspicious(node)) {
        deadEnds.add(id);
      }
    }
    return deadEnds;
  }

  /** "Izvor sredstava": mirror slučaj slepe ulice - čvor koji je samo slao sredstva
   * (out-degree > 0) a nikada ih nije primio (in-degree = 0), i nije već označen kao
   * sumnjiv. Tipično se javlja kod evidencije "sve transakcije jedne adrese" gde je
   * praćena adresa hub, a svaka druga strana se pojavljuje samo kao jednosmerni
   * pošiljalac (nikad kao primalac) - taj slučaj sam po sebi nema "slepih ulica" u
   * gornjem smislu, pa je ovaj filter njegov prirodni komplement. */
  private computeFundingSourceNodeIds(graph: NodeLinkGraphResponse): Set<string> {
    const { inDegree, outDegree } = this.computeNodeDegrees(graph);

    const fundingSources = new Set<string>();
    for (const node of graph.nodes) {
      const id = String(node.id);
      const hasIncoming = (inDegree.get(id) ?? 0) > 0;
      const hasOutgoing = (outDegree.get(id) ?? 0) > 0;
      if (hasOutgoing && !hasIncoming && !this.isNodeSuspicious(node)) {
        fundingSources.add(id);
      }
    }
    return fundingSources;
  }

  private renderGraph(): void {
    if (!this.graphCanvas) {
      return;
    }

    this.cy?.destroy();
    this.cy = null;
    if (this.layoutIndicatorTimer !== null) {
      clearTimeout(this.layoutIndicatorTimer);
      this.layoutIndicatorTimer = null;
    }
    this.isLayoutRunning = false;
    this.stopTimelinePlay();
    this.timelineEnabled = false;
    this.timelinePosition = 1;

    if (!this.graph) {
      this.deadEndNodeIds = new Set();
      this.fundingSourceNodeIds = new Set();
      return;
    }

    const elements = this.buildElements(this.graph);
    const nodeCount = this.graph.nodes.length;
    // Smaller nodes leave more breathing room for the layout to actually spread a
    // dense graph out - at hundreds of nodes, keeping the small-graph node size would
    // force overlap no matter how strong the layout's separation forces are.
    const sizeScale = nodeCount > 150 ? 0.4 : nodeCount > 60 ? 0.65 : 1;
    const minNodeSize = Math.round(36 * sizeScale);
    const maxNodeSize = Math.round(72 * sizeScale);
    const graphStyles: any = [
      {
        selector: 'core',
        style: {
          'selection-box-color': '#7dd3fc',
          'selection-box-border-color': '#7dd3fc',
          'active-bg-opacity': 0.12,
          'active-bg-color': '#7dd3fc',
        },
      },
      {
        selector: 'node',
        style: {
          label: 'data(label)',
          'text-valign': 'center',
          'text-halign': 'center',
          'font-size': 11,
          'min-zoomed-font-size': 8,
          color: '#ecf2ff',
          'text-outline-width': 2,
          'text-outline-color': '#07111f',
          'background-color': '#4f8cff',
          'border-width': 2,
          'border-color': '#9bd1ff',
          width: `mapData(totalAmount, 0, 250, ${minNodeSize}, ${maxNodeSize})`,
          height: `mapData(totalAmount, 0, 250, ${minNodeSize}, ${maxNodeSize})`,
        },
      },
      // Fill-color rules are ordered least → most severe: cytoscape applies later
      // rules last, so a node matching several flags shows the most severe color
      // while shape (hexagon/diamond) and border-style (dashed) still layer on
      // independently - nothing is hidden when multiple flags apply at once.
      {
        selector: 'node.anomaly-node',
        style: {
          'background-color': '#c98500',
          'border-color': '#ffd479',
          'border-style': 'dashed',
          'border-width': 3,
        },
      },
      {
        selector: 'node.bridge-node',
        style: {
          'background-color': '#199e70',
          'border-color': '#6ee7c4',
          shape: 'hexagon',
        },
      },
      {
        selector: 'node.high-risk',
        style: {
          'background-color': '#d95926',
          'border-color': '#ffab7a',
        },
      },
      {
        selector: 'node.peel-chain-node',
        style: {
          'background-color': '#9085e9',
          'border-color': '#c4bdf7',
          shape: 'diamond',
        },
      },
      {
        selector: 'node.blacklisted',
        style: {
          'background-color': '#e66767',
          'border-color': '#f8a8a8',
        },
      },
      // Cluster membership uses outline-* (a ring drawn outside the border), a property
      // independent of background-color/border-color/shape - so it layers on top of any
      // severity color or peel-chain/bridge shape above without ever hiding it.
      {
        selector: 'node.clustered-node',
        style: {
          'outline-width': 4,
          'outline-color': '#22d3ee',
          'outline-style': 'dashed',
          'outline-offset': 2,
        },
      },
      {
        selector: 'node:selected',
        style: {
          'overlay-opacity': 0.16,
          'overlay-color': '#7dd3fc',
          'border-width': 4,
        },
      },
      {
        selector: 'edge',
        style: {
          width: 'mapData(totalAmount, 0, 250, 1, 6)',
          'line-color': '#6ea8fe',
          'target-arrow-color': '#6ea8fe',
          'target-arrow-shape': 'triangle',
          'curve-style': 'bezier',
          opacity: 0.72,
          label: 'data(label)',
          'font-size': 9,
          'min-zoomed-font-size': 7,
          color: '#b3c3df',
          'text-background-color': '#07111f',
          'text-background-opacity': 0.8,
          'text-background-padding': '2px',
        },
      },
      {
        selector: 'edge.bridge-edge',
        style: {
          'line-style': 'dashed',
        },
      },
      // Chronologically first/last transaction in the graph - the temporal bounds of
      // this evidence, a natural place to start (or currently end) an investigation.
      {
        selector: 'edge.first-transaction',
        style: {
          'line-color': '#4ade80',
          'target-arrow-color': '#4ade80',
          width: 4,
          opacity: 1,
        },
      },
      {
        selector: 'edge.last-transaction',
        style: {
          'line-color': '#fbbf24',
          'target-arrow-color': '#fbbf24',
          width: 4,
          opacity: 1,
        },
      },
    ];

    this.cy = cytoscape({
      container: this.graphCanvas.nativeElement,
      elements,
      wheelSensitivity: 0.2,
      layout: {
        name: 'fcose',
        // 'proof' quality (slow cooling) looked meaningfully better in testing, but on
        // a few hundred nodes it can run for a minute or more - impractical to wait for.
        // 'default' converges in a few seconds; the larger separation/repulsion below
        // still meaningfully improves it within that time budget.
        quality: 'default',
        randomize: true,
        animate: false,
        fit: true,
        padding: 60,
        nodeSeparation: nodeCount > 60 ? 150 : 100,
        nodeRepulsion: nodeCount > 60 ? 10000 : 6000,
        idealEdgeLength: nodeCount > 60 ? 100 : 80,
      } as any,
      style: graphStyles,
    });

    // fcose's 'layoutstop' doesn't fire reliably here (observed hanging indefinitely,
    // likely tied to animate:false plus the case/evidence load path re-rendering the
    // graph a second time in quick succession) - a fixed estimate is simpler and more
    // predictable than chasing that event.
    if (nodeCount > 60) {
      this.isLayoutRunning = true;
      this.layoutIndicatorTimer = setTimeout(() => {
        this.isLayoutRunning = false;
        this.layoutIndicatorTimer = null;
      }, 6000);
    }

    this.cy.on('tap', 'node', (event) => {
      const nodeData = event.target.data() as GraphNodeData;
      this.selectedNode = nodeData;
      this.state.setSelectedNode(nodeData);
    });

    this.applyVisibilityFilters();
    this.syncSelection();
  }

  private syncSelection(): void {
    if (!this.cy || !this.selectedNode) {
      return;
    }

    const node = this.cy.$id(this.selectedNode.id);
    if (node.nonempty()) {
      this.cy.elements().unselect();
      node.select();
      this.cy.center(node);
      this.cy.fit(node, 120);
    }
  }

  /** Full-length addresses as node labels are unreadable clutter once a graph has more
   * than a handful of nodes - the full value is still always shown in the "Detalji čvora"
   * panel on click, this is purely for keeping the canvas itself legible. */
  private truncateAddress(value: string): string {
    if (value.length <= 14) {
      return value;
    }
    return `${value.slice(0, 6)}…${value.slice(-4)}`;
  }

  private buildElements(graph: NodeLinkGraphResponse): ElementDefinition[] {
    this.deadEndNodeIds = this.computeDeadEndNodeIds(graph);
    this.fundingSourceNodeIds = this.computeFundingSourceNodeIds(graph);

    const { rank: chronologicalRank, count: rankedCount, dates, captions, gapNotes, cumulativeTotals } =
      this.buildChronologicalRank(graph.links);
    this.timelineMaxRank = rankedCount;
    this.timelineDates = dates;
    this.timelineCaptions = captions;
    this.timelineGapNotes = gapNotes;
    this.timelineCumulativeTotals = cumulativeTotals;
    if (this.timelinePosition > rankedCount) {
      this.timelinePosition = rankedCount || 1;
    }

    // A node's own "first appearance" rank = the earliest rank among the edges that
    // touch it, so the timeline can reveal a node exactly when it first gets involved.
    const nodeFirstRank = new Map<string, number>();
    for (const link of graph.links) {
      const rank = chronologicalRank.get(link);
      if (rank == null) {
        continue;
      }
      for (const endpoint of [String(link.source), String(link.target)]) {
        const current = nodeFirstRank.get(endpoint);
        if (current == null || rank < current) {
          nodeFirstRank.set(endpoint, rank);
        }
      }
    }

    const nodeById = new Map(graph.nodes.map((node) => [String(node.id), node]));
    const suspiciousRanks = new Set<number>();
    for (const link of graph.links) {
      const rank = chronologicalRank.get(link);
      if (rank == null) {
        continue;
      }
      if (this.isNodeSuspicious(nodeById.get(String(link.source))) || this.isNodeSuspicious(nodeById.get(String(link.target)))) {
        suspiciousRanks.add(rank);
      }
    }
    this.timelineSuspiciousRanks = suspiciousRanks;

    const nodes = graph.nodes.map((node) => {
      const classes = this.nodeClasses(node).join(' ');
      return {
        data: {
          ...node,
          totalAmount: Number(node.risk_score ?? 0),
          label: this.truncateAddress(String(node.label ?? node.address ?? node.id)),
          chronoRank: nodeFirstRank.get(String(node.id)) ?? null,
        },
        classes,
      } as ElementDefinition;
    });

    const links = graph.links.map((link) => {
      const rank = chronologicalRank.get(link);
      const isFirst = rank === 1;
      const isLast = rank != null && rank === rankedCount && rankedCount > 1;
      const classes = this.linkClasses(link, isFirst, isLast).join(' ');
      const amountLabel = Number(link.total_amount ?? link.amount ?? 0).toFixed(2);
      return {
        data: {
          ...link,
          id: `${link.source}__${link.target}__${Math.round(Number(link.total_amount ?? link.amount ?? 0) * 1000)}`,
          label: rank != null ? `#${rank} · ${amountLabel}` : amountLabel,
          totalAmount: Number(link.total_amount ?? link.amount ?? 0),
          chronoRank: rank ?? null,
        },
        classes,
      } as ElementDefinition;
    });

    return [...nodes, ...links];
  }

  /** Chronological rank (1 = earliest) of each link by its first-seen timestamp, so the
   * very first and very last transactions in time can be called out distinctly on the
   * graph - the temporal bounds of the activity captured in this case, a natural
   * "where did this trail begin/end (so far)" anchor for an investigation. Links without
   * a parseable timestamp are left unranked rather than guessed at. */
  private buildChronologicalRank(links: GraphLinkData[]): {
    rank: Map<GraphLinkData, number>;
    count: number;
    dates: string[];
    captions: string[];
    gapNotes: (string | null)[];
    cumulativeTotals: number[];
  } {
    const dated = links
      .map((link) => ({ link, time: link.first_seen ? Date.parse(link.first_seen) : NaN }))
      .filter((entry) => !Number.isNaN(entry.time))
      .sort((a, b) => a.time - b.time);

    const gaps: number[] = [];
    for (let i = 1; i < dated.length; i++) {
      gaps.push(dated[i].time - dated[i - 1].time);
    }
    const averageGapMs = gaps.length > 0 ? gaps.reduce((sum, gap) => sum + gap, 0) / gaps.length : 0;
    // A gap only counts as "notable" (worth flagging as a dormancy period) if it's both
    // clearly out of the ordinary for this specific trail AND large in absolute terms -
    // otherwise a tight, evenly-spaced trail would flag its own normal jitter.
    const notableGapThresholdMs = Math.max(averageGapMs * 3, 6 * 60 * 60 * 1000);

    const rank = new Map<GraphLinkData, number>();
    const dates: string[] = [];
    const captions: string[] = [];
    const gapNotes: (string | null)[] = [];
    const cumulativeTotals: number[] = [];
    let runningTotal = 0;

    dated.forEach((entry, index) => {
      rank.set(entry.link, index + 1);
      const dateLabel = new Date(entry.time).toLocaleString();
      dates.push(dateLabel);

      const source = String(entry.link.source);
      const target = String(entry.link.target);
      const amount = Number(entry.link.total_amount ?? entry.link.amount ?? 0);
      captions.push(`${source} → ${target} · ${amount.toFixed(2)} · ${dateLabel}`);

      runningTotal += amount;
      cumulativeTotals.push(runningTotal);

      if (index === 0) {
        gapNotes.push(null);
      } else {
        const gap = entry.time - dated[index - 1].time;
        gapNotes.push(gap >= notableGapThresholdMs ? `⏱ Pauza od ${this.formatGapDuration(gap)} pre ove transakcije` : null);
      }
    });

    return { rank, count: dated.length, dates, captions, gapNotes, cumulativeTotals };
  }

  /** Human-readable duration for the gap note - coarse on purpose (one unit, one
   * decimal), since the point is "a dormant wallet reactivated", not precision timing. */
  private formatGapDuration(ms: number): string {
    const minutes = ms / 60_000;
    const hours = minutes / 60;
    const days = hours / 24;
    const months = days / 30.44;
    const years = days / 365.25;

    if (years >= 1) {
      return `${years.toFixed(1)} god.`;
    }
    if (months >= 1) {
      return `${months.toFixed(1)} mes.`;
    }
    if (days >= 1) {
      return `${days.toFixed(1)} d.`;
    }
    if (hours >= 1) {
      return `${hours.toFixed(1)} h.`;
    }
    return `${Math.round(minutes)} min.`;
  }

  private nodeClasses(node: GraphNodeData): string[] {
    const classes: string[] = [];
    const risk = Number(node.risk_score ?? 0);

    if (Boolean(node.blacklist_flag)) {
      classes.push('blacklisted');
    }
    if (risk >= 70) {
      classes.push('high-risk');
    }
    if (Boolean(node.chain_hop_flag)) {
      classes.push('bridge-node');
    }
    if (Boolean(node.peel_chain_flag)) {
      classes.push('peel-chain-node');
    }
    if (Boolean(node.anomaly_flag)) {
      classes.push('anomaly-node');
    }
    if (node.cluster_id) {
      classes.push('clustered-node');
    }

    return classes;
  }

  private linkClasses(link: GraphLinkData, isFirst: boolean, isLast: boolean): string[] {
    const classes: string[] = [];
    if (link.bridge_edge) {
      classes.push('bridge-edge');
    }
    if (isFirst) {
      classes.push('first-transaction');
    }
    if (isLast) {
      classes.push('last-transaction');
    }
    return classes;
  }
}