import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { ApiService } from '../../core/services/api.service';
import {
  CustodyChain,
  CustodyEvidenceChain,
  CustodyEvidenceSummary,
  CustodyTransactionSummary,
} from '../../models/blockchain-forensics.models';

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
  imports: [CommonModule, RouterLink],
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

  // --- Po dokaznom fajlu ---
  protected evidenceList: CustodyEvidenceSummary[] = [];
  protected isLoadingEvidenceList = false;
  protected evidenceListError: string | null = null;
  protected selectedEvidenceChain: CustodyEvidenceChain | null = null;
  protected isLoadingEvidenceChain = false;
  protected evidenceChainError: string | null = null;
  protected isExportingEvidencePdf = false;
  protected evidenceExportError: string | null = null;

  private transactionsLoaded = false;
  private evidenceListLoaded = false;

  /** Set when a deep link points at a transaction/evidence file belonging to a DIFFERENT
   * case than the one currently active - switching the globally selected case as a side
   * effect of opening this page would be surprising, so this is surfaced as a notice. */
  protected caseMismatchNotice: string | null = null;

  constructor(
    private readonly api: ApiService,
    protected readonly state: AnalysisStateService,
    private readonly route: ActivatedRoute,
  ) {}

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
      this.caseMismatchNotice =
        `Ovaj link se odnosi na slučaj ${deepLinkCase}, a trenutno je izabran drugi slučaj. ` +
        'Izaberite taj slučaj na stranici "Slučajevi" da biste videli njegov lanac dokaza.';
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
    this.api.getCustodyTransactions(caseId).subscribe({
      next: (response) => {
        this.transactions = response.transactions;
        this.transactionsLoaded = true;
        this.isLoadingList = false;
      },
      error: () => {
        this.isLoadingList = false;
        this.listError = 'Neuspešno učitavanje spiska transakcija.';
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
    this.api.getCustodyChain(caseId, txId).subscribe({
      next: (chain) => {
        this.selectedChain = chain;
        this.isLoadingChain = false;
      },
      error: () => {
        this.isLoadingChain = false;
        this.chainError = 'Nema zabeleženih pristupa ovoj transakciji.';
      },
    });
  }

  closeTransaction(): void {
    this.selectedChain = null;
    this.chainError = null;
  }

  exportPdf(): void {
    const caseId = this.activeCaseId;
    const txId = this.selectedChain?.tx_id;
    if (!caseId || !txId) {
      return;
    }
    this.isExportingPdf = true;
    this.exportError = null;
    this.api.exportCustodyPdf(caseId, txId).subscribe({
      next: (blob) => {
        this.isExportingPdf = false;
        this.saveBlob(blob, `lanac_dokaza_${txId}.pdf`);
      },
      error: () => {
        this.isExportingPdf = false;
        this.exportError = 'Neuspešno generisanje PDF izveštaja.';
      },
    });
  }

  trackByTx(_index: number, item: CustodyTransactionSummary): string {
    return item.tx_id;
  }

  // --- Po dokaznom fajlu -----------------------------------------------------------------

  loadEvidenceList(): void {
    const caseId = this.activeCaseId;
    if (!caseId) {
      return;
    }
    this.isLoadingEvidenceList = true;
    this.evidenceListError = null;
    this.api.getCustodyEvidenceList(caseId).subscribe({
      next: (response) => {
        this.evidenceList = response.evidence;
        this.evidenceListLoaded = true;
        this.isLoadingEvidenceList = false;
      },
      error: () => {
        this.isLoadingEvidenceList = false;
        this.evidenceListError = 'Neuspešno učitavanje spiska dokaznih fajlova.';
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
    this.api.getCustodyEvidenceChain(caseId, storedName).subscribe({
      next: (chain) => {
        this.selectedEvidenceChain = chain;
        this.isLoadingEvidenceChain = false;
      },
      error: () => {
        this.isLoadingEvidenceChain = false;
        this.evidenceChainError = 'Nema zabeleženih pristupa ovom dokaznom fajlu.';
      },
    });
  }

  closeEvidence(): void {
    this.selectedEvidenceChain = null;
    this.evidenceChainError = null;
  }

  exportEvidencePdf(): void {
    const caseId = this.activeCaseId;
    const storedName = this.selectedEvidenceChain?.evidence_stored_name;
    if (!caseId || !storedName) {
      return;
    }
    this.isExportingEvidencePdf = true;
    this.evidenceExportError = null;
    this.api.exportCustodyEvidencePdf(caseId, storedName).subscribe({
      next: (blob) => {
        this.isExportingEvidencePdf = false;
        this.saveBlob(blob, `lanac_dokaza_${storedName}.pdf`);
      },
      error: () => {
        this.isExportingEvidencePdf = false;
        this.evidenceExportError = 'Neuspešno generisanje PDF izveštaja.';
      },
    });
  }

  trackByEvidence(_index: number, item: CustodyEvidenceSummary): string {
    return item.evidence_stored_name;
  }

  // --- Zajedničko -------------------------------------------------------------------------

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

  /** "08.06.2026. 09:00" from an ISO timestamp - local time, matching how dates read
   * elsewhere in the app (activity log, taint-analysis exports). */
  formatDateTime(value: string | null): string {
    if (!value) {
      return '—';
    }
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }
    return parsed.toLocaleString('sr-RS', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  }
}
