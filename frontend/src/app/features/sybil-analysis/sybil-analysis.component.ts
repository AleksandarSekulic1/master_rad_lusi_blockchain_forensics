import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, OnInit } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { forkJoin, of } from 'rxjs';
import { catchError, distinctUntilChanged, map } from 'rxjs/operators';

import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { CaseDataApiService } from '../../core/services/case-data.api';
import { PathfindingApiService } from '../pathfinding/pathfinding.api';
import { SybilAnalysisApiService } from './sybil-analysis.api';
import { SettingsService } from '../../core/services/settings.service';
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

  constructor(
    private readonly state: AnalysisStateService,
    private readonly caseData: CaseDataApiService,
    private readonly pathfindingApi: PathfindingApiService,
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
    this.overviews.clear();
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
}
