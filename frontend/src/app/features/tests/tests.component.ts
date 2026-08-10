import { CommonModule } from '@angular/common';
import { Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../core/services/api.service';
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

  constructor(private readonly api: ApiService) {}

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
        this.suiteError = 'Neuspešno učitavanje sistemskih testova.';
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
      },
      error: () => {
        this.isRunningSuite = false;
        this.errorMessage = 'Neuspešno pokretanje sistemskih testova.';
      },
    });
  }

  /** Grouped by the containing class's Serbian docstring title, falling back to the class
   * name for a class that has no docstring. */
  get suiteGroups(): Array<{ group: string; tests: SuiteTest[] }> {
    const byGroup = new Map<string, SuiteTest[]>();
    for (const test of this.suiteTests) {
      const key = test.group_title || test.group || 'Ostali testovi';
      byGroup.set(key, [...(byGroup.get(key) ?? []), test]);
    }
    return [...byGroup.entries()].map(([group, tests]) => ({ group, tests }));
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
        this.errorMessage = 'Neuspešno učitavanje validacionih scenarija.';
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
        this.errorMessage = 'Neuspešno pokretanje scenarija.';
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
        this.errorMessage = `Neuspešno pokretanje scenarija "${scenario.name}".`;
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
      this.statusMessage = 'Naziv, bar jedna transakcija (sa iznosom > 0), bar jedan izvor i bar jedno očekivanje su obavezni.';
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
        this.statusMessage = `Scenario "${saved.name}" je sačuvan.`;
        this.loadScenarios();
        // Any stored result belongs to the previous definition, so it is dropped rather
        // than left on screen next to changed expectations.
        this.scenarioResults = new Map([...this.scenarioResults].filter(([id]) => id !== saved.id));
      },
      error: () => {
        this.isSaving = false;
        this.statusMessage = 'Neuspešno čuvanje scenarija.';
      },
    });
  }

  deleteScenario(scenario: TestScenario): void {
    if (!confirm(`Obrisati scenario "${scenario.name}"?`)) {
      return;
    }
    this.api.deleteScenario(scenario.id).subscribe({
      next: () => {
        this.statusMessage = `Scenario "${scenario.name}" je obrisan.`;
        this.loadScenarios();
      },
      error: () => {
        this.errorMessage = `Neuspešno brisanje scenarija "${scenario.name}".`;
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
