import { CommonModule } from '@angular/common';
import { Component, DestroyRef, OnInit } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { Subject, debounceTime, distinctUntilChanged } from 'rxjs';

import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { ApiService } from '../../core/services/api.service';
import { SettingsService } from '../../core/services/settings.service';
import { Case, CaseSummary } from '../../models/blockchain-forensics.models';

@Component({
  selector: 'app-cases',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './cases.component.html',
  styleUrl: './cases.component.scss',
})
export class CasesComponent implements OnInit {
  protected cases: CaseSummary[] = [];
  protected selectedCase: Case | null = null;
  protected newCaseName = '';
  protected newCaseDescription = '';
  protected isLoading = false;
  protected isCreating = false;
  /** Held as a thunk so the message re-renders in the active language after a toggle. */
  protected statusMessage: () => string = () => '';

  protected readonly pageSize = 5;
  protected currentPage = 1;

  protected searchQuery = '';
  private readonly searchChanges = new Subject<string>();

  constructor(
    private readonly api: ApiService,
    protected readonly state: AnalysisStateService,
    public readonly settings: SettingsService,
    destroyRef: DestroyRef,
  ) {
    this.statusMessage = () => this.t('Učitavanje slučajeva...', 'Loading cases...');

    this.searchChanges
      .pipe(debounceTime(250), distinctUntilChanged(), takeUntilDestroyed(destroyRef))
      .subscribe((term) => this.fetchCases(term));
  }

  protected t(sr: string, en: string): string {
    return this.settings.lang() === 'sr' ? sr : en;
  }

  ngOnInit(): void {
    this.loadCases();
  }

  onSearchChange(term: string): void {
    this.searchChanges.next(term);
  }

  clearSearch(): void {
    if (!this.searchQuery) {
      return;
    }
    this.searchQuery = '';
    this.fetchCases('');
  }

  get totalPages(): number {
    return Math.max(1, Math.ceil(this.cases.length / this.pageSize));
  }

  get pagedCases(): CaseSummary[] {
    const start = (this.currentPage - 1) * this.pageSize;
    return this.cases.slice(start, start + this.pageSize);
  }

  get pageNumbers(): number[] {
    return Array.from({ length: this.totalPages }, (_, index) => index + 1);
  }

  get rangeStart(): number {
    return this.cases.length === 0 ? 0 : (this.currentPage - 1) * this.pageSize + 1;
  }

  get rangeEnd(): number {
    return Math.min(this.currentPage * this.pageSize, this.cases.length);
  }

  goToPage(page: number): void {
    this.currentPage = Math.min(Math.max(1, page), this.totalPages);
  }

