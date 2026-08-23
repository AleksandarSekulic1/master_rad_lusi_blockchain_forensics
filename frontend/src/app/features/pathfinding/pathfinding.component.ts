import { CommonModule } from '@angular/common';
import { Component, DestroyRef, ElementRef, HostListener, OnDestroy, OnInit, ViewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { distinctUntilChanged, map } from 'rxjs/operators';

import cytoscape, { Core, ElementDefinition } from 'cytoscape';
import { jsPDF } from 'jspdf';
import autoTable from 'jspdf-autotable';

import { SignaturePadComponent } from '../../core/components/signature-pad/signature-pad.component';
import { ensureCytoscapeExtensionsRegistered } from '../../core/cytoscape-setup';
import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { ApiService } from '../../core/services/api.service';
import { AuthService } from '../../core/services/auth.service';
import {
  CasePathfindingResult,
  CaseSummary,
  EvidenceEntry,
  NodeLinkGraphResponse,
  PathfindingDestinationMode,
  TaintAnalysisResult,
  TransactionCustodyEntry,
} from '../../models/blockchain-forensics.models';
import { CustodyAccessDialogComponent } from '../custody-access-dialog/custody-access-dialog.component';

/** One row of the "list of transactions along the path" panel - derived entirely from
 * data the page already has loaded (this.graph.links), not fetched separately. When an
 * edge aggregates more than one individual transaction, the EARLIEST one is shown as the
 * hop's representative (deterministic, matches the "one line per hop" mockup) and
 * `extraTransactionCount` says how many more exist on that same edge - nothing is hidden
 * silently. */
interface PathHopDetail {
  source: string;
  target: string;
  amount: number | null;
  timestamp: string | null;
  txHash: string | null;
  extraTransactionCount: number;
  taintPercentage: number | null;
}

/** Pathfinding Analysis - "kojim putem se sredstva kreću između dve adrese", a
 * deliberately separate question (and page) from Taint Analysis's "kako se zaprljana
 * sredstva propagiraju kroz mrežu". First version: unweighted BFS, single path, no
 * CEX/cash-out detection - see backend/app/analytics/path_finding.py.
 *
 * Reuses the same shape as the Graf page (case/evidence picker, plain cytoscape graph,
 * ensureCytoscapeExtensionsRegistered) rather than introducing a new graph component -
 * graph-visualization.component.ts is NOT imported/modified, only used as the visual/
 * structural reference (see LANAC-DOKAZA and TAINT-ANALIZA docs for the same convention
 * of "look consistent, stay independent" between the app's analysis pages).
 */
@Component({
  selector: 'app-pathfinding',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, CustodyAccessDialogComponent, SignaturePadComponent],
  templateUrl: './pathfinding.component.html',
  styleUrl: './pathfinding.component.scss',
})
export class PathfindingComponent implements OnInit, OnDestroy {
  @ViewChild('pathCanvas', { static: true })
  protected pathCanvas!: ElementRef<HTMLDivElement>;

  protected activeCase: CaseSummary | null = null;
  protected evidenceOptions: EvidenceEntry[] = [];
  protected selectedEvidence: string | null = null;

  protected graph: NodeLinkGraphResponse | null = null;
  protected isLoadingGraph = false;
  protected graphError: string | null = null;

  protected fromAddress = '';
  protected toAddress = '';
  /** 'cash_out_point' is listed but disabled in the UI - not implemented yet. */
  protected destinationMode: PathfindingDestinationMode = 'specific_address';
  protected isSearching = false;
  protected searchError: string | null = null;
  protected result: CasePathfindingResult | null = null;

  // --- Path Analysis: taint trace for the found path, run on demand (see LANAC-DOKAZA.md
  // for why this goes through the same custody dialog as "Pokreni taint analizu"/
  // "Analiziraj graf" - it is the same kind of deliberate access to the evidence). ---
  protected isTaintDialogOpen = false;
  protected isRunningTaint = false;
  protected taintDialogError: string | null = null;
  protected pathTaintResult: TaintAnalysisResult | null = null;

  // --- PDF izveštaj: potpis + pečat + kontrolni broj, ista struktura kao Taint analiza
  // (§6.3 TAINT-ANALIZA.md) - kod ovde je samostalna kopija tog obrasca (ne deljen modul),
  // baš da se taint-analysis.component.ts ne bi ni dodirivao ni posredno. ---
  @ViewChild(SignaturePadComponent) private signaturePad?: SignaturePadComponent;
  protected isSignatureDialogOpen = false;
  protected signatureDeclarationAccepted = false;
  protected signatureError: string | null = null;
  protected isExportingPdf = false;

  private cy: Core | null = null;

