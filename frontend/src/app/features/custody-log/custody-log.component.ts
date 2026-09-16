import { CommonModule } from '@angular/common';
import { Component, ElementRef, OnInit, ViewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { CustodyLogApiService } from './custody-log.api';
import { AppLang, SettingsService } from '../../core/services/settings.service';
import { CustodyChain, CustodyEvidenceChain, CustodyEvidenceSummary, CustodyTransactionSummary } from './custody-log.models';

type CustodyTab = 'transaction' | 'evidence';

/** "Lanac dokaza" - Obrazac evidencije rukovanja dokaznim materijalom, at TWO
 * granularities kept side by side (see LANAC-DOKAZA.md for why both exist):
 * - "Transakcije": one form per individual transaction.
 * - "Dokazni fajlovi": one form per whole evidence file (CSV/on-chain export), the
 *   coarser view that also covers access from the Graph page's "Analiziraj graf".
 * Open to any authenticated user (analyst or admin, see custody.py) - unlike "Testovi",
 * there is no admin guard on this route.
 */
@Component({
  selector: 'app-custody-log',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './custody-log.component.html',
  styleUrl: './custody-log.component.scss',
})
export class CustodyLogComponent implements OnInit {
  protected activeTab: CustodyTab = 'transaction';

  // --- Po transakciji ---
  protected transactions: CustodyTransactionSummary[] = [];
  protected isLoadingList = false;
  protected listError: string | null = null;
  protected selectedChain: CustodyChain | null = null;
  protected isLoadingChain = false;
  protected chainError: string | null = null;
  protected isExportingPdf = false;
  protected exportError: string | null = null;

  // --- Paginacija (samo "Po transakciji" - taj spisak zna da naraste na stotine redova;
  // "Po dokaznom fajlu" ostaje bez nje, obično svega par fajlova po slučaju). Isti obrazac
  // kao activity-log.component.ts (pageSizeOptions/pageSize/currentPage/scrollToTableTop). ---
  protected readonly pageSizeOptions = [10, 20, 50] as const;
  protected pageSize: (typeof this.pageSizeOptions)[number] = 20;
  protected currentPage = 1;
  @ViewChild('tableTop') private tableTopRef?: ElementRef<HTMLElement>;

  // --- Po dokaznom fajlu ---
  protected evidenceList: CustodyEvidenceSummary[] = [];
  protected isLoadingEvidenceList = false;
  protected evidenceListError: string | null = null;
  protected selectedEvidenceChain: CustodyEvidenceChain | null = null;
  protected isLoadingEvidenceChain = false;
  protected evidenceChainError: string | null = null;
  protected isExportingEvidencePdf = false;
  protected evidenceExportError: string | null = null;

  // --- Jezik PDF izvoza - jedini izbor pre generisanja (potpisi su već deo evidencije,
  // pečat/kontrolni broj se ovde ne dodaju - vidi confirmExport() docstring). Deljeno
  // između oba taba, jer samo jedan od njih može biti otvoren u datom trenutku. ---
  protected isExportLangDialogOpen = false;
  protected exportPdfLang: AppLang = 'sr';
  private pendingExportScope: 'transaction' | 'evidence' | null = null;

  private transactionsLoaded = false;
  private evidenceListLoaded = false;

  /** Set when a deep link points at a transaction/evidence file belonging to a DIFFERENT
   * case than the one currently active - switching the globally selected case as a side
   * effect of opening this page would be surprising, so this is surfaced as a notice. */
  protected caseMismatchNotice: string | null = null;

  constructor(
    private readonly custodyLogApi: CustodyLogApiService,
    protected readonly state: AnalysisStateService,
    private readonly route: ActivatedRoute,
    protected readonly settings: SettingsService,
  ) {}

  /** Tiny inline translator: picks the Serbian or English string for the active language
   * (same pattern as the other pages' own t()). The reproduced official form itself
   * (Cyrillic field labels, "ОБРАЗАЦ...") stays Serbian regardless of language - see
   * LANAC-DOKAZA.md §1 - only the chrome around it is translated. */
  protected t(sr: string, en: string): string {
    return this.settings.lang() === 'sr' ? sr : en;
  }

  get activeCaseId(): string | null {
    return this.state.selectedCaseSnapshot?.id ?? null;
  }

  get activeCaseName(): string | null {
    return this.state.selectedCaseSnapshot?.name ?? null;
  }

  ngOnInit(): void {
    if (!this.activeCaseId) {
      return;
    }

    const deepLinkCase = this.route.snapshot.queryParamMap.get('caseId');
    if (deepLinkCase && deepLinkCase !== this.activeCaseId) {
      this.caseMismatchNotice = this.t(
        `Ovaj link se odnosi na slučaj ${deepLinkCase}, a trenutno je izabran drugi slučaj. Izaberite taj slučaj na stranici "Slučajevi" da biste videli njegov lanac dokaza.`,
        `This link refers to case ${deepLinkCase}, but a different case is currently selected. Select that case on the "Cases" page to see its chain of custody.`,
      );
    }

    const deepLinkEvidence = this.route.snapshot.queryParamMap.get('evidence');
    const deepLinkTx = this.route.snapshot.queryParamMap.get('tx');

    if (deepLinkEvidence && !this.caseMismatchNotice) {
      this.setTab('evidence');
      this.openEvidence(deepLinkEvidence);
      return;
    }

    this.setTab('transaction');
    if (deepLinkTx && !this.caseMismatchNotice) {
      this.openTransaction(deepLinkTx);
    }
  }

  setTab(tab: CustodyTab): void {
    this.activeTab = tab;
    if (tab === 'transaction' && !this.transactionsLoaded) {
      this.loadTransactions();
    }
    if (tab === 'evidence' && !this.evidenceListLoaded) {
      this.loadEvidenceList();
    }
  }

  // --- Po transakciji -------------------------------------------------------------------

  loadTransactions(): void {
    const caseId = this.activeCaseId;
    if (!caseId) {
      return;
    }
    this.isLoadingList = true;
    this.listError = null;
    this.custodyLogApi.getCustodyTransactions(caseId).subscribe({
      next: (response) => {
        this.transactions = response.transactions;
        this.transactionsLoaded = true;
        this.isLoadingList = false;
        this.currentPage = 1;
      },
      error: () => {
        this.isLoadingList = false;
        this.listError = this.t('Neuspešno učitavanje spiska transakcija.', 'Failed to load the list of transactions.');
      },
    });
  }

  openTransaction(txId: string): void {
    const caseId = this.activeCaseId;
    if (!caseId) {
      return;
    }
    this.isLoadingChain = true;
    this.chainError = null;
    this.exportError = null;
    this.selectedChain = null;
    this.custodyLogApi.getCustodyChain(caseId, txId).subscribe({
      next: (chain) => {
        this.selectedChain = chain;
        this.isLoadingChain = false;
      },
      error: () => {
        this.isLoadingChain = false;
        this.chainError = this.t('Nema zabeleženih pristupa ovoj transakciji.', 'No recorded access to this transaction.');
      },
    });
  }

  closeTransaction(): void {
    this.selectedChain = null;
    this.chainError = null;
  }

  private exportPdf(): void {
    const caseId = this.activeCaseId;
    const txId = this.selectedChain?.tx_id;
    if (!caseId || !txId) {
      return;
    }
    this.isExportingPdf = true;
    this.exportError = null;
    this.custodyLogApi.exportCustodyPdf(caseId, txId, this.exportPdfLang).subscribe({
      next: (blob) => {
        this.isExportingPdf = false;
        this.saveBlob(blob, `lanac_dokaza_${txId}.pdf`);
      },
      error: () => {
        this.isExportingPdf = false;
        this.exportError = this.t('Neuspešno generisanje PDF izveštaja.', 'Failed to generate the PDF report.');
      },
    });
  }

  trackByTx(_index: number, item: CustodyTransactionSummary): string {
    return item.tx_id;
  }

  // --- Paginacija (Po transakciji) -------------------------------------------------------

  protected get totalPages(): number {
    return Math.max(1, Math.ceil(this.transactions.length / this.pageSize));
  }

  protected get pagedTransactions(): CustodyTransactionSummary[] {
    const start = (this.currentPage - 1) * this.pageSize;
    return this.transactions.slice(start, start + this.pageSize);
  }

  /** First/last row numbers on the current page (1-based), for "21–40 od 137". */
  protected get pageRangeLabel(): string {
    if (this.transactions.length === 0) {
      return '0';
    }
    const start = (this.currentPage - 1) * this.pageSize + 1;
    const end = Math.min(this.transactions.length, this.currentPage * this.pageSize);
    return `${start}–${end}`;
  }

  protected setPageSize(size: number): void {
    this.pageSize = size as (typeof this.pageSizeOptions)[number];
    this.currentPage = 1;
    this.scrollToTableTop();
  }

  protected goToPage(page: number): void {
    const next = Math.min(Math.max(1, page), this.totalPages);
    if (next === this.currentPage) {
      return;
    }
    this.currentPage = next;
    this.scrollToTableTop();
  }

  private scrollToTableTop(): void {
    this.tableTopRef?.nativeElement.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // --- Po dokaznom fajlu -----------------------------------------------------------------

  loadEvidenceList(): void {
    const caseId = this.activeCaseId;
    if (!caseId) {
      return;
    }
    this.isLoadingEvidenceList = true;
    this.evidenceListError = null;
    this.custodyLogApi.getCustodyEvidenceList(caseId).subscribe({
      next: (response) => {
        this.evidenceList = response.evidence;
        this.evidenceListLoaded = true;
        this.isLoadingEvidenceList = false;
      },
      error: () => {
        this.isLoadingEvidenceList = false;
        this.evidenceListError = this.t('Neuspešno učitavanje spiska dokaznih fajlova.', 'Failed to load the list of evidence files.');
      },
    });
  }

  openEvidence(storedName: string): void {
    const caseId = this.activeCaseId;
    if (!caseId) {
      return;
    }
    this.isLoadingEvidenceChain = true;
    this.evidenceChainError = null;
    this.evidenceExportError = null;
    this.selectedEvidenceChain = null;
    this.custodyLogApi.getCustodyEvidenceChain(caseId, storedName).subscribe({
      next: (chain) => {
        this.selectedEvidenceChain = chain;
        this.isLoadingEvidenceChain = false;
      },
      error: () => {
        this.isLoadingEvidenceChain = false;
        this.evidenceChainError = this.t('Nema zabeleženih pristupa ovom dokaznom fajlu.', 'No recorded access to this evidence file.');
      },
    });
  }

  closeEvidence(): void {
    this.selectedEvidenceChain = null;
    this.evidenceChainError = null;
  }

  private exportEvidencePdf(): void {
    const caseId = this.activeCaseId;
    const storedName = this.selectedEvidenceChain?.evidence_stored_name;
    if (!caseId || !storedName) {
      return;
    }
    this.isExportingEvidencePdf = true;
    this.evidenceExportError = null;
    this.custodyLogApi.exportCustodyEvidencePdf(caseId, storedName, this.exportPdfLang).subscribe({
      next: (blob) => {
        this.isExportingEvidencePdf = false;
        this.saveBlob(blob, `lanac_dokaza_${storedName}.pdf`);
      },
      error: () => {
        this.isExportingEvidencePdf = false;
        this.evidenceExportError = this.t('Neuspešno generisanje PDF izveštaja.', 'Failed to generate the PDF report.');
      },
    });
  }

  trackByEvidence(_index: number, item: CustodyEvidenceSummary): string {
    return item.evidence_stored_name;
  }

  // --- Zajedničko -------------------------------------------------------------------------

  /** Opens the language picker before either PDF export. The custody form itself needs no
   * further input at export time - the per-row signatures are already part of the record
   * (captured back when each access happened, see CustodyAccessDialogComponent), so this
   * dialog asks for exactly one thing: which language to print the form in. */
  protected openExportDialog(scope: 'transaction' | 'evidence'): void {
    this.pendingExportScope = scope;
    this.exportPdfLang = this.settings.lang();
    this.isExportLangDialogOpen = true;
  }

  protected closeExportDialog(): void {
    this.isExportLangDialogOpen = false;
    this.pendingExportScope = null;
  }

  protected confirmExport(): void {
    const scope = this.pendingExportScope;
    this.isExportLangDialogOpen = false;
    this.pendingExportScope = null;
    if (scope === 'transaction') {
      this.exportPdf();
    } else if (scope === 'evidence') {
      this.exportEvidencePdf();
    }
  }

  private saveBlob(blob: Blob, fileName: string): void {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = fileName;
    link.click();
    URL.revokeObjectURL(url);
  }

  formatAmount(amount: number | null, currency: string | null): string {
    if (amount == null) {
      return '—';
    }
    return currency ? `${amount} ${currency}` : String(amount);
  }

  /** "08.06.2026. 09:00" (sr) / "08/06/2026, 09:00" (en) from an ISO timestamp - local
   * time, locale matched to the active language like the other pages' own PDF exports
   * (e.g. taint-analysis.component.ts's `toLocaleString(this.taintPdfLang === 'sr' ? ...`). */
  formatDateTime(value: string | null): string {
    if (!value) {
      return '—';
    }
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }
    const locale = this.settings.lang() === 'sr' ? 'sr-RS' : 'en-GB';
    return parsed.toLocaleString(locale, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  }
}