  formatDate(iso: string | null | undefined): string {
    if (!iso) {
      return 'n/a';
    }
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) {
      return String(iso);
    }
    const stamp = date.toISOString();
    return `${stamp.slice(0, 10)} ${stamp.slice(11, 16)} UTC`;
  }

  /** Re-fetches with the current search term (used by the Refresh button and after every
   * mutation). The debounced search box goes through fetchCases() directly. */
  loadCases(): void {
    this.fetchCases(this.searchQuery);
  }

  private fetchCases(term: string): void {
    const query = term.trim();
    this.isLoading = true;
    this.api.listCases(query).subscribe({
      next: (response) => {
        this.cases = response.cases;
        this.currentPage = Math.min(this.currentPage, this.totalPages);
        this.isLoading = false;
        this.statusMessage = () => {
          const count = this.cases.length;
          if (count === 0) {
            return query
              ? `${this.t('Nema rezultata za', 'No results for')} “${query}”.`
              : this.t('Još uvek nema slučajeva. Kreirajte jedan da biste počeli.', 'No cases yet. Create one to get started.');
          }
          return query
            ? `${count} ${this.t('rezultat(a) za', 'result(s) for')} “${query}”.`
            : `${count} ${this.t('slučaj(eva) u evidenciji.', 'case(s) on record.')}`;
        };

        const selectedId = this.state.selectedCaseSnapshot?.id;
        if (selectedId) {
          const stillExists = this.cases.find((entry) => entry.id === selectedId);
          if (stillExists) {
            this.selectCase(stillExists);
          }
        }
      },
      error: () => {
        this.isLoading = false;
        this.statusMessage = () => this.t('Neuspešno učitavanje slučajeva.', 'Failed to load cases.');
      },
    });
  }

  createCase(): void {
    const name = this.newCaseName.trim();
    if (!name) {
      this.statusMessage = () => this.t('Naziv slučaja je obavezan.', 'A case name is required.');
      return;
    }

    this.isCreating = true;
    this.api
      .createCase({
        name,
        description: this.newCaseDescription.trim() || null,
      })
      .subscribe({
        next: (createdCase) => {
          this.isCreating = false;
          this.newCaseName = '';
          this.newCaseDescription = '';
          this.statusMessage = () => `${this.t('Slučaj', 'Case')} "${createdCase.name}" ${this.t('je kreiran.', 'was created.')}`;
          // clear any active filter so the just-created case is actually visible in the list
          this.searchQuery = '';
          this.currentPage = 1;
          this.loadCases();
          this.selectCase(createdCase);
        },
        error: () => {
          this.isCreating = false;
          this.statusMessage = () => this.t('Neuspešno kreiranje slučaja.', 'Failed to create the case.');
        },
      });
  }

  selectCase(caseSummary: CaseSummary): void {
    this.state.setSelectedCase(caseSummary);
    this.api.getCase(caseSummary.id).subscribe({
      next: (caseDetail) => {
        this.selectedCase = caseDetail;
      },
      error: () => {
        this.statusMessage = () =>
          `${this.t('Neuspešno učitavanje depoa dokaza za slučaj', 'Failed to load the evidence locker for case')} ${caseSummary.id}.`;
      },
    });
  }

  isSelected(caseSummary: CaseSummary): boolean {
    return this.state.selectedCaseSnapshot?.id === caseSummary.id;
  }

  toggleCaseStatus(caseSummary: CaseSummary): void {
    const nextStatus = caseSummary.status === 'open' ? 'closed' : 'open';
    this.api.setCaseStatus(caseSummary.id, nextStatus).subscribe({
      next: () => {
        this.statusMessage = () =>
          `${this.t('Slučaj', 'Case')} "${caseSummary.name}" ${this.t('je sada', 'is now')} ` +
          `${nextStatus === 'open' ? this.t('otvoren', 'open') : this.t('zatvoren', 'closed')}.`;
        if (nextStatus === 'closed' && this.state.selectedCaseSnapshot?.id === caseSummary.id) {
          this.state.setSelectedCase(null);
        }
        this.loadCases();
      },
      error: () => {
        this.statusMessage = () =>
          `${this.t('Neuspešna promena statusa za slučaj', 'Failed to change status for case')} "${caseSummary.name}".`;
      },
    });
  }

  removeCase(caseSummary: CaseSummary): void {
    const confirmed = window.confirm(
      this.t(
        `Da li si siguran da želiš da obrišeš slučaj "${caseSummary.name}"? Ova akcija se ne može opozvati.`,
        `Are you sure you want to delete case "${caseSummary.name}"? This action cannot be undone.`,
      ),
    );
    if (!confirmed) {
      return;
    }

    this.api.deleteCase(caseSummary.id).subscribe({
      next: () => {
        this.statusMessage = () => `${this.t('Slučaj', 'Case')} "${caseSummary.name}" ${this.t('je obrisan.', 'was deleted.')}`;
        if (this.state.selectedCaseSnapshot?.id === caseSummary.id) {
          this.state.setSelectedCase(null);
          this.selectedCase = null;
        }
        this.loadCases();
      },
      error: () => {
        this.statusMessage = () => `${this.t('Neuspešno brisanje slučaja', 'Failed to delete case')} "${caseSummary.name}".`;
      },
    });
  }

  formatBytes(bytes: number): string {
    if (!bytes) {
      return '0 B';
    }
    const units = ['B', 'KB', 'MB', 'GB'];
    const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const value = bytes / Math.pow(1024, exponent);
    return `${value.toFixed(exponent === 0 ? 0 : 2)} ${units[exponent]}`;
  }
}
