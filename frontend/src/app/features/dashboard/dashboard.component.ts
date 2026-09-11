import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';

import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { ApiService } from '../../core/services/api.service';
import { SettingsService } from '../../core/services/settings.service';
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
  protected isDragging = false;
  protected isUploading = false;
  protected isRefreshing = false;
  /**
   * Held as a thunk (not a plain string) so the message re-renders in the
   * active language: `t()` is re-evaluated by the template on every change
   * detection pass, including right after the language toggle.
   */
  protected statusMessage: () => string = () => '';
  protected uploadResult: UploadCsvResponse | null = null;
  protected graphResult: NodeLinkGraphResponse | null = null;

  protected onchainQuery = '';
  protected onchainNetwork: OnchainNetwork = 'mainnet';
  protected onchainHashMode: OnchainMode = 'address_history';
  protected isFetchingOnchain = false;

  protected openCases: CaseSummary[] = [];

  constructor(
    private readonly api: ApiService,
    public readonly state: AnalysisStateService,
    public readonly settings: SettingsService,
  ) {
    this.statusMessage = () => this.t('Spremno za učitavanje dokaza.', 'Ready to upload evidence.');
  }

  /** Tiny inline translator: picks the Serbian or English string for the active language. */
  protected t(sr: string, en: string): string {
    return this.settings.lang() === 'sr' ? sr : en;
  }

  protected get selectedFileLabel(): string {
    return this.selectedFile ? this.selectedFile.name : this.t('Nijedan fajl nije izabran', 'No file selected');
  }

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
      this.statusMessage = () => this.t('Prvo izaberite CSV fajl.', 'Select a CSV file first.');
      return;
    }

    const caseId = this.state.selectedCaseSnapshot?.id;
    if (!caseId) {
      this.statusMessage = () => this.t('Izaberite slučaj pre učitavanja dokaza.', 'Select a case before uploading evidence.');
      return;
    }

    this.isUploading = true;
    this.statusMessage = () => this.t('Učitavanje i heš-ovanje dokaza...', 'Uploading and hashing evidence...');

    this.api.uploadCsv(this.selectedFile, caseId).subscribe({
      next: (uploadResult) => {
        this.uploadResult = uploadResult;
        this.state.setUploadResult(uploadResult);
        if (uploadResult.case) {
          this.state.setSelectedCase(uploadResult.case);
        }
        this.statusMessage = uploadResult.split
          ? () =>
              `${this.splitSummaryLabel(uploadResult)} ${this.t('Učitavanje kombinovanog grafa slučaja...', 'Loading combined case graph...')}`
          : () =>
              `${this.t('Dokaz sačuvan kao', 'Evidence saved as')} ${uploadResult.file_name}. ${this.t('Učitavanje kombinovanog grafa slučaja...', 'Loading combined case graph...')}`;
        this.loadCaseViews(caseId);
        this.loadOpenCases();
      },
      error: (error: unknown) => {
        this.isUploading = false;
        this.statusMessage = () => this.extractErrorMessage(error, this.t('Učitavanje nije uspelo.', 'Upload failed.'));
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
      this.statusMessage = () =>
        this.t(
          'Unesite validnu adresu (0x + 40 karaktera) ili heš transakcije (0x + 64 karaktera).',
          'Enter a valid address (0x + 40 chars) or transaction hash (0x + 64 chars).',
        );
      return;
    }

    const caseId = this.state.selectedCaseSnapshot?.id;
    if (!caseId) {
      this.statusMessage = () => this.t('Izaberite slučaj pre povlačenja transakcija.', 'Select a case before fetching transactions.');
      return;
    }

    this.isFetchingOnchain = true;
    this.statusMessage = () => {
      const networkLabel =
        this.onchainNetwork === 'mainnet'
          ? this.t('Ethereum mainnet-a', 'Ethereum mainnet')
          : this.t('Sepolia testnet-a', 'the Sepolia testnet');
      return `${this.t('Povlačenje sa', 'Fetching from')} ${networkLabel}...`;
    };

    const mode: OnchainMode = isTxHash ? this.onchainHashMode : 'address_history';
    this.api.fetchOnchainTransactions({ query, network: this.onchainNetwork, case_id: caseId, mode }).subscribe({
      next: (result) => {
        this.uploadResult = result;
        this.state.setUploadResult(result);
        if (result.case) {
          this.state.setSelectedCase(result.case);
        }
        this.isFetchingOnchain = false;
        this.statusMessage = () =>
          `${this.t('Povučeno', 'Fetched')} ${result.rows_total} ${this.t('transakcija', 'transactions')} (${result.resolved_query ?? query}). ${this.t('Učitavanje kombinovanog grafa slučaja...', 'Loading combined case graph...')}`;
        this.loadCaseViews(caseId);
        this.loadOpenCases();
      },
      error: (error: unknown) => {
        this.isFetchingOnchain = false;
        this.statusMessage = () =>
          this.extractErrorMessage(
            error,
            this.t('Povlačenje transakcija sa blockchain-a nije uspelo.', 'Fetching transactions from the blockchain failed.'),
          );
      },
    });
  }

  refreshLatestEvidence(): void {
    const selectedCase = this.state.selectedCaseSnapshot;
    if (!selectedCase) {
      this.statusMessage = () => this.t('Izaberite slučaj da biste osvežili prikaz.', 'Select a case to refresh the view.');
      return;
    }

    if (!selectedCase.evidence_count) {
      this.statusMessage = () => this.t('Slučaj još uvek nema učitane dokaze.', 'This case has no uploaded evidence yet.');
      return;
    }

    this.isRefreshing = true;
    this.statusMessage = () => this.t('Osvežavanje prikaza slučaja...', 'Refreshing case view...');
    this.loadCaseViews(selectedCase.id);
  }

  /** "Evidencija je sadržala 3 valute — automatski razdvojena u 3 fajla: ETH (2), USDC
   * (1), DAI (1)." - the per-currency split summary shown after an auto-split upload (see
   * ApiService.uploadCsv / upload.py's _split_and_store_by_currency). A file with no
   * declared currency at all is labeled distinctly from a real currency code. */
  private splitSummaryLabel(result: UploadCsvResponse): string {
    const files = result.files ?? [];
    const parts = files
      .map((file) => `${file.currency ?? this.t('bez valute', 'no currency')} (${file.rows_total})`)
      .join(', ');
    return this.t(
      `Evidencija je sadržala ${files.length} valuta — automatski razdvojena u ${files.length} fajla: ${parts}.`,
      `The evidence declared ${files.length} currencies — automatically split into ${files.length} files: ${parts}.`,
    );
  }

  private setSelectedFile(file: File | null): void {
    this.selectedFile = file;
    this.statusMessage = file
      ? () => `${this.t('Fajl', 'File')} ${file.name} ${this.t('je spreman za učitavanje.', 'is ready to upload.')}`
      : () => this.t('Nijedan fajl nije izabran.', 'No file selected.');
  }

  private bootstrapLatestCase(): void {
    const selectedCase = this.state.selectedCaseSnapshot;
    if (!selectedCase) {
      this.statusMessage = () =>
        this.t(
          'Izaberite slučaj da biste videli kombinovani graf i analitiku.',
          'Select a case to see the combined graph and analytics.',
        );
      return;
    }

    if (!selectedCase.evidence_count) {
      this.statusMessage = () => this.t('Slučaj još uvek nema učitane dokaze.', 'This case has no uploaded evidence yet.');
      return;
    }

    this.isRefreshing = true;
    this.statusMessage = () => this.t('Učitavanje kombinovanog grafa slučaja...', 'Loading combined case graph...');
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
        this.statusMessage = () =>
          `${this.t('Učitan kombinovani graf slučaja', 'Combined case graph loaded')} (${graph.rows ?? graph.nodes.length} ${this.t('redova', 'rows')}).`;
      },
      error: (error: unknown) => {
        this.isUploading = false;
        this.isRefreshing = false;
        this.statusMessage = () => this.extractErrorMessage(error, this.t('Učitavanje grafa za slučaj nije uspelo.', 'Loading the case graph failed.'));
      },
    });
  }

  private applyGraphAndAnalytics(graph: NodeLinkGraphResponse, analytics: AnalyticsResponse): void {
    this.graphResult = analytics;
    this.state.setGraph(graph);
    this.state.setAnalytics(analytics);
    this.state.ensureValidSelectedNode(analytics.nodes);
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
