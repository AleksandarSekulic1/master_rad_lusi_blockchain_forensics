import { CommonModule } from '@angular/common';
import { Component, DestroyRef, ElementRef, HostListener, OnDestroy, OnInit, ViewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { distinctUntilChanged, map } from 'rxjs/operators';

import cytoscape, { Core, ElementDefinition } from 'cytoscape';

import { ensureCytoscapeExtensionsRegistered } from '../../core/cytoscape-setup';
import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { ApiService } from '../../core/services/api.service';
import { CasePathfindingResult, CaseSummary, EvidenceEntry, NodeLinkGraphResponse } from '../../models/blockchain-forensics.models';

/** Pathfinding Analysis - "kojim putem se sredstva kreću između dve adrese", a
 * deliberately separate question (and page) from Taint Analysis's "kako se zaprljana
 * sredstva propagiraju kroz mrežu". First version: unweighted BFS, single path, no
 * CEX/cash-out detection - see backend/app/analytics/path_finding.py.
 *
 * Reuses the same shape as the Graf page (case/evidence picker, plain cytoscape graph,
 * ensureCytoscapeExtensionsRegistered) rather than introducing a new graph component -
 * graph-visualization.component.ts is NOT imported/modified, only used as the visual/
 * structural reference (see LANAC-DOKAZA and TAINT-ANALIZA docs for the same convention
 * of "look consistent, stay independent" between the app's analysis pages).
 */
@Component({
  selector: 'app-pathfinding',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './pathfinding.component.html',
  styleUrl: './pathfinding.component.scss',
})
export class PathfindingComponent implements OnInit, OnDestroy {
  @ViewChild('pathCanvas', { static: true })
  protected pathCanvas!: ElementRef<HTMLDivElement>;

  protected activeCase: CaseSummary | null = null;
  protected evidenceOptions: EvidenceEntry[] = [];
  protected selectedEvidence: string | null = null;

  protected graph: NodeLinkGraphResponse | null = null;
  protected isLoadingGraph = false;
  protected graphError: string | null = null;

  protected fromAddress = '';
  protected toAddress = '';
  protected isSearching = false;
  protected searchError: string | null = null;
  protected result: CasePathfindingResult | null = null;

  private cy: Core | null = null;

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
        distinctUntilChanged(),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(() => {
        this.activeCase = this.state.selectedCaseSnapshot;
        this.selectedEvidence = null;
        this.evidenceOptions = [];
        this.clearSearch();
        if (this.activeCase) {
          this.loadEvidenceOptions(this.activeCase.id);
          this.loadGraph();
        } else {
          this.graph = null;
          this.renderGraph();
        }
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
    this.clearSearch();
    this.loadGraph();
  }

  /** Plain, ungated graph - same as the Graf page's automatic preview. Pathfinding does
   * not run any analytics pipeline (no risk/blacklist coloring), so there is nothing here
   * that would call for the custody-access dialog the way "Pokreni taint analizu"/
   * "Analiziraj graf" do. */
  loadGraph(): void {
    const caseId = this.activeCase?.id;
    if (!caseId) {
      return;
    }

    if (!this.activeCase?.evidence_count) {
      this.graph = null;
      this.renderGraph();
      this.graphError = null;
      return;
    }

    this.isLoadingGraph = true;
    this.graphError = null;

    this.api.getCaseGraph(caseId, this.selectedEvidence).subscribe({
      next: (graph) => {
        this.graph = graph;
        this.isLoadingGraph = false;
        this.renderGraph();
      },
      error: () => {
        this.isLoadingGraph = false;
        this.graphError = 'Neuspešno učitavanje grafa za izabrani slučaj.';
      },
    });
  }

  get canSearch(): boolean {
    return !!this.activeCase && !!this.graph && this.fromAddress.trim().length > 0 && this.toAddress.trim().length > 0 && !this.isSearching;
  }

  findPath(): void {
    const caseId = this.activeCase?.id;
    if (!caseId || !this.canSearch) {
      return;
    }

    this.isSearching = true;
    this.searchError = null;
    this.result = null;

    this.api.findCasePath(caseId, this.fromAddress.trim(), this.toAddress.trim(), this.selectedEvidence).subscribe({
      next: (result) => {
        this.result = result;
        this.isSearching = false;
        this.applyPathHighlight(result.found ? result.path : null);
      },
      error: () => {
        this.isSearching = false;
        this.searchError = 'Pretraga puta nije uspela.';
        this.applyPathHighlight(null);
      },
    });
  }

  private clearSearch(): void {
    this.result = null;
    this.searchError = null;
    this.applyPathHighlight(null);
  }

  /** Highlights exactly the found path (nodes + the edges connecting consecutive hops)
   * and dims the rest of the graph - same visual language (class names AND cytoscape
   * style values) as Taint Analysis's own path highlight, see
   * taint-analysis.component.ts's applyPathHighlight/path-highlighted/path-dimmed. */
  private applyPathHighlight(path: string[] | null): void {
    if (!this.cy) {
      return;
    }

    this.cy.elements().removeClass('path-highlighted path-dimmed');
    if (!path || path.length === 0) {
      return;
    }

    const nodeIds = new Set(path);
    const edgeKeys = new Set<string>();
    for (let index = 0; index < path.length - 1; index++) {
      edgeKeys.add(`${path[index]}__${path[index + 1]}`);
    }

    this.cy.nodes().forEach((node) => {
      node.addClass(nodeIds.has(node.id()) ? 'path-highlighted' : 'path-dimmed');
    });
    this.cy.edges().forEach((edge) => {
      const key = `${edge.data('source')}__${edge.data('target')}`;
      edge.addClass(edgeKeys.has(key) ? 'path-highlighted' : 'path-dimmed');
    });

    this.cy.animate({ fit: { eles: this.cy.nodes().filter((node) => nodeIds.has(node.id())), padding: 120 } }, { duration: 350 });
  }

  /** Full-length addresses as node labels are unreadable clutter once a graph has more
   * than a handful of nodes - same truncation as the Graf page. */
  private truncateAddress(value: string): string {
    if (value.length <= 14) {
      return value;
    }
    return `${value.slice(0, 6)}…${value.slice(-4)}`;
  }

  private buildElements(graph: NodeLinkGraphResponse): ElementDefinition[] {
    const nodes: ElementDefinition[] = graph.nodes.map((node) => ({
      data: {
        ...node,
        label: this.truncateAddress(String(node.label ?? node.address ?? node.id)),
      },
    }));

    const links: ElementDefinition[] = graph.links.map((link) => ({
      data: {
        ...link,
        id: `${link.source}__${link.target}`,
        label: link.total_amount != null ? Number(link.total_amount).toFixed(2) : '',
      },
    }));

    return [...nodes, ...links];
  }

  private renderGraph(): void {
    if (!this.pathCanvas) {
      return;
    }

    this.cy?.destroy();
    this.cy = null;

    if (!this.graph) {
      return;
    }

    const elements = this.buildElements(this.graph);

    // Same escape hatch graph-visualization.component.ts uses: cytoscape's Stylesheet
    // type doesn't recognize every valid property (e.g. 'active-bg-color') as optional,
    // so a literal style array fails to typecheck even though it's valid at runtime.
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
            width: 40,
            height: 40,
          },
        },
        {
          selector: 'edge',
          style: {
            width: 2,
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
        // Same class names AND same style values as Taint Analysis's path highlight -
        // outline-* rather than border-* so it layers independently, opacity-only dim
        // rather than hiding so the overall graph shape stays legible for context.
        {
          selector: 'node.path-highlighted',
          style: {
            'outline-width': 4,
            'outline-color': '#7dd3fc',
            'outline-style': 'solid',
            'outline-offset': 2,
            'background-color': '#2f9e63',
            'border-color': '#8ff0bd',
          },
        },
        {
          selector: 'edge.path-highlighted',
          style: {
            'line-color': '#7dd3fc',
            'target-arrow-color': '#7dd3fc',
            opacity: 1,
            width: 4,
          },
        },
        {
          selector: 'node.path-dimmed',
          style: { opacity: 0.15 },
        },
        {
          selector: 'edge.path-dimmed',
          style: { opacity: 0.06 },
        },
    ];

    this.cy = cytoscape({
      container: this.pathCanvas.nativeElement,
      elements,
      wheelSensitivity: 0.2,
      layout: {
        name: 'fcose',
        quality: 'default',
        randomize: true,
        animate: false,
        fit: true,
        padding: 60,
        nodeSeparation: 100,
        nodeRepulsion: 6000,
        idealEdgeLength: 80,
      } as any,
      style: graphStyles,
    });
  }

  fitWholeGraph(): void {
    this.cy?.fit(undefined, 60);
  }
}
