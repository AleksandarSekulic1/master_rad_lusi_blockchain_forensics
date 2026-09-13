import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../core/services/api.service';
import { SettingsService } from '../../core/services/settings.service';
import {
  ScenarioExpectation,
  ScenarioResult,
  ScenarioTransaction,
  SuiteTest,
  TestScenario,
} from '../../models/blockchain-forensics.models';

/** Working copy of a scenario while it's being edited in the form. Kept separate from the
 * saved TestScenario so an abandoned edit never touches what's stored. */
interface ScenarioDraft {
  id: string | null;
  name: string;
  description: string;
  transactions: ScenarioTransaction[];
  seedInput: string;
  expectations: ScenarioExpectation[];
}

@Component({
  selector: 'app-tests',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './tests.component.html',
  styleUrl: './tests.component.scss',
})
export class TestsComponent implements OnInit {
  protected suiteTests: SuiteTest[] = [];
  protected suiteError: string | null = null;
  protected suiteRan = false;
  protected suitePassed = 0;
  protected suiteFailed = 0;
  protected suiteDurationMs = 0;
  protected isRunningSuite = false;

  protected scenarios: TestScenario[] = [];
  protected scenarioResults = new Map<string, ScenarioResult>();
  protected isRunningScenarios = false;
  protected runningScenarioId: string | null = null;

  protected draft: ScenarioDraft | null = null;
  protected isSaving = false;
  protected statusMessage: string | null = null;
  protected errorMessage: string | null = null;
  protected expandedTests = new Set<string>();
  /** Which test GROUPS (the class-docstring headings, e.g. "Period i vremenska zona") are
   * expanded - collapsed by default so the page opens as a scannable list of group names
   * rather than 300 individual test rows; a run auto-expands any group with a failure. */
  protected expandedGroups = new Set<string>();

  constructor(
    private readonly api: ApiService,
    protected readonly settings: SettingsService,
  ) {}

  /** Tiny inline translator: picks the Serbian or English string for the active language
   * (same pattern as every other page's own t()). Doesn't touch the pytest docstrings/
   * source code the suite itself reports (test.name/explanation/message/source, and the
   * group titles derived from them) - those describe REAL backend tests and stay exactly
   * as written in the test files, the same way a scenario's own name/addresses (entered
   * by whoever created it) aren't translated either. */
  protected t(sr: string, en: string): string {
    return this.settings.lang() === 'sr' ? sr : en;
  }

  ngOnInit(): void {
    this.loadSuite();
    this.loadScenarios();
  }

  // --- Fixed pytest suite (read-only) ---

  loadSuite(): void {
    this.api.listSuiteTests().subscribe({
      next: (response) => {
        // Collection only tells us WHICH tests exist, never their outcome - so a fresh
        // page load must not imply anything about pass/fail until a run happens.
        this.suiteTests = response.tests;
        this.suiteError = response.error;
        this.suiteRan = false;
      },
      error: () => {
        this.suiteError = this.t('Neuspešno učitavanje sistemskih testova.', 'Failed to load the system tests.');
      },
    });
  }

  runSuite(): void {
    this.isRunningSuite = true;
    this.errorMessage = null;
    this.api.runSuite().subscribe({
      next: (response) => {
        this.suiteTests = response.results;
        this.suitePassed = response.passed;
        this.suiteFailed = response.failed;
        this.suiteDurationMs = response.duration_ms;
        this.suiteError = response.error;
        this.suiteRan = true;
        this.isRunningSuite = false;
        // Open the groups that actually need attention, leave the rest collapsed - after a
        // run that's "which of these failed", not "every group at once".
        this.expandedGroups = new Set(
          this.suiteGroups.filter((group) => group.tests.some((test) => test.status === 'failed')).map((group) => group.group),
        );
      },
      error: () => {
        this.isRunningSuite = false;
        this.errorMessage = this.t('Neuspešno pokretanje sistemskih testova.', 'Failed to run the system tests.');
      },
    });
  }

  /** Grouped by the containing class's Serbian docstring title, falling back to the class
   * name for a class that has no docstring. */
  get suiteGroups(): Array<{ group: string; tests: SuiteTest[] }> {
    const byGroup = new Map<string, SuiteTest[]>();
    for (const test of this.suiteTests) {
      const key = test.group_title || test.group || this.t('Ostali testovi', 'Other tests');
      byGroup.set(key, [...(byGroup.get(key) ?? []), test]);
    }
    return [...byGroup.entries()].map(([group, tests]) => ({ group, tests }));
  }

  toggleGroup(group: string): void {
    if (this.expandedGroups.has(group)) {
      this.expandedGroups.delete(group);
    } else {
      this.expandedGroups.add(group);
    }
  }

  isGroupExpanded(group: string): boolean {
    return this.expandedGroups.has(group);
  }

  /** Failed-test count for a group's header chip - 0 renders as falsy, so *ngIf hides the
   * chip entirely for an all-passing group instead of showing "0 failed". */
  failedCountIn(tests: SuiteTest[]): number {
    return tests.filter((test) => test.status === 'failed').length;
  }

  toggleTestDetails(test: SuiteTest): void {
    if (this.expandedTests.has(test.id)) {
      this.expandedTests.delete(test.id);
    } else {
      this.expandedTests.add(test.id);
    }
  }

  isTestExpanded(test: SuiteTest): boolean {
    return this.expandedTests.has(test.id);
  }

  // --- Validation scenarios (full CRUD) ---

  loadScenarios(): void {
    this.api.listScenarios().subscribe({
      next: (response) => {
        this.scenarios = response.scenarios;
      },
      error: () => {
        this.errorMessage = this.t('Neuspešno učitavanje validacionih scenarija.', 'Failed to load the validation scenarios.');
      },
    });
  }

