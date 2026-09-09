import { CommonModule } from '@angular/common';
import { Component, Input, ViewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

import { jsPDF } from 'jspdf';
import autoTable from 'jspdf-autotable';

import { SignaturePadComponent } from '../../core/components/signature-pad/signature-pad.component';
import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { ApiService } from '../../core/services/api.service';
import { AuthService } from '../../core/services/auth.service';
import { AppLang, SettingsService } from '../../core/services/settings.service';
import { CaseReportContext } from '../../models/blockchain-forensics.models';

type Rgb = [number, number, number];

interface LoadedImage {
  dataUrl: string;
  width: number;
  height: number;
}

interface SigningRegistration {
  verification_code: string;
  content_hash: string;
  registered_at: string;
  analyst: string;
}

@Component({
  selector: 'app-report-export',
  standalone: true,
  imports: [CommonModule, FormsModule, SignaturePadComponent],
  templateUrl: './report-export.component.html',
  styleUrl: './report-export.component.scss',
})
export class ReportExportComponent {
  /** 'card' - the standalone panel (Dashboard). 'button' - just a compact trigger to drop
   * into a toolbar (the graph page's button row); the signing dialog is the same. */
  @Input() variant: 'card' | 'button' = 'card';

  protected isExporting = false;
  protected exportError: string | null = null;

  protected isSigningOpen = false;
  protected isLoadingContext = false;
  protected reportContext: CaseReportContext | null = null;

  /** The language the PDF is produced in - seeded from the app toggle when the dialog
   * opens, then confirmed (or changed) by the examiner before signing. Kept separate from
   * settings.lang() so a later UI toggle never rewrites a report mid-signing. */
  protected reportLang: AppLang = 'sr';
  protected examinerName = '';
  protected declarationAccepted = false;
  protected signatureError: string | null = null;

  @ViewChild(SignaturePadComponent) private signaturePad?: SignaturePadComponent;

  private static readonly NAVY: Rgb = [13, 24, 40];
  private static readonly ACCENT: Rgb = [43, 130, 191];
  private static readonly TEXT_GRAY: Rgb = [100, 112, 128];
  private static readonly TEXT_DARK: Rgb = [24, 28, 36];
  private static readonly WHITE: Rgb = [255, 255, 255];
  private static readonly AMBER: Rgb = [217, 119, 6];
  private static readonly RED: Rgb = [198, 40, 40];
  private static readonly GRAPH_BG: Rgb = [10, 20, 37];

  private static readonly ASCII_MAP: Record<string, string> = {
    č: 'c', ć: 'c', š: 's', ž: 'z', đ: 'dj', Č: 'C', Ć: 'C', Š: 'S', Ž: 'Z', Đ: 'Dj',
  };

  constructor(
    private readonly api: ApiService,
    private readonly state: AnalysisStateService,
    private readonly auth: AuthService,
    public readonly settings: SettingsService,
  ) {}

  /** Panel chrome - follows the live app language. */
  protected t(sr: string, en: string): string {
    return this.settings.lang() === 'sr' ? sr : en;
  }

  /** Report body - follows the language confirmed in the signing dialog. */
  private L(sr: string, en: string): string {
    return this.reportLang === 'sr' ? sr : en;
  }

  get activeCaseId(): string | null {
    return this.state.selectedCaseSnapshot?.id ?? null;
  }

  get canConfirm(): boolean {
    return (
      !this.isExporting &&
      !this.isLoadingContext &&
      this.reportContext !== null &&
      this.examinerName.trim().length > 0 &&
      (this.signaturePad?.hasStrokes ?? false) &&
      this.declarationAccepted
    );
  }

  openSigning(): void {
    const caseId = this.activeCaseId;
    if (!caseId || this.isExporting) {
      return;
    }

    this.reportLang = this.settings.lang();
    this.examinerName = this.auth.currentUser?.username ?? '';
    this.declarationAccepted = false;
    this.signatureError = null;
    this.exportError = null;
    this.reportContext = null;
    this.isSigningOpen = true;
    this.isLoadingContext = true;

    setTimeout(() => this.signaturePad?.clear());

    this.api.getCaseReportContext(caseId).subscribe({
      next: (context) => {
        this.reportContext = context;
        this.isLoadingContext = false;
      },
      error: () => {
        this.isLoadingContext = false;
        this.signatureError = this.t(
          'Neuspešno učitavanje podataka slučaja za izveštaj.',
          'Failed to load case data for the report.',
        );
      },
    });
  }

  closeSigning(): void {
    if (this.isExporting) {
      return;
    }
    this.isSigningOpen = false;
  }

  async confirmAndExport(): Promise<void> {
    if (!this.canConfirm || !this.reportContext || !this.activeCaseId) {
      return;
    }

    const context = this.reportContext;
    const caseId = this.activeCaseId;
    this.isExporting = true;
    this.signatureError = null;

    try {
      const signatureImage = this.signaturePad!.getDataUrl();
      const declaration = this.declarationText();

      const registration = await firstValueFrom(
        this.api.registerReport({
          case_id: caseId,
          case_name: context.case.name ?? '',
          declaration,
          content: this.reportContentPayload(context),
          summary: {
            rows: context.rows,
            nodes: context.nodes,
            edges: context.edges,
            blacklisted: context.summary.blacklisted_nodes ?? 0,
            high_risk: context.summary.high_risk_nodes ?? 0,
            clusters: context.summary.clusters ?? 0,
          },
          report_type: 'case_triage',
        }),
      );

      const graphImage = this.state.captureGraphImage();
      await this.buildPdf(context, { signatureImage, declaration, registration, graphImage });
      this.isSigningOpen = false;
    } catch {
      this.signatureError = this.t('Neuspešna izrada PDF izveštaja.', 'Failed to generate the PDF report.');
    } finally {
      this.isExporting = false;
    }
  }

  private declarationText(): string {
    return this.L(
      'Potvrđujem da sam pregledao prikupljene dokaze u okviru navedenog predmeta i da je ovaj izveštaj za trijažu ' +
        'izrađen radi odluke o daljoj analizi transakcija. Potpis iznad je moj.',
      'I confirm that I have reviewed the collected evidence for the stated case and that this triage report was ' +
        'produced to decide on further transaction analysis. The signature above is mine.',
    );
  }

  /** The stable figures a reader could dispute - altering any of them in the exported
   * document makes /verify-report fail. */
  private reportContentPayload(context: CaseReportContext): Record<string, unknown> {
    return {
      case_id: context.case.id,
      evidence_sha256: context.case.evidence.map((entry) => entry.sha256).sort(),
      rows: context.rows,
      nodes: context.nodes,
      edges: context.edges,
      blacklisted_nodes: context.summary.blacklisted_nodes ?? 0,
      high_risk_nodes: context.summary.high_risk_nodes ?? 0,
      clusters: context.summary.clusters ?? 0,
      generated_at: context.generated_at,
    };
  }

  // --- PDF ---------------------------------------------------------------------------

  private async buildPdf(
    context: CaseReportContext,
    signing: { signatureImage: string; declaration: string; registration: SigningRegistration; graphImage: string | null },
  ): Promise<void> {
    const { NAVY, ACCENT, TEXT_GRAY, TEXT_DARK, WHITE, AMBER, RED, GRAPH_BG } = ReportExportComponent;

    const doc = new jsPDF({ unit: 'mm', format: 'a4' });
    const fontOk = await this.registerFont(doc);
    const FF = fontOk ? 'DejaVu' : 'helvetica';
    const tx = (value: string): string => (fontOk ? value : this.asciiSafe(value));

    const pageWidth = doc.internal.pageSize.getWidth();
    const pageHeight = doc.internal.pageSize.getHeight();
    const marginX = 14;
    const usableWidth = pageWidth - marginX * 2;
    let y = 0;

    const setF = (style: 'normal' | 'bold' | 'italic', size: number): void => {
      // The embedded DejaVu family has no italic face registered, so fall back to normal
      // for it; the core helvetica fallback does have a real italic.
      const resolved = style === 'italic' && fontOk ? 'normal' : style;
      doc.setFont(FF, resolved);
      doc.setFontSize(size);
    };

    const ensureSpace = (needed: number): void => {
      if (y + needed > pageHeight - 16) {
        doc.addPage();
        y = 16;
      }
    };

    const sectionTitle = (title: string): void => {
      ensureSpace(16);
      y += 3;
      setF('bold', 12);
      doc.setTextColor(...NAVY);
      doc.text(tx(title), marginX, y);
      doc.setDrawColor(...ACCENT);
      doc.setLineWidth(0.6);
      doc.line(marginX, y + 2, pageWidth - marginX, y + 2);
      y += 8;
      doc.setTextColor(...TEXT_DARK);
    };

    const kv = (label: string, value: string): void => {
      ensureSpace(8);
      setF('bold', 9.5);
      doc.setTextColor(...TEXT_GRAY);
      doc.text(tx(label), marginX, y);
      setF('normal', 10);
      doc.setTextColor(...TEXT_DARK);
      const lines = doc.splitTextToSize(tx(value || 'n/a'), usableWidth - 44) as string[];
      doc.text(lines, marginX + 44, y);
      y += Math.max(6, lines.length * 5);
    };

    const drawSummaryCards = (cards: Array<[string, string | number, Rgb]>): void => {
      ensureSpace(24);
      const gap = 3;
      const colWidth = (usableWidth - gap * (cards.length - 1)) / cards.length;
      const y0 = y;
      let x = marginX;
      for (const [label, value, color] of cards) {
        doc.setFillColor(...color);
        doc.rect(x, y0, colWidth, 16, 'F');
        doc.setTextColor(...WHITE);
        setF('bold', 14);
        doc.text(String(value), x + colWidth / 2, y0 + 7, { align: 'center' });
        setF('normal', 6.8);
        doc.text(doc.splitTextToSize(tx(label), colWidth - 3) as string[], x + colWidth / 2, y0 + 12, { align: 'center' });
        x += colWidth + gap;
      }
      y = y0 + 16 + 6;
      doc.setTextColor(...TEXT_DARK);
    };

    const table = (head: string[], body: string[][], columnStyles?: Record<number, { cellWidth: number }>): void => {
      ensureSpace(20);
      autoTable(doc, {
        startY: y,
        margin: { left: marginX, right: marginX },
        head: [head.map(tx)],
        body: body.map((row) => row.map(tx)),
        styles: { font: FF, fontSize: 7.6, cellPadding: 1.6, textColor: TEXT_DARK, overflow: 'linebreak' },
        headStyles: { fillColor: NAVY, textColor: WHITE, fontStyle: 'bold' },
        alternateRowStyles: { fillColor: [240, 245, 250] },
        columnStyles,
      });
      y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 4;
    };

    // --- 1. Header banner: cat emblem beside the title, inside the navy bar -----------
    const barHeight = 26;
    doc.setFillColor(...NAVY);
    doc.rect(0, 0, pageWidth, barHeight, 'F');

    let titleX = marginX;
    try {
      const cat = await this.loadImage('assets/cat_pdf.png');
      const emblem = 20;
      const emblemW = (cat.width / cat.height) * emblem;
      doc.addImage(cat.dataUrl, 'PNG', marginX, (barHeight - emblem) / 2, emblemW, emblem);
      titleX = marginX + emblemW + 5;
    } catch {
      // no emblem file - the title just stays at the left margin
    }

    doc.setTextColor(...WHITE);
    setF('bold', 15);
    doc.text(tx(this.L('Lusi v1.0 — Izveštaj za trijažu predmeta', 'Lusi v1.0 — Case triage report')), titleX, 11);
    setF('normal', 10);
    const subtitleLines = doc.splitTextToSize(
      tx(`${this.L('Predmet', 'Case')}: ${context.case.name ?? ''}`),
      pageWidth - titleX - marginX,
    ) as string[];
    doc.text(subtitleLines[0], titleX, 19);
    doc.setTextColor(...TEXT_DARK);

    y = barHeight + 6;

    // --- 2. Triage note --------------------------------------------------------------
    const noteLines = doc.splitTextToSize(
      tx(
        this.L(
          'Ovaj dokument je procena prikupljenih dokaza pre analize. Služi da se, na osnovu obima grafa i početnih ' +
            'pokazatelja rizika, odluči da li je potrebna dublja analiza transakcija (taint, pathfinding, DEX). Ne sadrži ' +
            'zaključke analize niti detalje pojedinačnih čvorova.',
          'This document is a pre-analysis assessment of the collected evidence. It supports the decision — based on the ' +
            'size of the graph and initial risk indicators — of whether a deeper transaction analysis (taint, ' +
            'pathfinding, DEX) is needed. It contains no analysis conclusions and no per-node detail.',
        ),
      ),
      usableWidth - 8,
    ) as string[];
    const noteHeight = noteLines.length * 4.3 + 7;
    ensureSpace(noteHeight + 4);
    doc.setFillColor(253, 250, 240);
    doc.setDrawColor(...AMBER);
    doc.setLineWidth(0.4);
    doc.roundedRect(marginX, y - 4, usableWidth, noteHeight, 2, 2, 'FD');
    setF('italic', 8.6);
    doc.setTextColor(...TEXT_DARK);
    doc.text(noteLines, marginX + 4, y + 1);
    y += noteHeight + 3;

    // --- 3. Case details -----------------------------------------------------------
    sectionTitle(this.L('Podaci o predmetu', 'Case details'));
    kv(this.L('IDENTIFIKATOR', 'CASE ID'), context.case.id);
    kv(this.L('ANALITIČAR', 'ANALYST'), context.case.analyst ?? '');
    kv(this.L('STATUS', 'STATUS'), context.case.status ?? 'open');
    kv(this.L('OPIS', 'DESCRIPTION'), context.case.description ?? 'n/a');
    kv(this.L('KREIRAN', 'CREATED AT'), context.case.created_at ?? '');
    kv(this.L('IZMENJEN', 'UPDATED AT'), context.case.updated_at ?? '');
    kv(this.L('GENERISANO', 'GENERATED AT'), context.generated_at ?? '');

    // --- 4. Analysis summary -----------------------------------------------------------
    const blacklisted = context.summary.blacklisted_nodes ?? 0;
    const highRisk = context.summary.high_risk_nodes ?? 0;
    const clusters = context.summary.clusters ?? 0;
    sectionTitle(this.L('Rezime analize', 'Analysis summary'));
    drawSummaryCards([
      [this.L('Redova podataka', 'Data rows'), context.rows, ACCENT],
      [this.L('Čvorova', 'Nodes'), context.nodes, ACCENT],
      [this.L('Veza', 'Edges'), context.edges, ACCENT],
      [this.L('Na crnoj listi', 'Blacklisted'), blacklisted, blacklisted ? RED : TEXT_GRAY],
      [this.L('Visok rizik', 'High-risk'), highRisk, highRisk ? AMBER : TEXT_GRAY],
      [this.L('Klastera', 'Clusters'), clusters, ACCENT],
    ]);

    // --- 5. Transaction graph excerpt ------------------------------------------------
    sectionTitle(this.L('Isečak grafa transakcija', 'Transaction graph excerpt'));
    if (signing.graphImage) {
      try {
        const size = await this.imageSize(signing.graphImage);
        const maxHeight = 128;
        let renderWidth = usableWidth;
        let renderHeight = (size.height / size.width) * renderWidth;
        if (renderHeight > maxHeight) {
          renderHeight = maxHeight;
          renderWidth = (size.width / size.height) * renderHeight;
        }
        ensureSpace(renderHeight + 10);
        const imgX = marginX + (usableWidth - renderWidth) / 2;
        doc.setFillColor(...GRAPH_BG);
        doc.rect(imgX, y, renderWidth, renderHeight, 'F');
        doc.addImage(signing.graphImage, 'PNG', imgX, y, renderWidth, renderHeight);
        y += renderHeight + 4;
      } catch {
        // fall through to the "not available" line below
        signing.graphImage = null;
      }
    }
    if (!signing.graphImage) {
      ensureSpace(10);
      setF('italic', 9);
      doc.setTextColor(...TEXT_GRAY);
      doc.text(
        tx(this.L('Graf nije bio učitan u trenutku izrade izveštaja.', 'The graph was not loaded when this report was produced.')),
        marginX,
        y,
      );
      y += 6;
      doc.setTextColor(...TEXT_DARK);
    }
    setF('normal', 8.6);
    doc.setTextColor(...TEXT_GRAY);
    doc.text(
      tx(
        `${context.nodes} ${this.L('čvorova', 'nodes')}  ·  ${context.edges} ${this.L('veza', 'edges')}  ·  ` +
          `${blacklisted} ${this.L('na crnoj listi', 'blacklisted')}  ·  ${highRisk} ${this.L('visok rizik', 'high-risk')}`,
      ),
      marginX,
      y,
    );
    y += 5;
    doc.text(
      doc.splitTextToSize(
        tx(
          this.L(
            'Slika prikazuje graf onako kako je bio prikazan na ekranu u trenutku izvoza.',
            'The image shows the graph as it was displayed on screen at the time of export.',
          ),
        ),
        usableWidth,
      ) as string[],
      marginX,
      y,
    );
    y += 8;
    doc.setTextColor(...TEXT_DARK);

    // --- 6. Evidence locker -------------------------------------------------------
    sectionTitle(this.L('Sef dokaza', 'Evidence locker'));
    table(
      [
        this.L('Naziv fajla', 'File name'),
        'SHA-256',
        this.L('Veličina', 'Size'),
        this.L('Analitičar', 'Analyst'),
        this.L('Uvezeno', 'Imported at'),
      ],
      context.case.evidence.map((entry) => [
        entry.file_name ?? '',
        entry.sha256 ?? '',
        this.formatBytes(entry.size_bytes),
        entry.analyst ?? '',
        entry.imported_at ?? '',
      ]),
      { 0: { cellWidth: 52 }, 1: { cellWidth: 46 }, 2: { cellWidth: 18 }, 3: { cellWidth: 16 }, 4: { cellWidth: 50 } },
    );
    if (context.case.evidence.length === 0) {
      setF('normal', 9);
      doc.setTextColor(...TEXT_GRAY);
      doc.text(tx(this.L('Za ovaj predmet još nema evidentiranih dokaza.', 'No evidence has been recorded for this case yet.')), marginX, y);
      y += 6;
      doc.setTextColor(...TEXT_DARK);
    }

    // --- 7. Evidence contribution breakdown -------------------------------------------
    sectionTitle(this.L('Doprinos evidencije', 'Evidence contribution breakdown'));
    table(
      [
        this.L('Naziv fajla', 'File name'),
        this.L('Redova', 'Rows'),
        this.L('Ukupan iznos', 'Total amount'),
        this.L('Adresa', 'Addresses'),
        this.L('Visok rizik', 'High-risk'),
        this.L('Crna lista', 'Blacklisted'),
      ],
      context.evidence_contributions.map((item) => [
        item.file_name ?? '',
        String(item.rows ?? 0),
        this.formatAmount(item.total_amount ?? 0),
        String(item.addresses_touched ?? 0),
        String(item.high_risk_addresses ?? 0),
        String(item.blacklisted_addresses ?? 0),
      ]),
      { 0: { cellWidth: 60 }, 1: { cellWidth: 16 }, 2: { cellWidth: 32 }, 3: { cellWidth: 22 }, 4: { cellWidth: 26 }, 5: { cellWidth: 26 } },
    );

    // --- 8. Audit log -----------------------------------------------------------
    sectionTitle(this.L('Dnevnik revizije', 'Audit log'));
    table(
      [this.L('Vreme', 'Timestamp'), this.L('Radnja', 'Action'), this.L('Fajl', 'File'), this.L('Korisnik', 'User')],
      context.audit_entries.map((entry) => [
        entry.timestamp ?? '',
        entry.action ?? '',
        entry.file_name ?? '',
        entry.user ?? '',
      ]),
      { 0: { cellWidth: 40 }, 1: { cellWidth: 28 }, 3: { cellWidth: 22 } },
    );

    // --- 9. Examiner sign-off ------------------------------------------------------
    doc.addPage();
    y = 16;
    sectionTitle(this.L('Potpis i overa', 'Signature and certification'));

    setF('normal', 9);
    doc.setTextColor(...TEXT_DARK);
    const declLines = doc.splitTextToSize(tx(signing.declaration), usableWidth) as string[];
    doc.text(declLines, marginX, y);
    y += declLines.length * 4.6 + 6;

    const sigBoxWidth = usableWidth * 0.52;
    const sigBoxHeight = 34;
    doc.setDrawColor(...TEXT_GRAY);
    doc.setLineWidth(0.3);
    doc.rect(marginX, y, sigBoxWidth, sigBoxHeight);
    doc.addImage(signing.signatureImage, 'PNG', marginX + 2, y + 2, sigBoxWidth - 4, sigBoxHeight - 4);

    setF('normal', 8);
    doc.setTextColor(...TEXT_GRAY);
    doc.text(tx(this.L('Potpis forenzičara', 'Examiner signature')), marginX, y + sigBoxHeight + 4);
    setF('bold', 9.5);
    doc.setTextColor(...TEXT_DARK);
    doc.text(tx(this.examinerName.trim() || signing.registration.analyst), marginX, y + sigBoxHeight + 9);

    try {
      const seal = await this.loadImage('assets/seal.png');
      const sealHeight = 32;
      const sealWidth = (seal.width / seal.height) * sealHeight;
      const sealX = marginX + sigBoxWidth + (usableWidth - sigBoxWidth - sealWidth) / 2;
      doc.addImage(seal.dataUrl, 'PNG', sealX, y + (sigBoxHeight - sealHeight) / 2, sealWidth, sealHeight);
    } catch {
      // seal is decorative - a missing file must not fail the export
    }

    y += sigBoxHeight + 16;

    sectionTitle(this.L('Provera verodostojnosti', 'Authenticity check'));
    setF('bold', 9);
    doc.setTextColor(...TEXT_GRAY);
    doc.text(tx(this.L('KONTROLNI BROJ', 'VERIFICATION CODE')), marginX, y);
    doc.setFont('courier', 'bold');
    doc.setFontSize(13);
    doc.setTextColor(...NAVY);
    doc.text(signing.registration.verification_code, marginX + 48, y + 0.5);
    y += 8;

    setF('bold', 9);
    doc.setTextColor(...TEXT_GRAY);
    doc.text(tx(this.L('OTISAK SADRŽAJA', 'CONTENT HASH')), marginX, y);
    doc.setFont('courier', 'normal');
    doc.setFontSize(7.5);
    doc.setTextColor(...TEXT_DARK);
    doc.text(doc.splitTextToSize(signing.registration.content_hash, usableWidth - 48) as string[], marginX + 48, y);
    y += 10;

    setF('normal', 9);
    doc.setTextColor(...TEXT_DARK);
    const verifyLines = doc.splitTextToSize(
      tx(
        this.L(
          'Verodostojnost se proverava u aplikaciji Lusi (Provera izveštaja), unosom gornjeg kontrolnog broja. Ako se ' +
            'otisak sadržaja poklapa sa zabeleženim, podaci u izveštaju su isti kao u trenutku izvoza.',
          'Authenticity is checked in the Lusi app (Verify report) by entering the verification code above. If the ' +
            'content hash matches the recorded one, the report data is identical to what it was at export time.',
        ),
      ),
      usableWidth,
    ) as string[];
    doc.text(verifyLines, marginX, y);
    y += verifyLines.length * 4.6 + 4;

    setF('italic', 8);
    doc.setTextColor(...TEXT_GRAY);
    const limitLines = doc.splitTextToSize(
      tx(
        this.L(
          'Ograničenje: potpis iznad je izjava forenzičara, a ne kriptografski dokaz — ostaje netaknut i ako neko ' +
            'izmeni dokument. Provera potvrđuje da se PODACI poklapaju sa registrovanim, ne da je PDF bajt-po-bajt isti.',
          'Limitation: the signature above is the examiner’s declaration, not a cryptographic proof — it stays ' +
            'intact even if the document is altered. The check confirms the DATA matches what was registered, not that ' +
            'the PDF is byte-for-byte identical.',
        ),
      ),
      usableWidth,
    ) as string[];
    doc.text(limitLines, marginX, y);

    // --- Footer on every page --------------------------------------------------------
    const pageCount = doc.getNumberOfPages();
    for (let page = 1; page <= pageCount; page++) {
      doc.setPage(page);
      setF('normal', 8);
      doc.setTextColor(...TEXT_GRAY);
      doc.text(
        tx(`Lusi v1.0 — ${this.L('forenzički izvoz', 'forensic export')} | ${this.L('Strana', 'Page')} ${page}/${pageCount}`),
        pageWidth / 2,
        pageHeight - 8,
        { align: 'center' },
      );
    }

    doc.save(`${context.case.id}_${this.reportLang === 'sr' ? 'izvestaj_trijaza' : 'triage_report'}.pdf`);
  }

  // --- helpers ---------------------------------------------------------------------

  private asciiSafe(value: string | null | undefined): string {
    return (value ?? '').replace(/[čćšžđČĆŠŽĐ]/g, (match) => ReportExportComponent.ASCII_MAP[match] ?? match);
  }

  private formatBytes(value: number | null | undefined): string {
    let size = Number(value);
    if (!Number.isFinite(size)) {
      return 'n/a';
    }
    for (const unit of ['B', 'KB', 'MB', 'GB']) {
      if (size < 1024 || unit === 'GB') {
        return unit === 'B' ? `${size.toFixed(0)} ${unit}` : `${size.toFixed(2)} ${unit}`;
      }
      size /= 1024;
    }
    return `${size.toFixed(2)} GB`;
  }

  private formatAmount(value: number): string {
    return Number(value || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 6 });
  }

  private imageSize(dataUrl: string): Promise<{ width: number; height: number }> {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
      image.onerror = () => reject(new Error('image load failed'));
      image.src = dataUrl;
    });
  }

  private async loadImage(path: string): Promise<LoadedImage> {
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
    const size = await this.imageSize(dataUrl);
    return { dataUrl, width: size.width, height: size.height };
  }

  private async registerFont(doc: jsPDF): Promise<boolean> {
    try {
      const [regular, bold] = await Promise.all([
        this.fetchBase64('assets/fonts/DejaVuSans.ttf'),
        this.fetchBase64('assets/fonts/DejaVuSans-Bold.ttf'),
      ]);
      doc.addFileToVFS('DejaVuSans.ttf', regular);
      doc.addFont('DejaVuSans.ttf', 'DejaVu', 'normal');
      doc.addFileToVFS('DejaVuSans-Bold.ttf', bold);
      doc.addFont('DejaVuSans-Bold.ttf', 'DejaVu', 'bold');
      return true;
    } catch {
      return false;
    }
  }

  private async fetchBase64(path: string): Promise<string> {
    const response = await fetch(path);
    if (!response.ok) {
      throw new Error(`font not found: ${path}`);
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    let binary = '';
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }
}
