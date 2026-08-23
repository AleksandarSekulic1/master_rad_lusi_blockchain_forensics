import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, OnInit, Output, ViewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { SignaturePadComponent } from '../../core/components/signature-pad/signature-pad.component';
import { ApiService } from '../../core/services/api.service';
import { AuthService } from '../../core/services/auth.service';
import { CustodyFieldSuggestions, TransactionCustodyEntry } from '../../models/blockchain-forensics.models';

const EMPTY_SUGGESTIONS: CustodyFieldSuggestions = {
  identifikator_predmeta: [],
  identifikator_dokaznog_materijala: [],
  proizvodjac: [],
  model: [],
  serijski_broj: [],
};

/** Gate in front of every analysis run: running a taint analysis is treated as
 * re-accessing every transaction it processes, so the reason and the analyst's signature
 * have to be entered again each time (see LANAC-DOKAZA.md). The parent is responsible for
 * mounting this behind an `*ngIf` (like the existing `.signature-overlay` in taint
 * analysis) so a fresh component instance - and therefore a blank form - is guaranteed on
 * every open, instead of state leaking over from a previous access.
 */
@Component({
  selector: 'app-custody-access-dialog',
  standalone: true,
  imports: [CommonModule, FormsModule, SignaturePadComponent],
  templateUrl: './custody-access-dialog.component.html',
  styleUrl: './custody-access-dialog.component.scss',
})
export class CustodyAccessDialogComponent implements OnInit {
  @Input({ required: true }) caseId!: string;
  @Input() caseName: string | null = null;
  @Input() evidenceFileName: string | null = null;
  @Input() isSubmitting = false;
  @Input() submitError: string | null = null;

  @Output() readonly confirmed = new EventEmitter<TransactionCustodyEntry>();
  @Output() readonly cancelled = new EventEmitter<void>();

  @ViewChild(SignaturePadComponent) private signaturePad?: SignaturePadComponent;

  protected suggestions: CustodyFieldSuggestions = EMPTY_SUGGESTIONS;
  protected declarationAccepted = false;

  protected identifikatorPredmeta = '';
  protected identifikatorDokaznogMaterijala = '';
  protected proizvodjac = 'N/A';
  protected model = 'N/A';
  protected serijskiBroj = 'N/A';
  protected imePrezime = '';
  protected opisRadnje = '';

  constructor(
    private readonly api: ApiService,
    private readonly auth: AuthService,
  ) {}

  ngOnInit(): void {
    this.identifikatorPredmeta = this.caseName || this.caseId;
    this.identifikatorDokaznogMaterijala = this.evidenceFileName || 'sva evidencija (kombinovano)';
    // Convenience starting point only - the analyst's real name still has to be typed/
    // confirmed by hand, a login handle is not necessarily their legal name.
    this.imePrezime = this.auth.currentUser?.username || '';

    this.api.getCustodySuggestions(this.caseId).subscribe({
      next: (response) => (this.suggestions = response),
      error: () => (this.suggestions = EMPTY_SUGGESTIONS),
    });
  }

  get canSubmit(): boolean {
    return (
      this.imePrezime.trim().length > 0 &&
      this.opisRadnje.trim().length > 0 &&
      (this.signaturePad?.hasStrokes ?? false) &&
      this.declarationAccepted &&
      !this.isSubmitting
    );
  }

  submit(): void {
    if (!this.canSubmit) {
      return;
    }
    this.confirmed.emit({
      ime_prezime: this.imePrezime.trim(),
      opis_radnje: this.opisRadnje.trim(),
      signature_image: this.signaturePad!.getDataUrl(),
      identifikator_predmeta: this.identifikatorPredmeta.trim() || null,
      identifikator_dokaznog_materijala: this.identifikatorDokaznogMaterijala.trim() || null,
      proizvodjac: this.proizvodjac.trim() || 'N/A',
      model: this.model.trim() || 'N/A',
      serijski_broj: this.serijskiBroj.trim() || 'N/A',
    });
  }

  cancel(): void {
    this.cancelled.emit();
  }
}
