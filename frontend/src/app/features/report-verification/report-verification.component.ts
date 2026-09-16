import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ReportVerificationApiService } from './report-verification.api';
import { SettingsService } from '../../core/services/settings.service';
import { ReportVerificationResult } from './report-verification.models';

@Component({
  selector: 'app-report-verification',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './report-verification.component.html',
  styleUrl: './report-verification.component.scss',
})
export class ReportVerificationComponent {
  protected code = '';
  protected contentHash = '';
  protected isChecking = false;
  protected result: ReportVerificationResult | null = null;
  protected errorMessage: string | null = null;

  constructor(
    private readonly reportVerificationApi: ReportVerificationApiService,
    public readonly settings: SettingsService,
  ) {}

  /** Tiny inline translator: picks the Serbian or English string for the active language
   * (same pattern as the other analysis pages' own t()). */
  protected t(sr: string, en: string): string {
    return this.settings.lang() === 'sr' ? sr : en;
  }

  get canCheck(): boolean {
    return this.code.trim().length > 0 && !this.isChecking;
  }

  verify(): void {
    if (!this.canCheck) {
      return;
    }
    this.isChecking = true;
    this.errorMessage = null;
    this.result = null;

    this.reportVerificationApi.verifyReport(this.code.trim(), this.contentHash.trim() || null).subscribe({
      next: (result) => {
        this.result = result;
        this.isChecking = false;
      },
      error: () => {
        this.isChecking = false;
        this.errorMessage = this.t('Provera nije uspela. Pokušajte ponovo.', 'The check failed. Please try again.');
      },
    });
  }

  reset(): void {
    this.code = '';
    this.contentHash = '';
    this.result = null;
    this.errorMessage = null;
  }

  /** Three outcomes that must never be confused: the document checks out, the document was
   * altered, or the code is not in the registry at all. The last one is NOT a failed
   * content check - it means this report did not come from this installation. */
  get outcome(): 'valid' | 'tampered' | 'unknown' | 'found-unchecked' | null {
    if (!this.result) {
      return null;
    }
    if (!this.result.found) {
      return 'unknown';
    }
    if (this.result.matches === null) {
      return 'found-unchecked';
    }
    return this.result.matches ? 'valid' : 'tampered';
  }

  /** Every `summary` key any report type registers via CaseDataApiService.registerReport - one
   * shared, generic verification page for all of them (taint, pathfinding, behavioral,
   * dex_swap, graph_analysis, case_triage), so a key any of those adds here shows a real
   * label instead of falling back to its raw snake_case name. */
  private readonly summaryLabels: Record<string, [string, string]> = {
    // Taint (taint-analysis.component.ts)
    tainted_addresses: ['Zaprljanih adresa', 'Tainted addresses'],
    cash_out_points: ['Tačaka unovčavanja', 'Cash-out points'],
    seeds: ['Izvora (seed)', 'Seed addresses'],
    // Pathfinding (pathfinding.component.ts)
    hops: ['Broj skokova', 'Hops'],
    destination_mode: ['Način određivanja odredišta', 'Destination mode'],
    taint_trace: ['Taint provera puta', 'Taint trace'],
    // Behavioral analysis (behavioral-analysis.component.ts)
    addresses: ['Analiziranih adresa', 'Addresses analysed'],
    evidence_scope: ['Obim evidencije', 'Evidence scope'],
    // DEX Swap analysis (dex-swap-analysis.component.ts)
    addresses_analyzed: ['Analiziranih adresa', 'Addresses analysed'],
    total_events: ['Ukupno događaja', 'Total events'],
    detected_count: ['Detektovano', 'Detected'],
    potential_count: ['Moguće', 'Potential'],
    // Graph analysis / case triage (report-export.component.ts)
    nodes: ['Čvorova', 'Nodes'],
    edges: ['Veza', 'Edges'],
    blacklisted: ['Na crnoj listi', 'Blacklisted'],
    high_risk: ['Visok rizik', 'High risk'],
    clusters: ['Klastera', 'Clusters'],
    flagged_nodes: ['Označenih čvorova', 'Flagged nodes'],
    analyzed: ['Analitika primenjena', 'Analytics applied'],
    rows: ['Redova podataka', 'Data rows'],
  };

  get summaryRows(): Array<{ label: string; value: string }> {
    const summary = this.result?.entry?.summary ?? {};
    return Object.entries(summary).map(([key, value]) => {
      const labelPair = this.summaryLabels[key];
      return { label: labelPair ? this.t(labelPair[0], labelPair[1]) : key, value: String(value) };
    });
  }
}
