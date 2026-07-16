import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

import {
  AnalyticsResponse,
  CaseSummary,
  GraphNodeData,
  NodeLinkGraphResponse,
  UploadCsvResponse,
} from '../../models/blockchain-forensics.models';

@Injectable({
  providedIn: 'root',
})
export class AnalysisStateService {
  private readonly uploadSubject = new BehaviorSubject<UploadCsvResponse | null>(null);
  private readonly graphSubject = new BehaviorSubject<NodeLinkGraphResponse | null>(null);
  private readonly analyticsSubject = new BehaviorSubject<AnalyticsResponse | null>(null);
  private readonly selectedNodeSubject = new BehaviorSubject<GraphNodeData | null>(null);
  private readonly selectedCaseSubject = new BehaviorSubject<CaseSummary | null>(null);

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