import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

import {
  AnalyticsResponse,
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

  readonly upload$ = this.uploadSubject.asObservable();
  readonly graph$ = this.graphSubject.asObservable();
  readonly analytics$ = this.analyticsSubject.asObservable();
  readonly selectedNode$ = this.selectedNodeSubject.asObservable();

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
}