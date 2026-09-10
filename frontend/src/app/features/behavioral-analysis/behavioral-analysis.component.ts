import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, OnInit, ViewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { firstValueFrom, forkJoin, of } from 'rxjs';
import { catchError, distinctUntilChanged, map } from 'rxjs/operators';

import { jsPDF } from 'jspdf';
import autoTable from 'jspdf-autotable';

import { SignaturePadComponent } from '../../core/components/signature-pad/signature-pad.component';
import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { ApiService } from '../../core/services/api.service';
import { AuthService } from '../../core/services/auth.service';
import { AppLang, SettingsService } from '../../core/services/settings.service';
import {
  BehavioralAnalysisPeakPeriod,
  BehavioralAnalysisResult,
  CaseSummary,
  EvidenceEntry,
  NodeLinkGraphResponse,
  TimezoneEstimate,
  TransactionCustodyEntry,
} from '../../models/blockchain-forensics.models';
import { CustodyAccessDialogComponent } from '../custody-access-dialog/custody-access-dialog.component';
import { estimateTimezoneCompatibility } from './timezone-heuristic';

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
  imports: [CommonModule, FormsModule, RouterLink, CustodyAccessDialogComponent, SignaturePadComponent],
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

  // --- Lanac dokaza: razlog pristupa + potpis pre svakog pokretanja, isti dijalog kao na
  // Taint / Pathfinding / DEX Swaps. Running the analysis re-reads every transaction of the
  // selected evidence to build the graph, so it counts as a deliberate access. ---
  protected isCustodyDialogOpen = false;
  protected custodyDialogError: string | null = null;

  // --- PDF izveštaj: potpis + pečat + kontrolni broj, ista struktura kao Taint /
  // Pathfinding (samostalna kopija tog obrasca, ne deljeni modul). ---
  @ViewChild(SignaturePadComponent) private signaturePad?: SignaturePadComponent;
  protected isSignatureDialogOpen = false;
  protected signatureDeclarationAccepted = false;
  protected signatureError: string | null = null;
  protected isExportingPdf = false;
  /** Language the exported PDF is produced in - seeded from the app toggle when the signing
   * dialog opens, then confirmed by the analyst on the modal. */
  protected behavioralPdfLang: AppLang = 'sr';

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
    private readonly auth: AuthService,
    private readonly destroyRef: DestroyRef,
    public readonly settings: SettingsService,
  ) {}

  /** Tiny inline translator: picks the Serbian or English string for the active language. */
  protected t(sr: string, en: string): string {
    return this.settings.lang() === 'sr' ? sr : en;
  }

  /** PDF-string translator: SR or EN by the language chosen on the signing modal, then
   * ASCII-folded (harmless for English) since the PDF core font is Latin-1 only. */
  private lx(sr: string, en: string): string {
    return this.asciiSafe(this.behavioralPdfLang === 'sr' ? sr : en);
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

  /** Stages every case address that isn't already queued/analysed - one click instead of
   * picking them one by one from the list. */
  protected addAllCaseAddresses(): void {
    if (this.availableCaseAddresses.length === 0) {
      return;
    }
    this.queue = [...this.queue, ...this.availableCaseAddresses];
  }

  protected removeFromQueue(address: string): void {
    this.queue = this.queue.filter((queued) => queued !== address);
  }

  protected clearQueue(): void {
    this.queue = [];
  }

  protected get canAnalyze(): boolean {
    return !!this.activeCase && this.queue.length > 0 && !this.isAnalyzing;
  }

  /** "Analiziraj" no longer runs anything directly - it opens the chain-of-custody dialog
   * (access reason + signature), same gate as Taint / Pathfinding / DEX Swaps. The run
   * only happens on confirmCustodyAndAnalyze(). */
  protected analyze(): void {
    if (!this.canAnalyze) {
      return;
    }
    this.custodyDialogError = null;
    this.isCustodyDialogOpen = true;
  }

  protected closeCustodyDialog(): void {
    this.isCustodyDialogOpen = false;
  }

  /** File name of the currently scoped evidence, for the custody dialog's default
   * "identifikator dokaznog materijala" - null means the combined view (all evidence). */
  protected get selectedEvidenceFileName(): string | null {
    if (!this.selectedEvidence) {
      return null;
    }
    return this.evidenceOptions.find((entry) => entry.stored_name === this.selectedEvidence)?.file_name ?? null;
  }

  /** Runs the whole queue with one signed custody entry - the scope of that one access is
   * the full selected evidence (see LANAC-DOKAZA.md §2), regardless of how many addresses
   * are analysed off it. */
  protected confirmCustodyAndAnalyze(custody: TransactionCustodyEntry): void {
    const caseId = this.activeCase?.id;
    if (!caseId || this.queue.length === 0 || this.isAnalyzing) {
      return;
    }

    const addresses = [...this.queue];
    this.isAnalyzing = true;
    this.analysisError = null;
    this.custodyDialogError = null;

    forkJoin(
      addresses.map((address) =>
        this.api.runBehavioralAnalysis(caseId, address, this.selectedEvidence, custody).pipe(
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
      this.isAnalyzing = false;

      const anyOk = runs.some((run) => run.result);
      if (anyOk) {
        this.queue = [];
        this.isCustodyDialogOpen = false;
      } else {
        // Nothing went through - keep the dialog open so the analyst sees why.
        this.custodyDialogError = runs[0]?.error ?? this.t('Analiza nije uspela.', 'The analysis failed.');
      }

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
    if (this.activeAddress === address || (this.isCombinedActive && !this.canShowCombined)) {
      this.activeAddress = this.runs.find((run) => run.result)?.address ?? this.runs[0]?.address ?? null;
    }
  }

  // --- Optional "all addresses combined" view -------------------------------------------
  // A synthetic run whose distributions are the element-wise SUM of every successful
  // per-address result. It does NOT change any individual run - it is a separate, opt-in
  // tab. Internal transfers between two analysed addresses are counted once per side (i.e.
  // twice) - stated in the UI/report. The timezone estimate is recomputed from the summed
  // hourly distribution with the same heuristic the backend uses (see ./timezone-heuristic).
  private static readonly COMBINED_KEY = '__combined__';

  protected get combinedKey(): string {
    return BehavioralAnalysisComponent.COMBINED_KEY;
  }

  protected get canShowCombined(): boolean {
    return this.exportableRuns.length >= 2;
  }

  protected get isCombinedActive(): boolean {
    return this.activeAddress === BehavioralAnalysisComponent.COMBINED_KEY;
  }

  protected showCombined(): void {
    this.activeAddress = BehavioralAnalysisComponent.COMBINED_KEY;
  }

  protected get combinedRun(): AddressBehavioralRun | null {
    const runs = this.exportableRuns;
    if (runs.length < 2) {
      return null;
    }
    const hourly: Record<string, number> = {};
    const daily: Record<string, number> = {};
    const grid: Record<string, Record<string, number>> = {};
    for (const day of this.days) {
      daily[day] = 0;
      grid[day] = {};
      for (const hour of this.hours) {
        grid[day][hour] = 0;
      }
    }
    for (const hour of this.hours) {
      hourly[hour] = 0;
    }

    let total = 0;
    for (const run of runs) {
      const r = run.result!;
      total += r.total_transactions;
      for (const hour of this.hours) {
        hourly[hour] += r.hourly_distribution[hour] ?? 0;
      }
      for (const day of this.days) {
        daily[day] += r.day_of_week_distribution[day] ?? 0;
        for (const hour of this.hours) {
          grid[day][hour] += r.hour_by_day_distribution?.[day]?.[hour] ?? 0;
        }
      }
    }

    let mostActiveHour: string | null = null;
    let mostActiveHourCount = 0;
    for (const hour of this.hours) {
      if (hourly[hour] > mostActiveHourCount) {
        mostActiveHourCount = hourly[hour];
        mostActiveHour = hour;
      }
    }
    let mostActiveDay: string | null = null;
    let mostActiveDayCount = 0;
    for (const day of this.days) {
      if (daily[day] > mostActiveDayCount) {
        mostActiveDayCount = daily[day];
        mostActiveDay = day;
      }
    }
    let peak: BehavioralAnalysisPeakPeriod | null = null;
    let peakCount = 0;
    for (const day of this.days) {
      for (const hour of this.hours) {
        if (grid[day][hour] > peakCount) {
          peakCount = grid[day][hour];
          peak = { day, hour, count: grid[day][hour], label: `${day} ${hour}:00 UTC` };
        }
      }
    }

    const first = runs[0].result!;
    const combined: BehavioralAnalysisResult = {
      case_id: first.case_id,
      evidence: first.evidence,
      address: BehavioralAnalysisComponent.COMBINED_KEY,
      total_transactions: total,
      hourly_distribution: hourly,
      day_of_week_distribution: daily,
      hour_by_day_distribution: grid,
      timezone_estimate: estimateTimezoneCompatibility(hourly, total),
      stats: {
        most_active_hour: mostActiveHour,
        most_active_hour_count: mostActiveHourCount,
        most_active_day: mostActiveDay,
        most_active_day_count: mostActiveDayCount,
        peak_period: peak,
        total_analyzed_transactions: total,
      },
      generated_at: first.generated_at,
    };
    return { address: BehavioralAnalysisComponent.COMBINED_KEY, result: combined, error: null };
  }

  protected get activeRun(): AddressBehavioralRun | null {
    if (this.isCombinedActive) {
      return this.combinedRun;
    }
    return this.runs.find((run) => run.address === this.activeAddress) ?? null;
  }

  protected get activeResult(): BehavioralAnalysisResult | null {
    return this.activeRun?.result ?? null;
  }

  /** Friendly label for the currently shown run - the address, or "N addresses combined". */
  protected get activeAddressLabel(): string {
    if (this.isCombinedActive) {
      return this.t(`${this.exportableRuns.length} adresa zajedno`, `${this.exportableRuns.length} addresses combined`);
    }
    return this.activeAddress ?? '';
  }

  protected get failedRuns(): AddressBehavioralRun[] {
    return this.runs.filter((run) => run.error);
  }

  private clearResult(): void {
    this.runs = [];
    this.activeAddress = null;
    this.analysisError = null;
    this.isCustodyDialogOpen = false;
    this.custodyDialogError = null;
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

  // ==========================================================================================
  // PDF izveštaj: potpis, pečat, kontrolni broj, provera verodostojnosti - ista struktura
  // kao Taint / Pathfinding (samostalna kopija tog obrasca ovde, ne deljeni modul). Koristi
  // iste generičke rute POST /api/v1/reports/register i GET /api/v1/reports/verify.
  // ==========================================================================================

  private static readonly PDF_NAVY: [number, number, number] = [13, 24, 40];
  private static readonly PDF_ACCENT: [number, number, number] = [43, 130, 191];
  private static readonly PDF_TEXT_GRAY: [number, number, number] = [100, 112, 128];
  private static readonly PDF_TEXT_DARK: [number, number, number] = [24, 28, 36];
  private static readonly PDF_WHITE: [number, number, number] = [255, 255, 255];
  private static readonly PDF_AMBER: [number, number, number] = [217, 119, 6];
  private static readonly PDF_GREEN: [number, number, number] = [22, 163, 74];

  private static readonly ASCII_MAP: Record<string, string> = {
    č: 'c', ć: 'c', š: 's', ž: 'z', đ: 'dj',
    Č: 'C', Ć: 'C', Š: 'S', Ž: 'Z', Đ: 'Dj',
  };

  /** jsPDF core fonts don't reliably cover č/ć/š/ž/đ, so report text is transliterated
   * rather than embedding a Unicode TTF just for this document. */
  private asciiSafe(value: string | null | undefined): string {
    return (value ?? '').replace(/[čćšžđČĆŠŽĐ]/g, (match) => BehavioralAnalysisComponent.ASCII_MAP[match] ?? match);
  }

  private truncateAddress(value: string): string {
    if (value.length <= 22) {
      return value;
    }
    return `${value.slice(0, 10)}…${value.slice(-6)}`;
  }

  private static loadImageSize(dataUrl: string): Promise<{ width: number; height: number }> {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
      image.onerror = () => reject(new Error('image load failed'));
      image.src = dataUrl;
    });
  }

  private async loadPdfImage(path: string): Promise<{ dataUrl: string; width: number; height: number }> {
    const response = await fetch(path);
    if (!response.ok) {
      throw new Error(`asset not found: ${path}`);
    }
    const blob = await response.blob();
    const dataUrl = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = () => reject(new Error('asset read failed'));
      reader.readAsDataURL(blob);
    });
    const size = await BehavioralAnalysisComponent.loadImageSize(dataUrl);
    return { dataUrl, width: size.width, height: size.height };
  }

  /** Runs with at least a result, in the order they were analysed - what the report covers. */
  protected get exportableRuns(): AddressBehavioralRun[] {
    return this.runs.filter((run) => run.result);
  }

  protected get canExportPdf(): boolean {
    return this.exportableRuns.length > 0 && !this.isAnalyzing && !this.isExportingPdf;
  }

  openSignatureDialog(): void {
    if (!this.activeCase || this.exportableRuns.length === 0 || this.isExportingPdf) {
      return;
    }
    this.isSignatureDialogOpen = true;
    this.signatureDeclarationAccepted = false;
    this.signatureError = null;
    this.behavioralPdfLang = this.settings.lang();
    setTimeout(() => this.signaturePad?.clear());
  }

  closeSignatureDialog(): void {
    this.isSignatureDialogOpen = false;
  }

  get canSubmitSignature(): boolean {
    return (this.signaturePad?.hasStrokes ?? false) && this.signatureDeclarationAccepted && !this.isExportingPdf;
  }

  private signatureDeclaration(): string {
    return this.behavioralPdfLang === 'sr'
      ? 'Potvrđujem da sam izradio ovaj izveštaj u okviru navedenog predmeta i da su u njemu prikazani rezultati onakvi '
        + 'kakve je aplikacija izračunala nad navedenom evidencijom.'
      : 'I confirm that I produced this report within the stated case and that the results shown in it are those the '
        + 'application computed over the stated evidence.';
  }

  /** The exact figures a reader could dispute - altering any of them in the exported
   * document makes the verification hash check fail. */
  private reportContentPayload(): Record<string, unknown> {
    return {
      case_id: this.activeCase!.id,
      evidence: this.selectedEvidence ?? 'combined',
      evidence_sha256: this.evidenceOptions.map((entry) => entry.sha256).sort(),
      addresses: this.exportableRuns.map((run) => ({
        address: run.address,
        total_transactions: run.result!.total_transactions,
        most_active_hour: run.result!.stats.most_active_hour,
        most_active_day: run.result!.stats.most_active_day,
        hourly_distribution: run.result!.hourly_distribution,
        day_of_week_distribution: run.result!.day_of_week_distribution,
        timezone_estimate: {
          available: run.result!.timezone_estimate.available,
          utc_offset_range_label: run.result!.timezone_estimate.utc_offset_range_label ?? null,
        },
      })),
    };
  }

  async confirmSignatureAndExport(): Promise<void> {
    if (!this.canSubmitSignature || !this.activeCase || this.exportableRuns.length === 0) {
      return;
    }

    this.isExportingPdf = true;
    this.signatureError = null;
    try {
      const signatureImage = this.signaturePad!.getDataUrl();
      const declaration = this.signatureDeclaration();

      const registration = await firstValueFrom(
        this.api.registerReport({
          case_id: this.activeCase.id,
          case_name: this.activeCase.name ?? '',
          declaration,
          content: this.reportContentPayload(),
          summary: {
            addresses: this.exportableRuns.length,
            evidence_scope: this.selectedEvidence ?? 'combined',
          },
          report_type: 'behavioral',
        }),
      );

      const catEmblem = await this.loadPdfImage('assets/cat_pdf.png').catch(() => null);
      const sealImage = await this.loadPdfImage('assets/seal.png').catch(() => null);
      this.buildBehavioralPdf({ signatureImage, declaration, registration }, { catEmblem, sealImage });
      this.isSignatureDialogOpen = false;
    } catch {
      this.signatureError = this.t('Neuspešno generisanje PDF izveštaja.', 'Failed to generate the PDF report.');
    } finally {
      this.isExportingPdf = false;
    }
  }

  /** Envelope (first→last active UTC hour) for one specific result. */
  private activePeriodFor(result: BehavioralAnalysisResult): string | null {
    const active = this.hours.filter((hour) => (result.hourly_distribution[hour] ?? 0) > 0);
    if (active.length === 0) {
      return null;
    }
    return `${active[0]}:00-${active[active.length - 1]}:00 UTC`;
  }

  /** Auto-composed plain-language wrap-up for one address - the same facts as the tables,
   * read as prose so the report doesn't force the reader to reconstruct the story. */
  private buildAddressConclusion(run: AddressBehavioralRun): string {
    const result = run.result!;
    const L = (sr: string, en: string): string => this.lx(sr, en);
    const stats = result.stats;
    const n = result.total_transactions;
    const isCombined = run.address === BehavioralAnalysisComponent.COMBINED_KEY;
    const addrCount = this.exportableRuns.length;

    if (n === 0) {
      return L(
        'U ovoj evidenciji nema vremenski oznacene aktivnosti za ovu adresu, pa nema obrasca za analizu.',
        'This evidence contains no time-stamped activity for this address, so there is no pattern to analyse.',
      );
    }

    const parts: string[] = [
      isCombined
        ? L(
            `Skup od ${addrCount} adresa je zajedno zabelezio ${n} ${n === 1 ? 'transakciju' : 'transakcija'} (zbir obrazaca; interni prenosi izmedju ovih adresa broje se dvaput).`,
            `A set of ${addrCount} addresses together recorded ${n} ${n === 1 ? 'transaction' : 'transactions'} (sum of patterns; internal transfers between these addresses are counted twice).`,
          )
        : L(
            `Adresa je u ovoj evidenciji zabelezila ${n} ${n === 1 ? 'transakciju' : 'transakcija'}.`,
            `The address recorded ${n} ${n === 1 ? 'transaction' : 'transactions'} in this evidence.`,
          ),
    ];

    if (stats.most_active_hour) {
      parts.push(
        L(
          `Najveca aktivnost je u ${stats.most_active_hour}:00 UTC (${stats.most_active_hour_count} ${stats.most_active_hour_count === 1 ? 'transakcija' : 'transakcija'}), a najaktivniji dan je ${stats.most_active_day ?? '-'}.`,
          `Peak activity is at ${stats.most_active_hour}:00 UTC (${stats.most_active_hour_count} ${stats.most_active_hour_count === 1 ? 'transaction' : 'transactions'}), and the busiest day is ${stats.most_active_day ?? '-'}.`,
        ),
      );
    }

    const period = this.activePeriodFor(result);
    if (period) {
      parts.push(L(`Sva aktivnost pada u pojas ${period}.`, `All activity falls within the ${period} band.`));
    }

    const tz = result.timezone_estimate;
    if (tz.available) {
      const regions = this.regionLabelsPdf(tz.possible_regions);
      parts.push(
        L(
          `Vremenski obrazac je kompatibilan sa zonom ${tz.utc_offset_range_label} (${regions}); pouzdanost procene: ${this.confidenceLabelPdf(tz.confidence)}.`,
          `The time pattern is compatible with the zone ${tz.utc_offset_range_label} (${regions}); estimate confidence: ${this.confidenceLabelPdf(tz.confidence)}.`,
        ),
      );
    } else {
      parts.push(
        tz.reason === 'no_compatible_offset'
          ? L(
              'Aktivnost je razvucena kroz ceo dan, pa nijedna vremenska zona nije dovoljno kompatibilna za procenu.',
              'Activity is spread across the whole day, so no time zone is compatible enough for an estimate.',
            )
          : L(
              'Premalo transakcija (< 8) za pouzdanu procenu vremenske zone.',
              'Too few transactions (< 8) for a reliable timezone estimate.',
            ),
      );
    }

    parts.push(
      L(
        'Napomena: vremenski obrazac je heuristicki indikator i ne predstavlja dokaz stvarne lokacije vlasnika adrese.',
        'Note: the time pattern is a heuristic indicator, not proof of the address owner\'s actual location.',
      ),
    );

    return parts.join(' ');
  }

  private confidenceLabelPdf(confidence: TimezoneEstimate['confidence']): string {
    if (confidence === 'High') {
      return this.lx('visoka', 'high');
    }
    if (confidence === 'Medium') {
      return this.lx('srednja', 'medium');
    }
    return this.lx('niska', 'low');
  }

  private regionLabelsPdf(regions: readonly string[] | undefined): string {
    if (!regions?.length) {
      return '-';
    }
    if (this.behavioralPdfLang !== 'sr') {
      return regions.join(', ');
    }
    return regions.map((region) => BehavioralAnalysisComponent.REGION_LABELS_SR[region] ?? region).join(', ');
  }

  private buildBehavioralPdf(
    signing: {
      signatureImage: string;
      declaration: string;
      registration: { verification_code: string; content_hash: string; registered_at: string; analyst: string };
    },
    assets: {
      catEmblem: { dataUrl: string; width: number; height: number } | null;
      sealImage: { dataUrl: string; width: number; height: number } | null;
    },
  ): void {
    const L = (sr: string, en: string): string => this.lx(sr, en);
    const caseSummary = this.activeCase!;
    const runs = this.exportableRuns;
    const NAVY = BehavioralAnalysisComponent.PDF_NAVY;
    const ACCENT = BehavioralAnalysisComponent.PDF_ACCENT;
    const TEXT_GRAY = BehavioralAnalysisComponent.PDF_TEXT_GRAY;
    const TEXT_DARK = BehavioralAnalysisComponent.PDF_TEXT_DARK;
    const WHITE = BehavioralAnalysisComponent.PDF_WHITE;

    const doc = new jsPDF({ unit: 'mm', format: 'a4' });
    const pageWidth = doc.internal.pageSize.getWidth();
    const pageHeight = doc.internal.pageSize.getHeight();
    const marginX = 14;
    const usableWidth = pageWidth - marginX * 2;
    let y = 32;

    const barHeight = 24;
    doc.setFillColor(...NAVY);
    doc.rect(0, 0, pageWidth, barHeight, 'F');
    let titleX = marginX;
    if (assets.catEmblem) {
      const emblem = 19;
      const emblemW = (assets.catEmblem.width / assets.catEmblem.height) * emblem;
      doc.addImage(assets.catEmblem.dataUrl, 'PNG', marginX, (barHeight - emblem) / 2, emblemW, emblem);
      titleX = marginX + emblemW + 5;
    }
    doc.setTextColor(...WHITE);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(15);
    doc.text(L('Lusi v1.0 - Izvestaj bihevioralne analize', 'Lusi v1.0 - Behavioral analysis report'), titleX, 11);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(10);
    doc.text(`${L('Slucaj', 'Case')}: ${this.asciiSafe(caseSummary.name)}`, titleX, 19);
    doc.setTextColor(...TEXT_DARK);

    const kv = (label: string, value: string): void => {
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(9.5);
      doc.setTextColor(...TEXT_GRAY);
      doc.text(label, marginX, y);
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(10);
      doc.setTextColor(...TEXT_DARK);
      const lines: string[] = doc.splitTextToSize(value || 'n/a', usableWidth - 42);
      doc.text(lines, marginX + 42, y);
      y += Math.max(6, lines.length * 5);
    };

    kv(L('IDENTIFIKATOR', 'CASE ID'), caseSummary.id);
    kv(L('IZVEZAO', 'EXPORTED BY'), this.asciiSafe(this.auth.currentUser?.username ?? caseSummary.analyst));
    kv(
      L('EVIDENCIJA', 'EVIDENCE'),
      this.selectedEvidence
        ? this.asciiSafe(this.selectedEvidenceFileName ?? this.selectedEvidence)
        : L('Sve transakcije (kombinovano)', 'All transactions (combined)'),
    );
    kv(L('ANALIZIRANO ADRESA', 'ADDRESSES ANALYSED'), String(runs.length));
    kv(L('GENERISANO', 'GENERATED AT'), new Date().toLocaleString(this.behavioralPdfLang === 'sr' ? 'sr-RS' : 'en-GB'));
    y += 2;

    const methodologyNoteLines = doc.splitTextToSize(
      L(
        'Ovaj izvestaj prikazuje UTC obrazac aktivnosti (po satu u danu i danu u nedelji) za navedene adrese, izracunat '
          + 'iz grafa izgradjenog nad navedenom evidencijom. Vremena su UTC; procena vremenske zone je heuristika i NIJE '
          + 'dokaz lokacije (vidi kraj izvestaja).',
        'This report shows the UTC activity pattern (hour-of-day and day-of-week) for the listed addresses, computed '
          + 'from a graph built over the stated evidence. Times are UTC; the timezone estimate is a heuristic and is NOT '
          + 'a location claim (see the end of the report).',
      ),
      usableWidth - 8,
    );
    const noteBoxHeight = methodologyNoteLines.length * 4.2 + 7;
    doc.setFillColor(253, 250, 240);
    doc.setDrawColor(...BehavioralAnalysisComponent.PDF_AMBER);
    doc.setLineWidth(0.4);
    doc.roundedRect(marginX, y - 4, usableWidth, noteBoxHeight, 2, 2, 'FD');
    doc.setFont('helvetica', 'italic');
    doc.setFontSize(8.5);
    doc.setTextColor(...TEXT_DARK);
    doc.text(methodologyNoteLines, marginX + 4, y + 1);
    y += noteBoxHeight + 3;
    doc.setFont('helvetica', 'normal');

    const sectionTitle = (title: string): void => {
      y += 3;
      if (y > pageHeight - 24) {
        doc.addPage();
        y = 16;
      }
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(12);
      doc.setTextColor(...NAVY);
      const lines: string[] = doc.splitTextToSize(title, usableWidth);
      doc.text(lines, marginX, y);
      y += (lines.length - 1) * 5;
      doc.setDrawColor(...ACCENT);
      doc.setLineWidth(0.6);
      doc.line(marginX, y + 2, pageWidth - marginX, y + 2);
      y += 8;
      doc.setTextColor(...TEXT_DARK);
    };

    const drawSummaryCards = (cards: Array<[string, string, [number, number, number]]>): void => {
      const gap = 3;
      const colWidth = (usableWidth - gap * (cards.length - 1)) / cards.length;
      const y0 = y;
      let x = marginX;
      for (const [label, value, color] of cards) {
        doc.setFillColor(...color);
        doc.rect(x, y0, colWidth, 16, 'F');
        doc.setTextColor(...WHITE);
        doc.setFont('helvetica', 'bold');
        doc.setFontSize(11);
        doc.text(doc.splitTextToSize(value, colWidth - 4), x + colWidth / 2, y0 + 6.5, { align: 'center' });
        doc.setFont('helvetica', 'normal');
        doc.setFontSize(6.5);
        doc.text(doc.splitTextToSize(label, colWidth - 3), x + colWidth / 2, y0 + 12, { align: 'center' });
        x += colWidth + gap;
      }
      y = y0 + 16 + 6;
      doc.setTextColor(...TEXT_DARK);
    };

    const paragraph = (text: string): void => {
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(9);
      doc.setTextColor(...TEXT_DARK);
      const lines: string[] = doc.splitTextToSize(text, usableWidth);
      if (y + lines.length * 4.6 > pageHeight - 18) {
        doc.addPage();
        y = 16;
      }
      doc.text(lines, marginX, y);
      y += lines.length * 4.6 + 3;
    };

    const renderRunSection = (run: AddressBehavioralRun, heading: string): void => {
      const result = run.result!;
      sectionTitle(heading);

      if (run.address === BehavioralAnalysisComponent.COMBINED_KEY) {
        paragraph(L(
          `Zbir vremenskih obrazaca svih ${runs.length} gore analiziranih adresa. Interne transakcije izmedju ovih `
            + 'adresa uracunate su dvaput (jednom po svakoj strani). Smisleno samo ako se skup adresa smatra jednim '
            + 'entitetom.',
          `The sum of the activity patterns of all ${runs.length} addresses analysed above. Internal transactions `
            + 'between these addresses are counted twice (once per side). Meaningful only if the set of addresses is '
            + 'treated as a single entity.',
        ));
      }

      drawSummaryCards([
        [L('Ukupno transakcija', 'Total transactions'), String(result.total_transactions), ACCENT],
        [
          L('Najaktivniji sat', 'Most active hour'),
          result.stats.most_active_hour ? `${result.stats.most_active_hour}:00 UTC` : 'n/a',
          ACCENT,
        ],
        [L('Najaktivniji dan', 'Most active day'), result.stats.most_active_day ?? 'n/a', ACCENT],
        [L('Aktivan period', 'Active period'), this.activePeriodFor(result) ?? 'n/a', BehavioralAnalysisComponent.PDF_AMBER],
      ]);

      doc.setFont('helvetica', 'bold');
      doc.setFontSize(10);
      doc.setTextColor(...NAVY);
      doc.text(L('Zakljucak', 'Conclusion'), marginX, y);
      y += 5;
      paragraph(this.buildAddressConclusion(run));

      // Timezone estimate block
      const tz = result.timezone_estimate;
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(10);
      doc.setTextColor(...NAVY);
      doc.text(L('Procena vremenske zone', 'Timezone estimate'), marginX, y);
      y += 5;
      if (tz.available) {
        drawSummaryCards([
          [L('Kompatibilne zone', 'Compatible zones'), this.asciiSafe(tz.utc_offset_range_label ?? '-'), BehavioralAnalysisComponent.PDF_GREEN],
          [(tz.possible_regions?.length ?? 0) > 1 ? L('Regioni', 'Regions') : L('Region', 'Region'), this.regionLabelsPdf(tz.possible_regions), BehavioralAnalysisComponent.PDF_GREEN],
          [L('Pouzdanost', 'Confidence'), this.confidenceLabelPdf(tz.confidence), ACCENT],
        ]);
      } else {
        paragraph(this.lx(
          tz.reason === 'no_compatible_offset'
            ? 'Aktivnost je razvucena kroz ceo dan - nijedna vremenska zona nije dovoljno kompatibilna za procenu.'
            : 'Nedovoljno podataka (< 8 transakcija) za pouzdanu procenu vremenske zone.',
          tz.reason === 'no_compatible_offset'
            ? 'Activity is spread across the whole day - no time zone is compatible enough for an estimate.'
            : 'Insufficient data (< 8 transactions) for a reliable timezone estimate.',
        ));
      }

      // Hour-of-day table (two hour/count pairs per row to stay compact).
      if (y > pageHeight - 60) {
        doc.addPage();
        y = 16;
      }
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(10);
      doc.setTextColor(...NAVY);
      doc.text(L('Aktivnost po satu (UTC)', 'Activity by hour (UTC)'), marginX, y);
      y += 3;
      const hourRows: string[][] = [];
      for (let i = 0; i < 12; i++) {
        const a = this.hours[i];
        const b = this.hours[i + 12];
        hourRows.push([
          `${a}:00`,
          String(result.hourly_distribution[a] ?? 0),
          `${b}:00`,
          String(result.hourly_distribution[b] ?? 0),
        ]);
      }
      autoTable(doc, {
        startY: y,
        margin: { left: marginX, right: marginX },
        head: [[L('Sat', 'Hour'), L('Broj', 'Count'), L('Sat', 'Hour'), L('Broj', 'Count')]],
        body: hourRows,
        styles: { fontSize: 8, cellPadding: 1.6, font: 'courier', textColor: TEXT_DARK, halign: 'center' },
        headStyles: { fillColor: NAVY, textColor: WHITE, font: 'helvetica', fontStyle: 'bold' },
        alternateRowStyles: { fillColor: [240, 245, 250] },
      });
      y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 6;

      if (y > pageHeight - 55) {
        doc.addPage();
        y = 16;
      }
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(10);
      doc.setTextColor(...NAVY);
      doc.text(L('Aktivnost po danu', 'Activity by day'), marginX, y);
      y += 3;
      autoTable(doc, {
        startY: y,
        margin: { left: marginX, right: marginX },
        head: [[L('Dan', 'Day'), L('Broj transakcija', 'Transaction count')]],
        body: this.days.map((day) => [day, String(result.day_of_week_distribution[day] ?? 0)]),
        styles: { fontSize: 8.5, cellPadding: 1.8, font: 'helvetica', textColor: TEXT_DARK },
        headStyles: { fillColor: NAVY, textColor: WHITE, fontStyle: 'bold' },
        alternateRowStyles: { fillColor: [240, 245, 250] },
      });
      y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 6;
    };

    runs.forEach((run, index) => {
      if (index > 0) {
        doc.addPage();
        y = 16;
      }
      renderRunSection(run, `${index + 1}. ${run.address}`);
    });

    const combined = this.combinedRun;
    if (combined) {
      doc.addPage();
      y = 16;
      renderRunSection(
        combined,
        L(`Sve adrese zajedno (${runs.length})`, `All addresses combined (${runs.length})`),
      );
    }

    // --- Methodology appendix -----------------------------------------------------------
    doc.addPage();
    y = 16;
    sectionTitle(L('Metodologija i ogranicenja', 'Methodology and limitations'));
    const bullet = (text: string): void => {
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(9);
      doc.setTextColor(...TEXT_DARK);
      const lines: string[] = doc.splitTextToSize(text, usableWidth - 6);
      if (y + lines.length * 4.4 > pageHeight - 18) {
        doc.addPage();
        y = 16;
      }
      doc.setFillColor(...ACCENT);
      doc.circle(marginX + 1.4, y - 1.2, 0.7, 'F');
      doc.text(lines, marginX + 6, y);
      y += lines.length * 4.4 + 1.6;
    };
    bullet(L(
      'Graf se gradi iz navedene evidencije (kombinovane ili jednog fajla); za svaku adresu se broje transakcije po '
        + 'UTC satu (00-23) i danu u nedelji (ponedeljak-nedelja). Raspodele su uvek popunjene nulama.',
      'A graph is built from the stated evidence (combined or one file); for each address, transactions are counted by '
        + 'UTC hour (00-23) and weekday (Monday-Sunday). Distributions are always zero-filled.',
    ));
    bullet(L(
      'Vremena su iskljucivo UTC - nema pretpostavke o vremenskoj zoni pri bucketing-u. "Aktivan period" je raspon od '
        + 'najranijeg do najkasnijeg UTC sata sa bilo kakvom aktivnoscu.',
      'Times are UTC only - no timezone assumption is made when bucketing. "Active period" is the span from the '
        + 'earliest to the latest UTC hour with any activity.',
    ));
    bullet(L(
      'Procena vremenske zone je heuristika: trazi UTC ofset(e) kod kojih <= 15% aktivnosti pada u lokalnu noc '
        + '(00-05h). Zahteva >= 8 transakcija. Rezultat je tvrdnja o ARITMETICKOJ KOMPATIBILNOSTI obrasca sa satom, NE '
        + 'tvrdnja o geografiji ili identitetu. Regioni su kontinentalni, nikad drzava.',
      'The timezone estimate is a heuristic: it looks for UTC offset(s) where <= 15% of activity falls in the local '
        + 'night (00-05h). It requires >= 8 transactions. The result is a claim about the ARITHMETIC COMPATIBILITY of '
        + 'the pattern with a clock, NOT a claim about geography or identity. Regions are continent-level, never a country.',
    ));
    bullet(L(
      'Adresa nije identitet; jedan nalog moze koristiti vise ljudi/servisa i obratno. Poluglasni ofseti (npr. '
        + 'UTC+5:30) se ne razlikuju u ovoj verziji.',
      'An address is not an identity; one account may be used by several people/services and vice versa. Half-hour '
        + 'offsets (e.g. UTC+5:30) are not distinguished in this version.',
    ));

    // --- Signature + seal --------------------------------------------------------------
    doc.addPage();
    y = 16;
    sectionTitle(L('Potpis i overa', 'Signature and certification'));
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9);
    doc.setTextColor(...TEXT_DARK);
    const declarationLines = doc.splitTextToSize(this.asciiSafe(signing.declaration), usableWidth);
    doc.text(declarationLines, marginX, y);
    y += declarationLines.length * 4.6 + 6;

    const signatureBoxWidth = usableWidth * 0.52;
    const signatureBoxHeight = 34;
    doc.setDrawColor(...TEXT_GRAY);
    doc.setLineWidth(0.3);
    doc.rect(marginX, y, signatureBoxWidth, signatureBoxHeight);
    doc.addImage(signing.signatureImage, 'PNG', marginX + 2, y + 2, signatureBoxWidth - 4, signatureBoxHeight - 4);

    doc.setFontSize(8);
    doc.setTextColor(...TEXT_GRAY);
    doc.text(L('Potpis analiticara', 'Analyst signature'), marginX, y + signatureBoxHeight + 4);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(9.5);
    doc.setTextColor(...TEXT_DARK);
    doc.text(this.asciiSafe(signing.registration.analyst), marginX, y + signatureBoxHeight + 9);

    const sealCenterX = marginX + signatureBoxWidth + (usableWidth - signatureBoxWidth) / 2;
    const sealCenterY = y + signatureBoxHeight / 2;
    if (assets.sealImage) {
      const sealHeight = 34;
      const sealWidth = (assets.sealImage.width / assets.sealImage.height) * sealHeight;
      doc.addImage(assets.sealImage.dataUrl, 'PNG', sealCenterX - sealWidth / 2, sealCenterY - sealHeight / 2, sealWidth, sealHeight);
    } else {
      const sealRadius = 19;
      doc.setDrawColor(...NAVY);
      doc.setLineWidth(1.1);
      doc.circle(sealCenterX, sealCenterY, sealRadius);
      doc.setLineWidth(0.4);
      doc.circle(sealCenterX, sealCenterY, sealRadius - 2.5);
      doc.setTextColor(...NAVY);
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(11);
      doc.text('LUSI', sealCenterX, sealCenterY - 3, { align: 'center' });
      doc.setFontSize(6.5);
      doc.setFont('helvetica', 'normal');
      doc.text(L('DIGITALNA FORENZIKA', 'DIGITAL FORENSICS'), sealCenterX, sealCenterY + 2, { align: 'center' });
    }
    doc.setTextColor(...NAVY);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(7);
    doc.text(
      `${L('OVERENO', 'CERTIFIED')} ${new Date(signing.registration.registered_at).toLocaleDateString()}`,
      sealCenterX,
      y + signatureBoxHeight + 4,
      { align: 'center' },
    );
    doc.setFont('helvetica', 'normal');
    y += signatureBoxHeight + 16;

    sectionTitle(L('Provera verodostojnosti', 'Authenticity check'));
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(9);
    doc.setTextColor(...TEXT_GRAY);
    doc.text(L('KONTROLNI BROJ', 'VERIFICATION CODE'), marginX, y);
    doc.setFont('courier', 'bold');
    doc.setFontSize(13);
    doc.setTextColor(...NAVY);
    doc.text(signing.registration.verification_code, marginX + 45, y + 0.5);
    y += 8;

    doc.setFont('helvetica', 'bold');
    doc.setFontSize(9);
    doc.setTextColor(...TEXT_GRAY);
    doc.text(L('OTISAK SADRZAJA', 'CONTENT HASH'), marginX, y);
    doc.setFont('courier', 'normal');
    doc.setFontSize(7.5);
    doc.setTextColor(...TEXT_DARK);
    doc.text(doc.splitTextToSize(signing.registration.content_hash, usableWidth - 45), marginX + 45, y);
    y += 9;

    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9);
    doc.setTextColor(...TEXT_DARK);
    const verifyLines = doc.splitTextToSize(
      L(
        'Verodostojnost se proverava u aplikaciji Lusi, unosom gornjeg kontrolnog broja. Ako se otisak sadrzaja poklapa '
          + 'sa zabelezenim, podaci u izvestaju su isti kao u trenutku izvoza. Ako se ne poklapa, izvestaj je izmenjen '
          + 'posle izvoza.',
        'Authenticity is verified in the Lusi application by entering the verification code above. If the content hash '
          + 'matches the recorded one, the data in this report is the same as at export time. If it does not match, the '
          + 'report was altered after export.',
      ),
      usableWidth,
    );
    doc.text(verifyLines, marginX, y);
    y += verifyLines.length * 4.6 + 4;

    doc.setFont('helvetica', 'italic');
    doc.setFontSize(8);
    doc.setTextColor(...TEXT_GRAY);
    const limitLines = doc.splitTextToSize(
      L(
        'Ogranicenje: potpis iznad je izjava analiticara, a ne kriptografski dokaz - on ostaje netaknut i ako neko '
          + 'izmeni dokument. Izmena se otkriva iskljucivo poredjenjem otiska sadrzaja. Provera potvrdjuje da se PODACI '
          + 'poklapaju sa registrovanim, ne da je PDF fajl bajt-po-bajt isti.',
        'Limitation: the signature above is the analyst\'s declaration, not a cryptographic proof - it stays intact '
          + 'even if someone edits the document. An alteration is detected solely by comparing the content hash. The '
          + 'check confirms that the DATA matches what was registered, not that the PDF file is byte-for-byte identical.',
      ),
      usableWidth,
    );
    doc.text(limitLines, marginX, y);

    const pageCount = doc.getNumberOfPages();
    for (let page = 1; page <= pageCount; page++) {
      doc.setPage(page);
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(8);
      doc.setTextColor(...TEXT_GRAY);
      doc.text(
        `Lusi v1.0 forensic export | ${L('Strana', 'Page')} ${page}/${pageCount}`,
        pageWidth / 2,
        pageHeight - 8,
        { align: 'center' },
      );
    }

    doc.save(`${caseSummary.id}_behavioral_report.pdf`);
  }
}
