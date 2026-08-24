import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, OnInit } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { distinctUntilChanged, map } from 'rxjs/operators';

import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { ApiService } from '../../core/services/api.service';
import { BehavioralAnalysisResult, CaseSummary, EvidenceEntry } from '../../models/blockchain-forensics.models';

/** Behavioral Analysis / Time-of-Day Analysis - "kad je ova adresa aktivna, po satu i danu
 * u nedelji", deliberately separate page from Graph/Taint/Pathfinding (see those
 * components' own headers for their own separate questions). First version: UTC only, no
 * timezone/continent inference - see backend/app/analytics/behavioral_analysis.py.
 *
 * Reuses the same case/evidence-picker shell as Pathfinding/Taint (AnalysisStateService,
 * ApiService.getCase for the evidence list) but renders no cytoscape graph at all - the
 * heatmap below is the only visualization this page needs.
 */
@Component({
  selector: 'app-behavioral-analysis',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './behavioral-analysis.component.html',
  styleUrl: './behavioral-analysis.component.scss',
})
export class BehavioralAnalysisComponent implements OnInit {
  protected activeCase: CaseSummary | null = null;
  protected evidenceOptions: EvidenceEntry[] = [];
  protected selectedEvidence: string | null = null;

  protected address = '';
  protected isAnalyzing = false;
  protected analysisError: string | null = null;
  protected result: BehavioralAnalysisResult | null = null;

  // Fixed, always-fully-zero-filled axes - same order the backend itself uses (Monday
  // first, hour 00 first), so the grid never depends on whatever keys happen to be present
  // in a given response.
  protected readonly days: readonly string[] = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  protected readonly dayShortLabels: Record<string, string> = {
    Monday: 'Mon',
    Tuesday: 'Tue',
    Wednesday: 'Wed',
    Thursday: 'Thu',
    Friday: 'Fri',
    Saturday: 'Sat',
    Sunday: 'Sun',
  };
  protected readonly hours: readonly string[] = Array.from({ length: 24 }, (_, hour) => String(hour).padStart(2, '0'));

  // Sequential heatmap ramp: one hue (same hue family as the app's existing accent
  // #7dd3fc), light->dark. Dark surface, so "busiest" is the LIGHT end, matching what
  // #7dd3fc already means everywhere else in the app (the highlighted/important marker -
  // see e.g. pathfinding.component.ts's node/path highlight).
  private static readonly HEATMAP_HUE = 199;
  private static readonly HEATMAP_SATURATION = 90;
  private static readonly HEATMAP_MIN_LIGHTNESS = 22;
  private static readonly HEATMAP_MAX_LIGHTNESS = 78;

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

    this.api.getBehavioralAnalysis(caseId, address, this.selectedEvidence).subscribe({
      next: (result) => {
        this.result = result;
        this.isAnalyzing = false;
      },
      error: (error: HttpErrorResponse) => {
        this.isAnalyzing = false;
        this.analysisError =
          error.status === 404
            ? 'Adresa nije pronađena u evidenciji ovog slučaja.'
            : 'Neuspešna analiza vremenskog obrasca.';
      },
    });
  }

  private clearResult(): void {
    this.result = null;
    this.analysisError = null;
  }

  // --- Heatmap cell helpers ---------------------------------------------------------

  protected cellCount(day: string, hour: string): number {
    return this.result?.hour_by_day_distribution?.[day]?.[hour] ?? 0;
  }

  private get maxCellCount(): number {
    if (!this.result) {
      return 0;
    }
    let max = 0;
    for (const day of this.days) {
      for (const hour of this.hours) {
        max = Math.max(max, this.result.hour_by_day_distribution[day]?.[hour] ?? 0);
      }
    }
    return max;
  }

  /** A count of 0 is rendered as a flat, near-invisible surface tile - deliberately NOT
   * part of the hue ramp, so "no activity" reads as structurally different from
   * "least-busy but still active", not just the dimmest step of the same color (same
   * distinction a GitHub-style contribution graph makes). Non-zero counts use sqrt
   * scaling (not linear) so mid-range differences stay visible even when one cell
   * dominates the whole week. */
  protected cellColor(day: string, hour: string): string {
    const count = this.cellCount(day, hour);
    if (count <= 0) {
      return 'rgba(148, 163, 184, 0.05)';
    }
    const max = this.maxCellCount;
    const intensity = max > 0 ? Math.sqrt(count / max) : 0;
    const lightness =
      BehavioralAnalysisComponent.HEATMAP_MIN_LIGHTNESS +
      (BehavioralAnalysisComponent.HEATMAP_MAX_LIGHTNESS - BehavioralAnalysisComponent.HEATMAP_MIN_LIGHTNESS) * intensity;
    return `hsl(${BehavioralAnalysisComponent.HEATMAP_HUE}deg ${BehavioralAnalysisComponent.HEATMAP_SATURATION}% ${lightness.toFixed(0)}%)`;
  }

  protected cellLabel(day: string, hour: string): string {
    const count = this.cellCount(day, hour);
    return `${day} ${hour}:00 UTC — ${count} ${count === 1 ? 'transaction' : 'transactions'}`;
  }

  /** Envelope from the earliest to the latest UTC hour-of-day with any activity at all
   * (aggregated across every day) - NOT one of the backend's own stats (which only report
   * the single busiest hour/day/cell); derived here directly from hourly_distribution,
   * since it is a simple read over data already on the page. Null when the address has no
   * timestamped activity at all. */
  protected get activePeriodLabel(): string | null {
    if (!this.result) {
      return null;
    }
    const activeHours = this.hours.filter((hour) => (this.result!.hourly_distribution[hour] ?? 0) > 0);
    if (activeHours.length === 0) {
      return null;
    }
    const first = activeHours[0];
    const last = activeHours[activeHours.length - 1];
    return `${first}:00–${last}:00 UTC`;
  }
}
