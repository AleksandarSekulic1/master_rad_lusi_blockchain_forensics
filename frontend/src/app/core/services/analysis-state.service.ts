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