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
import {
  CasePathfindingResult,
  CaseSummary,
  EvidenceEntry,
  NodeLinkGraphResponse,
  PathfindingDestinationMode,
  TaintAnalysisResult,
  TransactionCustodyEntry,
} from '../../models/blockchain-forensics.models';
import { CustodyAccessDialogComponent } from '../custody-access-dialog/custody-access-dialog.component';

/** One row of the "list of transactions along the path" panel - derived entirely from
 * data the page already has loaded (this.graph.links), not fetched separately. When an
 * edge aggregates more than one individual transaction, the EARLIEST one is shown as the
 * hop's representative (deterministic, matches the "one line per hop" mockup) and
 * `extraTransactionCount` says how many more exist on that same edge - nothing is hidden
 * silently. */
interface PathHopDetail {
  source: string;
  target: string;
  amount: number | null;
  timestamp: string | null;
  txHash: string | null;
  extraTransactionCount: number;
  taintPercentage: number | null;
}

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
  imports: [CommonModule, FormsModule, RouterLink, CustodyAccessDialogComponent],
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
  /** 'cash_out_point' is listed but disabled in the UI - not implemented yet. */
  protected destinationMode: PathfindingDestinationMode = 'specific_address';
  protected isSearching = false;
  protected searchError: string | null = null;
  protected result: CasePathfindingResult | null = null;

  // --- Path Analysis: taint trace for the found path, run on demand (see LANAC-DOKAZA.md
  // for why this goes through the same custody dialog as "Pokreni taint analizu"/
  // "Analiziraj graf" - it is the same kind of deliberate access to the evidence). ---
  protected isTaintDialogOpen = false;
  protected isRunningTaint = false;
  protected taintDialogError: string | null = null;
  protected pathTaintResult: TaintAnalysisResult | null = null;

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

  onDestinationModeChange(mode: PathfindingDestinationMode): void {
    this.destinationMode = mode;
    this.clearSearch();
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
    if (!this.activeCase || !this.graph || this.isSearching || this.fromAddress.trim().length === 0) {
      return false;
    }
    // 'Destination' only needs manual input in 'specific_address' mode - 'nearest_cex' is
    // resolved server-side from the graph itself, nothing to type.
    return this.destinationMode !== 'specific_address' || this.toAddress.trim().length > 0;
  }

  findPath(): void {
    const caseId = this.activeCase?.id;
    if (!caseId || !this.canSearch) {
      return;
    }

    this.isSearching = true;
    this.searchError = null;
    this.result = null;
    // A fresh path invalidates any taint trace computed for the PREVIOUS path - otherwise
    // the "Path Analysis" panel could keep showing stale percentages that no longer
    // describe what's on screen.
    this.pathTaintResult = null;
    this.taintDialogError = null;

    const to = this.destinationMode === 'specific_address' ? this.toAddress.trim() : null;

    this.api.findCasePath(caseId, this.fromAddress.trim(), this.destinationMode, to, this.selectedEvidence).subscribe({
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
    this.pathTaintResult = null;
    this.taintDialogError = null;
    this.applyPathHighlight(null);
  }

  // --- Path Analysis: forensic details for the found path -------------------------------

  /** File name of the currently scoped evidence, for the custody dialog's default
   * "identifikator dokaznog materijala" - null means the combined view (all evidence). */
  protected get selectedEvidenceFileName(): string | null {
    if (!this.selectedEvidence) {
      return null;
    }
    return this.evidenceOptions.find((entry) => entry.stored_name === this.selectedEvidence)?.file_name ?? null;
  }

  /** Opens the access-reason dialog. Tracing HOW TAINTED the money on this specific path
   * is means running the taint_analysis plugin (seeded from the path's own origin
   * address), which is exactly the kind of deliberate access to the evidence "Pokreni
   * taint analizu"/"Analiziraj graf" already gate the same way (see LANAC-DOKAZA.md). */
  openTaintDialog(): void {
    if (!this.activeCase?.id || !this.result?.found) {
      return;
    }
    this.taintDialogError = null;
    this.isTaintDialogOpen = true;
  }

  closeTaintDialog(): void {
    this.isTaintDialogOpen = false;
  }

  /** Seeds the EXISTING taint_analysis plugin with just this path's origin address
   * (path[0]) - a seed always starts at 100% by definition of the haircut model, so
   * "Initial taint" below is exactly that, and "Final taint" is however much of THAT
   * specific money the model says survived by the time it reached the path's last
   * address, via this exact chain of hops. Nothing about taint_analysis itself changes -
   * this only ever calls the same POST /cases/{id}/analytics/run every other page uses. */
  confirmCustodyAndRunTaint(custody: TransactionCustodyEntry): void {
    const caseId = this.activeCase?.id;
    const seedAddress = this.result?.path[0];
    if (!caseId || !seedAddress) {
      return;
    }

    this.isRunningTaint = true;
    this.taintDialogError = null;

    this.api.runCaseAnalytics(caseId, this.selectedEvidence, [seedAddress], custody).subscribe({
      next: (response) => {
        this.pathTaintResult = (response.analytics?.['taint_analysis'] as TaintAnalysisResult | undefined) ?? null;
        this.isRunningTaint = false;
        this.isTaintDialogOpen = false;
      },
      error: () => {
        this.isRunningTaint = false;
        this.taintDialogError = 'Neuspešno pokretanje taint analize.';
      },
    });
  }

  private get taintByAddress(): Map<string, number> {
    return new Map((this.pathTaintResult?.results ?? []).map((entry) => [entry.address, entry.taint_percentage]));
  }

  /** One row per hop, built entirely from data already on the page (this.graph.links) -
   * no extra request. See PathHopDetail for why the EARLIEST transaction on an edge is
   * the one shown when a hop aggregates more than one. */
  get pathHops(): PathHopDetail[] {
    const path = this.result?.found ? this.result.path : null;
    if (!path || !this.graph) {
      return [];
    }

    const taint = this.taintByAddress;
    const hops: PathHopDetail[] = [];

    for (let index = 0; index < path.length - 1; index++) {
      const source = path[index];
      const target = path[index + 1];
      const link = this.graph.links.find((candidate) => String(candidate.source) === source && String(candidate.target) === target);
      const transactions = ((link?.transactions ?? []) as Array<{ amount?: number; timestamp?: string; metadata?: string | null }>)
        .slice()
        .sort((a, b) => String(a.timestamp ?? '').localeCompare(String(b.timestamp ?? '')));
      const earliest = transactions[0];

      hops.push({
        source,
        target,
        amount: earliest?.amount ?? null,
        timestamp: earliest?.timestamp ?? null,
        txHash: earliest?.metadata ?? null,
        extraTransactionCount: Math.max(0, transactions.length - 1),
        taintPercentage: taint.get(target) ?? null,
      });
    }

    return hops;
  }

  get initialAmount(): number | null {
    return this.pathHops[0]?.amount ?? null;
  }

  get finalAmount(): number | null {
    const hops = this.pathHops;
    return hops.length ? hops[hops.length - 1].amount : null;
  }

  /** Taint % of the path's OWN origin address - always 100% once a trace has been run,
   * since that address is exactly what was seeded (see confirmCustodyAndRunTaint). Null
   * (not 0) when no trace has been run yet, so the template can tell "not computed" apart
   * from "computed and genuinely zero". */
  get initialTaint(): number | null {
    if (!this.pathTaintResult) {
      return null;
    }
    const address = this.result?.path[0];
    return address ? this.taintByAddress.get(address) ?? 0 : null;
  }

  get finalTaint(): number | null {
    if (!this.pathTaintResult || !this.result?.found) {
      return null;
    }
    const path = this.result.path;
    const address = path[path.length - 1];
    return address ? this.taintByAddress.get(address) ?? 0 : null;
  }

  get taintDilution(): number | null {
    return this.initialTaint != null && this.finalTaint != null ? this.initialTaint - this.finalTaint : null;
  }

  /** "2 dana 6 sati" from the first to the last hop's own (earliest-transaction) timestamp
   * - null when either end is missing (e.g. an edge with no timestamped transaction data)
   * rather than a misleading "0 min". BFS itself does not consider time, so this duration
   * describes the hops AS PICKED here, not a chronologically verified single fund flow -
   * see PATHFINDING-ANALIZA.md for that caveat. */
  get pathDurationLabel(): string | null {
    const hops = this.pathHops;
    const first = hops[0]?.timestamp;
    const last = hops[hops.length - 1]?.timestamp;
    if (!first || !last) {
      return null;
    }
    const ms = new Date(last).getTime() - new Date(first).getTime();
    if (Number.isNaN(ms) || ms < 0) {
      return null;
    }
    return this.formatDuration(ms);
  }

  private formatDuration(ms: number): string {
    const totalMinutes = Math.round(ms / 60_000);
    const days = Math.floor(totalMinutes / 1440);
    const hours = Math.floor((totalMinutes % 1440) / 60);
    const minutes = totalMinutes % 60;

    const parts: string[] = [];
    if (days > 0) {
      parts.push(`${days} ${days === 1 ? 'dan' : 'dana'}`);
    }
    if (hours > 0) {
      parts.push(`${hours} ${hours === 1 ? 'sat' : 'sati'}`);
    }
    if (days === 0 && hours === 0) {
      parts.push(`${minutes} min`);
    }
    return parts.join(' ');
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
