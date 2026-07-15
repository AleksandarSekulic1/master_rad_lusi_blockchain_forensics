import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { AnalysisStateService } from '../../core/services/analysis-state.service';
import { ApiService } from '../../core/services/api.service';
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
  protected statusMessage = 'Loading cases...';

  constructor(
    private readonly api: ApiService,
    protected readonly state: AnalysisStateService,
  ) {}

  ngOnInit(): void {
    this.loadCases();
  }

  loadCases(): void {
    this.isLoading = true;
    this.api.listCases().subscribe({
      next: (response) => {
        this.cases = response.cases;
        this.isLoading = false;
        this.statusMessage = this.cases.length > 0 ? `${this.cases.length} case(s) on file.` : 'No cases yet. Create one to get started.';

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
        this.statusMessage = 'Failed to load cases.';
      },
    });
  }

  createCase(): void {
    const name = this.newCaseName.trim();
    if (!name) {
      this.statusMessage = 'Case name is required.';
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
          this.statusMessage = `Case "${createdCase.name}" created.`;
          this.loadCases();
          this.selectCase(createdCase);
        },
        error: () => {
          this.isCreating = false;
          this.statusMessage = 'Failed to create case.';
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
        this.statusMessage = `Failed to load evidence locker for case ${caseSummary.id}.`;
      },
    });
  }

  isSelected(caseSummary: CaseSummary): boolean {
    return this.state.selectedCaseSnapshot?.id === caseSummary.id;
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
