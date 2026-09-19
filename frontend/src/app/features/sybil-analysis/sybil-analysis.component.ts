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
import { AuthService } from '../../core/services/auth.service';
import { CaseDataApiService } from '../../core/services/case-data.api';
import { PathfindingApiService } from '../pathfinding/pathfinding.api';
import { SybilAnalysisApiService } from './sybil-analysis.api';
import { AppLang, SettingsService } from '../../core/services/settings.service';
import {
  AnalyticsResponse,
  CaseSummary,
  DexSwapAnalysisResult,
  EvidenceEntry,
  GraphNodeData,
  SybilAnalysisResult,
  SybilCluster,
  TransactionCustodyEntry,
} from '../../core/models/shared.models';
import { CustodyAccessDialogComponent } from '../custody-access-dialog/custody-access-dialog.component';

/** Key findings pulled from the EXISTING Graph/Taint pipeline (POST .../analytics/run,
 * seeded with the cluster's own addresses - see runCaseAnalytics), read from the
 * per-node fields it already annotates (risk_score, blacklist_flag, cluster_id from
 * wallet clustering, taint_percentage, chain_hop_flag - see graph_building.py /
 * risk_scoring.py / wallet_clustering.py / taint_analysis.py). Nothing here is computed
 * independently - it is a client-side reduction of that existing response down to what
 * matters for THIS cluster's addresses. */
interface ClusterGraphTaintSummary {
  maxRiskScore: number | null;
  maxRiskAddress: string | null;
  blacklistedAddresses: { address: string; label: string | null }[];
  /** Wallet-clustering groups (cluster_id) shared by 2+ of the Sybil cluster's own
   * addresses - a genuinely different, corroborating signal (transaction-graph structure)
   * from the Sybil heuristic's own (purely time/contract-based) grouping. */
  sharedWalletClusters: { clusterId: string; addresses: string[] }[];
  maxTaintPct: number | null;
  maxTaintAddress: string | null;
  /** Taint the CONTRACT itself now carries, seeded FROM the cluster's addresses - a
   * nonzero value means a traceable share of the contract's balance came from these
   * addresses (see taint_analysis.py's proportional model). */
  contractTaintPct: number | null;
  chainHopAddresses: string[];
}

interface ClusterPathfindingSummary {
  directPath: { fromAddress: string; toAddress: string; found: boolean; hops: number } | null;
  nearestCex: { fromAddress: string; found: boolean; hops: number; label: string | null } | null;
}

interface ClusterDexSummary {
  addressesWithSwaps: number;
  totalAddresses: number;
  detectedCount: number;
  potentialCount: number;
  dexNames: string[];
}

