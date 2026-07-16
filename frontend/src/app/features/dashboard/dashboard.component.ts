import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';

import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { ApiService } from '../../core/services/api.service';
import { AnalyticsResponse, CaseSummary, GraphNodeData, NodeLinkGraphResponse, OnchainMode, OnchainNetwork, UploadCsvResponse } from '../../models/blockchain-forensics.models';
import { GraphVisualizationComponent } from '../graph-visualization/graph-visualization.component';
import { ReportExportComponent } from '../report-export/report-export.component';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, GraphVisualizationComponent, ReportExportComponent],
  templateUrl: './dashboard.component.html',
  styleUrl: './dashboard.component.scss',
})
export class DashboardComponent implements OnInit {
  protected selectedFile: File | null = null;
  protected selectedFileLabel = 'Nijedan fajl nije izabran';
  protected searchQuery = '';
  protected isDragging = false;
  protected isUploading = false;
  protected isRefreshing = false;
  protected statusMessage = 'Spremno za učitavanje dokaza.';
  protected uploadResult: UploadCsvResponse | null = null;
  protected graphResult: NodeLinkGraphResponse | null = null;
  protected searchResults: Array<{ node: GraphNodeData; score: number }> = [];

  protected onchainQuery = '';
  protected onchainNetwork: OnchainNetwork = 'mainnet';
  protected onchainHashMode: OnchainMode = 'address_history';
  protected isFetchingOnchain = false;

  protected openCases: CaseSummary[] = [];

  constructor(
    private readonly api: ApiService,
    public readonly state: AnalysisStateService,
  ) {}

  ngOnInit(): void {
    this.bootstrapLatestCase();
    this.loadOpenCases();
  }

  loadOpenCases(): void {
    this.api.listCases().subscribe({
      next: (response) => {
        this.openCases = response.cases.filter((entry) => entry.status === 'open');

        const selectedId = this.state.selectedCaseSnapshot?.id;
        if (selectedId && !this.openCases.some((entry) => entry.id === selectedId)) {
          this.state.setSelectedCase(null);
        }
      },
    });
  }

  onCaseSelected(caseId: string): void {
    const found = this.openCases.find((entry) => entry.id === caseId) ?? null;
    this.state.setSelectedCase(found);
  }

  get transactionCount(): number {
    const graph = this.graphResult ?? this.state.graphSnapshot;
    if (!graph) {
      return this.uploadResult?.rows_total ?? 0;
    }

    return graph.links.reduce((sum, link) => sum + Number(link.transaction_count ?? 1), 0);
  }

  get totalVolume(): number {
    const graph = this.graphResult ?? this.state.graphSnapshot;
    if (!graph) {
      return 0;
    }

    return graph.links.reduce((sum, link) => sum + Number(link.total_amount ?? link.amount ?? 0), 0);
  }

  get flaggedEntitiesCount(): number {
    const analytics = this.state.analyticsSnapshot;
    if (analytics?.summary) {
      return analytics.summary.high_risk_nodes;
    }

    const graph = this.graphResult ?? this.state.graphSnapshot;
    if (!graph) {
      return 0;
    }

    return graph.nodes.filter((node) => this.isFlagged(node)).length;
  }

  get clusterCount(): number {
    const analytics = this.state.analyticsSnapshot;
    if (analytics?.summary) {
      return analytics.summary.clusters;
    }

    const graph = this.graphResult ?? this.state.graphSnapshot;
    if (!graph) {
      return 0;
    }

    return new Set(graph.nodes.map((node) => node.cluster_id).filter(Boolean)).size;
  }

