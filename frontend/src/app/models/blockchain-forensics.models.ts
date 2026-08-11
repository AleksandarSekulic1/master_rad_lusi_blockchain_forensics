export interface AuditLogEntry {
  file_name: string;
  sha256_hash: string;
  action: string;
  user: string;
  timestamp?: string;
}

export interface TransactionPreviewRow {
  sender_address: string | null;
  recipient_address: string | null;
  amount: number | null;
  timestamp: string | null;
  metadata?: string | null;
}

export interface UploadCsvResponse {
  file_name: string;
  sha256: string;
  audit_log: AuditLogEntry;
  rows_total: number;
  preview: TransactionPreviewRow[];
  case?: CaseSummary;
  evidence?: EvidenceEntry;
  resolved_query?: string;
}

export type OnchainNetwork = 'mainnet' | 'sepolia';
export type OnchainMode = 'address_history' | 'tx_single' | 'tx_expand_sender';

export interface FetchOnchainRequest {
  query: string;
  network: OnchainNetwork;
  case_id: string;
  mode: OnchainMode;
}

export interface GraphNodeData {
  id: string;
  address?: string;
  label?: string;
  risk_score?: number;
  blacklist_flag?: boolean;
  blacklist_label?: string;
  blacklist_sources?: string[];
  cluster_id?: string;
  cluster_size?: number;
  anomaly_flag?: boolean;
  anomaly_score?: number;
  anomaly_reason?: string;
  peel_chain_flag?: boolean;
  peel_chain_id?: string;
  peel_chain_step?: number;
  peel_chain_role?: string;
  chain_hop_flag?: boolean;
  chain_hop_type?: string;
  chain_hop_reasons?: string[];
  total_received?: number;
  total_sent?: number;
  net_flow?: number;
  taint_percentage?: number;
  is_taint_seed?: boolean;
  /** How much of taint_percentage came from each seed address, e.g. {"0xA": 66.67,
   * "0xB": 33.33} - only meaningful/populated when more than one seed was selected. */
  taint_by_source?: Record<string, number>;
  [key: string]: unknown;
}

export interface TaintedHop {
  rank: number;
  source: string;
  target: string;
  timestamp: string;
  amount: number;
  tainted_amount: number;
  taint_pct_at_hop: number;
  source_taint_pct_after: number;
  target_taint_pct_after: number;
  /** What % of THIS hop's tainted amount came from each seed, e.g. {"0xA": 60, "0xB": 40}
   * when funds from multiple seeds had already mixed together before this transfer. */
  taint_by_source: Record<string, number>;
  /** Whatever the source CSV's tx_hash/hash column carried for this exact transaction, if
   * any - null when the evidence never had one. */
  tx_metadata: string | null;
}

export interface TaintTimelineEntry {
  rank: number;
  taint_percentage: number;
  /** "in" when this address received the transfer, "out" when it sent it - outflows leave
   * the % unchanged (proportional haircut), so only "in" entries actually explain a
   * percentage change; kept for both directions anyway so the full ledger is complete. */
  direction: 'in' | 'out';
  counterparty: string;
  amount: number;
  tainted_amount: number;
  timestamp: string;
  /** Per-seed split of this node's balance right after this exact event - lets the
   * per-seed filter apply correctly while the timeline is scrubbing, not just in the
   * final/full view (see taint-analysis.component.ts's nodeFilteredPct/getNodeTaintAtRank). */
  taint_by_source: Record<string, number>;
}

export interface TaintTimelineEvent {
  rank: number;
  source: string;
  target: string;
  amount: number;
  timestamp: string;
  tx_metadata: string | null;
}

export interface TaintNodeResult {
  address: string;
  taint_percentage: number;
  is_taint_seed: boolean;
  taint_by_source: Record<string, number>;
}

export interface TaintAnalysisResult {
  plugin: 'taint_analysis';
  description: string;
  seed_addresses: string[];
  tainted_node_count: number;
  tainted_hops: TaintedHop[];
  results: TaintNodeResult[];
  node_first_rank: Record<string, number>;
  edge_first_rank: Record<string, number>;
  node_taint_series: Record<string, TaintTimelineEntry[]>;
  timeline_max_rank: number;
  timeline_events: TaintTimelineEvent[];
}

export interface GraphLinkData {
  source: string;
  target: string;
  amount?: number;
  total_amount?: number;
  transaction_count?: number;
  first_seen?: string | null;
  last_seen?: string | null;
  transactions?: Array<Record<string, unknown>>;
  bridge_edge?: boolean;
  [key: string]: unknown;
}

export interface NodeLinkGraphResponse {
  directed: boolean;
  multigraph: boolean;
  graph: Record<string, unknown>;
  nodes: GraphNodeData[];
  links: GraphLinkData[];
  source_file?: string;
  rows?: number;
  generated_at?: string;
  analytics?: Record<string, unknown>;
  summary?: {
    blacklisted_nodes: number;
    high_risk_nodes: number;
    clusters: number;
  };
}

export interface AnalyticsRequest {
  file_name?: string;
  plugins?: string[] | null;
}

