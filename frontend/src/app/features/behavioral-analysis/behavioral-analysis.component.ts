import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, OnInit } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { forkJoin, of } from 'rxjs';
import { catchError, distinctUntilChanged, map } from 'rxjs/operators';

import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { ApiService } from '../../core/services/api.service';
import { SettingsService } from '../../core/services/settings.service';
import {
  BehavioralAnalysisResult,
  CaseSummary,
  EvidenceEntry,
  NodeLinkGraphResponse,
  TimezoneEstimate,
} from '../../models/blockchain-forensics.models';

/** One completed (or failed) per-address behavioral run. Kept as a flat list so the page
 * can analyse several addresses at once and let the investigator flip between them. */
interface AddressBehavioralRun {
  address: string;
  result: BehavioralAnalysisResult | null;
  error: string | null;
}

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

  /** Free-text buffer for the "type an address" input. */
  protected address = '';
  protected isAnalyzing = false;
  /** Page-level error (e.g. the case address list could not be loaded). Per-address
   * failures live on their own run entry, see `runs`. */
  protected analysisError: string | null = null;

  /** Addresses staged for the next run (chips under the input). */
  protected queue: string[] = [];
  /** Every address that appears in the current case/evidence graph - the pick-list. */
  protected caseAddresses: string[] = [];
  protected isLoadingCaseAddresses = false;

  /** Completed runs, one per analysed address. */
  protected runs: AddressBehavioralRun[] = [];
  /** Which run's heatmap/stats are currently shown. */
  protected activeAddress: string | null = null;

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
    public readonly settings: SettingsService,
  ) {}

  /** Tiny inline translator: picks the Serbian or English string for the active language. */
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
        this.queue = [];
        this.clearResult();
        if (this.activeCase) {
          this.loadEvidenceOptions(this.activeCase.id);
          this.loadCaseAddresses();
        }
      });
  }

  /** Pulls every address in the current case/evidence graph so the investigator can pick
   * from a list instead of pasting hashes. Reuses the plain case graph endpoint (same one
   * the Graf/Taint pages seed from) - no new backend route. */
  private loadCaseAddresses(): void {
    const caseId = this.activeCase?.id;
    if (!caseId) {
      this.caseAddresses = [];
      return;
    }
    this.isLoadingCaseAddresses = true;
    this.api.getCaseGraph(caseId, this.selectedEvidence).subscribe({
      next: (graph: NodeLinkGraphResponse) => {
        this.caseAddresses = [...new Set(graph.nodes.map((node) => String(node.id)))].sort((a, b) =>
          a.localeCompare(b),
        );
        this.isLoadingCaseAddresses = false;
      },
      error: () => {
        this.caseAddresses = [];
        this.isLoadingCaseAddresses = false;
      },
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
    this.queue = [];
    this.clearResult();
    this.loadCaseAddresses();
  }

  // --- Staging the addresses to analyse --------------------------------------------

  /** Case addresses not already queued or analysed - the live options for the pick-list. */
  protected get availableCaseAddresses(): string[] {
    const taken = new Set([...this.queue, ...this.runs.map((run) => run.address)].map((a) => a.toLowerCase()));
    return this.caseAddresses.filter((address) => !taken.has(address.toLowerCase()));
  }

  protected isQueued(address: string): boolean {
    const lower = address.trim().toLowerCase();
    return this.queue.some((queued) => queued.toLowerCase() === lower);
  }

  /** Adds one address (from the text field or the pick-list) to the queue. */
  protected addToQueue(raw: string): void {
    const address = raw.trim();
    if (!address || this.isQueued(address)) {
      return;
    }
    this.queue = [...this.queue, address];
  }

  protected addTypedAddress(): void {
    this.addToQueue(this.address);
    this.address = '';
  }

  protected onPickAddress(address: string): void {
    if (address) {
      this.addToQueue(address);
    }
  }

  protected removeFromQueue(address: string): void {
    this.queue = this.queue.filter((queued) => queued !== address);
  }

  protected get canAnalyze(): boolean {
    return !!this.activeCase && this.queue.length > 0 && !this.isAnalyzing;
  }

  protected analyze(): void {
    const caseId = this.activeCase?.id;
    if (!caseId || this.queue.length === 0 || this.isAnalyzing) {
      return;
    }

    const addresses = [...this.queue];
    this.isAnalyzing = true;
    this.analysisError = null;

    forkJoin(
      addresses.map((address) =>
        this.api.getBehavioralAnalysis(caseId, address, this.selectedEvidence).pipe(
          map((result): AddressBehavioralRun => ({ address, result, error: null })),
          catchError((error: HttpErrorResponse) =>
            of<AddressBehavioralRun>({
              address,
              result: null,
              error:
                error.status === 404
                  ? this.t('Adresa nije pronađena u evidenciji ovog slučaja.', 'The address was not found in this case’s evidence.')
                  : this.t('Neuspešna analiza vremenskog obrasca.', 'The time-of-activity analysis failed.'),
            }),
          ),
        ),
      ),
    ).subscribe((runs) => {
      // Newly analysed addresses replace any earlier run for the same address, keep the rest.
      const analysed = new Set(runs.map((run) => run.address.toLowerCase()));
      this.runs = [...this.runs.filter((run) => !analysed.has(run.address.toLowerCase())), ...runs];
      this.queue = [];
      this.isAnalyzing = false;

      const firstOk = runs.find((run) => run.result) ?? this.runs.find((run) => run.result);
      if (firstOk && (!this.activeAddress || !this.activeRun)) {
        this.activeAddress = firstOk.address;
      }
    });
  }

  protected setActiveAddress(address: string): void {
    this.activeAddress = address;
  }

  protected removeRun(address: string): void {
    this.runs = this.runs.filter((run) => run.address !== address);
    if (this.activeAddress === address) {
      this.activeAddress = this.runs.find((run) => run.result)?.address ?? this.runs[0]?.address ?? null;
    }
  }

  protected get activeRun(): AddressBehavioralRun | null {
    return this.runs.find((run) => run.address === this.activeAddress) ?? null;
  }

  protected get activeResult(): BehavioralAnalysisResult | null {
    return this.activeRun?.result ?? null;
  }

  protected get failedRuns(): AddressBehavioralRun[] {
    return this.runs.filter((run) => run.error);
  }

  private clearResult(): void {
    this.runs = [];
    this.activeAddress = null;
    this.analysisError = null;
  }

  // --- Heatmap cell helpers ---------------------------------------------------------

  protected cellCount(day: string, hour: string): number {
    return this.activeResult?.hour_by_day_distribution?.[day]?.[hour] ?? 0;
  }

  private get maxCellCount(): number {
    if (!this.activeResult) {
      return 0;
    }
    let max = 0;
    for (const day of this.days) {
      for (const hour of this.hours) {
        max = Math.max(max, this.activeResult.hour_by_day_distribution[day]?.[hour] ?? 0);
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
    const unit = count === 1 ? this.t('transakcija', 'transaction') : this.t('transakcija', 'transactions');
    return `${day} ${hour}:00 UTC — ${count} ${unit}`;
  }

  /** Envelope from the earliest to the latest UTC hour-of-day with any activity at all
   * (aggregated across every day) - NOT one of the backend's own stats (which only report
   * the single busiest hour/day/cell); derived here directly from hourly_distribution,
   * since it is a simple read over data already on the page. Null when the address has no
   * timestamped activity at all. */
  protected get activePeriodLabel(): string | null {
    if (!this.activeResult) {
      return null;
    }
    const activeHours = this.hours.filter((hour) => (this.activeResult!.hourly_distribution[hour] ?? 0) > 0);
    if (activeHours.length === 0) {
      return null;
    }
    const first = activeHours[0];
    const last = activeHours[activeHours.length - 1];
    return `${first}:00–${last}:00 UTC`;
  }

  // --- Timezone-estimate labels: the backend sends English/Serbian-only strings for a few
  // of these fields, so they are re-derived here in the active UI language. ---

  private static readonly REGION_LABELS_SR: Record<string, string> = {
    Europe: 'Evropa',
    Africa: 'Afrika',
    'Middle East': 'Bliski istok',
    Asia: 'Azija',
    Oceania: 'Okeanija',
    'North America': 'Severna Amerika',
    'South America': 'Južna Amerika',
  };

  protected timezoneUnavailableMessage(tz: TimezoneEstimate): string {
    if (tz.reason === 'no_compatible_offset') {
      return this.t(
        'Aktivnost je razvučena kroz ceo dan — nijedna vremenska zona nije dovoljno kompatibilna za procenu.',
        'Activity is spread across the whole day — no time zone is compatible enough for an estimate.',
      );
    }
    // insufficient_transactions (and any future reason) fall back to the count message.
    return this.t(
      'Nedovoljno podataka za pouzdanu procenu vremenske zone (potrebno je bar 8 transakcija).',
      'Insufficient data for a reliable timezone estimate (at least 8 transactions are needed).',
    );
  }

  protected timezoneDisclaimer(tz: TimezoneEstimate): string {
    return this.t(
      tz.disclaimer ??
        'Vremenski obrazac predstavlja heuristički indikator i ne predstavlja dokaz stvarne lokacije vlasnika adrese.',
      'The time pattern is a heuristic indicator, not proof of the address owner’s actual location.',
    );
  }

  protected confidenceLabel(confidence: TimezoneEstimate['confidence']): string {
    if (confidence === 'High') {
      return this.t('visoka', 'high');
    }
    if (confidence === 'Medium') {
      return this.t('srednja', 'medium');
    }
    return this.t('niska', 'low');
  }

  protected regionLabels(regions: readonly string[] | undefined): string {
    if (!regions?.length) {
      return '—';
    }
    if (this.settings.lang() !== 'sr') {
      return regions.join(', ');
    }
    return regions.map((region) => BehavioralAnalysisComponent.REGION_LABELS_SR[region] ?? region).join(', ');
  }
}
