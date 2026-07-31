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
  CaseSummary,
  EvidenceEntry,
  GraphNodeData,
  NodeLinkGraphResponse,
  TaintAnalysisResult,
  TaintedHop,
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
  protected taintError: string | null = null;
  protected hasRunTaint = false;
  protected taintResult: TaintAnalysisResult | null = null;
  protected selectedNode: GraphNodeData | null = null;
  protected showAllTopTainted = false;

  private cy: Core | null = null;
  private lastClickedHopKey: string | null = null;
  private lastHopClickWentToTarget = false;
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
    this.showAllTopTainted = false;
    this.lastClickedHopKey = null;
  }

  startNewAnalysis(): void {
    this.resetAnalysisState();
    this.renderGraph();
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
    this.addSeedAddress(address);
  }

  /** Mirror of addManualSeed - lets you paste/type an address to remove it directly,
   * instead of having to visually find its chip among dozens of others to click its ×. */
  removeManualSeed(): void {
    const address = this.manualSeedInput.trim();
    this.manualSeedInput = '';
    if (!address) {
      return;
    }
    this.removeSeedAddress(address);
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
        this.hasRunTaint = true;
        this.isRunningTaint = false;
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

  get selectedNodeHops(): TaintedHop[] {
    if (!this.selectedNode || !this.taintResult) {
      return [];
    }
    const id = String(this.selectedNode.id);
    return this.taintResult.tainted_hops.filter((hop) => hop.source === id || hop.target === id);
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

  /** Clicking a ranked address in the sidebar both opens its inspector details (same as
   * clicking it on canvas) and pans/zooms the graph to it - on a few-hundred-node graph,
   * finding a specific ranked node by eye in the hairball isn't realistic otherwise. */
  selectNodeFromList(address: string): void {
    const node = this.graph?.nodes.find((candidate) => String(candidate.id) === address);
    if (!node) {
      return;
    }
    this.selectedNode = node;

    const element = this.cy?.getElementById(address);
    if (!element || element.empty()) {
      return;
    }
    this.cy?.elements().unselect();
    element.select();
    this.cy?.animate({ fit: { eles: element, padding: 200 } }, { duration: 300 });
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
            // No address/hash text on canvas at all - on a few-hundred-node graph it's
            // pure clutter, and the full address is always one click away in the
            // inspector panel. Just the percentage (once analysis has run) stays, since
            // color alone isn't always a safe read for two nearby values (e.g. 66.67%
            // vs 100% looked too similar in testing).
            label: this.hasRunTaint ? `${taintPercentage}%` : '',
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
            label: 'data(label)',
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
      ],
    });

    this.cy.on('tap', 'node', (event) => {
      const nodeData = event.target.data() as GraphNodeData;
      if (this.isSeedSelectionMode) {
        this.toggleSeed(String(nodeData.id));
      } else {
        this.selectedNode = nodeData;
      }
    });

    // The seed-panel above the canvas just appeared/disappeared (or changed) as part of
    // this same render pass - give the DOM a frame to settle into its final layout, then
    // make sure cytoscape's container measurement matches it exactly.
    this.scheduleCyResize();
  }

  fitWholeGraph(): void {
    this.cy?.fit(undefined, 60);
  }
}