  constructor(
    private readonly state: AnalysisStateService,
    private readonly api: ApiService,
    private readonly auth: AuthService,
    private readonly destroyRef: DestroyRef,
  ) {
    ensureCytoscapeExtensionsRegistered();
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
        this.clearSearch();
        if (this.activeCase) {
          this.loadEvidenceOptions(this.activeCase.id);
          this.loadGraph();
        } else {
          this.graph = null;
          this.renderGraph();
        }
      });
  }

  ngOnDestroy(): void {
    this.cy?.destroy();
    this.cy = null;
  }

  @HostListener('window:resize')
  onResize(): void {
    this.cy?.resize();
    this.cy?.fit(undefined, 80);
  }

  loadEvidenceOptions(caseId: string): void {
    this.api.getCase(caseId).subscribe({
      next: (caseDetail) => {
        this.evidenceOptions = caseDetail.evidence;
      },
      error: () => {
        this.evidenceOptions = [];
      },
    });
  }

  onEvidenceSelected(storedName: string): void {
    this.selectedEvidence = storedName || null;
    this.clearSearch();
    this.loadGraph();
  }

  onDestinationModeChange(mode: PathfindingDestinationMode): void {
    this.destinationMode = mode;
    this.clearSearch();
  }

  /** Plain, ungated graph - same as the Graf page's automatic preview. Pathfinding does
   * not run any analytics pipeline (no risk/blacklist coloring), so there is nothing here
   * that would call for the custody-access dialog the way "Pokreni taint analizu"/
   * "Analiziraj graf" do. */
  loadGraph(): void {
    const caseId = this.activeCase?.id;
    if (!caseId) {
      return;
    }

    if (!this.activeCase?.evidence_count) {
      this.graph = null;
      this.renderGraph();
      this.graphError = null;
      return;
    }

    this.isLoadingGraph = true;
    this.graphError = null;

    this.api.getCaseGraph(caseId, this.selectedEvidence).subscribe({
      next: (graph) => {
        this.graph = graph;
        this.isLoadingGraph = false;
        this.renderGraph();
      },
      error: () => {
        this.isLoadingGraph = false;
        this.graphError = 'Neuspešno učitavanje grafa za izabrani slučaj.';
      },
    });
  }

  get canSearch(): boolean {
    if (!this.activeCase || !this.graph || this.isSearching || this.fromAddress.trim().length === 0) {
      return false;
    }
    // 'Destination' only needs manual input in 'specific_address' mode - 'nearest_cex' is
    // resolved server-side from the graph itself, nothing to type.
    return this.destinationMode !== 'specific_address' || this.toAddress.trim().length > 0;
  }

  findPath(): void {
    const caseId = this.activeCase?.id;
    if (!caseId || !this.canSearch) {
      return;
    }

    this.isSearching = true;
    this.searchError = null;
    this.result = null;
    // A fresh path invalidates any taint trace computed for the PREVIOUS path - otherwise
    // the "Path Analysis" panel could keep showing stale percentages that no longer
    // describe what's on screen.
    this.pathTaintResult = null;
    this.taintDialogError = null;

    const to = this.destinationMode === 'specific_address' ? this.toAddress.trim() : null;

    this.api.findCasePath(caseId, this.fromAddress.trim(), this.destinationMode, to, this.selectedEvidence).subscribe({
      next: (result) => {
        this.result = result;
        this.isSearching = false;
        this.applyPathHighlight(result.found ? result.path : null);
      },
      error: () => {
        this.isSearching = false;
        this.searchError = 'Pretraga puta nije uspela.';
        this.applyPathHighlight(null);
      },
    });
  }

  private clearSearch(): void {
    this.result = null;
    this.searchError = null;
    this.pathTaintResult = null;
    this.taintDialogError = null;
    this.applyPathHighlight(null);
  }

  // --- Path Analysis: forensic details for the found path -------------------------------

  /** File name of the currently scoped evidence, for the custody dialog's default
   * "identifikator dokaznog materijala" - null means the combined view (all evidence). */
  protected get selectedEvidenceFileName(): string | null {
    if (!this.selectedEvidence) {
      return null;
    }
    return this.evidenceOptions.find((entry) => entry.stored_name === this.selectedEvidence)?.file_name ?? null;
  }

  /** Opens the access-reason dialog. Tracing HOW TAINTED the money on this specific path
   * is means running the taint_analysis plugin (seeded from the path's own origin
   * address), which is exactly the kind of deliberate access to the evidence "Pokreni
   * taint analizu"/"Analiziraj graf" already gate the same way (see LANAC-DOKAZA.md). */
  openTaintDialog(): void {
    if (!this.activeCase?.id || !this.result?.found) {
      return;
    }
    this.taintDialogError = null;
    this.isTaintDialogOpen = true;
  }

  closeTaintDialog(): void {
    this.isTaintDialogOpen = false;
  }

  /** Seeds the EXISTING taint_analysis plugin with just this path's origin address
   * (path[0]) - a seed always starts at 100% by definition of the haircut model, so
   * "Initial taint" below is exactly that, and "Final taint" is however much of THAT
   * specific money the model says survived by the time it reached the path's last
   * address, via this exact chain of hops. Nothing about taint_analysis itself changes -
   * this only ever calls the same POST /cases/{id}/analytics/run every other page uses. */
  confirmCustodyAndRunTaint(custody: TransactionCustodyEntry): void {
    const caseId = this.activeCase?.id;
    const seedAddress = this.result?.path[0];
    if (!caseId || !seedAddress) {
      return;
    }

    this.isRunningTaint = true;
    this.taintDialogError = null;

    this.api.runCaseAnalytics(caseId, this.selectedEvidence, [seedAddress], custody).subscribe({
      next: (response) => {
        this.pathTaintResult = (response.analytics?.['taint_analysis'] as TaintAnalysisResult | undefined) ?? null;
        this.isRunningTaint = false;
        this.isTaintDialogOpen = false;
      },
      error: () => {
        this.isRunningTaint = false;
        this.taintDialogError = 'Neuspešno pokretanje taint analize.';
      },
    });
  }

  private get taintByAddress(): Map<string, number> {
    return new Map((this.pathTaintResult?.results ?? []).map((entry) => [entry.address, entry.taint_percentage]));
  }

  /** One row per hop, built entirely from data already on the page (this.graph.links) -
   * no extra request. See PathHopDetail for why the EARLIEST transaction on an edge is
   * the one shown when a hop aggregates more than one. */
  get pathHops(): PathHopDetail[] {
    const path = this.result?.found ? this.result.path : null;
    if (!path || !this.graph) {
      return [];
    }

    const taint = this.taintByAddress;
    const hops: PathHopDetail[] = [];

    for (let index = 0; index < path.length - 1; index++) {
      const source = path[index];
      const target = path[index + 1];
      const link = this.graph.links.find((candidate) => String(candidate.source) === source && String(candidate.target) === target);
      const transactions = ((link?.transactions ?? []) as Array<{ amount?: number; timestamp?: string; metadata?: string | null }>)
        .slice()
        .sort((a, b) => String(a.timestamp ?? '').localeCompare(String(b.timestamp ?? '')));
      const earliest = transactions[0];

      hops.push({
        source,
        target,
        amount: earliest?.amount ?? null,
        timestamp: earliest?.timestamp ?? null,
        txHash: earliest?.metadata ?? null,
        extraTransactionCount: Math.max(0, transactions.length - 1),
        taintPercentage: taint.get(target) ?? null,
      });
    }

    return hops;
  }

  get initialAmount(): number | null {
    return this.pathHops[0]?.amount ?? null;
  }

  get finalAmount(): number | null {
    const hops = this.pathHops;
    return hops.length ? hops[hops.length - 1].amount : null;
  }

  /** Taint % of the path's OWN origin address - always 100% once a trace has been run,
   * since that address is exactly what was seeded (see confirmCustodyAndRunTaint). Null
   * (not 0) when no trace has been run yet, so the template can tell "not computed" apart
   * from "computed and genuinely zero". */
  get initialTaint(): number | null {
    if (!this.pathTaintResult) {
      return null;
    }
    const address = this.result?.path[0];
    return address ? this.taintByAddress.get(address) ?? 0 : null;
  }

  get finalTaint(): number | null {
    if (!this.pathTaintResult || !this.result?.found) {
      return null;
    }
    const path = this.result.path;
    const address = path[path.length - 1];
    return address ? this.taintByAddress.get(address) ?? 0 : null;
  }

  get taintDilution(): number | null {
    return this.initialTaint != null && this.finalTaint != null ? this.initialTaint - this.finalTaint : null;
  }

  /** "2 dana 6 sati" from the first to the last hop's own (earliest-transaction) timestamp
   * - null when either end is missing (e.g. an edge with no timestamped transaction data)
   * rather than a misleading "0 min". BFS itself does not consider time, so this duration
   * describes the hops AS PICKED here, not a chronologically verified single fund flow -
   * see PATHFINDING-ANALIZA.md for that caveat. */
  get pathDurationLabel(): string | null {
    const hops = this.pathHops;
    const first = hops[0]?.timestamp;
    const last = hops[hops.length - 1]?.timestamp;
    if (!first || !last) {
      return null;
    }
    const ms = new Date(last).getTime() - new Date(first).getTime();
    if (Number.isNaN(ms) || ms < 0) {
      return null;
    }
    return this.formatDuration(ms);
  }

  private formatDuration(ms: number): string {
    const totalMinutes = Math.round(ms / 60_000);
    const days = Math.floor(totalMinutes / 1440);
    const hours = Math.floor((totalMinutes % 1440) / 60);
    const minutes = totalMinutes % 60;

    const parts: string[] = [];
    if (days > 0) {
      parts.push(`${days} ${days === 1 ? 'dan' : 'dana'}`);
    }
    if (hours > 0) {
      parts.push(`${hours} ${hours === 1 ? 'sat' : 'sati'}`);
    }
    if (days === 0 && hours === 0) {
      parts.push(`${minutes} min`);
    }
    return parts.join(' ');
  }

  /** Highlights exactly the found path (nodes + the edges connecting consecutive hops)
   * and dims the rest of the graph - same visual language (class names AND cytoscape
   * style values) as Taint Analysis's own path highlight, see
   * taint-analysis.component.ts's applyPathHighlight/path-highlighted/path-dimmed. */
  private applyPathHighlight(path: string[] | null): void {
    if (!this.cy) {
      return;
    }

    this.cy.elements().removeClass('path-highlighted path-dimmed');
    if (!path || path.length === 0) {
      return;
    }

    const nodeIds = new Set(path);
    const edgeKeys = new Set<string>();
    for (let index = 0; index < path.length - 1; index++) {
      edgeKeys.add(`${path[index]}__${path[index + 1]}`);
    }

    this.cy.nodes().forEach((node) => {
      node.addClass(nodeIds.has(node.id()) ? 'path-highlighted' : 'path-dimmed');
    });
    this.cy.edges().forEach((edge) => {
      const key = `${edge.data('source')}__${edge.data('target')}`;
      edge.addClass(edgeKeys.has(key) ? 'path-highlighted' : 'path-dimmed');
    });

    this.cy.animate({ fit: { eles: this.cy.nodes().filter((node) => nodeIds.has(node.id())), padding: 120 } }, { duration: 350 });
  }

  /** Full-length addresses as node labels are unreadable clutter once a graph has more
   * than a handful of nodes - same truncation as the Graf page. */
  private truncateAddress(value: string): string {
    if (value.length <= 14) {
      return value;
    }
    return `${value.slice(0, 6)}…${value.slice(-4)}`;
  }

  private buildElements(graph: NodeLinkGraphResponse): ElementDefinition[] {
    const nodes: ElementDefinition[] = graph.nodes.map((node) => ({
      data: {
        ...node,
        label: this.truncateAddress(String(node.label ?? node.address ?? node.id)),
      },
    }));

    const links: ElementDefinition[] = graph.links.map((link) => ({
      data: {
        ...link,
        id: `${link.source}__${link.target}`,
        label: link.total_amount != null ? Number(link.total_amount).toFixed(2) : '',
      },
    }));

    return [...nodes, ...links];
  }

  private renderGraph(): void {
    if (!this.pathCanvas) {
      return;
    }

    this.cy?.destroy();
    this.cy = null;

    if (!this.graph) {
      return;
    }

    const elements = this.buildElements(this.graph);

    // Same escape hatch graph-visualization.component.ts uses: cytoscape's Stylesheet
    // type doesn't recognize every valid property (e.g. 'active-bg-color') as optional,
    // so a literal style array fails to typecheck even though it's valid at runtime.
    const graphStyles: any = [
        {
          selector: 'core',
          style: {
            'selection-box-color': '#7dd3fc',
            'selection-box-border-color': '#7dd3fc',
            'active-bg-opacity': 0.12,
            'active-bg-color': '#7dd3fc',
          },
        },
        {
          selector: 'node',
          style: {
            label: 'data(label)',
            'text-valign': 'center',
            'text-halign': 'center',
            'font-size': 11,
            'min-zoomed-font-size': 8,
            color: '#ecf2ff',
            'text-outline-width': 2,
            'text-outline-color': '#07111f',
            'background-color': '#4f8cff',
            'border-width': 2,
            'border-color': '#9bd1ff',
            width: 40,
            height: 40,
          },
        },
        {
          selector: 'edge',
          style: {
            width: 2,
            'line-color': '#6ea8fe',
            'target-arrow-color': '#6ea8fe',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            opacity: 0.72,
            label: 'data(label)',
            'font-size': 9,
            'min-zoomed-font-size': 7,
            color: '#b3c3df',
            'text-background-color': '#07111f',
            'text-background-opacity': 0.8,
            'text-background-padding': '2px',
          },
        },
        // Same class names AND same style values as Taint Analysis's path highlight -
        // outline-* rather than border-* so it layers independently, opacity-only dim
        // rather than hiding so the overall graph shape stays legible for context.
        {
          selector: 'node.path-highlighted',
          style: {
            'outline-width': 4,
            'outline-color': '#7dd3fc',
            'outline-style': 'solid',
            'outline-offset': 2,
            'background-color': '#2f9e63',
            'border-color': '#8ff0bd',
          },
        },
        {
          selector: 'edge.path-highlighted',
          style: {
            'line-color': '#7dd3fc',
            'target-arrow-color': '#7dd3fc',
            opacity: 1,
            width: 4,
          },
        },
        {
          selector: 'node.path-dimmed',
          style: { opacity: 0.15 },
        },
        {
          selector: 'edge.path-dimmed',
          style: { opacity: 0.06 },
        },
    ];

    this.cy = cytoscape({
      container: this.pathCanvas.nativeElement,
      elements,
      wheelSensitivity: 0.2,
      layout: {
        name: 'fcose',
        quality: 'default',
        randomize: true,
        animate: false,
        fit: true,
        padding: 60,
        nodeSeparation: 100,
        nodeRepulsion: 6000,
        idealEdgeLength: 80,
      } as any,
      style: graphStyles,
    });
  }

  fitWholeGraph(): void {
    this.cy?.fit(undefined, 60);
  }

  // ==========================================================================================
  // PDF izveštaj: potpis, pečat, kontrolni broj, provera verodostojnosti - ista struktura
  // kao Taint Analysis (TAINT-ANALIZA.md §6.3), namerno pisana kao samostalna kopija ovde
  // (a ne izvučena u deljeni modul) da taint-analysis.component.ts ostane potpuno nedirano.
  // Koristi iste, već postojeće, generičke backend rute - POST /api/v1/reports/register i
  // GET /api/v1/reports/verify (app/services/report_registry.py) ne znaju niti im je bitno
  // da li je izveštaj taint ili pathfinding; "Provera izveštaja" stranica radi bez izmena.
  // ==========================================================================================

  private static readonly PDF_NAVY: [number, number, number] = [13, 24, 40];
  private static readonly PDF_ACCENT: [number, number, number] = [43, 130, 191];
  private static readonly PDF_TEXT_GRAY: [number, number, number] = [100, 112, 128];
  private static readonly PDF_TEXT_DARK: [number, number, number] = [24, 28, 36];
  private static readonly PDF_WHITE: [number, number, number] = [255, 255, 255];
  private static readonly PDF_AMBER: [number, number, number] = [217, 119, 6];
  private static readonly PDF_GREEN: [number, number, number] = [22, 163, 74];
  private static readonly PDF_CANVAS_BG = '#070d1a';

  private static readonly ASCII_MAP: Record<string, string> = {
    č: 'c', ć: 'c', š: 's', ž: 'z', đ: 'dj',
    Č: 'C', Ć: 'C', Š: 'S', Ž: 'Z', Đ: 'Dj',
  };

  /** Same reasoning as Taint Analysis's own asciiSafe: jsPDF's core fonts don't reliably
   * cover č/ć/š/ž/đ, so report text is transliterated rather than embedding a Unicode TTF
   * just for this document. Addresses/numbers are unaffected. */
  private asciiSafe(value: string | null | undefined): string {
    return (value ?? '').replace(/[čćšžđČĆŠŽĐ]/g, (match) => PathfindingComponent.ASCII_MAP[match] ?? match);
  }

  private static formatPdfAmount(value: number): string {
    return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 });
  }

  private static loadImageSize(dataUrl: string): Promise<{ width: number; height: number }> {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
      image.onerror = () => reject(new Error('image load failed'));
      image.src = dataUrl;
    });
  }

  /** Opens the signing dialog. Export always goes through it, same as Taint Analysis: the
   * report leaves the application as a standalone document, so it has to carry both who
   * vouches for it and the means to check it later. */
  openSignatureDialog(): void {
    if (!this.cy || !this.result?.found || !this.activeCase || this.isExportingPdf) {
      return;
    }
    this.isSignatureDialogOpen = true;
    this.signatureDeclarationAccepted = false;
    this.signatureError = null;
    setTimeout(() => this.signaturePad?.clear());
  }

  closeSignatureDialog(): void {
    this.isSignatureDialogOpen = false;
  }

  get canSubmitSignature(): boolean {
    return (this.signaturePad?.hasStrokes ?? false) && this.signatureDeclarationAccepted && !this.isExportingPdf;
  }

  protected static readonly SIGNATURE_DECLARATION =
    'Potvrđujem da sam izradio ovaj izveštaj u okviru navedenog predmeta i da su u njemu prikazani rezultati onakvi '
    + 'kakve je aplikacija izračunala nad navedenom evidencijom.';

  /** The exact data the verification hash is computed over - kept to the figures a reader
   * could dispute (the path itself, amounts, taint trace if run), so altering any of them
   * in the exported document makes the check fail. */
  private reportContentPayload(): Record<string, unknown> {
    const result = this.result!;
    return {
      case_id: this.activeCase!.id,
      evidence: this.selectedEvidence ?? 'combined',
      evidence_sha256: this.evidenceOptions.map((entry) => entry.sha256).sort(),
      from_address: this.fromAddress.trim(),
      destination_mode: this.destinationMode,
      destination_address:
        this.destinationMode === 'specific_address' ? this.toAddress.trim() : (result.destination_address ?? null),
      path: result.path,
      hops: result.hops,
      taint: this.pathTaintResult
        ? { initial: this.initialTaint, final: this.finalTaint, dilution: this.taintDilution }
        : null,
    };
  }

  async confirmSignatureAndExport(): Promise<void> {
    if (!this.canSubmitSignature || !this.cy || !this.result?.found || !this.activeCase) {
      return;
    }

    this.isExportingPdf = true;
    this.signatureError = null;
    try {
      const signatureImage = this.signaturePad!.getDataUrl();
      const declaration = PathfindingComponent.SIGNATURE_DECLARATION;

      // Registered BEFORE the document is built: the verification code has to be printed
      // inside the very report it identifies.
      const registration = await firstValueFrom(
        this.api.registerReport({
          case_id: this.activeCase.id,
          case_name: this.activeCase.name ?? '',
          declaration,
          content: this.reportContentPayload(),
          summary: {
            hops: this.result.hops,
            destination_mode: this.destinationMode,
            taint_trace: this.pathTaintResult ? 'da' : 'ne',
          },
        }),
      );

      const graphImage = this.cy.png({ full: true, scale: 2, bg: PathfindingComponent.PDF_CANVAS_BG });
      const imageSize = await PathfindingComponent.loadImageSize(graphImage);
      this.buildPathfindingPdf(graphImage, imageSize, { signatureImage, declaration, registration });
      this.isSignatureDialogOpen = false;
    } catch {
      this.signatureError = 'Neuspešno generisanje PDF izveštaja.';
    } finally {
      this.isExportingPdf = false;
    }
  }

  private buildPathfindingPdf(
    graphImage: string,
    imageSize: { width: number; height: number },
    signing: {
      signatureImage: string;
      declaration: string;
      registration: { verification_code: string; content_hash: string; registered_at: string; analyst: string };
    },
  ): void {
    const result = this.result!;
    const caseSummary = this.activeCase!;
    const NAVY = PathfindingComponent.PDF_NAVY;
    const ACCENT = PathfindingComponent.PDF_ACCENT;
    const TEXT_GRAY = PathfindingComponent.PDF_TEXT_GRAY;
    const TEXT_DARK = PathfindingComponent.PDF_TEXT_DARK;
    const WHITE = PathfindingComponent.PDF_WHITE;

    const doc = new jsPDF({ unit: 'mm', format: 'a4' });
    const pageWidth = doc.internal.pageSize.getWidth();
    const pageHeight = doc.internal.pageSize.getHeight();
    const marginX = 14;
    const usableWidth = pageWidth - marginX * 2;
    let y = 32;

    doc.setFillColor(...NAVY);
    doc.rect(0, 0, pageWidth, 24, 'F');
    doc.setTextColor(...WHITE);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(15);
    doc.text('Lusi v1.0 - Izvestaj Pathfinding analize', marginX, 11);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(10);
    doc.text(`Slucaj: ${this.asciiSafe(caseSummary.name)}`, marginX, 19);
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
    kv('IZVEZAO', this.asciiSafe(this.auth.currentUser?.username ?? caseSummary.analyst));
    kv('EVIDENCIJA', this.selectedEvidence ? this.asciiSafe(this.selectedEvidence) : 'Sve transakcije (kombinovano)');
    kv('NACIN ODREDISTA', this.destinationMode === 'nearest_cex' ? 'Nearest known CEX' : 'Specific address');
    kv('GENERISANO', new Date().toLocaleString());
    y += 2;

    // Short "how to read this" note, placed before any result - same convention as Taint
    // Analysis's own report: what the model does and does not claim isn't obvious to a
    // reader who never sees the source code.
    const methodologyNoteLines = doc.splitTextToSize(
      'Ovaj izvestaj prikazuje JEDNU putanju stvarnih transakcija, pronadjenu nezatezenom (BFS) pretragom - '
        + 'najkraca po broju skokova, ne po iznosu ili verovatnoci, i bez provere hronoloskog redosleda skokova. '
        + 'Detaljno objasnjenje i ogranicenja nalaze se na kraju izvestaja.',
      usableWidth - 8,
    );
    const noteBoxHeight = methodologyNoteLines.length * 4.2 + 7;
    doc.setFillColor(253, 250, 240);
    doc.setDrawColor(...PathfindingComponent.PDF_AMBER);
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
        doc.setFontSize(13);
        doc.text(String(value), x + colWidth / 2, y0 + 6.5, { align: 'center' });
        doc.setFont('helvetica', 'normal');
        doc.setFontSize(7);
        doc.text(doc.splitTextToSize(label, colWidth - 4), x + colWidth / 2, y0 + 11, { align: 'center' });
        x += colWidth + gap;
      }
      y = y0 + 15 + 6;
      doc.setTextColor(...TEXT_DARK);
    };

    sectionTitle('Rezime puta');
    const formatAmountOrNa = (value: number | null): string => (value === null ? 'n/a' : PathfindingComponent.formatPdfAmount(value));
    drawSummaryCards([
      ['Broj skokova', result.hops, ACCENT],
      ['Adresa na putu', result.path.length, ACCENT],
      ['Pocetni iznos', formatAmountOrNa(this.initialAmount), ACCENT],
      ['Krajnji iznos', formatAmountOrNa(this.finalAmount), ACCENT],
    ]);
    if (this.pathDurationLabel) {
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(9);
      doc.setTextColor(...TEXT_GRAY);
      doc.text(`Trajanje puta: ${this.asciiSafe(this.pathDurationLabel)}`, marginX, y);
      y += 8;
      doc.setTextColor(...TEXT_DARK);
    }

    if (this.destinationMode === 'nearest_cex' && result.destination_label) {
      const destLine =
        `Odrediste (Nearest known CEX): ${result.destination_address} [${this.asciiSafe(result.destination_label)}]`;
      const destLines = doc.splitTextToSize(destLine, usableWidth - 8);
      const destBoxHeight = destLines.length * 4.2 + 6;
      doc.setFillColor(240, 253, 244);
      doc.setDrawColor(...PathfindingComponent.PDF_GREEN);
      doc.setLineWidth(0.4);
      doc.roundedRect(marginX, y - 4, usableWidth, destBoxHeight, 2, 2, 'FD');
      doc.setFont('courier', 'normal');
      doc.setFontSize(8.5);
      doc.setTextColor(...TEXT_DARK);
      doc.text(destLines, marginX + 4, y + 1);
      y += destBoxHeight + 4;
    }

    if (this.pathTaintResult) {
      sectionTitle('Taint provera puta');
      doc.setFont('helvetica', 'italic');
      doc.setFontSize(8);
      doc.setTextColor(...TEXT_GRAY);
      const taintNoteLines = doc.splitTextToSize(
        'Taint analiza pokrenuta sa jednim seed-om: polazna adresa ove putanje (dobija 100% po definiciji modela). '
          + 'Krajnji procenat pokazuje koliko je od te vrednosti stiglo do poslednje adrese, bas kroz ovu putanju.',
        usableWidth,
      );
      doc.text(taintNoteLines, marginX, y);
      y += taintNoteLines.length * 4 + 4;
      doc.setFont('helvetica', 'normal');
      doc.setTextColor(...TEXT_DARK);
      drawSummaryCards([
        ['Pocetni taint', `${this.initialTaint ?? 0}%`, PathfindingComponent.PDF_GREEN],
        ['Krajnji taint', `${this.finalTaint ?? 0}%`, PathfindingComponent.PDF_AMBER],
        ['Razblazenje', `${this.taintDilution ?? 0}%`, ACCENT],
      ]);
    }

    sectionTitle('Putanja');
    autoTable(doc, {
      startY: y,
      margin: { left: marginX, right: marginX },
      head: [['Br.', 'Adresa']],
      body: result.path.map((address, index) => [String(index + 1), address]),
      styles: { fontSize: 8.5, cellPadding: 1.8, font: 'courier', textColor: TEXT_DARK },
      headStyles: { fillColor: NAVY, textColor: WHITE, font: 'helvetica', fontStyle: 'bold' },
      alternateRowStyles: { fillColor: [240, 245, 250] },
    });
    y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 10;

    if (y > pageHeight - 60) {
      doc.addPage();
      y = 16;
    }
    sectionTitle('Transakcije na putu');
    const hopRows = this.pathHops.map((hop, index) => [
      String(index + 1),
      this.truncateAddress(hop.source),
      this.truncateAddress(hop.target),
      hop.amount !== null ? PathfindingComponent.formatPdfAmount(hop.amount) : 'n/a',
      hop.taintPercentage !== null ? `${hop.taintPercentage}%` : '-',
      hop.timestamp ? new Date(hop.timestamp).toLocaleString() : 'n/a',
      this.asciiSafe(hop.txHash ?? 'n/a'),
    ]);
    autoTable(doc, {
      startY: y,
      margin: { left: marginX, right: marginX },
      head: [['#', 'Od', 'Do', 'Iznos', 'Taint', 'Vreme', 'Tx hes']],
      body: hopRows,
      styles: { fontSize: 7.3, cellPadding: 1.5, font: 'courier', textColor: TEXT_DARK },
      headStyles: { fillColor: NAVY, textColor: WHITE, font: 'helvetica', fontStyle: 'bold' },
      alternateRowStyles: { fillColor: [240, 245, 250] },
    });
    y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 10;

    // Graph snapshot, exactly as currently rendered (path highlighted, rest dimmed) - same
    // technique as Taint Analysis: this.cy.png() before this method is called.
    const maxImgHeight = 90;
    let renderWidth = usableWidth;
    let renderHeight = (imageSize.height / imageSize.width) * renderWidth;
    if (renderHeight > maxImgHeight) {
      renderHeight = maxImgHeight;
      renderWidth = (imageSize.width / imageSize.height) * renderHeight;
    }
    const sectionTitleHeight = 11;
    if (y + sectionTitleHeight + renderHeight > pageHeight - 20) {
      doc.addPage();
      y = 16;
    }
    sectionTitle('Graficki prikaz puta');
    const imgX = marginX + (usableWidth - renderWidth) / 2;
    doc.setFillColor(7, 13, 26);
    doc.rect(imgX, y, renderWidth, renderHeight, 'F');
    doc.addImage(graphImage, 'PNG', imgX, y, renderWidth, renderHeight);
    y += renderHeight + 5;
    doc.setFont('helvetica', 'italic');
    doc.setFontSize(8);
    doc.setTextColor(...TEXT_GRAY);
    const legendLines = doc.splitTextToSize(
      'Istaknuta (cijan) putanja je pronadjeni put; ostatak grafa je zatamnjen. Slika prikazuje tacno ono sto je '
        + 'bilo vidljivo na ekranu u trenutku izvoza.',
      usableWidth,
    );
    doc.text(legendLines, marginX, y);
    y += legendLines.length * 4 + 6;
    doc.setTextColor(...TEXT_DARK);

    if (y > pageHeight - 60) {
      doc.addPage();
      y = 16;
    }
    sectionTitle('Metodologija i ogranicenja');
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9);
    doc.setTextColor(...TEXT_DARK);
    const methodologyLines = doc.splitTextToSize(
      'Putanja je pronadjena nezatezenom (BFS) pretragom preko usmerenih grana (posiljalac -> primalac) - najkraca '
        + 'po broju skokova, ne po iznosu ili verovatnoci. Model ne proverava da li skokovi hronoloski predstavljaju '
        + 'jedan kontinuirani tok istog novca - moguce je da put topoloski postoji a hronoloski nije realan tok. '
        + 'Odrediste "Nearest known CEX" (ako je koriscen) zasniva se iskljucivo na lokalnom registru poznatih '
        + 'adresa (berze/mikseri/sankcionisane adrese, known_entities.json) - adresa se nikad ne pretpostavlja na '
        + 'osnovu izgleda ili naziva. Opciona taint provera (ako je pokrenuta) koristi isti proporcionalni '
        + '("haircut") model kao Taint analiza, seedovan iskljucivo polaznom adresom ove putanje.',
      usableWidth,
    );
    doc.text(methodologyLines, marginX, y);
    y += methodologyLines.length * 4.4 + 4;

    // Potpis i overa - identicna struktura i vrednosti kao u Taint Analysis izvestaju (isti
    // vizuelni jezik kroz celu aplikaciju). Nacrtani potpis je IZJAVA analiticara, ne dokaz
    // nepromenjenosti - to obezbedjuju kontrolni broj i otisak sadrzaja ispod.
    doc.addPage();
    y = 16;
    sectionTitle('Potpis i overa');

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
    doc.text('Potpis analiticara', marginX, y + signatureBoxHeight + 4);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(9.5);
    doc.setTextColor(...TEXT_DARK);
    doc.text(this.asciiSafe(signing.registration.analyst), marginX, y + signatureBoxHeight + 9);

    // Round stamp, drawn as vectors rather than an image so it stays crisp at any zoom.
    const sealCenterX = marginX + signatureBoxWidth + (usableWidth - signatureBoxWidth) / 2;
    const sealCenterY = y + signatureBoxHeight / 2;
    const sealRadius = 19;
    doc.setDrawColor(...NAVY);
    doc.setLineWidth(1.1);
    doc.circle(sealCenterX, sealCenterY, sealRadius);
    doc.setLineWidth(0.4);
    doc.circle(sealCenterX, sealCenterY, sealRadius - 2.5);
    doc.setTextColor(...NAVY);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(11);
    doc.text('LUSI', sealCenterX, sealCenterY - 5, { align: 'center' });
    doc.setFontSize(6.5);
    doc.setFont('helvetica', 'normal');
    doc.text('DIGITALNA FORENZIKA', sealCenterX, sealCenterY - 0.5, { align: 'center' });
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(7);
    doc.text('OVERENO', sealCenterX, sealCenterY + 4.5, { align: 'center' });
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(6);
    doc.text(new Date(signing.registration.registered_at).toLocaleDateString(), sealCenterX, sealCenterY + 9, {
      align: 'center',
    });

    y += signatureBoxHeight + 16;

    sectionTitle('Provera verodostojnosti');
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(9);
    doc.setTextColor(...TEXT_GRAY);
    doc.text('KONTROLNI BROJ', marginX, y);
    doc.setFont('courier', 'bold');
    doc.setFontSize(13);
    doc.setTextColor(...NAVY);
    doc.text(signing.registration.verification_code, marginX + 45, y + 0.5);
    y += 8;

    doc.setFont('helvetica', 'bold');
    doc.setFontSize(9);
    doc.setTextColor(...TEXT_GRAY);
    doc.text('OTISAK SADRZAJA', marginX, y);
    doc.setFont('courier', 'normal');
    doc.setFontSize(7.5);
    doc.setTextColor(...TEXT_DARK);
    doc.text(doc.splitTextToSize(signing.registration.content_hash, usableWidth - 45), marginX + 45, y);
    y += 9;

    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9);
    doc.setTextColor(...TEXT_DARK);
    const verifyLines = doc.splitTextToSize(
      'Verodostojnost se proverava u aplikaciji Lusi, unosom gornjeg kontrolnog broja. Ako se otisak sadrzaja '
        + 'poklapa sa zabelezenim, podaci u izvestaju su isti kao u trenutku izvoza. Ako se ne poklapa, izvestaj je '
        + 'izmenjen posle izvoza.',
      usableWidth,
    );
    doc.text(verifyLines, marginX, y);
    y += verifyLines.length * 4.6 + 4;

    doc.setFont('helvetica', 'italic');
    doc.setFontSize(8);
    doc.setTextColor(...TEXT_GRAY);
    const limitLines = doc.splitTextToSize(
      'Ogranicenje: potpis iznad je izjava analiticara, a ne kriptografski dokaz — on ostaje netaknut i ako neko '
        + 'izmeni dokument. Izmena se otkriva iskljucivo poredjenjem otiska sadrzaja. Provera potvrdjuje da se '
        + 'PODACI poklapaju sa registrovanim, ne da je PDF fajl bajt-po-bajt isti; za to bi bio potreban '
        + 'kriptografski potpis dokumenta (npr. PAdES), sto nije deo ove aplikacije.',
      usableWidth,
    );
    doc.text(limitLines, marginX, y);

    const pageCount = doc.getNumberOfPages();
    for (let page = 1; page <= pageCount; page++) {
      doc.setPage(page);
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(8);
      doc.setTextColor(...TEXT_GRAY);
      doc.text(`Lusi v1.0 forensic export | Strana ${page}/${pageCount}`, pageWidth / 2, pageHeight - 8, { align: 'center' });
    }

    doc.save(`${caseSummary.id}_pathfinding_report.pdf`);
  }
}
