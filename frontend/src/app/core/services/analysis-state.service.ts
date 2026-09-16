import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

import {
  AnalyticsResponse,
  CaseSummary,
  GraphNodeData,
  NodeLinkGraphResponse,
  UploadCsvResponse,
} from '../../models/blockchain-forensics.models';

const SELECTED_CASE_KEY = 'lusi_selected_case';

@Injectable({
  providedIn: 'root',
})
export class AnalysisStateService {
  private readonly uploadSubject = new BehaviorSubject<UploadCsvResponse | null>(null);
  private readonly graphSubject = new BehaviorSubject<NodeLinkGraphResponse | null>(null);
  private readonly analyticsSubject = new BehaviorSubject<AnalyticsResponse | null>(null);
  private readonly selectedNodeSubject = new BehaviorSubject<GraphNodeData | null>(null);
  /** One-shot handoff for "open this address in Pathfinding" links (e.g.
   * token-approval.component.ts's "Otvori u Pathfinding") - same idea as selectedNode
   * above, but consumed once by pathfinding.component.ts's ngOnInit rather than observed
   * continuously, since Pathfinding only needs to pick it up on load, not react to it
   * live while already open. */
  private pendingPathfindingSeed: { from: string; to?: string } | null = null;
  /** One-shot handoff for "send these addresses to Taint Analysis" (e.g.
   * token-approval.component.ts's "Pošalji u Taint analizu" panel, built from the
   * risky spenders/owners its own risk scoring already ranked) - same one-shot idea as
   * pendingPathfindingSeed above, consumed once by taint-analysis.component.ts's
   * ngOnInit rather than observed continuously. */
  private pendingTaintSeeds: string[] | null = null;
  // Restored from localStorage so the active case survives a page refresh - it stays
  // active until the user clicks it again or picks another one.
  private readonly selectedCaseSubject = new BehaviorSubject<CaseSummary | null>(this.readStoredCase());

  readonly upload$ = this.uploadSubject.asObservable();
  readonly graph$ = this.graphSubject.asObservable();
  readonly analytics$ = this.analyticsSubject.asObservable();
  readonly selectedNode$ = this.selectedNodeSubject.asObservable();
  readonly selectedCase$ = this.selectedCaseSubject.asObservable();

  setUploadResult(result: UploadCsvResponse | null): void {
    this.uploadSubject.next(result);
  }

  setGraph(graph: NodeLinkGraphResponse | null): void {
    this.graphSubject.next(graph);
  }

  setAnalytics(analytics: AnalyticsResponse | null): void {
    this.analyticsSubject.next(analytics);
  }

  setSelectedNode(node: GraphNodeData | null): void {
    this.selectedNodeSubject.next(node);
  }

  setPendingPathfindingSeed(seed: { from: string; to?: string }): void {
    this.pendingPathfindingSeed = seed;
  }

  /** Returns the pending seed (if any) and clears it - a second visit to Pathfinding
   * without a fresh "Otvori u Pathfinding" click never replays a stale address. */
  consumePendingPathfindingSeed(): { from: string; to?: string } | null {
    const seed = this.pendingPathfindingSeed;
    this.pendingPathfindingSeed = null;
    return seed;
  }

  setPendingTaintSeeds(addresses: string[]): void {
    this.pendingTaintSeeds = addresses;
  }

  /** Returns the pending taint seed list (if any) and clears it - a second, unrelated
   * visit to Taint Analysis never replays a stale batch from an earlier session. */
  consumePendingTaintSeeds(): string[] | null {
    const addresses = this.pendingTaintSeeds;
    this.pendingTaintSeeds = null;
    return addresses;
  }

  /** Keeps the current selection if it still exists in the new node set, otherwise
   * falls back to the first node (or clears it if the graph is empty). Without this,
   * switching case/evidence would leave a stale selection from a graph that no longer
   * contains that node. */
  ensureValidSelectedNode(nodes: GraphNodeData[]): void {
    const currentId = this.selectedNodeSubject.value?.id;
    const stillExists = currentId != null && nodes.some((node) => node.id === currentId);
    if (!stillExists) {
      this.setSelectedNode(nodes.length > 0 ? nodes[0] : null);
    }
  }

  setSelectedCase(caseSummary: CaseSummary | null): void {
    this.selectedCaseSubject.next(caseSummary);
    try {
      if (caseSummary) {
        localStorage.setItem(SELECTED_CASE_KEY, JSON.stringify(caseSummary));
      } else {
        localStorage.removeItem(SELECTED_CASE_KEY);
      }
    } catch {
      /* storage unavailable - selection just won't survive a refresh */
    }
  }

  private readStoredCase(): CaseSummary | null {
    try {
      const raw = localStorage.getItem(SELECTED_CASE_KEY);
      return raw ? (JSON.parse(raw) as CaseSummary) : null;
    } catch {
      return null;
    }
  }

  /** Lets a panel grab a PNG of the live transaction graph without importing
   * GraphVisualizationComponent. The graph component registers a closure over its own
   * cytoscape instance while mounted and clears it on destroy; callers get `null` when the
   * graph isn't currently on screen. */
  private graphImageProvider: (() => string | null) | null = null;

  registerGraphImageProvider(provider: (() => string | null) | null): void {
    this.graphImageProvider = provider;
  }

  captureGraphImage(): string | null {
    try {
      return this.graphImageProvider?.() ?? null;
    } catch {
      return null;
    }
  }

  /** Same idea as graphImageProvider above, but for a vector (SVG) snapshot - see
   * report-export.component.ts's exportSvg(). Kept as a separate provider rather than a
   * second argument on the PNG one so either capture can fail/be absent independently
   * (e.g. cytoscape-svg not registered yet) without touching the PNG path. */
  private graphSvgProvider: (() => string | null) | null = null;

  registerGraphSvgProvider(provider: (() => string | null) | null): void {
    this.graphSvgProvider = provider;
  }

  captureGraphSvg(): string | null {
    try {
      return this.graphSvgProvider?.() ?? null;
    } catch {
      return null;
    }
  }

  get uploadSnapshot(): UploadCsvResponse | null {
    return this.uploadSubject.value;
  }

  get graphSnapshot(): NodeLinkGraphResponse | null {
    return this.graphSubject.value;
  }

  get analyticsSnapshot(): AnalyticsResponse | null {
    return this.analyticsSubject.value;
  }

  get selectedNodeSnapshot(): GraphNodeData | null {
    return this.selectedNodeSubject.value;
  }

  get selectedCaseSnapshot(): CaseSummary | null {
    return this.selectedCaseSubject.value;
  }
}