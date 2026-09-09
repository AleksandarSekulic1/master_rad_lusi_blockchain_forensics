import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, OnInit, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../core/services/api.service';
import { SettingsService } from '../../core/services/settings.service';
import { InvestigatorLinkConfidence, InvestigatorNote } from '../../models/blockchain-forensics.models';

/** Compact modal launched from the graph node-details panel (CASE-MANAGEMENT-IMPLEMENTATION.md
 * §16). Two tabs: investigator notes for the selected address (list + add + edit + delete),
 * and "new investigator link" from the selected address. It is deliberately NOT a new page
 * and NOT a second inspector: it appears only when the investigator explicitly opens it,
 * so detailed notes stay out of sight until then. It does not repeat any of the node's
 * on-chain information - only the address, as context.
 *
 * Same overlay pattern as CustodyAccessDialogComponent (fixed backdrop, click-outside to
 * close). The parent mounts it behind an *ngIf so a fresh instance is guaranteed on each
 * open.
 */
@Component({
  selector: 'app-investigator-node-dialog',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './investigator-node-dialog.component.html',
  styleUrl: './investigator-node-dialog.component.scss',
})
export class InvestigatorNodeDialogComponent implements OnInit {
  @Input({ required: true }) investigationId!: string;
  @Input({ required: true }) address!: string;
  @Input() investigationName: string | null = null;
  @Input() mode: 'notes' | 'link' = 'notes';

  /** Fired after any note create/edit/delete, so the parent can refresh its note count. */
  @Output() readonly notesChanged = new EventEmitter<void>();
  /** Fired after a link is created, so the parent can refresh the graph link overlay. */
  @Output() readonly linkCreated = new EventEmitter<void>();
  @Output() readonly closed = new EventEmitter<void>();

  protected activeTab: 'notes' | 'link' = 'notes';

  // --- Notes ---
  protected notes: InvestigatorNote[] = [];
  protected isLoadingNotes = false;
  protected notesError: string | null = null;
  protected newNoteText = '';
  protected isAddingNote = false;
  protected editingNoteId: string | null = null;
  protected editNoteText = '';
  protected isSavingNote = false;

  // --- New link ---
  protected linkTarget = '';
  protected linkReason = '';
  protected linkEvidence = '';
  protected linkConfidence: InvestigatorLinkConfidence = 'Medium';
  protected readonly confidenceOptions: InvestigatorLinkConfidence[] = ['Low', 'Medium', 'High'];
  protected isCreatingLink = false;
  protected linkError: string | null = null;

  constructor(
    private readonly api: ApiService,
    public readonly settings: SettingsService,
  ) {}

  protected t(sr: string, en: string): string {
    return this.settings.lang() === 'sr' ? sr : en;
  }

  ngOnInit(): void {
    this.activeTab = this.mode;
    this.loadNotes();
  }

  protected loadNotes(): void {
    this.isLoadingNotes = true;
    this.notesError = null;
    this.api.getInvestigatorNotes(this.investigationId, this.address).subscribe({
      next: (res) => {
        this.notes = res.notes;
        this.isLoadingNotes = false;
      },
      error: () => {
        this.notesError = this.t('Neuspešno učitavanje beleški.', 'Failed to load notes.');
        this.isLoadingNotes = false;
      },
    });
  }

  protected get canAddNote(): boolean {
    return this.newNoteText.trim().length > 0 && !this.isAddingNote;
  }

  protected addNote(): void {
    if (!this.canAddNote) {
      return;
    }
    this.isAddingNote = true;
    this.notesError = null;
    this.api
      .addInvestigatorNote(this.investigationId, { address: this.address, text: this.newNoteText.trim() })
      .subscribe({
        next: () => {
          this.newNoteText = '';
          this.isAddingNote = false;
          this.loadNotes();
          this.notesChanged.emit();
        },
        error: () => {
          this.notesError = this.t('Neuspešno dodavanje beleške.', 'Failed to add the note.');
          this.isAddingNote = false;
        },
      });
  }

  protected startEdit(note: InvestigatorNote): void {
    this.editingNoteId = note.id;
    this.editNoteText = note.text;
  }

  protected cancelEdit(): void {
    this.editingNoteId = null;
    this.editNoteText = '';
  }

  protected saveEdit(note: InvestigatorNote): void {
    const text = this.editNoteText.trim();
    if (!text || this.isSavingNote) {
      return;
    }
    this.isSavingNote = true;
    this.api.updateInvestigatorNote(this.investigationId, note.id, { text }).subscribe({
      next: () => {
        this.isSavingNote = false;
        this.cancelEdit();
        this.loadNotes();
        this.notesChanged.emit();
      },
      error: () => {
        this.isSavingNote = false;
        this.notesError = this.t('Neuspešna izmena beleške.', 'Failed to edit the note.');
      },
    });
  }

  protected deleteNote(note: InvestigatorNote): void {
    if (!window.confirm(this.t('Obrisati ovu belešku?', 'Delete this note?'))) {
      return;
    }
    this.api.deleteInvestigatorNote(this.investigationId, note.id).subscribe({
      next: () => {
        this.loadNotes();
        this.notesChanged.emit();
      },
      error: () => (this.notesError = this.t('Neuspešno brisanje beleške.', 'Failed to delete the note.')),
    });
  }

  protected get canCreateLink(): boolean {
    const target = this.linkTarget.trim();
    return (
      target.length > 0 &&
      target !== this.address &&
      this.linkReason.trim().length > 0 &&
      this.linkEvidence.trim().length > 0 &&
      !this.isCreatingLink
    );
  }

  protected createLink(): void {
    if (!this.canCreateLink) {
      return;
    }
    this.isCreatingLink = true;
    this.linkError = null;
    this.api
      .addInvestigatorLink(this.investigationId, {
        source_address: this.address,
        target_address: this.linkTarget.trim(),
        reason: this.linkReason.trim(),
        evidence: this.linkEvidence.trim(),
        confidence: this.linkConfidence,
      })
      .subscribe({
        next: () => {
          this.isCreatingLink = false;
          this.linkCreated.emit();
        },
        error: () => {
          this.isCreatingLink = false;
          this.linkError = this.t(
            'Neuspešno kreiranje veze. Proverite da su adrese različite i da su svi podaci uneti.',
            'Failed to create the link. Check that the addresses are different and all fields are filled in.',
          );
        },
      });
  }

  protected close(): void {
    this.closed.emit();
  }
}
