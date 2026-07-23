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

  private cy: Core | null = null;
  private layoutIndicatorTimer: ReturnType<typeof setTimeout> | null = null;

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
  }

  @HostListener('window:resize')
  onResize(): void {
    this.cy?.resize();
    this.cy?.fit(undefined, 80);
  }

  fitWholeGraph(): void {
    this.cy?.fit(undefined, 60);
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

    if (!this.graph) {
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
    const nodes = graph.nodes.map((node) => {
      const classes = this.nodeClasses(node).join(' ');
      return {
        data: {
          ...node,
          totalAmount: Number(node.risk_score ?? 0),
          label: this.truncateAddress(String(node.label ?? node.address ?? node.id)),
        },
        classes,
      } as ElementDefinition;
    });

    const { rank: chronologicalRank, count: rankedCount } = this.buildChronologicalRank(graph.links);

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
  private buildChronologicalRank(links: GraphLinkData[]): { rank: Map<GraphLinkData, number>; count: number } {
    const dated = links
      .map((link) => ({ link, time: link.first_seen ? Date.parse(link.first_seen) : NaN }))
      .filter((entry) => !Number.isNaN(entry.time))
      .sort((a, b) => a.time - b.time);

    const rank = new Map<GraphLinkData, number>();
    dated.forEach((entry, index) => rank.set(entry.link, index + 1));
    return { rank, count: dated.length };
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