export interface PathFindingRequest {
  file_name?: string;
  source_address: string;
  target_address: string;
  strategy?: string;
  cutoff?: number;
  max_paths?: number;
}

export interface PathSummary {
  path: string[];
  total_amount: number;
  transaction_count: number;
  score?: number;
  nodes?: Array<Record<string, unknown>>;
}

export interface PathFindingResponse {
  source_file?: string;
  rows?: number;
  source_address: string;
  target_address: string;
  strategy: string;
  shortest_path: PathSummary | null;
  paths: PathSummary[];
}

export interface AnalyticsResponse extends NodeLinkGraphResponse {
  analytics: Record<string, unknown>;
  summary: {
    blacklisted_nodes: number;
    high_risk_nodes: number;
    clusters: number;
  };
}

export interface GraphSearchResult {
  node: GraphNodeData;
  score: number;
}

export type AddressType = 'contract' | 'eoa' | 'unknown';
export type KnownEntityCategory = 'exchange' | 'mixer' | 'sanctioned';

export interface KnownEntity {
  name: string;
  category: KnownEntityCategory;
}

export interface AddressEnrichment {
  address: string;
  address_type: AddressType;
  ens_name: string | null;
  balance_eth: number | null;
  known_entity: string | null;
  known_entity_category: KnownEntityCategory | null;
  first_seen_onchain: string | null;
  last_seen_onchain: string | null;
  funding_source: string | null;
  funding_amount_eth: number | null;
  funding_source_type: AddressType | null;
  funding_source_ens: string | null;
  funding_source_entity: string | null;
  funding_source_entity_category: KnownEntityCategory | null;
  tokens: string[];
  tokens_total_count: number;
}

export interface EvidenceEntry {
  file_name: string;
  stored_name: string;
  imported_at: string;
  size_bytes: number;
  sha256: string;
  analyst: string;
}

export type CaseStatus = 'open' | 'closed';

export interface CaseSummary {
  id: string;
  name: string;
  description: string | null;
  analyst: string;
  status: CaseStatus;
  created_at: string;
  updated_at: string;
  evidence_count: number;
  total_size_bytes: number;
  last_imported_at: string | null;
}

export interface Case extends CaseSummary {
  evidence: EvidenceEntry[];
}

export interface CreateCaseRequest {
  name: string;
  description?: string | null;
}

export type UserRole = 'admin' | 'analyst';
export type UserStatus = 'active' | 'blocked';

export interface AuthUser {
  id: string;
  username: string;
  role: UserRole;
  status?: UserStatus;
  created_at?: string;
  updated_at?: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: AuthUser;
}

export interface CreateUserRequest {
  username: string;
  password: string;
  role: UserRole;
}

export interface ResetLinkResponse {
  reset_link: string;
  token: string;
}

/** One recorded analyst action (see backend app/evidence/audit_log.py). Everything except
 * timestamp/action/user is optional, because a single log carries several kinds of action:
 * evidence intake has a file + hash but no seed list, an analysis run has a case + seed
 * list but no file, path finding has a file but no case. */
export interface ActivityLogEntry {
  timestamp: string;
  user: string;
  action: string;
  case_id: string | null;
  /** The case's name AS IT WAS when the action happened - kept verbatim rather than
   * resolved from case_id at read time, so renaming or deleting a case can't rewrite
   * history. */
  case_name: string | null;
  file_name: string | null;
  sha256: string | null;
  details: Record<string, unknown> | null;
}

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

export interface SeedSuggestionItem {
  address: string;
  /** Plain-language reasons; a suggestion never appears without at least one. */
  reasons: string[];
}

export interface SeedSuggestionCheck {
  id: string;
  label: string;
  description: string;
  category: 'origin' | 'laundering';
  matches: number;
}

export interface SeedSuggestionResponse {
  /** Defensible starting points for taint analysis (blacklist, OFAC). */
  origin_candidates: SeedSuggestionItem[];
  /** Mixers, relays, pass-through wallets - findings, but wrong to use as seeds. */
  laundering_points: SeedSuggestionItem[];
  /** Every rule that ran, including those that matched nothing, so "clean" can be told
   * apart from "not checked". */
  checks_performed: SeedSuggestionCheck[];
  total_addresses: number;
  /** Set when an empty result is caused by the shape of the evidence (a single-address
   * history pull) rather than by the data being clean - the two must not be confused. */
  coverage_note: string | null;
}

export type ActivityPeriodMode = 'all' | 'day' | 'range';

export interface ActivityReportPreview {
  count: number;
  period: string;
  scope: 'all' | 'self';
  available_users: string[];
  /** Subset of available_users that still exist as accounts; the rest are historical. */
  active_users: string[];
}

export interface ActivityLogResponse {
  entries: ActivityLogEntry[];
  /** "all" for an admin (every account), "self" for everyone else - decided server-side. */
  scope: 'all' | 'self';
  filtered_user: string | null;
  /** Only populated for admins; the roster to offer in the per-user filter. */
  available_users: string[];
}