interface ClusterForensicOverview {
  isLoading: boolean;
  graphTaint: ClusterGraphTaintSummary | null;
  pathfinding: ClusterPathfindingSummary | null;
  dex: ClusterDexSummary | null;
}

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
  imports: [CommonModule, FormsModule, RouterLink, CustodyAccessDialogComponent, SignaturePadComponent],
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

  /** Per-cluster "Forenzički pregled" state (see runForensicOverview) - keyed by
   * cluster_id, populated on demand (never automatically for every cluster at once, to
   * keep this an opt-in, bounded number of extra backend calls rather than a page that
   * silently fans out N analyses on load). */
  private readonly overviews = new Map<string, ClusterForensicOverview>();

  // --- Lanac dokaza (see SYBIL-ANALIZA.md / LANAC-DOKAZA.md) - scanning the case's
  // evidence for synchronized clusters is a deliberate access to every transaction it
  // touches, same as "Pokreni taint analizu"/"FIND PATH"/"Analiziraj graf"/DEX Swaps'
  // ANALYZE, so it goes through the same shared custody-access dialog. ---
  protected isCustodyDialogOpen = false;
  protected custodyDialogError: string | null = null;

  // --- PDF export (see SYBIL-ANALIZA.md #11) - same signed-report mechanism as DEX Swap/
  // Taint/Pathfinding: a control number is registered server-side BEFORE the document is
  // built (so it can be printed inside the very report it identifies), the analyst draws a
  // signature declaring they produced it, and the PDF itself is assembled client-side. ---
  @ViewChild(SignaturePadComponent) private signaturePad?: SignaturePadComponent;
  protected isSignatureDialogOpen = false;
  protected signatureDeclarationAccepted = false;
  protected signatureError: string | null = null;
  protected isExportingPdf = false;
  /** Language the exported PDF is produced in - seeded from the app toggle when the
   * signing dialog opens, then confirmed by the analyst on the modal (same pattern as
   * taint-analysis/dex-swap-analysis's own *PdfLang). */
  protected sybilPdfLang: AppLang = 'sr';

  constructor(
    private readonly state: AnalysisStateService,
    private readonly caseData: CaseDataApiService,
    private readonly pathfindingApi: PathfindingApiService,
    private readonly sybilAnalysisApi: SybilAnalysisApiService,
    private readonly auth: AuthService,
    private readonly destroyRef: DestroyRef,
    public readonly settings: SettingsService,
  ) {}

  protected t(sr: string, en: string): string {
    return this.settings.lang() === 'sr' ? sr : en;
  }

  /** PDF-string translator: SR or EN by the language chosen on the signing modal, then
   * ASCII-folded (harmless for English) since the PDF core font is Latin-1 only - same
   * pattern as dex-swap-analysis.component.ts's lx(). */
  private lx(sr: string, en: string): string {
    return this.asciiSafe(this.sybilPdfLang === 'sr' ? sr : en);
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
    this.overviews.clear();
    this.isSignatureDialogOpen = false;
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

  // --- Forenzički pregled: za izabrani klaster, automatski unakrsno pozovi POSTOJEĆE
  // Graph/Taint (analytics/run), Pathfinding i DEX Swap analize i izvuci samo najvažnije
  // nalaze za adrese/kontrakt tog klastera - kratak pregled, ne pretrpana stranica. Ovo je
  // orijentacioni, PASIVAN unakrsni pregled (bez custody-ja) - isti tretman kao Graf
  // stranicin automatski pregled pri izboru evidencije ili DEX Swap overlay-a; formalan,
  // potpisan nalaz i dalje zahteva da se svaka analiza pokrene na svojoj stranici. -------

  protected overviewFor(clusterId: string): ClusterForensicOverview | null {
    return this.overviews.get(clusterId) ?? null;
  }

  protected isOverviewLoading(clusterId: string): boolean {
    return this.overviews.get(clusterId)?.isLoading ?? false;
  }

  protected runForensicOverview(cluster: SybilCluster): void {
    const caseId = this.activeCase?.id;
    if (!caseId || this.isOverviewLoading(cluster.cluster_id)) {
      return;
    }

    this.overviews.set(cluster.cluster_id, { isLoading: true, graphTaint: null, pathfinding: null, dex: null });

    // 1) Graph + Taint: ONE call to the existing analytics pipeline, seeded with this
    // cluster's own addresses, covers both - taint_analysis is just one plugin in that
    // same pipeline (see case_analytics_run.router / SYBIL-ANALIZA.md). No custody: same
    // "passive preview" treatment the Graph page's own auto-preview already uses.
    const graphTaint$ = this.caseData.runCaseAnalytics(caseId, this.selectedEvidence, cluster.addresses, null).pipe(
      map((response) => this.buildGraphTaintSummary(response, cluster)),
      catchError(() => of(null)),
    );

    // 2) Pathfinding: bounded to two representative, cheap calls (not one per address
    // pair) - a direct fund-flow path between the first two flagged addresses, and the
    // nearest known exchange from the first one. bfs_shortest_path is DIRECTED, so "not
    // found" here means no direct forward path in THIS evidence - it does not rule out a
    // shared funding source upstream (see the panel's own caveat text in the template).
    const primary = cluster.addresses[0];
    const secondary = cluster.addresses[1] ?? null;
    const directPath$ = secondary
      ? this.pathfindingApi.findCasePath(caseId, primary, 'specific_address', secondary, this.selectedEvidence, null).pipe(
          map((result) => ({ fromAddress: primary, toAddress: secondary, found: result.found, hops: result.hops })),
          catchError(() => of(null)),
        )
      : of(null);
    const nearestCex$ = this.pathfindingApi.findCasePath(caseId, primary, 'nearest_cex', null, this.selectedEvidence, null).pipe(
      map((result) => ({ fromAddress: primary, found: result.found, hops: result.hops, label: result.destination_label ?? null })),
      catchError(() => of(null)),
    );

    // 3) DEX Swap Analysis: one case-wide, passive GET (same call the Graph page's own
    // overlay already uses), filtered client-side to this cluster's addresses - avoids one
    // request per address.
    const dex$ = this.caseData.getDexSwapAnalysis(caseId, null, this.selectedEvidence).pipe(
      map((result) => this.buildDexSummary(result, cluster)),
      catchError(() => of(null)),
    );

    forkJoin([graphTaint$, directPath$, nearestCex$, dex$]).subscribe(([graphTaint, directPath, nearestCex, dex]) => {
      this.overviews.set(cluster.cluster_id, {
        isLoading: false,
        graphTaint,
        pathfinding: directPath || nearestCex ? { directPath, nearestCex } : null,
        dex,
      });
    });
  }

  private buildGraphTaintSummary(response: AnalyticsResponse, cluster: SybilCluster): ClusterGraphTaintSummary {
    const nodeById = new Map(response.nodes.map((node) => [String(node.id), node]));
    const addressNodes = cluster.addresses
      .map((address) => nodeById.get(address))
      .filter((node): node is GraphNodeData => !!node);
    const contractNode = nodeById.get(cluster.contract_address) ?? null;

    let maxRiskScore: number | null = null;
    let maxRiskAddress: string | null = null;
    const blacklistedAddresses: { address: string; label: string | null }[] = [];
    const addressesByWalletCluster = new Map<string, string[]>();
    let maxTaintPct: number | null = null;
    let maxTaintAddress: string | null = null;
    const chainHopAddresses: string[] = [];

    for (const node of addressNodes) {
      const address = String(node.id);
      if (typeof node.risk_score === 'number' && (maxRiskScore === null || node.risk_score > maxRiskScore)) {
        maxRiskScore = node.risk_score;
        maxRiskAddress = address;
      }
      if (node.blacklist_flag) {
        blacklistedAddresses.push({ address, label: node.blacklist_label ?? null });
      }
      if (node.cluster_id) {
        const members = addressesByWalletCluster.get(node.cluster_id) ?? [];
        members.push(address);
        addressesByWalletCluster.set(node.cluster_id, members);
      }
      if (typeof node.taint_percentage === 'number' && node.taint_percentage > 0) {
        if (maxTaintPct === null || node.taint_percentage > maxTaintPct) {
          maxTaintPct = node.taint_percentage;
          maxTaintAddress = address;
        }
      }
      if (node.chain_hop_flag) {
        chainHopAddresses.push(address);
      }
    }

    const sharedWalletClusters = [...addressesByWalletCluster.entries()]
      .filter(([, addresses]) => addresses.length >= 2)
      .map(([clusterId, addresses]) => ({ clusterId, addresses }));

    return {
      maxRiskScore,
      maxRiskAddress,
      blacklistedAddresses,
      sharedWalletClusters,
      maxTaintPct,
      maxTaintAddress,
      contractTaintPct: typeof contractNode?.taint_percentage === 'number' ? contractNode.taint_percentage : null,
      chainHopAddresses,
    };
  }

  private buildDexSummary(result: DexSwapAnalysisResult, cluster: SybilCluster): ClusterDexSummary {
    const clusterAddresses = new Set(cluster.addresses);
    const relevantEvents = result.events.filter((event) => clusterAddresses.has(event.user_address));
    const detectedCount = relevantEvents.filter((event) => event.confidence === 'Detected').length;
    return {
      addressesWithSwaps: new Set(relevantEvents.map((event) => event.user_address)).size,
      totalAddresses: cluster.addresses.length,
      detectedCount,
      potentialCount: relevantEvents.length - detectedCount,
      dexNames: [...new Set(relevantEvents.map((event) => event.dex_name))],
    };
  }

  /** Whether the loaded Graph/Taint summary has anything worth showing at all, so the
   * template can print an honest "no additional findings" line instead of an empty list. */
  protected hasGraphTaintFindings(summary: ClusterGraphTaintSummary): boolean {
    return (
      summary.maxRiskScore !== null ||
      summary.blacklistedAddresses.length > 0 ||
      summary.sharedWalletClusters.length > 0 ||
      summary.maxTaintPct !== null ||
      summary.chainHopAddresses.length > 0
    );
  }

  // --- Kratak, automatski generisan forenzički zaključak - sastavljen ISKLJUČIVO od
  // brojeva/polja iz `result` (stvarno pronađeni dokazi), nikad od pretpostavki van njih -
  // isti duh kao dex-swap-analysis.component.ts's buildConclusionParagraph, ovde prikazan
  // direktno na stranici (ne u PDF-u), pošto Sybil Analiza (za sada) nema PDF izveštaj. ---

  protected get forensicConclusion(): string | null {
    const result = this.result;
    if (!result) {
      return null;
    }

    if (result.total_clusters === 0) {
      return this.t(
        `Analizom nije pronađen nijedan Sybil/bot klaster koji zadovoljava zadate parametre ` +
          `(min. ${result.min_addresses} adresa, vremenski prozor ${result.time_window_seconds}s). ` +
          `Ovaj zaključak je zasnovan isključivo na pronađenim dokazima u trenutnoj evidenciji.`,
        `The analysis found no Sybil/bot cluster satisfying the given parameters ` +
          `(min. ${result.min_addresses} addresses, ${result.time_window_seconds}s time window). ` +
          `This conclusion is based solely on the evidence found in the current evidence set.`,
      );
    }

    const top = result.clusters[0]; // already sorted by risk_score, descending
    const criticalOrHighCount = result.clusters.filter((c) => c.risk_level === 'critical' || c.risk_level === 'high').length;

    const sentences: string[] = [
      this.t(
        `Analizom ${result.total_clusters === 1 ? 'je pronađen' : 'su pronađena'} ${result.total_clusters} ` +
          `${result.total_clusters === 1 ? 'klaster' : 'klastera'} sinhronizovane aktivnosti, koji ${result.total_clusters === 1 ? 'obuhvata' : 'obuhvataju'} ` +
          `ukupno ${result.addresses_flagged} označenih adresa.`,
        `The analysis found ${result.total_clusters} synchronized-activity ${result.total_clusters === 1 ? 'cluster' : 'clusters'}, ` +
          `covering a total of ${result.addresses_flagged} flagged addresses.`,
      ),
      this.t(
        `Najizraženiji je klaster ${top.cluster_id} (${top.contract_name}${top.function_name ? ', funkcija "' + top.function_name + '"' : ''}) - ` +
          `${top.address_count} adresa, ${top.activity_count} aktivnosti u periodu od ${top.window_duration_seconds}s, sa risk skorom ${top.risk_score}/100 (${this.riskLabel(top.risk_level)}).`,
        `The most prominent is cluster ${top.cluster_id} (${top.contract_name}${top.function_name ? ', function "' + top.function_name + '"' : ''}) - ` +
          `${top.address_count} addresses, ${top.activity_count} activities within ${top.window_duration_seconds}s, risk score ${top.risk_score}/100 (${this.riskLabel(top.risk_level)}).`,
      ),
    ];

    if (criticalOrHighCount > 0) {
      sentences.push(
        this.t(
          `${criticalOrHighCount} od ${result.total_clusters} klastera ${criticalOrHighCount === 1 ? 'ima' : 'ima'} visok ili kritičan risk nivo i zahteva prioritetnu dalju proveru.`,
          `${criticalOrHighCount} of ${result.total_clusters} clusters have a high or critical risk level and warrant priority follow-up.`,
        ),
      );
    }

    if (result.custody_findings_recorded) {
      sentences.push(
        this.t(
          `${result.custody_findings_recorded} transakcija iz cele evidencije je zabeleženo u lancu dokaza sa strukturiranim SYBIL_CLUSTER nalazom (vidi "Lanac dokaza").`,
          `${result.custody_findings_recorded} transactions across the whole evidence were recorded in the chain of custody with a structured SYBIL_CLUSTER finding (see "Chain of custody").`,
        ),
      );
    }

    sentences.push(
      this.t(
        'Ovaj zaključak je automatski sastavljen isključivo od gore pronađenih dokaza i predstavlja heuristiku, ne dokaz zajedničkog vlasništva.',
        'This conclusion is automatically composed solely from the evidence found above and is a heuristic, not proof of common ownership.',
      ),
    );

    return sentences.join(' ');
  }

  // --- PDF izveštaj ------------------------------------------------------------------
  // Isti potpisan-izveštaj mehanizam kao DEX Swap/Taint/Pathfinding: kontrolni broj se
  // registruje PRE nego što je dokument izgrađen (da bi mogao da se odštampa unutar samog
  // izveštaja koji identifikuje), analitičar crta potpis kao izjavu da je izveštaj njegov
  // rad, a sam PDF se sastavlja na klijentu (jspdf + jspdf-autotable), radi vizuelne
  // doslednosti sa ostalim izveštajima u aplikaciji. Izveštaj obuhvata: Sybil klaster(e),
  // ključne dokaze (blockchain činjenice po transakciji), rezultate Forenzičkog pregleda
  // (Graph/Taint/Pathfinding/DEX - kad je pokrenut za dati klaster), potvrdu upisa u lanac
  // dokaza, i završni forenzički zaključak (isti tekst kao na ekranu, §9.2/§13).

  private static readonly PDF_NAVY: [number, number, number] = [13, 24, 40];
  private static readonly PDF_ACCENT: [number, number, number] = [43, 130, 191];
  private static readonly PDF_TEXT_GRAY: [number, number, number] = [100, 112, 128];
  private static readonly PDF_TEXT_DARK: [number, number, number] = [24, 28, 36];
  private static readonly PDF_WHITE: [number, number, number] = [255, 255, 255];
  private static readonly PDF_AMBER: [number, number, number] = [217, 119, 6];
  private static readonly PDF_NONE: [number, number, number] = [100, 116, 139];
  private static readonly PDF_LOW: [number, number, number] = [43, 130, 191];
  private static readonly PDF_MEDIUM: [number, number, number] = [202, 138, 4];
  private static readonly PDF_HIGH: [number, number, number] = [234, 88, 12];
  private static readonly PDF_CRITICAL: [number, number, number] = [220, 38, 38];

  private static riskColor(level: SybilCluster['risk_level']): [number, number, number] {
    switch (level) {
      case 'critical':
        return SybilAnalysisComponent.PDF_CRITICAL;
      case 'high':
        return SybilAnalysisComponent.PDF_HIGH;
      case 'medium':
        return SybilAnalysisComponent.PDF_MEDIUM;
      case 'low':
        return SybilAnalysisComponent.PDF_LOW;
      default:
        return SybilAnalysisComponent.PDF_NONE;
    }
  }

  /** jsPDF's core fonts don't cover č/ć/š/ž/đ reliably - same tradeoff (and same fixed
   * transliteration table) as taint-analysis/dex-swap-analysis.component.ts's asciiSafe. */
  private static readonly ASCII_MAP: Record<string, string> = {
    č: 'c', ć: 'c', š: 's', ž: 'z', đ: 'dj',
    Č: 'C', Ć: 'C', Š: 'S', Ž: 'Z', Đ: 'Dj',
  };

  private asciiSafe(value: string | null | undefined): string {
    return (value ?? '').replace(/[čćšžđČĆŠŽĐ]/g, (match) => SybilAnalysisComponent.ASCII_MAP[match] ?? match);
  }

  protected get canExportPdf(): boolean {
    return !!this.result && !this.isAnalyzing && !this.isExportingPdf;
  }

  /** Loads the cat emblem/seal PNGs for the PDF header/signature block - same helper as
   * taint-analysis/dex-swap-analysis/pathfinding.component.ts's own loadPdfImage, not a
   * shared module (small per-component PDF helpers are copied in this app, not
   * centralized). */
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
    const size = await SybilAnalysisComponent.loadImageSize(dataUrl);
    return { dataUrl, width: size.width, height: size.height };
  }

  openSignatureDialog(): void {
    if (!this.canExportPdf) {
      return;
    }
    this.isSignatureDialogOpen = true;
    this.signatureDeclarationAccepted = false;
    this.signatureError = null;
    this.sybilPdfLang = this.settings.lang();
    setTimeout(() => this.signaturePad?.clear());
  }

  closeSignatureDialog(): void {
    this.isSignatureDialogOpen = false;
  }

  get canSubmitSignature(): boolean {
    return (this.signaturePad?.hasStrokes ?? false) && this.signatureDeclarationAccepted && !this.isExportingPdf;
  }

  /** SR or EN by the language chosen on the signing modal (sybilPdfLang) - same pattern as
   * dex-swap-analysis.component.ts's own inline declaration ternary. */
  private signatureDeclaration(): string {
    return this.sybilPdfLang === 'sr'
      ? 'Potvrđujem da sam izradio ovaj izveštaj u okviru navedenog predmeta i da su u njemu prikazani rezultati '
        + 'onakvi kakve je aplikacija izračunala (heuristički) nad navedenom evidencijom.'
      : 'I confirm that I produced this report within the stated case and that the results shown in it are those '
        + 'the application computed over the stated evidence (heuristically).';
  }

  /** The exact data the verification hash is computed over - kept to the figures a reader
   * could dispute (cluster membership, counts, risk score, forensic-overview figures when
   * they were actually loaded), sorted so field order never affects the hash. Descriptive
   * prose (`reasons[]`) is deliberately excluded, same discipline as dex-swap-analysis
   * .component.ts's own reportContentPayload. */
  private reportContentPayload(): Record<string, unknown> {
    const result = this.result!;
    return {
      case_id: this.activeCase!.id,
      evidence: this.selectedEvidence ?? 'combined',
      target_address: result.target_address,
      contract: result.contract,
      time_window_seconds: result.time_window_seconds,
      min_addresses: result.min_addresses,
      custody_findings_recorded: result.custody_findings_recorded ?? 0,
      clusters: [...result.clusters]
        .map((cluster) => {
          const overview = this.overviewFor(cluster.cluster_id);
          return {
            cluster_id: cluster.cluster_id,
            contract_address: cluster.contract_address,
            function_name: cluster.function_name,
            addresses: [...cluster.addresses].sort(),
            activity_count: cluster.activity_count,
            window_start: cluster.window_start,
            window_end: cluster.window_end,
            risk_score: cluster.risk_score,
            risk_level: cluster.risk_level,
            transactions: [...cluster.transactions]
              .map((tx) => ({ sender_address: tx.sender_address, amount: tx.amount, timestamp: tx.timestamp, tx_hash: tx.tx_hash }))
              .sort((a, b) => a.timestamp.localeCompare(b.timestamp) || a.sender_address.localeCompare(b.sender_address)),
            forensic_overview: overview && !overview.isLoading
              ? {
                  graph_taint_max_risk_score: overview.graphTaint?.maxRiskScore ?? null,
                  graph_taint_max_taint_pct: overview.graphTaint?.maxTaintPct ?? null,
                  graph_taint_shared_wallet_clusters: overview.graphTaint?.sharedWalletClusters.length ?? 0,
                  pathfinding_direct_path_found: overview.pathfinding?.directPath?.found ?? null,
                  pathfinding_direct_path_hops: overview.pathfinding?.directPath?.hops ?? null,
                  dex_addresses_with_swaps: overview.dex?.addressesWithSwaps ?? null,
                }
              : null,
          };
        })
        .sort((a, b) => a.cluster_id.localeCompare(b.cluster_id)),
    };
  }

  async confirmSignatureAndExport(): Promise<void> {
    if (!this.canSubmitSignature || !this.result || !this.activeCase) {
      return;
    }

    this.isExportingPdf = true;
    this.signatureError = null;
    try {
      const signatureImage = this.signaturePad!.getDataUrl();
      const declaration = this.signatureDeclaration();
      const result = this.result;
      const highestRiskScore = result.clusters.reduce((max, cluster) => Math.max(max, cluster.risk_score), 0);

      // Registered BEFORE the document is built: the verification code has to be printed
      // inside the very report it identifies.
      const registration = await firstValueFrom(
        this.caseData.registerReport({
          case_id: this.activeCase.id,
          case_name: this.activeCase.name ?? '',
          declaration,
          content: this.reportContentPayload(),
          summary: {
            total_clusters: result.total_clusters,
            addresses_flagged: result.addresses_flagged,
            highest_risk_score: highestRiskScore,
            custody_findings_recorded: result.custody_findings_recorded ?? 0,
          },
          report_type: 'sybil',
        }),
      );

      const catEmblem = await this.loadPdfImage('assets/cat_pdf.png').catch(() => null);
      const sealImage = await this.loadPdfImage('assets/seal.png').catch(() => null);
      this.buildSybilPdf({ signatureImage, declaration, registration }, { catEmblem, sealImage });
      this.isSignatureDialogOpen = false;
    } catch {
      this.signatureError = this.t('Neuspešno generisanje PDF izveštaja.', 'Failed to generate the PDF report.');
    } finally {
      this.isExportingPdf = false;
    }
  }

  private buildSybilPdf(
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
    const result = this.result!;
    const NAVY = SybilAnalysisComponent.PDF_NAVY;
    const ACCENT = SybilAnalysisComponent.PDF_ACCENT;
    const TEXT_GRAY = SybilAnalysisComponent.PDF_TEXT_GRAY;
    const TEXT_DARK = SybilAnalysisComponent.PDF_TEXT_DARK;
    const WHITE = SybilAnalysisComponent.PDF_WHITE;

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
    doc.text(L('Lusi v1.0 - Izvestaj Sybil & Bot Network analize', 'Lusi v1.0 - Sybil & Bot Network analysis report'), titleX, 11);
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

    kv('CASE ID', caseSummary.id);
    kv(L('IZVEZAO', 'EXPORTED BY'), this.asciiSafe(this.auth.currentUser?.username ?? caseSummary.analyst));
    kv(
      L('EVIDENCIJA', 'EVIDENCE'),
      this.selectedEvidence ? this.asciiSafe(this.selectedEvidence) : L('Sve transakcije (kombinovano)', 'All transactions (combined)'),
    );
    kv(L('ADRESA/KONTRAKT FILTER', 'ADDRESS/CONTRACT FILTER'), `${result.target_address ?? L('sve', 'all')} / ${result.contract ?? L('svi', 'all')}`);
    kv(
      L('PARAMETRI', 'PARAMETERS'),
      L(
        `vremenski prozor ${result.time_window_seconds}s, min. ${result.min_addresses} adresa`,
        `time window ${result.time_window_seconds}s, min. ${result.min_addresses} addresses`,
      ),
    );
    kv(L('GENERISANO', 'GENERATED AT'), new Date().toLocaleString(this.sybilPdfLang === 'sr' ? 'sr-RS' : 'en-GB'));
    y += 2;

    // Short version of the disclaimer, placed BEFORE any result - never omitted, never
    // softened: this is a heuristic, not proof, on every page this note could plausibly be
    // missed from. Composed here (not read from result.disclaimer) so it follows the
    // chosen report language.
    const methodologyNoteLines = doc.splitTextToSize(
      L(
        'Sybil & Bot Network Analysis je heuristika zasnovana na vremenskoj sinhronizaciji i zajednickom kontraktu/'
          + 'funkciji poziva - NIKADA ne predstavlja dokaz da navedene adrese pripadaju istoj osobi ili entitetu.',
        'Sybil & Bot Network Analysis is a heuristic based on time synchronization and a shared contract/function call '
          + '- it NEVER constitutes proof that the listed addresses belong to the same person or entity.',
      )
        + ' '
        + L(
          'Detaljno objasnjenje heuristike i njena ogranicenja nalaze se na kraju ovog izvestaja.',
          'A detailed explanation of the heuristic and its limitations is at the end of this report.',
        ),
      usableWidth - 8,
    );
    const noteBoxHeight = methodologyNoteLines.length * 4.2 + 7;
    doc.setFillColor(253, 250, 240);
    doc.setDrawColor(...SybilAnalysisComponent.PDF_AMBER);
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
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(12);
      doc.setTextColor(...NAVY);
      doc.text(title, marginX, y);
      doc.setDrawColor(...ACCENT);
      doc.setLineWidth(0.6);
      doc.line(marginX, y + 2, pageWidth - marginX, y + 2);
      y += 8;
      doc.setTextColor(...TEXT_DARK);
    };

    const ensureSpace = (needed: number): void => {
      if (y + needed > pageHeight - 18) {
        doc.addPage();
        y = 16;
      }
    };

    const drawSummaryCards = (cards: Array<[string, string | number, [number, number, number]]>): void => {
      const gap = 3;
      const colWidth = (usableWidth - gap * (cards.length - 1)) / cards.length;
      const y0 = y;
      let x = marginX;
      for (const [label, value, color] of cards) {
        doc.setFillColor(...color);
        doc.rect(x, y0, colWidth, 15, 'F');
        doc.setTextColor(...WHITE);
        doc.setFont('helvetica', 'bold');
        doc.setFontSize(14);
        doc.text(String(value), x + colWidth / 2, y0 + 6.5, { align: 'center' });
        doc.setFont('helvetica', 'normal');
        doc.setFontSize(7);
        doc.text(doc.splitTextToSize(label, colWidth - 4), x + colWidth / 2, y0 + 11, { align: 'center' });
        x += colWidth + gap;
      }
      y = y0 + 15 + 6;
      doc.setTextColor(...TEXT_DARK);
    };

    const highestRiskScore = result.clusters.reduce((max, cluster) => Math.max(max, cluster.risk_score), 0);

    sectionTitle(L('Rezime analize', 'Analysis summary'));
    drawSummaryCards([
      [L('Klastera pronadjeno', 'Clusters found'), result.total_clusters, ACCENT],
      [L('Oznacenih adresa', 'Flagged addresses'), result.addresses_flagged, SybilAnalysisComponent.PDF_HIGH],
      [L('Najvisi risk score', 'Highest risk score'), `${highestRiskScore}/100`, SybilAnalysisComponent.PDF_CRITICAL],
      [L('Nalaza u lancu dokaza', 'Chain-of-custody findings'), result.custody_findings_recorded ?? 0, TEXT_GRAY],
    ]);

    // --- Jedna sekcija po klasteru: Sybil klaster -> Kljucni dokazi (blockchain cinjenice)
    // -> Forenzicki pregled (Graph/Taint/Pathfinding/DEX, kad je pokrenut) -> heuristicki
    // zakljucak tog klastera (razlozi skora). ------------------------------------------
    result.clusters.forEach((cluster, index) => {
      ensureSpace(30);
      sectionTitle(`${index + 1}. ${L('Klaster', 'Cluster')} ${cluster.cluster_id}`);

      doc.setFont('helvetica', 'bold');
      doc.setFontSize(10);
      doc.setTextColor(...TEXT_DARK);
      doc.text(this.asciiSafe(cluster.contract_name), marginX, y);
      if (cluster.function_name) {
        doc.setFont('helvetica', 'normal');
        doc.setFontSize(9);
        doc.setTextColor(...TEXT_GRAY);
        doc.text(`${L('funkcija', 'function')}: ${this.asciiSafe(cluster.function_name)}`, marginX + 70, y);
      }
      y += 6;

      const riskColor = SybilAnalysisComponent.riskColor(cluster.risk_level);
      doc.setFillColor(...riskColor);
      doc.roundedRect(marginX, y - 4, 42, 7, 1.5, 1.5, 'F');
      doc.setTextColor(...WHITE);
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(8.5);
      doc.text(`${cluster.risk_score}/100 - ${this.asciiSafe(this.riskLabel(cluster.risk_level)).toUpperCase()}`, marginX + 21, y, { align: 'center' });
      doc.setTextColor(...TEXT_DARK);
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(9);
      doc.text(
        L(
          `${cluster.address_count} adresa - ${cluster.activity_count} aktivnosti - ${cluster.window_duration_seconds}s prozor`,
          `${cluster.address_count} addresses - ${cluster.activity_count} activities - ${cluster.window_duration_seconds}s window`,
        ),
        marginX + 48,
        y,
      );
      y += 9;

      // Kljucni dokazi - blockchain cinjenice po transakciji (jasno OZNACENO kao cinjenice,
      // ne heuristika - vidi kolonu "Heuristicki zakljucak" ispod ove tabele).
      sectionTitle(L('Kljucni dokazi (blockchain cinjenice)', 'Key evidence (blockchain facts)'));
      autoTable(doc, {
        startY: y,
        margin: { left: marginX, right: marginX },
        head: [[L('Posiljalac', 'Sender'), L('Iznos', 'Amount'), L('Vreme', 'Time'), 'Tx hash', L('Funkcija', 'Function')]],
        body: cluster.transactions.map((tx) => [
          tx.sender_address,
          String(tx.amount),
          new Date(tx.timestamp).toLocaleString(this.sybilPdfLang === 'sr' ? 'sr-RS' : 'en-GB'),
          this.asciiSafe(tx.tx_hash ?? 'n/a'),
          this.asciiSafe(tx.function_name ?? 'n/a'),
        ]),
        styles: { fontSize: 7.5, cellPadding: 1.4, font: 'courier', textColor: TEXT_DARK },
        headStyles: { fillColor: NAVY, textColor: WHITE, font: 'helvetica', fontStyle: 'bold' },
        alternateRowStyles: { fillColor: [240, 245, 250] },
        columnStyles: { 2: { font: 'helvetica' }, 4: { font: 'helvetica' } },
      });
      y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 4;

      // Heuristicki zakljucak - eksplicitno odvojen naslov i boja od "Kljucnih dokaza"
      // iznad, ista disciplina kao backend-ov sybil_evidence (blockchain_facts vs.
      // heuristic_conclusions - vidi SYBIL-ANALIZA.md #9.1).
      ensureSpace(20 + cluster.reasons.length * 4.4);
      sectionTitle(L('Heuristicki zakljucak ovog klastera', "This cluster's heuristic conclusion"));
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(8.5);
      doc.setTextColor(...TEXT_DARK);
      for (const reason of cluster.reasons) {
        const lines: string[] = doc.splitTextToSize(this.asciiSafe(reason), usableWidth - 6);
        ensureSpace(lines.length * 4.2 + 2);
        doc.setFillColor(...ACCENT);
        doc.circle(marginX + 1.2, y - 1.2, 0.7, 'F');
        doc.text(lines, marginX + 5, y);
        y += lines.length * 4.2 + 1.2;
      }

      // Forenzicki pregled (Graph/Taint/Pathfinding/DEX) - samo ako je analiticar stvarno
      // pokrenuo dugme "Forenzicki pregled" za OVAJ klaster pre izvoza; inace posteno kaze
      // da nije pokrenut, umesto da izmisli prazne redove.
      ensureSpace(24);
      sectionTitle(L('Forenzicki pregled (Graph/Taint/Putanje/DEX)', 'Forensic overview (Graph/Taint/Pathfinding/DEX)'));
      const overview = this.overviewFor(cluster.cluster_id);
      if (!overview || overview.isLoading) {
        doc.setFont('helvetica', 'italic');
        doc.setFontSize(8.5);
        doc.setTextColor(...TEXT_GRAY);
        doc.text(
          L(
            'Forenzicki pregled nije pokrenut za ovaj klaster pre izvoza izvestaja.',
            'The forensic overview was not run for this cluster before the report was exported.',
          ),
          marginX,
          y,
        );
        y += 6;
      } else {
        const overviewLines: string[] = [];
        const gt = overview.graphTaint;
        if (gt) {
          if (gt.maxRiskScore !== null) {
            overviewLines.push(L(`Graf: najvisi risk score ${gt.maxRiskScore}/100 (${gt.maxRiskAddress})`, `Graph: highest risk score ${gt.maxRiskScore}/100 (${gt.maxRiskAddress})`));
          }
          if (gt.blacklistedAddresses.length > 0) {
            overviewLines.push(L(`Graf: ${gt.blacklistedAddresses.length} adresa na crnoj listi`, `Graph: ${gt.blacklistedAddresses.length} blacklisted address(es)`));
          }
          if (gt.sharedWalletClusters.length > 0) {
            overviewLines.push(L('Graf: adrese dele wallet-clustering grupu (nezavisan signal)', 'Graph: addresses share a wallet-clustering group (independent signal)'));
          }
          if (gt.maxTaintPct !== null) {
            overviewLines.push(L(`Taint: najvisi ${gt.maxTaintPct}% (${gt.maxTaintAddress})`, `Taint: highest ${gt.maxTaintPct}% (${gt.maxTaintAddress})`));
          }
        }
        const pf = overview.pathfinding;
        if (pf?.directPath) {
          overviewLines.push(
            pf.directPath.found
              ? L(`Putanja: direktan tok ${pf.directPath.fromAddress} -> ${pf.directPath.toAddress}, ${pf.directPath.hops} skokova`, `Path: direct flow ${pf.directPath.fromAddress} -> ${pf.directPath.toAddress}, ${pf.directPath.hops} hops`)
              : L('Putanja: nije pronadjena direktna putanja izmedju dve oznacene adrese', 'Path: no direct path found between two flagged addresses'),
          );
        }
        if (pf?.nearestCex?.found) {
          overviewLines.push(L(`Putanja: najbliza berza ${pf.nearestCex.label ?? '?'} (${pf.nearestCex.hops} skokova)`, `Path: nearest exchange ${pf.nearestCex.label ?? '?'} (${pf.nearestCex.hops} hops)`));
        }
        const dex = overview.dex;
        if (dex && dex.addressesWithSwaps > 0) {
          overviewLines.push(L(`DEX: ${dex.addressesWithSwaps}/${dex.totalAddresses} adresa ima detektovan swap (${dex.dexNames.join(', ')})`, `DEX: ${dex.addressesWithSwaps}/${dex.totalAddresses} address(es) have a detected swap (${dex.dexNames.join(', ')})`));
        }
        if (overviewLines.length === 0) {
          overviewLines.push(L('Forenzicki pregled pokrenut - nema dodatnih nalaza.', 'Forensic overview run - no additional findings.'));
        }
        doc.setFont('helvetica', 'normal');
        doc.setFontSize(8.5);
        doc.setTextColor(...TEXT_DARK);
        for (const line of overviewLines) {
          const lines: string[] = doc.splitTextToSize(this.asciiSafe(line), usableWidth - 6);
          ensureSpace(lines.length * 4.2 + 2);
          doc.setFillColor(...ACCENT);
          doc.circle(marginX + 1.2, y - 1.2, 0.7, 'F');
          doc.text(lines, marginX + 5, y);
          y += lines.length * 4.2 + 1.2;
        }
      }
      y += 3;
    });

    // --- Lanac dokaza -------------------------------------------------------------------
    ensureSpace(24);
    sectionTitle(L('Lanac dokaza (Chain of Evidence)', 'Chain of evidence'));
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9.5);
    doc.setTextColor(...TEXT_DARK);
    const custodyLines = doc.splitTextToSize(
      (result.custody_findings_recorded ?? 0) > 0
        ? L(
            `${result.custody_findings_recorded} transakcija iz cele skenirane evidencije je zabelezeno u lancu dokaza (custody_log) sa `
              + 'strukturiranim SYBIL_CLUSTER dokazom, jasno razdvojenim na blockchain cinjenice i heuristicke zakljucke - videti stranicu '
              + '"Lanac dokaza" za detalje po transakciji.',
            `${result.custody_findings_recorded} transactions across the whole scanned evidence were recorded in the chain of custody `
              + '(custody_log) with a structured SYBIL_CLUSTER item, clearly split into blockchain facts and heuristic conclusions - see '
              + 'the "Chain of custody" page for the per-transaction detail.',
          )
        : L(
            'Ovaj prikaz nije upisan u lanac dokaza (izvestaj je izvezen bez prethodnog potpisanog pokretanja analize sa custody podacima).',
            'This view was not recorded in the chain of custody (the report was exported without a prior signed, custody-bearing analysis run).',
          ),
      usableWidth,
    );
    doc.text(custodyLines, marginX, y);
    y += custodyLines.length * 4.6 + 4;

    // --- Zavrsni forenzicki zakljucak (isti tekst kao na ekranu) -------------------------
    ensureSpace(24);
    sectionTitle(L('Zavrsni forenzicki zakljucak', 'Final forensic conclusion'));
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9.5);
    doc.setTextColor(...TEXT_DARK);
    const conclusionText = this.asciiSafe(this.forensicConclusion ?? '');
    const conclusionLines = doc.splitTextToSize(conclusionText, usableWidth);
    ensureSpace(conclusionLines.length * 4.6);
    doc.text(conclusionLines, marginX, y);
    y += conclusionLines.length * 4.6;

    // --- Metodologija i ogranicenja appendix ---------------------------------------------
    const paragraph = (text: string, options?: { bold?: boolean; size?: number; gap?: number }): void => {
      const size = options?.size ?? 9;
      doc.setFont('helvetica', options?.bold ? 'bold' : 'normal');
      doc.setFontSize(size);
      doc.setTextColor(...TEXT_DARK);
      const lines: string[] = doc.splitTextToSize(text, usableWidth);
      const lineHeight = size * 0.48;
      ensureSpace(lines.length * lineHeight);
      doc.text(lines, marginX, y);
      y += lines.length * lineHeight + (options?.gap ?? 3);
    };

    const bullet = (text: string): void => {
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(9);
      doc.setTextColor(...TEXT_DARK);
      const lines: string[] = doc.splitTextToSize(text, usableWidth - 6);
      ensureSpace(lines.length * 4.4);
      doc.setFillColor(...ACCENT);
      doc.circle(marginX + 1.4, y - 1.2, 0.7, 'F');
      doc.text(lines, marginX + 6, y);
      y += lines.length * 4.4 + 1.6;
    };

    y += 6;
    ensureSpace(50);
    sectionTitle(L('Metodologija i ogranicenja', 'Methodology and limitations'));

    paragraph(L('Heuristika', 'Heuristic'), { bold: true, size: 10, gap: 2 });
    paragraph(
      L(
        'Klaster se prijavljuje kad je bar zadati minimalan broj RAZLICITIH adresa pozvalo isti kontrakt (i, kad je '
          + 'deklarisano, istu funkciju) unutar kratkog, hronoloski povezanog vremenskog prozora. Vise sinhronizovanih '
          + 'adresa, identican iznos i ponavljanje iste kohorte u vise klastera povecavaju risk score, do maksimalnih 100.',
        'A cluster is reported when at least the given minimum number of DIFFERENT addresses called the same contract '
          + '(and, when declared, the same function) within a short, chronologically chained time window. More '
          + 'synchronized addresses, an identical amount, and the same cohort recurring across clusters increase the '
          + 'risk score, up to a maximum of 100.',
      ),
      { gap: 5 },
    );

    paragraph(L('Sta ovo NIJE', 'What this is NOT'), { bold: true, size: 10, gap: 2 });
    paragraph(
      L(
        'Ovo je heuristika, ne dokaz. Legitimni, nekoordinisani skupovi korisnika (javna prodaja, popularan airdrop, '
          + 'viralna kampanja) mogu proizvesti identican obrazac. Nijedan nalaz ne dokazuje da oznacene adrese pripadaju '
          + 'istoj osobi ili entitetu.',
        'This is a heuristic, not proof. Legitimate, uncoordinated groups of users (a public sale, a popular airdrop, a '
          + 'viral campaign) can produce an identical pattern. No finding proves that the flagged addresses belong to '
          + 'the same person or entity.',
      ),
      { gap: 5 },
    );

    paragraph(L('Ogranicenja podataka', 'Data limitations'), { bold: true, size: 10, gap: 2 });
    bullet(
      L(
        'Nema ABI dekodiranja - "poziv pametnog ugovora" je aproksimiran kao transakcija ciji je primalac data adresa; '
          + 'nije nezavisno potvrdjeno da je ta adresa zaista pametan ugovor (nema eth_getCode provere).',
        'No ABI decoding - a "smart contract call" is approximated as a transaction whose recipient is the given '
          + 'address; it is not independently confirmed that address is actually a smart contract (no eth_getCode check).',
      ),
    );
    bullet(
      L(
        'Deklarisana funkcija je opciona kolona u evidenciji, retko popunjena - bez nje, grupisanje radi samo po kontraktu.',
        'The declared function is an optional evidence column, rarely populated - without it, grouping is by contract only.',
      ),
    );
    bullet(
      L(
        'Forenzicki pregled (Graph/Taint/Putanje/DEX) je orijentacioni i PASIVAN (bez potpisa u lanac dokaza) - prikazan '
          + 'gore samo za klastere za koje je analiticar stvarno pokrenuo dugme pre izvoza.',
        'The forensic overview (Graph/Taint/Pathfinding/DEX) is orientation-only and PASSIVE (not recorded in the chain '
          + 'of custody) - shown above only for clusters where the analyst actually ran the button before exporting.',
      ),
    );
    bullet(
      L(
        'Adresa/kontrakt se traze tacnim poklapanjem (case-sensitive), ista konvencija kao Pathfinding/Behavioral/DEX Swap.',
        'Address/contract are matched exactly (case-sensitive), the same convention as Pathfinding/Behavioral/DEX Swap.',
      ),
    );

    // --- Potpis i overa -------------------------------------------------------------
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
      `${L('OVERENO', 'CERTIFIED')} ${new Date(signing.registration.registered_at).toLocaleDateString(this.sybilPdfLang === 'sr' ? 'sr-RS' : 'en-GB')}`,
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
          + 'matches the recorded one, the data in the report is the same as at export time. If it does not match, the '
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
          + 'izmeni dokument. Izmena se otkriva iskljucivo poredjenjem otiska sadrzaja.',
        'Limitation: the signature above is the analyst\'s declaration, not a cryptographic proof - it stays intact '
          + 'even if someone edits the document. An alteration is detected solely by comparing the content hash.',
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
      doc.text(`Lusi v1.0 forensic export | ${L('Strana', 'Page')} ${page}/${pageCount}`, pageWidth / 2, pageHeight - 8, { align: 'center' });
    }

    doc.save(`${caseSummary.id}_sybil_analysis_report.pdf`);
  }
}
