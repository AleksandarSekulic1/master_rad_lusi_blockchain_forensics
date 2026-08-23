import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../core/services/api.service';
import { ReportVerificationResult } from '../../models/blockchain-forensics.models';

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

  constructor(private readonly api: ApiService) {}

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

    this.api.verifyReport(this.code.trim(), this.contentHash.trim() || null).subscribe({
      next: (result) => {
        this.result = result;
        this.isChecking = false;
      },
      error: () => {
        this.isChecking = false;
        this.errorMessage = 'Provera nije uspela. Pokušajte ponovo.';
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

  get summaryRows(): Array<{ label: string; value: string }> {
    const summary = this.result?.entry?.summary ?? {};
    const labels: Record<string, string> = {
      tainted_addresses: 'Zaprljanih adresa',
      cash_out_points: 'Tačaka unovčavanja',
      seeds: 'Izvora (seed)',
      // Pathfinding Analysis izveštaji (POST /cases/{id}/pathfinding) koriste istu
      // generičku registraciju/proveru izveštaja kao Taint analiza - samo dodaju svoje
      // ključeve u summary, bez ikakve izmene ove (deljene, ne-taint) stranice.
      hops: 'Broj skokova',
      destination_mode: 'Način određivanja odredišta',
      taint_trace: 'Taint provera puta',
    };
    return Object.entries(summary).map(([key, value]) => ({
      label: labels[key] ?? key,
      value: String(value),
    }));
  }
}
