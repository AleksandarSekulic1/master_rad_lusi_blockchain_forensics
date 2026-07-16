import { CommonModule } from '@angular/common';
import { Component, DestroyRef, ElementRef, HostListener, OnDestroy, OnInit, ViewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';
import { distinctUntilChanged, map } from 'rxjs/operators';

import cytoscape, { Core, ElementDefinition } from 'cytoscape';

import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { ApiService } from '../../core/services/api.service';
import { CaseSummary, EvidenceEntry, GraphLinkData, GraphNodeData, NodeLinkGraphResponse } from '../../models/blockchain-forensics.models';

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

  private cy: Core | null = null;

  constructor(
    private readonly state: AnalysisStateService,
    private readonly api: ApiService,
    private readonly destroyRef: DestroyRef,
  ) {}

  ngOnInit(): void {
    this.state.graph$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((graph) => {
      this.graph = graph;
      this.hasLoadedGraph = Boolean(graph);
      this.renderGraph();
    });

    this.state.selectedNode$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((node) => {
      this.selectedNode = node;
      this.syncSelection();
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
        if (!this.state.selectedNodeSnapshot && analytics.nodes.length > 0) {
          this.state.setSelectedNode(analytics.nodes[0]);
        }
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
  }

  @HostListener('window:resize')
  onResize(): void {
    this.cy?.resize();
    this.cy?.fit(undefined, 80);
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

    if (!this.graph) {
      return;
    }

    const elements = this.buildElements(this.graph);
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
          color: '#ecf2ff',
          'text-outline-width': 2,
          'text-outline-color': '#07111f',
          'background-color': '#4f8cff',
          'border-width': 2,
          'border-color': '#9bd1ff',
          width: 'mapData(totalAmount, 0, 250, 36, 72)',
          height: 'mapData(totalAmount, 0, 250, 36, 72)',
        },
      },
      {
        selector: 'node.high-risk',
        style: {
          'background-color': '#ef4444',
          'border-color': '#fca5a5',
        },
      },
      {
        selector: 'node.blacklisted',
        style: {
          'background-color': '#dc2626',
          'border-color': '#f87171',
        },
      },
      {
        selector: 'node.bridge-node',
        style: {
          shape: 'hexagon',
        },
      },
      {
        selector: 'node.peel-chain-node',
        style: {
          shape: 'diamond',
        },
      },
      {
        selector: 'node.anomaly-node',
        style: {
          'border-style': 'dashed',
          'border-width': 3,
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
    ];

    this.cy = cytoscape({
      container: this.graphCanvas.nativeElement,
      elements,
      wheelSensitivity: 0.2,
      layout: {
        name: 'cose',
        animate: false,
        fit: true,
        padding: 60,
        randomize: false,
      },
      style: graphStyles,
    });

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

  private buildElements(graph: NodeLinkGraphResponse): ElementDefinition[] {
    const nodes = graph.nodes.map((node) => {
      const classes = this.nodeClasses(node).join(' ');
      return {
        data: {
          ...node,
          totalAmount: Number(node.risk_score ?? 0),
          label: node.label ?? node.address ?? node.id,
        },
        classes,
      } as ElementDefinition;
    });

    const links = graph.links.map((link) => {
      const classes = this.linkClasses(link).join(' ');
      return {
        data: {
          ...link,
          id: `${link.source}__${link.target}__${Math.round(Number(link.total_amount ?? link.amount ?? 0) * 1000)}`,
          label: Number(link.total_amount ?? link.amount ?? 0).toFixed(2),
          totalAmount: Number(link.total_amount ?? link.amount ?? 0),
        },
        classes,
      } as ElementDefinition;
    });

    return [...nodes, ...links];
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

    return classes;
  }

  private linkClasses(link: GraphLinkData): string[] {
    const classes: string[] = [];
    if (link.bridge_edge) {
      classes.push('bridge-edge');
    }
    return classes;
  }
}