  get blacklistedCount(): number {
    const analytics = this.state.analyticsSnapshot;
    if (analytics?.summary) {
      return analytics.summary.blacklisted_nodes;
    }

    const graph = this.graphResult ?? this.state.graphSnapshot;
    if (!graph) {
      return 0;
    }

    return graph.nodes.filter((node) => Boolean(node.blacklist_flag)).length;
  }

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    this.setSelectedFile(file);
  }

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    this.isDragging = true;
  }

  onDragLeave(event: DragEvent): void {
    event.preventDefault();
    this.isDragging = false;
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    this.isDragging = false;

    const file = event.dataTransfer?.files?.[0] ?? null;
    this.setSelectedFile(file);
  }

  uploadEvidence(): void {
    if (!this.selectedFile) {
      this.statusMessage = 'Prvo izaberite CSV fajl.';
      return;
    }

    const caseId = this.state.selectedCaseSnapshot?.id;
    if (!caseId) {
      this.statusMessage = 'Izaberite slučaj pre učitavanja dokaza.';
      return;
    }

    this.isUploading = true;
    this.statusMessage = 'Učitavanje i heš-ovanje dokaza...';

    this.api.uploadCsv(this.selectedFile, caseId).subscribe({
      next: (uploadResult) => {
        this.uploadResult = uploadResult;
        this.state.setUploadResult(uploadResult);
        if (uploadResult.case) {
          this.state.setSelectedCase(uploadResult.case);
        }
        this.statusMessage = `Dokaz sačuvan kao ${uploadResult.file_name}. Učitavanje kombinovanog grafa slučaja...`;
        this.loadCaseViews(caseId);
        this.loadOpenCases();
      },
      error: (error: unknown) => {
        this.isUploading = false;
        this.statusMessage = this.extractErrorMessage(error, 'Učitavanje nije uspelo.');
      },
    });
  }

  get isOnchainQueryTxHash(): boolean {
    return /^0x[0-9a-fA-F]{64}$/.test(this.onchainQuery.trim());
  }

  fetchOnchainTransactions(): void {
    const query = this.onchainQuery.trim();
    const isAddress = /^0x[0-9a-fA-F]{40}$/.test(query);
    const isTxHash = /^0x[0-9a-fA-F]{64}$/.test(query);

    if (!isAddress && !isTxHash) {
      this.statusMessage = 'Unesite validnu adresu (0x + 40 karaktera) ili heš transakcije (0x + 64 karaktera).';
      return;
    }

    const caseId = this.state.selectedCaseSnapshot?.id;
    if (!caseId) {
      this.statusMessage = 'Izaberite slučaj pre povlačenja transakcija.';
      return;
    }

    this.isFetchingOnchain = true;
    this.statusMessage = `Povlačenje sa ${this.onchainNetwork === 'mainnet' ? 'Ethereum mainnet-a' : 'Sepolia testnet-a'}...`;

    const mode: OnchainMode = isTxHash ? this.onchainHashMode : 'address_history';
    this.api.fetchOnchainTransactions({ query, network: this.onchainNetwork, case_id: caseId, mode }).subscribe({
      next: (result) => {
        this.uploadResult = result;
        this.state.setUploadResult(result);
        if (result.case) {
          this.state.setSelectedCase(result.case);
        }
        this.isFetchingOnchain = false;
        this.statusMessage = `Povučeno ${result.rows_total} transakcija (${result.resolved_query ?? query}). Učitavanje kombinovanog grafa slučaja...`;
        this.loadCaseViews(caseId);
        this.loadOpenCases();
      },
      error: (error: unknown) => {
        this.isFetchingOnchain = false;
        this.statusMessage = this.extractErrorMessage(error, 'Povlačenje transakcija sa blockchain-a nije uspelo.');
      },
    });
  }

  refreshLatestEvidence(): void {
    const selectedCase = this.state.selectedCaseSnapshot;
    if (!selectedCase) {
      this.statusMessage = 'Izaberite slučaj da biste osvežili prikaz.';
      return;
    }

    if (!selectedCase.evidence_count) {
      this.statusMessage = 'Slučaj još uvek nema učitane dokaze.';
      return;
    }

    this.isRefreshing = true;
    this.statusMessage = 'Osvežavanje prikaza slučaja...';
    this.loadCaseViews(selectedCase.id);
  }

  executeSearch(): void {
    const query = this.searchQuery.trim().toLowerCase();
    const graph = this.graphResult ?? this.state.graphSnapshot;
    if (!query || !graph) {
      this.searchResults = [];
      return;
    }

    const matches = graph.nodes
      .map((node) => ({ node, score: this.scoreNode(node, query) }))
      .filter(({ score }) => score > 0)
      .sort((left, right) => right.score - left.score)
      .slice(0, 12);

    this.searchResults = matches;
    this.statusMessage = matches.length > 0 ? `Pronađeno ${matches.length} odgovarajućih adresa.` : 'Nema pronađenih adresa.';
  }

  selectSearchResult(result: { node: GraphNodeData; score: number }): void {
    this.state.setSelectedNode(result.node);
    this.statusMessage = `Izabrano: ${result.node.address ?? result.node.id}.`;
  }

  clearSearch(): void {
    this.searchQuery = '';
    this.searchResults = [];
  }

  private setSelectedFile(file: File | null): void {
    this.selectedFile = file;
    this.selectedFileLabel = file ? file.name : 'Nijedan fajl nije izabran';
    this.statusMessage = file ? `Fajl ${file.name} je spreman za učitavanje.` : 'Nijedan fajl nije izabran.';
  }

  private bootstrapLatestCase(): void {
    const selectedCase = this.state.selectedCaseSnapshot;
    if (!selectedCase) {
      this.statusMessage = 'Izaberite slučaj da biste videli kombinovani graf i analitiku.';
      return;
    }

    if (!selectedCase.evidence_count) {
      this.statusMessage = 'Slučaj još uvek nema učitane dokaze.';
      return;
    }

    this.isRefreshing = true;
    this.statusMessage = 'Učitavanje kombinovanog grafa slučaja...';
    this.loadCaseViews(selectedCase.id);
  }

  private loadCaseViews(caseId: string): void {
    forkJoin({
      graph: this.api.getCaseGraph(caseId),
      analytics: this.api.runCaseAnalytics(caseId),
    }).subscribe({
      next: ({ graph, analytics }) => {
        this.applyGraphAndAnalytics(graph, analytics);
        this.isUploading = false;
        this.isRefreshing = false;
        this.statusMessage = `Učitan kombinovani graf slučaja (${graph.rows ?? graph.nodes.length} redova).`;
      },
      error: (error: unknown) => {
        this.isUploading = false;
        this.isRefreshing = false;
        this.statusMessage = this.extractErrorMessage(error, 'Učitavanje grafa za slučaj nije uspelo.');
      },
    });
  }

  private applyGraphAndAnalytics(graph: NodeLinkGraphResponse, analytics: AnalyticsResponse): void {
    this.graphResult = analytics;
    this.state.setGraph(graph);
    this.state.setAnalytics(analytics);

    if (!this.state.selectedNodeSnapshot && analytics.nodes.length > 0) {
      this.state.setSelectedNode(analytics.nodes[0]);
    }
  }

  private scoreNode(node: GraphNodeData, query: string): number {
    const address = String(node.address ?? node.id ?? '').toLowerCase();
    const label = String(node.label ?? '').toLowerCase();

    if (address === query || label === query) {
      return 100;
    }

    let score = 0;
    if (address.includes(query)) {
      score += 5;
    }
    if (label.includes(query)) {
      score += 3;
    }
    if (String(node.cluster_id ?? '').toLowerCase().includes(query)) {
      score += 2;
    }
    if (Boolean(node.blacklist_flag)) {
      score += 1;
    }
    if (Boolean(node.anomaly_flag)) {
      score += 1;
    }
    return score;
  }

  private isFlagged(node: GraphNodeData): boolean {
    return Boolean(node.blacklist_flag) || Boolean(node.anomaly_flag) || Number(node.risk_score ?? 0) >= 70;
  }

  private extractErrorMessage(error: unknown, fallback: string): string {
    if (typeof error === 'object' && error !== null && 'error' in error) {
      const errorObject = error as { error?: { detail?: string } | string };
      if (typeof errorObject.error === 'string') {
        return errorObject.error;
      }
      if (errorObject.error && typeof errorObject.error === 'object' && 'detail' in errorObject.error) {
        return errorObject.error.detail ?? fallback;
      }
    }

    return fallback;
  }
}