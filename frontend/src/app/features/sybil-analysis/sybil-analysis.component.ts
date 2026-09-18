import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, OnInit } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { distinctUntilChanged, map } from 'rxjs/operators';

import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { CaseDataApiService } from '../../core/services/case-data.api';
import { SybilAnalysisApiService } from './sybil-analysis.api';
import { SettingsService } from '../../core/services/settings.service';
import { CaseSummary, EvidenceEntry, SybilAnalysisResult, SybilCluster, TransactionCustodyEntry } from '../../core/models/shared.models';
import { CustodyAccessDialogComponent } from '../custody-access-dialog/custody-access-dialog.component';

/** Sybil & Bot Network Analysis - "did several DIFFERENT addresses interact with the same
 * smart contract and/or the same function in a short, synchronized burst", deliberately
 * separate page from Graph/Taint/Pathfinding/Behavioral/DEX Swaps/Token Approval (see
 * those components' own headers for their own separate questions). This is a HEURISTIC,
 * never proof that the flagged addresses share a real-world owner - see
 * backend/app/analytics/sybil_analysis.py and SYBIL-ANALIZA.md.
 *
 * Unlike DEX Swaps/Behavioral (per-address queue), a Sybil cluster is inherently case-wide
 * - it spans several addresses by definition - so this page runs ONE query at a time over
 * the whole (optionally address/contract-scoped) evidence, same shape as DEX Swaps' "scan
 * all addresses" mode.
 */
@Component({
  selector: 'app-sybil-analysis',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, CustodyAccessDialogComponent],
  templateUrl: './sybil-analysis.component.html',
  styleUrl: './sybil-analysis.component.scss',
})
export class SybilAnalysisComponent implements OnInit {
  protected activeCase: CaseSummary | null = null;
  protected evidenceOptions: EvidenceEntry[] = [];
  protected selectedEvidence: string | null = null;

  protected addressFilter = '';
  protected contractFilter = '';
  protected timeWindowSeconds = 300;
  protected minAddresses = 3;

  protected isAnalyzing = false;
  protected analysisError: string | null = null;
  protected result: SybilAnalysisResult | null = null;

  /** Which cluster cards currently show their transaction drill-down / full reasons. */
  private readonly expandedClusters = new Set<string>();

  // --- Lanac dokaza (see SYBIL-ANALIZA.md / LANAC-DOKAZA.md) - scanning the case's
  // evidence for synchronized clusters is a deliberate access to every transaction it
  // touches, same as "Pokreni taint analizu"/"FIND PATH"/"Analiziraj graf"/DEX Swaps'
  // ANALYZE, so it goes through the same shared custody-access dialog. ---
  protected isCustodyDialogOpen = false;
  protected custodyDialogError: string | null = null;

  constructor(
    private readonly state: AnalysisStateService,
    private readonly caseData: CaseDataApiService,
    private readonly sybilAnalysisApi: SybilAnalysisApiService,
    private readonly destroyRef: DestroyRef,
    public readonly settings: SettingsService,
  ) {}

  protected t(sr: string, en: string): string {
    return this.settings.lang() === 'sr' ? sr : en;
  }

  ngOnInit(): void {
    this.state.selectedCase$
      .pipe(
        map((caseSummary) => caseSummary?.id ?? null),
        distinctUntilChanged(),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(() => {
        this.activeCase = this.state.selectedCaseSnapshot;
        this.selectedEvidence = null;
        this.evidenceOptions = [];
        this.clearResult();
        if (this.activeCase) {
          this.loadEvidenceOptions(this.activeCase.id);
        }
      });
  }

  private loadEvidenceOptions(caseId: string): void {
    this.caseData.getCase(caseId).subscribe({
      next: (caseDetail) => {
        this.evidenceOptions = caseDetail.evidence;
      },
      error: () => {
        this.evidenceOptions = [];
      },
    });
  }

  protected onEvidenceSelected(storedName: string): void {
    this.selectedEvidence = storedName || null;
    this.clearResult();
  }

  protected get selectedEvidenceFileName(): string | null {
    if (!this.selectedEvidence) {
      return null;
    }
    return this.evidenceOptions.find((entry) => entry.stored_name === this.selectedEvidence)?.file_name ?? null;
  }

  protected get canAnalyze(): boolean {
    return !!this.activeCase && !this.isAnalyzing;
  }

  protected openCustodyDialog(): void {
    if (!this.canAnalyze) {
      return;
    }
    this.custodyDialogError = null;
    this.isCustodyDialogOpen = true;
  }

  protected closeCustodyDialog(): void {
    this.isCustodyDialogOpen = false;
  }

  protected confirmCustodyAndAnalyze(custody: TransactionCustodyEntry): void {
    const caseId = this.activeCase?.id;
    if (!caseId || this.isAnalyzing) {
      return;
    }

    this.isAnalyzing = true;
    this.analysisError = null;
    this.custodyDialogError = null;

    const address = this.addressFilter.trim() || null;
    const contract = this.contractFilter.trim() || null;

    this.sybilAnalysisApi
      .runSybilAnalysis(caseId, address, contract, this.selectedEvidence, this.timeWindowSeconds, this.minAddresses, custody)
      .subscribe({
        next: (result) => {
          this.isAnalyzing = false;
          this.result = result;
          this.expandedClusters.clear();
          this.isCustodyDialogOpen = false;
        },
        error: (error: HttpErrorResponse) => {
          this.isAnalyzing = false;
          const message =
            error.status === 404
              ? this.t('Adresa/kontrakt nije pronađen u evidenciji ovog slučaja.', 'The address/contract was not found in this case’s evidence.')
              : this.t('Neuspešna Sybil analiza.', 'The Sybil analysis failed.');
          this.custodyDialogError = message;
        },
      });
  }

  private clearResult(): void {
    this.result = null;
    this.analysisError = null;
    this.isCustodyDialogOpen = false;
    this.custodyDialogError = null;
    this.expandedClusters.clear();
  }

  // --- Cluster card display helpers ---------------------------------------------------

  protected toggleExpanded(clusterId: string): void {
    if (this.expandedClusters.has(clusterId)) {
      this.expandedClusters.delete(clusterId);
    } else {
      this.expandedClusters.add(clusterId);
    }
  }

  protected isExpanded(clusterId: string): boolean {
    return this.expandedClusters.has(clusterId);
  }

  protected riskLabel(level: SybilCluster['risk_level']): string {
    switch (level) {
      case 'critical':
        return this.t('Kritičan', 'Critical');
      case 'high':
        return this.t('Visok', 'High');
      case 'medium':
        return this.t('Srednji', 'Medium');
      case 'low':
        return this.t('Nizak', 'Low');
      default:
        return this.t('Nema', 'None');
    }
  }
}
