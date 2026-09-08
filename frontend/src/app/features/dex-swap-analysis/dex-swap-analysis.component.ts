import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, OnInit } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { distinctUntilChanged, map } from 'rxjs/operators';

import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { ApiService } from '../../core/services/api.service';
import { CaseSummary, DexSwapAnalysisResult, DexSwapEvent, EvidenceEntry } from '../../models/blockchain-forensics.models';

/** DEX Swap Analysis - "did this address swap one token for another through a DEX",
 * deliberately separate page from Graph/Taint/Pathfinding/Behavioral (see those
 * components' own headers for their own separate questions). This is a HEURISTIC, not a
 * proof - see backend/app/analytics/dex_swap_analysis.py and DEX-SWAP-ANALIZA.md.
 *
 * Reuses the same case/evidence-picker shell as the sibling analysis pages
 * (AnalysisStateService, ApiService.getCase for the evidence list), but the page itself
 * stays deliberately small: one address field, one button, a flat list of swap cards. No
 * charts, no cytoscape graph, no side panel of extra stats - the single number that
 * matters on each card is INPUT TOKEN -> DEX -> OUTPUT TOKEN.
 */
@Component({
  selector: 'app-dex-swap-analysis',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './dex-swap-analysis.component.html',
  styleUrl: './dex-swap-analysis.component.scss',
})
export class DexSwapAnalysisComponent implements OnInit {
  protected activeCase: CaseSummary | null = null;
  protected evidenceOptions: EvidenceEntry[] = [];
  protected selectedEvidence: string | null = null;

  protected address = '';
  protected isAnalyzing = false;
  protected analysisError: string | null = null;
  protected result: DexSwapAnalysisResult | null = null;

  constructor(
    private readonly state: AnalysisStateService,
    private readonly api: ApiService,
    private readonly destroyRef: DestroyRef,
  ) {}

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
    this.api.getCase(caseId).subscribe({
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

  protected get canAnalyze(): boolean {
    return !!this.activeCase && this.address.trim().length > 0 && !this.isAnalyzing;
  }

  protected analyze(): void {
    const caseId = this.activeCase?.id;
    const address = this.address.trim();
    if (!caseId || !address || this.isAnalyzing) {
      return;
    }

    this.isAnalyzing = true;
    this.analysisError = null;
    this.result = null;

    this.api.getDexSwapAnalysis(caseId, address, this.selectedEvidence).subscribe({
      next: (result) => {
        this.result = result;
        this.isAnalyzing = false;
      },
      error: (error: HttpErrorResponse) => {
        this.isAnalyzing = false;
        this.analysisError =
          error.status === 404 ? 'Adresa nije pronađena u evidenciji ovog slučaja.' : 'Neuspešna DEX swap analiza.';
      },
    });
  }

  private clearResult(): void {
    this.result = null;
    this.analysisError = null;
  }

  // --- Card display helpers ----------------------------------------------------------

  /** null means the evidence never declared a currency for that leg - shown as a plain
   * "?" (with a title explaining why) rather than guessing a symbol. */
  protected tokenLabel(token: string | null): string {
    return token ?? '?';
  }

  protected tokenTitle(token: string | null): string | null {
    return token ? null : 'Valuta/token nije deklarisana u evidenciji za ovaj krak.';
  }

  /** A single representative tx hash for the card footer: for a Detected swap both legs
   * share one real hash, so either field works; for a Potential swap the two legs were
   * never confirmed to be the same on-chain transaction, so both are shown (when
   * present) rather than picking one and implying a certainty that isn't there. */
  protected txHashLines(event: DexSwapEvent): { label: string; hash: string }[] {
    if (event.confidence === 'Detected') {
      const hash = event.input_transaction_hash ?? event.output_transaction_hash;
      return hash ? [{ label: 'Tx', hash }] : [];
    }

    const lines: { label: string; hash: string }[] = [];
    if (event.input_transaction_hash) {
      lines.push({ label: 'Tx in', hash: event.input_transaction_hash });
    }
    if (event.output_transaction_hash) {
      lines.push({ label: 'Tx out', hash: event.output_transaction_hash });
    }
    return lines;
  }
}
