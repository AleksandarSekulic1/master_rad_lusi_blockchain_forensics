/** One test in the fixed pytest suite. These come from version-controlled files and are
 * deliberately not editable through the API - see backend app/api/routes/tests.py. */
export interface SuiteTest {
  id: string;
  /** First line of the test's own docstring - the Serbian display name. */
  name: string;
  /** Rest of the docstring: what the test proves and why it matters. */
  explanation: string;
  /** The test function's actual source, so the page can show exactly what is asserted. */
  source: string;
  /** First line of the containing class's docstring - the Serbian group heading. */
  group_title: string;
  raw_name: string;
  group: string;
  module: string;
  status?: 'passed' | 'failed' | 'skipped';
  message?: string | null;
  duration_ms?: number;
}

export interface SuiteListResponse {
  tests: SuiteTest[];
  total: number;
  error: string | null;
}

export interface SuiteRunResponse {
  results: SuiteTest[];
  total: number;
  passed: number;
  failed: number;
  skipped: number;
  duration_ms: number;
  ran_at: string;
  error: string | null;
}

export interface ScenarioTransaction {
  sender: string;
  recipient: string;
  amount: number;
  timestamp: string;
}

export interface ScenarioExpectation {
  address: string;
  expected_percentage: number;
}

/** A validation scenario is pure data (transactions + seeds + expected percentages), never
 * code - which is what makes full create/edit/delete safe to expose. */
export interface TestScenario {
  id: string;
  name: string;
  description: string;
  transactions: ScenarioTransaction[];
  seed_addresses: string[];
  expectations: ScenarioExpectation[];
  created_at: string;
  updated_at: string;
  created_by: string;
}

export interface ScenarioRequest {
  name: string;
  description: string;
  transactions: ScenarioTransaction[];
  seed_addresses: string[];
  expectations: ScenarioExpectation[];
}

export interface ScenarioCheckResult {
  address: string;
  expected_percentage: number;
  actual_percentage: number | null;
  passed: boolean;
  message: string | null;
}

export interface ScenarioResult {
  scenario_id: string;
  name: string;
  description: string;
  status: 'passed' | 'failed' | 'error';
  error: string | null;
  checks: ScenarioCheckResult[];
  passed_checks: number;
  total_checks: number;
  duration_ms: number;
}

export interface ScenarioRunResponse {
  results: ScenarioResult[];
  total: number;
  passed: number;
  failed: number;
  errors: number;
  duration_ms: number;
  ran_at: string;
}
