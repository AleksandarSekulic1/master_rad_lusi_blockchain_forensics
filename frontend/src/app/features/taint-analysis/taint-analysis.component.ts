import { CommonModule } from '@angular/common';
import { Component, DestroyRef, ElementRef, OnDestroy, OnInit, ViewChild } from '@angular/core';
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

  private cy: Core | null = null;
  private static readonly TAINT_LOW_COLOR = '#1c2333';
  private static readonly TAINT_MID_COLOR = '#f59e0b';
  private static readonly TAINT_HIGH_COLOR = '#ff4d4d';
  private static readonly TAINT_MID_THRESHOLD = 50;

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
    const index = this.seedAddresses.indexOf(nodeId);
    if (index >= 0) {
      this.seedAddresses.splice(index, 1);
    } else {
      this.seedAddresses.push(nodeId);
    }
    this.cy?.getElementById(nodeId).toggleClass('taint-seed', this.seedAddresses.includes(nodeId));
  }

  removeSeed(address: string): void {
    this.toggleSeed(address);
  }

  addManualSeed(): void {
    const address = this.manualSeedInput.trim();
    this.manualSeedInput = '';
    if (!address || this.seedAddresses.includes(address)) {
      return;
    }
    this.seedAddresses.push(address);
    this.cy?.getElementById(address).addClass('taint-seed');
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

  get topTaintedNodes(): Array<{ address: string; taint_percentage: number; is_taint_seed: boolean }> {
    return (this.taintResult?.results ?? []).filter((item) => item.taint_percentage > 0).slice(0, 15);
  }

  get selectedNodeHops(): TaintedHop[] {
    if (!this.selectedNode || !this.taintResult) {
      return [];
    }
    const id = String(this.selectedNode.id);
    return this.taintResult.tainted_hops.filter((hop) => hop.source === id || hop.target === id);
  }

  private truncateAddress(value: string): string {
    if (value.length <= 14) {
      return value;
    }
    return `${value.slice(0, 6)}…${value.slice(-4)}`;
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
        const addressLabel = this.truncateAddress(String(node.label ?? node.address ?? node.id));
        return {
          data: {
            ...node,
            id,
            taint_percentage: taintPercentage,
            // Percentage goes on the node itself, not just the inspector panel - two
            // colors on a sequential ramp can look close enough at a glance (see
            // 66.67% vs 100% in testing) that color alone isn't a safe read.
            label: this.hasRunTaint ? `${addressLabel}\n${taintPercentage}%` : addressLabel,
          },
          classes: isSeed ? 'taint-seed' : '',
        } as ElementDefinition;
      }),
      ...this.graph.links.map((link) => ({
        data: {
          ...link,
          id: `${link.source}__${link.target}__${Math.round(Number(link.total_amount ?? link.amount ?? 0) * 1000)}`,
        },
      })) as ElementDefinition[],
    ];

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
      } as any,
      style: [
        {
          selector: 'node',
          style: {
            label: 'data(label)',
            'text-valign': 'center',
            'text-halign': 'center',
            'text-wrap': 'wrap',
            'font-size': 10,
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
            width: 34,
            height: 34,
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
            width: 'mapData(totalAmount, 0, 250, 1, 5)',
            'line-color': '#3a4a63',
            'target-arrow-color': '#3a4a63',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            opacity: 0.65,
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
  }

  fitWholeGraph(): void {
    this.cy?.fit(undefined, 60);
  }
}
