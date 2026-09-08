import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, DestroyRef, OnInit, ViewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { distinctUntilChanged, map } from 'rxjs/operators';

import { jsPDF } from 'jspdf';
import autoTable from 'jspdf-autotable';

import { SignaturePadComponent } from '../../core/components/signature-pad/signature-pad.component';
import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { ApiService } from '../../core/services/api.service';
import { AuthService } from '../../core/services/auth.service';
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
  imports: [CommonModule, FormsModule, RouterLink, SignaturePadComponent],
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

  // --- PDF export (see DEX-SWAP-ANALIZA.md #11) - same signed-report mechanism as
  // Taint/Pathfinding: a control number is registered server-side BEFORE the document is
  // built (so it can be printed inside the very report it identifies), the analyst draws
  // a signature declaring they produced it, and the PDF itself is assembled client-side. ---
  @ViewChild(SignaturePadComponent) private signaturePad?: SignaturePadComponent;
  protected isSignatureDialogOpen = false;
  protected signatureDeclarationAccepted = false;
  protected signatureError: string | null = null;
  protected isExportingPdf = false;

  constructor(
    private readonly state: AnalysisStateService,
    private readonly api: ApiService,
    private readonly auth: AuthService,
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

  /** Same 3-level read as graph-visualization.component.ts's swapConfidenceLevel -
   * duplicated rather than shared (see that method's own comment for why: small
   * per-component display helpers are copied in this app, not centralized). Kept
   * identical on purpose so a report generated here and the Graph page's overlay never
   * disagree about how confident a given swap is. */
  protected confidenceLevel(event: DexSwapEvent): 'High' | 'Medium' | 'Low' {
    if (event.confidence === 'Detected') {
      return 'High';
    }
    return event.dex_match_basis.startsWith('keyword_match_generic') ? 'Low' : 'Medium';
  }

  // --- PDF export ----------------------------------------------------------------------

  private static readonly PDF_NAVY: [number, number, number] = [13, 24, 40];
  private static readonly PDF_ACCENT: [number, number, number] = [43, 130, 191];
  private static readonly PDF_TEXT_GRAY: [number, number, number] = [100, 112, 128];
  private static readonly PDF_TEXT_DARK: [number, number, number] = [24, 28, 36];
  private static readonly PDF_WHITE: [number, number, number] = [255, 255, 255];
  private static readonly PDF_AMBER: [number, number, number] = [217, 119, 6];
  private static readonly PDF_HIGH: [number, number, number] = [124, 58, 237];
  private static readonly PDF_MEDIUM: [number, number, number] = [43, 130, 191];
  private static readonly PDF_LOW: [number, number, number] = [100, 112, 128];

  private static confidenceColor(level: 'High' | 'Medium' | 'Low'): [number, number, number] {
    if (level === 'High') {
      return DexSwapAnalysisComponent.PDF_HIGH;
    }
    if (level === 'Medium') {
      return DexSwapAnalysisComponent.PDF_MEDIUM;
    }
    return DexSwapAnalysisComponent.PDF_LOW;
  }

  /** jsPDF's core fonts don't cover č/ć/š/ž/đ reliably - same tradeoff (and same fixed
   * transliteration table) as taint-analysis.component.ts's asciiSafe: not worth
   * embedding a Unicode TTF just for this report, and addresses/numbers are unaffected
   * since they never contain these characters. */
  private static readonly ASCII_MAP: Record<string, string> = {
    č: 'c', ć: 'c', š: 's', ž: 'z', đ: 'dj',
    Č: 'C', Ć: 'C', Š: 'S', Ž: 'Z', Đ: 'Dj',
  };

  private asciiSafe(value: string | null | undefined): string {
    return (value ?? '').replace(/[čćšžđČĆŠŽĐ]/g, (match) => DexSwapAnalysisComponent.ASCII_MAP[match] ?? match);
  }

  private static formatPdfAmount(value: number): string {
    return value.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 6 });
  }

  private formatTokenAmount(amount: number, token: string | null): string {
    return `${DexSwapAnalysisComponent.formatPdfAmount(amount)} ${token ?? '?'}`;
  }

  openSignatureDialog(): void {
    if (!this.result || this.isExportingPdf) {
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
    + 'kakve je aplikacija izračunala (heuristički) nad navedenom evidencijom.';

  /** The exact data the verification hash is computed over - kept to the figures a
   * reader could dispute (which events, which confidence, which amounts/tokens), sorted
   * so field order never affects the hash. */
  private reportContentPayload(): Record<string, unknown> {
    const result = this.result!;
    return {
      case_id: this.activeCase!.id,
      evidence: this.selectedEvidence ?? 'combined',
      address: result.address,
      max_gap_seconds: result.max_gap_seconds,
      events: [...result.events]
        .map((event) => ({
          user_address: event.user_address,
          dex_address: event.dex_address,
          confidence: event.confidence,
          input_token: event.input_token,
          input_amount: event.input_amount,
          input_timestamp: event.input_timestamp,
          output_token: event.output_token,
          output_amount: event.output_amount,
          output_timestamp: event.output_timestamp,
          match_basis: event.match_basis,
        }))
        .sort((a, b) => a.input_timestamp.localeCompare(b.input_timestamp) || a.dex_address.localeCompare(b.dex_address)),
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
      const declaration = DexSwapAnalysisComponent.SIGNATURE_DECLARATION;

      // Registered BEFORE the document is built: the verification code has to be printed
      // inside the very report it identifies.
      const registration = await firstValueFrom(
        this.api.registerReport({
          case_id: this.activeCase.id,
          case_name: this.activeCase.name ?? '',
          declaration,
          content: this.reportContentPayload(),
          summary: {
            total_events: this.result.total_events,
            detected_count: this.result.detected_count,
            potential_count: this.result.potential_count,
            address: this.result.address,
          },
        }),
      );

      this.buildDexSwapPdf({ signatureImage, declaration, registration });
      this.isSignatureDialogOpen = false;
    } catch {
      this.signatureError = 'Neuspešno generisanje PDF izveštaja.';
    } finally {
      this.isExportingPdf = false;
    }
  }

  private buildDexSwapPdf(signing: {
    signatureImage: string;
    declaration: string;
    registration: { verification_code: string; content_hash: string; registered_at: string; analyst: string };
  }): void {
    const result = this.result!;
    const caseSummary = this.activeCase!;
    const NAVY = DexSwapAnalysisComponent.PDF_NAVY;
    const ACCENT = DexSwapAnalysisComponent.PDF_ACCENT;
    const TEXT_GRAY = DexSwapAnalysisComponent.PDF_TEXT_GRAY;
    const TEXT_DARK = DexSwapAnalysisComponent.PDF_TEXT_DARK;
    const WHITE = DexSwapAnalysisComponent.PDF_WHITE;

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
    doc.text('Lusi v1.0 - Izvestaj DEX Swap analize', marginX, 11);
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
    kv('ANALIZIRANA ADRESA', result.address ?? 'n/a');
    kv('IZVEZAO', this.asciiSafe(this.auth.currentUser?.username ?? caseSummary.analyst));
    kv('EVIDENCIJA', this.selectedEvidence ? this.asciiSafe(this.selectedEvidence) : 'Sve transakcije (kombinovano)');
    kv('VREMENSKI PROZOR', `${result.max_gap_seconds}s (maksimalan razmak izmedju ulaznog i izlaznog kraka)`);
    kv('GENERISANO', new Date().toLocaleString());
    y += 2;

    // Short version of the disclaimer, placed BEFORE any result - the full version is
    // the methodology appendix at the end. Never omitted, never softened: this is a
    // heuristic, not proof, on every page this note could plausibly be missed from.
    const methodologyNoteLines = doc.splitTextToSize(
      this.asciiSafe(result.disclaimer)
        + ' Detaljno objasnjenje heuristike i njena ogranicenja nalaze se na kraju ovog izvestaja.',
      usableWidth - 8,
    );
    const noteBoxHeight = methodologyNoteLines.length * 4.2 + 7;
    doc.setFillColor(253, 250, 240);
    doc.setDrawColor(...DexSwapAnalysisComponent.PDF_AMBER);
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

    sectionTitle('Rezime analize');
    drawSummaryCards([
      ['Detektovano dogadjaja', result.total_events, ACCENT],
      ['Detected (isti tx hash)', result.detected_count, DexSwapAnalysisComponent.PDF_HIGH],
      ['Potential (adresa + vreme)', result.potential_count, DexSwapAnalysisComponent.PDF_MEDIUM],
      ['DEX kontrakata razmotreno', result.dex_nodes_considered.length, TEXT_GRAY],
    ]);

    const lowConfidenceEvents = result.events.filter((event) => this.confidenceLevel(event) === 'Low');
    const findingLines: string[] = [
      `${result.total_events} ${result.total_events === 1 ? 'DEX swap dogadjaj' : 'DEX swap dogadjaja'} detektovano za adresu ${result.address ?? 'n/a'}`,
    ];
    if (result.detected_count > 0) {
      findingLines.push(`${result.detected_count} potvrdjeno deljenim transaction hash-om (najjaci raspoloziv signal)`);
    }
    if (result.potential_count > 0) {
      findingLines.push(`${result.potential_count} uparenih samo po adresi i vremenskoj bliskosti`);
    }
    findingLines.push(
      result.data_completeness.currency_declared
        ? 'Evidencija deklarise valutu/token za bar jednu transakciju'
        : 'UPOZORENJE: evidencija ne deklarise valutu/token - input/output tokeni su nepoznati (?)',
    );
    if (lowConfidenceEvents.length > 0) {
      findingLines.push(`${lowConfidenceEvents.length} dogadjaja prepoznato samo preko generic kljucne reci - preporucena dodatna provera`);
    }

    sectionTitle('Kljucni nalazi');
    const findingsLineHeight = 6.5;
    const findingsBoxTop = y - 5;
    const findingsBoxHeight = findingLines.length * findingsLineHeight + 6;
    doc.setFillColor(240, 247, 253);
    doc.setDrawColor(...ACCENT);
    doc.setLineWidth(0.4);
    doc.roundedRect(marginX, findingsBoxTop, usableWidth, findingsBoxHeight, 2, 2, 'FD');

    const drawCheckmark = (checkX: number, baseline: number): void => {
      doc.setDrawColor(...ACCENT);
      doc.setLineWidth(0.6);
      doc.lines(
        [
          [1.1, 1.5],
          [2.0, -2.9],
        ],
        checkX,
        baseline - 1.4,
        [1, 1],
        'S',
        false,
      );
    };

    let findingY = findingsBoxTop + 7;
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(10);
    doc.setTextColor(...TEXT_DARK);
    for (const line of findingLines) {
      drawCheckmark(marginX + 4, findingY);
      doc.text(line, marginX + 11, findingY);
      findingY += findingsLineHeight;
    }
    y = findingsBoxTop + findingsBoxHeight + 9;

    sectionTitle('DEX kontrakti razmotreni');
    autoTable(doc, {
      startY: y,
      margin: { left: marginX, right: marginX },
      head: [['Adresa', 'Naziv', 'Osnov prepoznavanja']],
      body:
        result.dex_nodes_considered.length > 0
          ? result.dex_nodes_considered.map((node) => [node.address, this.asciiSafe(node.name), node.match_basis])
          : [['-', '(nijedan prepoznat u ovoj evidenciji)', '-']],
      styles: { fontSize: 8, cellPadding: 1.6, font: 'courier', textColor: TEXT_DARK },
      headStyles: { fillColor: NAVY, textColor: WHITE, font: 'helvetica', fontStyle: 'bold' },
      alternateRowStyles: { fillColor: [240, 245, 250] },
      columnStyles: { 1: { font: 'helvetica' }, 2: { font: 'helvetica' } },
    });
    y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 4;

    if (y > pageHeight - 40) {
      doc.addPage();
      y = 16;
    }
    sectionTitle('Detektovani swap dogadjaji');
    const sortedEvents = [...result.events].sort((a, b) => a.input_timestamp.localeCompare(b.input_timestamp));
    autoTable(doc, {
      startY: y,
      margin: { left: marginX, right: marginX },
      head: [['#', 'DEX', 'Input', 'Output', 'Pouzdanost', 'Vreme', 'Tx hash']],
      body: sortedEvents.map((event, index) => [
        String(index + 1),
        this.asciiSafe(event.dex_name),
        this.formatTokenAmount(event.input_amount, event.input_token),
        this.formatTokenAmount(event.output_amount, event.output_token),
        `${this.confidenceLevel(event)} (${event.confidence})`,
        new Date(event.input_timestamp).toLocaleString(),
        this.asciiSafe(event.input_transaction_hash ?? event.output_transaction_hash ?? 'n/a'),
      ]),
      styles: { fontSize: 7.5, cellPadding: 1.4, font: 'courier', textColor: TEXT_DARK },
      headStyles: { fillColor: NAVY, textColor: WHITE, font: 'helvetica', fontStyle: 'bold' },
      alternateRowStyles: { fillColor: [240, 245, 250] },
      columnStyles: { 0: { cellWidth: 7, font: 'helvetica' }, 4: { cellWidth: 26, font: 'helvetica' } },
      didParseCell: (data) => {
        // Color the "Pouzdanost" column's text by confidence tier, same mapping as the
        // on-screen badge and the Graph page's overlay - reading the level back out of
        // the cell text itself rather than threading a second column through, since
        // autoTable's row index already lines up with sortedEvents.
        if (data.section === 'body' && data.column.index === 4) {
          const level = sortedEvents[data.row.index] ? this.confidenceLevel(sortedEvents[data.row.index]) : 'Low';
          data.cell.styles.textColor = DexSwapAnalysisComponent.confidenceColor(level);
          data.cell.styles.fontStyle = 'bold';
        }
      },
    });
    y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 4;

    if (lowConfidenceEvents.length > 0) {
      if (y > pageHeight - 40) {
        doc.addPage();
        y = 16;
      }
      sectionTitle('Dogadjaji sa niskom pouzdanoscu DEX identifikacije');
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(8.5);
      doc.setTextColor(...TEXT_GRAY);
      const lowNote = doc.splitTextToSize(
        'Ovi dogadjaji su prepoznati kao DEX kontrakt samo preko generic kljucne reci (npr. "router", "dex", '
          + '"aggregator"), ne preko poznate adrese ili prepoznatljive marke - slabiji signal, preporucena dodatna '
          + 'rucna provera pre oslanjanja na nalaz.',
        usableWidth - 8,
      );
      const lowBoxHeight = lowNote.length * 4.2 + 7;
      doc.setFillColor(253, 250, 240);
      doc.setDrawColor(...DexSwapAnalysisComponent.PDF_AMBER);
      doc.setLineWidth(0.4);
      doc.roundedRect(marginX, y - 4, usableWidth, lowBoxHeight, 2, 2, 'FD');
      doc.setFont('helvetica', 'italic');
      doc.setFontSize(8.5);
      doc.setTextColor(...TEXT_DARK);
      doc.text(lowNote, marginX + 4, y + 1);
      y += lowBoxHeight + 4;
      doc.setFont('helvetica', 'normal');

      autoTable(doc, {
        startY: y,
        margin: { left: marginX, right: marginX },
        head: [['DEX adresa', 'Osnov prepoznavanja']],
        body: lowConfidenceEvents.map((event) => [event.dex_address, event.dex_match_basis]),
        styles: { fontSize: 8, cellPadding: 1.4, font: 'courier', textColor: TEXT_DARK },
        headStyles: { fillColor: DexSwapAnalysisComponent.PDF_AMBER, textColor: WHITE, font: 'helvetica', fontStyle: 'bold' },
        alternateRowStyles: { fillColor: [253, 246, 227] },
      });
      y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 4;
    }

    y += 4;
    if (y > pageHeight - 45) {
      doc.addPage();
      y = 16;
    }
    sectionTitle('Zakljucak analize');
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9.5);
    doc.setTextColor(...TEXT_DARK);
    const conclusionLines = doc.splitTextToSize(this.buildConclusionParagraph(), usableWidth);
    doc.text(conclusionLines, marginX, y);
    y += conclusionLines.length * 4.6;

    // --- Methodology appendix ---------------------------------------------------------
    const paragraph = (text: string, options?: { bold?: boolean; size?: number; gap?: number }): void => {
      const size = options?.size ?? 9;
      doc.setFont('helvetica', options?.bold ? 'bold' : 'normal');
      doc.setFontSize(size);
      doc.setTextColor(...TEXT_DARK);
      const lines: string[] = doc.splitTextToSize(text, usableWidth);
      const lineHeight = size * 0.48;
      if (y + lines.length * lineHeight > pageHeight - 18) {
        doc.addPage();
        y = 16;
      }
      doc.text(lines, marginX, y);
      y += lines.length * lineHeight + (options?.gap ?? 3);
    };

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

    y += 8;
    if (y > pageHeight - 60) {
      doc.addPage();
      y = 16;
    }
    sectionTitle('Metodologija i ogranicenja');

    paragraph('Heuristika prepoznavanja', { bold: true, size: 10, gap: 2 });
    paragraph(
      'Dogadjaj se prijavljuje kad adresa posalje sredstva na prepoznat/verovatan DEX kontrakt, a zatim u kratkom '
        + 'vremenskom periodu primi drugi token nazad od ISTOG kontrakta, na ISTU adresu. "Detected" znaci da oba kraka '
        + 'dele isti transaction hash - najjaci raspoloziv signal. "Potential" znaci uparivanje iskljucivo po adresi i '
        + 'vremenskoj bliskosti, bez potvrde da je rec o istoj on-chain transakciji.',
    );
    paragraph(
      'Par se NE prijavljuje kao swap kad je deklarisana valuta ista na oba kraka (npr. ETH -> ETH, obican bounce), '
        + 'kad povratni transfer ide na DRUGU adresu umesto nazad posiljaocu, ili kad razmak izmedju krakova premasuje '
        + 'podeseni vremenski prozor.',
      { gap: 5 },
    );

    paragraph('Sta ovo NIJE', { bold: true, size: 10, gap: 2 });
    paragraph(
      'Ovo je heuristika, ne dokaz. Ne postoji kriptografska potvrda da su dva transfera deo iste swap transakcije - '
        + 'cak i "Detected" pouzdanost se oslanja na to da evidencija ispravno belezi isti hash na oba kraka, ne na '
        + 'nezavisnu on-chain verifikaciju.',
      { gap: 5 },
    );

    paragraph('Ogranicenja podataka', { bold: true, size: 10, gap: 2 });
    bullet(
      'Valuta/token je opciona kolona u evidenciji i cesto nije popunjena - kad nedostaje, input/output token su '
        + 'nepoznati (?), a uparivanje se oslanja iskljucivo na adresu i vreme, bez potvrde da su u pitanju dva razlicita '
        + 'tokena.',
    );
    bullet(
      'Nema uvoza pravih ERC-20 Transfer dogadjaja (Etherscan tokentx) - on-chain uvoz u ovoj aplikaciji trenutno '
        + 'povlaci samo native transfere.',
    );
    bullet(
      'Nema logike "povezane adrese" - ako swap izlaz ide na drugu adresu koja bi se (npr. preko wallet clustering-a) '
        + 'mogla povezati sa istim vlasnikom, ovaj modul to ne uparuje.',
    );
    bullet(
      'Spisak poznatih DEX adresa je mali, rucno kuriran (nije zivi registar) - potpunost i tacnost svakog unosa nije '
        + 'garantovana.',
    );
    bullet(
      'Generic kljucna rec ("router", "dex", "aggregator") je slab signal za identifikaciju DEX kontrakta - oznaceno '
        + 'posebno u nalazu kad je to jedini osnov (vidi sekciju iznad, ako postoji).',
    );
    bullet('Adresa se trazi tacnim poklapanjem (case-sensitive), ista konvencija kao Pathfinding/Behavioral stranice.');

    // --- Signature and seal ---------------------------------------------------------
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
      'Verodostojnost se proverava u aplikaciji Lusi, unosom gornjeg kontrolnog broja. Ako se otisak sadrzaja poklapa '
        + 'sa zabelezenim, podaci u izvestaju su isti kao u trenutku izvoza. Ako se ne poklapa, izvestaj je izmenjen '
        + 'posle izvoza.',
      usableWidth,
    );
    doc.text(verifyLines, marginX, y);
    y += verifyLines.length * 4.6 + 4;

    doc.setFont('helvetica', 'italic');
    doc.setFontSize(8);
    doc.setTextColor(...TEXT_GRAY);
    const limitLines = doc.splitTextToSize(
      'Ogranicenje: potpis iznad je izjava analiticara, a ne kriptografski dokaz — on ostaje netaknut i ako neko '
        + 'izmeni dokument. Izmena se otkriva iskljucivo poredjenjem otiska sadrzaja. Provera potvrdjuje da se PODACI '
        + 'poklapaju sa registrovanim, ne da je PDF fajl bajt-po-bajt isti; za to bi bio potreban kriptografski potpis '
        + 'dokumenta (npr. PAdES), sto nije deo ove aplikacije.',
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

    doc.save(`${caseSummary.id}_dex_swap_report.pdf`);
  }

  /** Auto-composed plain-language wrap-up, same spirit as taint-analysis.component.ts's
   * buildConclusionParagraph - the same facts as the summary cards/key findings above,
   * read as prose so the report doesn't force a reader to reconstruct the story
   * themselves from raw tables. */
  private buildConclusionParagraph(): string {
    const result = this.result!;
    const sentences: string[] = [
      `Analiza je za adresu ${result.address ?? 'n/a'} identifikovala ${result.total_events} `
        + `${result.total_events === 1 ? 'potencijalni DEX swap dogadjaj' : 'potencijalnih DEX swap dogadjaja'}, `
        + `od cega ${result.detected_count} sa "Detected" i ${result.potential_count} sa "Potential" pouzdanoscu.`,
    ];

    if (!result.data_completeness.currency_declared) {
      sentences.push(
        'Evidencija ne deklarise valutu/token ni za jednu transakciju, pa identitet ulaznog i izlaznog tokena nije '
          + 'potvrdjen za nijedan dogadjaj - uparivanje se oslanja iskljucivo na adresu DEX kontrakta i vremensku '
          + 'bliskost.',
      );
    }

    const lowCount = result.events.filter((event) => this.confidenceLevel(event) === 'Low').length;
    if (lowCount > 0) {
      sentences.push(
        `${lowCount} ${lowCount === 1 ? 'dogadjaj je' : 'dogadjaja je'} oznaceno niskom pouzdanoscu DEX identifikacije `
          + '(prepoznato samo preko generic kljucne reci) i zahteva dodatnu rucnu proveru pre oslanjanja na nalaz.',
      );
    }

    sentences.push(
      'Nijedan nalaz u ovom izvestaju ne predstavlja kriptografski dokaz da se radi o swap transakciji - videti '
        + 'sekciju "Metodologija i ogranicenja".',
    );

    return sentences.join(' ');
  }
}