  runAllScenarios(): void {
    this.isRunningScenarios = true;
    this.errorMessage = null;
    this.api.runScenarios().subscribe({
      next: (response) => {
        this.scenarioResults = new Map(response.results.map((result) => [result.scenario_id, result]));
        this.isRunningScenarios = false;
      },
      error: () => {
        this.isRunningScenarios = false;
        this.errorMessage = this.t('Neuspešno pokretanje scenarija.', 'Failed to run the scenarios.');
      },
    });
  }

  runOneScenario(scenario: TestScenario): void {
    this.runningScenarioId = scenario.id;
    this.api.runScenarios(scenario.id).subscribe({
      next: (response) => {
        const result = response.results[0];
        if (result) {
          this.scenarioResults = new Map(this.scenarioResults).set(result.scenario_id, result);
        }
        this.runningScenarioId = null;
      },
      error: () => {
        this.runningScenarioId = null;
        this.errorMessage = this.t(`Neuspešno pokretanje scenarija "${scenario.name}".`, `Failed to run the scenario "${scenario.name}".`);
      },
    });
  }

  resultFor(scenario: TestScenario): ScenarioResult | undefined {
    return this.scenarioResults.get(scenario.id);
  }

  get scenarioSummary(): { passed: number; failed: number; total: number } | null {
    if (this.scenarioResults.size === 0) {
      return null;
    }
    const results = [...this.scenarioResults.values()];
    return {
      passed: results.filter((result) => result.status === 'passed').length,
      failed: results.filter((result) => result.status !== 'passed').length,
      total: results.length,
    };
  }

  // --- Scenario form ---

  startNewScenario(): void {
    this.draft = {
      id: null,
      name: '',
      description: '',
      transactions: [{ sender: '', recipient: '', amount: 0, timestamp: this.defaultTimestamp() }],
      seedInput: '',
      expectations: [{ address: '', expected_percentage: 100 }],
    };
    this.statusMessage = null;
  }

  editScenario(scenario: TestScenario): void {
    this.draft = {
      id: scenario.id,
      name: scenario.name,
      description: scenario.description,
      transactions: scenario.transactions.map((tx) => ({ ...tx })),
      seedInput: scenario.seed_addresses.join(', '),
      expectations: scenario.expectations.map((item) => ({ ...item })),
    };
    this.statusMessage = null;
  }

  cancelEdit(): void {
    this.draft = null;
  }

  addTransaction(): void {
    this.draft?.transactions.push({ sender: '', recipient: '', amount: 0, timestamp: this.defaultTimestamp() });
  }

  removeTransaction(index: number): void {
    this.draft?.transactions.splice(index, 1);
  }

  addExpectation(): void {
    this.draft?.expectations.push({ address: '', expected_percentage: 100 });
  }

  removeExpectation(index: number): void {
    this.draft?.expectations.splice(index, 1);
  }

  saveScenario(): void {
    if (!this.draft) {
      return;
    }
    const seedAddresses = this.draft.seedInput
      .split(',')
      .map((value) => value.trim())
      .filter((value) => value.length > 0);

    const transactions = this.draft.transactions.filter((tx) => tx.sender.trim() && tx.recipient.trim() && tx.amount > 0);
    const expectations = this.draft.expectations.filter((item) => item.address.trim());

    if (!this.draft.name.trim() || transactions.length === 0 || seedAddresses.length === 0 || expectations.length === 0) {
      this.statusMessage = this.t(
        'Naziv, bar jedna transakcija (sa iznosom > 0), bar jedan izvor i bar jedno očekivanje su obavezni.',
        'Name, at least one transaction (with amount > 0), at least one seed and at least one expectation are required.',
      );
      return;
    }

    const request = {
      name: this.draft.name.trim(),
      description: this.draft.description.trim(),
      transactions,
      seed_addresses: seedAddresses,
      expectations,
    };

    this.isSaving = true;
    const call = this.draft.id ? this.api.updateScenario(this.draft.id, request) : this.api.createScenario(request);
    call.subscribe({
      next: (saved) => {
        this.isSaving = false;
        this.draft = null;
        this.statusMessage = this.t(`Scenario "${saved.name}" je sačuvan.`, `Scenario "${saved.name}" was saved.`);
        this.loadScenarios();
        // Any stored result belongs to the previous definition, so it is dropped rather
        // than left on screen next to changed expectations.
        this.scenarioResults = new Map([...this.scenarioResults].filter(([id]) => id !== saved.id));
      },
      error: () => {
        this.isSaving = false;
        this.statusMessage = this.t('Neuspešno čuvanje scenarija.', 'Failed to save the scenario.');
      },
    });
  }

  deleteScenario(scenario: TestScenario): void {
    if (!confirm(this.t(`Obrisati scenario "${scenario.name}"?`, `Delete the scenario "${scenario.name}"?`))) {
      return;
    }
    this.api.deleteScenario(scenario.id).subscribe({
      next: () => {
        this.statusMessage = this.t(`Scenario "${scenario.name}" je obrisan.`, `Scenario "${scenario.name}" was deleted.`);
        this.loadScenarios();
      },
      error: () => {
        this.errorMessage = this.t(`Neuspešno brisanje scenarija "${scenario.name}".`, `Failed to delete the scenario "${scenario.name}".`);
      },
    });
  }

  trackByScenario(_index: number, scenario: TestScenario): string {
    return scenario.id;
  }

  trackByTest(_index: number, test: SuiteTest): string {
    return test.id;
  }

  private defaultTimestamp(): string {
    return new Date().toISOString().slice(0, 19) + 'Z';
  }
}
