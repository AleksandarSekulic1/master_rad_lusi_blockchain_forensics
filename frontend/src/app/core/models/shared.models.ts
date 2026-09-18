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

/** One evidence file produced by an automatic multi-currency split (see
 * UploadCsvResponse.split below) - `currency` is null for the bucket of rows that never
 * declared one at all (kept apart rather than guessed into one of the other groups). */
export interface UploadCsvSplitFile {
  file_name: string;
  currency: string | null;
  rows_total: number;
  sha256: string;
  evidence?: EvidenceEntry;
}

export interface UploadCsvResponse {
  /** True when the uploaded file declared more than one currency and was split into one
   * evidence file per currency instead of being stored as-is (see `files` below) - the
   * taint model would otherwise sum amounts across currencies as if they were the same
   * unit. The top-level file_name/sha256/preview/evidence fields are not meaningful for a
   * split response; read `files` instead. */
  split?: boolean;
  /** Original name of the file the analyst dropped, when `split` is true. */
  source_file_name?: string;
  files?: UploadCsvSplitFile[];
  /** Present for a normal (non-split) upload; absent when `split` is true. */
  file_name?: string;
  sha256?: string;
  audit_log?: AuditLogEntry;
  rows_total: number;
  preview?: TransactionPreviewRow[];
  case?: CaseSummary;
  evidence?: EvidenceEntry;
  resolved_query?: string;
}

export type OnchainNetwork = 'mainnet' | 'sepolia' | 'bitcoin_mainnet';

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
  /** Addresses that both receive and forward - i.e. how far past the first hop the
   * evidence actually follows the money. */
  relay_count: number;
  node_count: number;
  /** True when the evidence barely goes past one hop. Then "cash-out point" findings are
   * an artefact of the collection method (onward transfers were never pulled) and nothing
   * can dilute anyone, so percentages sit at 100%. */
  single_hop_evidence: boolean;
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

// --- DEX Swap Analysis (see DEX-SWAP-ANALIZA.md) - a heuristic, not a proof. Every event
// carries an explicit `confidence` ('Detected' when both legs share the same real
// transaction hash, 'Potential' when matched only by DEX address + a short time window),
// and the result always carries `disclaimer`, which the UI must render, not just the
// per-event data. ---

/** One candidate swap: `user_address` sent `input_token`/`input_amount` to `dex_address`,
 * then received `output_token`/`output_amount` back from that same address. Token fields
 * are null when the evidence never declared a currency for that leg - render that as
 * "unknown", never guess a symbol (see `data_completeness` below). */
export interface DexSwapEvent {
  type: 'SWAP';
  confidence: 'Detected' | 'Potential';
  label: string;
  user_address: string;
  dex_address: string;
  dex_name: string;
  dex_match_basis: string;
  input_token: string | null;
  input_amount: number;
  input_transaction_hash: string | null;
  input_timestamp: string;
  output_token: string | null;
  output_amount: number;
  output_transaction_hash: string | null;
  output_timestamp: string;
  time_gap_seconds: number;
  match_basis: 'shared_transaction_hash' | 'time_window';
  reasons: string[];
}

export interface DexSwapNodeConsidered {
  address: string;
  name: string;
  match_basis: string;
}

/** Whether ANY transaction in the analyzed evidence declared a currency/token at all -
 * false means every event's input_token/output_token is null, and the UI should explain
 * why rather than silently showing blanks. */
export interface DexSwapDataCompleteness {
  currency_declared: boolean;
  note: string;
}

/** Result of the case-scoped DEX Swap Analysis endpoint (GET
 * /cases/{id}/dex-swap-analysis). `address` on the request is optional server-side, but
 * this app's page always supplies one (see dex-swap-analysis.component.ts) - `address` on
 * the response mirrors back whatever was requested (null when omitted). */
export interface DexSwapAnalysisResult {
  case_id: string;
  evidence: string | null;
  address: string | null;
  total_events: number;
  detected_count: number;
  potential_count: number;
  events: DexSwapEvent[];
  dex_nodes_considered: DexSwapNodeConsidered[];
  data_completeness: DexSwapDataCompleteness;
  max_gap_seconds: number;
  disclaimer: string;
  generated_at: string;
}

// --- Sybil & Bot Network Analysis (see SYBIL-ANALIZA.md) - a heuristic, not a proof.
// Flags groups of DIFFERENT addresses that call the same smart contract and/or the same
// function within a short, synchronized time window. `disclaimer` must always be rendered
// alongside the results - this NEVER means the flagged addresses share a real owner. ---

/** One transaction inside a flagged cluster, kept for drill-down. */
export interface SybilClusterTransaction {
  sender_address: string;
  amount: number;
  timestamp: string;
  tx_hash: string | null;
  function_name: string | null;
}

/** One potential Sybil/bot cluster: `address_count` different addresses called
 * `contract_address` (and, when declared, the same `function_name`) within
 * `window_duration_seconds`. `risk_score` (0-100) and `risk_level` are a heuristic
 * confidence signal, never proof of common ownership - see `reasons` for the breakdown. */
export interface SybilCluster {
  cluster_id: string;
  contract_address: string;
  contract_name: string;
  contract_match_basis: string | null;
  function_name: string | null;
  address_count: number;
  addresses: string[];
  activity_count: number;
  window_start: string;
  window_end: string;
  window_duration_seconds: number;
  avg_gap_seconds: number;
  max_gap_seconds_observed: number;
  modal_amount: number;
  modal_amount_count: number;
  identical_amount_ratio: number;
  repeated_address_count: number;
  risk_score: number;
  risk_level: 'none' | 'low' | 'medium' | 'high' | 'critical';
  reasons: string[];
  transactions: SybilClusterTransaction[];
}

/** Result of the case-scoped Sybil Analysis endpoint (GET/POST .../sybil-analysis[/run]). */
export interface SybilAnalysisResult {
  case_id: string;
  evidence: string | null;
  target_address: string | null;
  contract: string | null;
  time_window_seconds: number;
  min_addresses: number;
  total_clusters: number;
  addresses_flagged: number;
  function_data_available: boolean;
  clusters: SybilCluster[];
  disclaimer: string;
  generated_at: string;
}

export interface AnalyticsResponse extends NodeLinkGraphResponse {
  analytics: Record<string, unknown>;
  summary: {
    blacklisted_nodes: number;
    high_risk_nodes: number;
    clusters: number;
  };
}

export type AddressType = 'contract' | 'eoa' | 'unknown';

export type KnownEntityCategory = 'exchange' | 'mixer' | 'sanctioned';

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
  /** Currency the amounts are denominated in, or null when the file never declared one -
   * which is not the same as ETH. Mixing currencies makes the taint percentage
   * meaningless, so uploads containing more than one are rejected outright. */
  currency?: string | null;
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

// --- Investigator layer (see CASE-MANAGEMENT-IMPLEMENTATION.md). An investigation is a
// container for investigator-generated conclusions, kept separate from the evidence Case
// above. ---

/** The investigator-layer container (backend: InvestigationCase). Not the evidence Case. */
export interface Investigation {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export type InvestigatorLinkConfidence = 'Low' | 'Medium' | 'High';

/** A manually recorded SUSPECTED RELATION between two blockchain addresses, based on
 * OFF-CHAIN evidence - an "investigator association". NOT a proven fact and NOT a
 * transaction-graph edge: it is drawn on the graph only as a visually distinct overlay,
 * never as a real transaction edge. `directed` is always false (undirected association);
 * `source_address`/`target_address` order carries no meaning. */
export interface InvestigatorLink {
  id: string;
  investigation_id: string;
  source_address: string;
  target_address: string;
  directed: boolean;
  reason: string;
  evidence: string;
  confidence: InvestigatorLinkConfidence;
  author: string;
  created_at: string;
  updated_at: string;
}

export interface InvestigatorLinkListResponse {
  investigation_id: string;
  address: string | null;
  /** Render this alongside the links - they are not blockchain facts. */
  disclaimer: string;
  links: InvestigatorLink[];
}

export type InvestigatorNoteTargetType = 'address' | 'transaction';

/** An investigator's own observation attached to exactly one of an address/node or a
 * transaction/edge. NOT a blockchain fact - stored only in the investigator layer. */
export interface InvestigatorNote {
  id: string;
  investigation_id: string;
  target_type: InvestigatorNoteTargetType;
  address: string | null;
  tx_id: string | null;
  text: string;
  author: string;
  created_at: string;
  updated_at: string;
}

export interface InvestigatorNoteListResponse {
  investigation_id: string;
  address: string | null;
  tx_id: string | null;
  target_type: InvestigatorNoteTargetType | null;
  notes: InvestigatorNote[];
}

/** An address the investigator pinned on the graph. Persisted per investigation (step 10)
 * so it survives a reload; `x`/`y` are the cytoscape model-coordinate position to restore. */
export interface PinnedNode {
  investigation_id: string;
  address: string;
  x: number | null;
  y: number | null;
  pinned_by: string;
  pinned_at: string;
  updated_at: string;
}

export interface PinnedNodeListResponse {
  investigation_id: string;
  pins: PinnedNode[];
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

/** What was recorded when a report was signed and exported. */
export interface ReportRegistryEntry {
  verification_code: string;
  content_hash: string;
  case_id: string;
  case_name: string;
  analyst: string;
  declaration: string;
  summary: Record<string, number | string>;
  registered_at: string;
}

// --- Lanac dokaza po transakciji (Obrazac evidencije rukovanja dokaznim materijalom) ---

/** What the analyst asserts when running an analysis - who they are, why, and their
 * signature. Required on every run; the backend appends one row built from this into the
 * custody log of every transaction the run touches. */
export interface TransactionCustodyEntry {
  ime_prezime: string;
  opis_radnje: string;
  signature_image: string;
  identifikator_predmeta?: string | null;
  identifikator_dokaznog_materijala?: string | null;
  proizvodjac?: string | null;
  model?: string | null;
  serijski_broj?: string | null;
}

/** One flagged node summarised for the Graph analysis PDF. */
export interface GraphReportFlaggedNode {
  address: string;
  risk: number;
  flags: string[];
  blacklist: string;
}

/** One investigator (off-chain) link summarised for the Graph analysis PDF. */
export interface GraphReportInvestigatorLink {
  source: string;
  target: string;
  confidence: string;
  reason: string;
}

/** Graph-specific payload the /graph page hands to ReportExportComponent so its PDF is a
 * graph-analysis report (findings, flagged nodes, focused node, investigator layer) rather
 * than the Dashboard's pre-analysis triage document. */
export interface GraphReportData {
  /** "Sve transakcije (kombinovano)" or the scoped evidence file name. */
  scope: string;
  /** Whether the risk/blacklist analytics pipeline was run (coloured graph) or it is a raw view. */
  analyzed: boolean;
  generatedAt: string;
  counts: {
    nodes: number;
    edges: number;
    blacklisted: number;
    highRisk: number;
    peel: number;
    chainHop: number;
    clusters: number;
    dexSwaps: number;
  };
  /** Human-readable filters/overlays active at export time - for reproducibility. */
  activeFilters: string[];
  flaggedNodes: GraphReportFlaggedNode[];
  focusedNode: {
    address: string;
    risk: number;
    flags: string[];
    blacklistSources: string;
    cluster: string;
  } | null;
  investigator: {
    name: string;
    notes: number;
    pinned: number;
    links: GraphReportInvestigatorLink[];
  } | null;
}

// --- Token Approval / Ice Phishing Analysis (see TOKEN-APPROVAL-IMPLEMENTATION.md) -----
// Backend-Phase-1 shapes only (extraction/correlation/risk indicators) - there is no
// custody-gated "run" variant yet, so no request-body types are needed here, only the
// GET responses. Mirrors backend/app/analytics/token_approval_analysis.py field-for-field;
// see that module's docstrings for what each field means and why it can be null.

/** 'declared' = the evidence's own `is_unlimited` column said so directly (strongest);
 * 'potential_by_magnitude' = only a configurable size heuristic, never confirmed (see
 * TOKEN-APPROVAL-IMPLEMENTATION.md #7.4/#8.3); null = not unlimited by either signal. */
export type TokenApprovalUnlimitedBasis = 'declared' | 'potential_by_magnitude' | null;

export type TokenApprovalRiskLevel = 'LOW' | 'MEDIUM' | 'HIGH';

/** APPROVED / APPROVED + USED / APPROVED + REVOKED / APPROVED + USED + REVOKED / UNKNOWN -
 * a combinable label (see TOKEN-APPROVAL-IMPLEMENTATION.md #14.2), not a single enum -
 * typed as `string` rather than a union so an unrecognised future combination still
 * renders instead of failing to compile. */
export type TokenApprovalCorrelationStatus = string;

export interface TokenApprovalRiskIndicator {
  code: string;
  label: string;
  reasons: string[];
}

export interface TokenApprovalTransferSummary {
  amount: number;
  timestamp: string;
  transaction_hash: string | null;
  block_number: number | null;
  recipient: string | null;
}

export interface TokenApprovalTransactionHashes {
  approval: string | null;
  revocation: string | null;
  transfer_from: string[];
}

/** One row of GET .../token-approval-correlation's `correlations[]` - one NONZERO
 * approve()/permit() grant plus everything known about its later usage. A pure
 * approve(spender, 0) revocation call never gets its own entry here - it only shows up as
 * this field set on the grant it ended (revoked/revocation_timestamp/
 * revocation_transaction_hash/seconds_to_revocation). */
export interface TokenApprovalCorrelationEntry {
  owner: string;
  spender: string;
  token_address: string | null;
  token_identified: boolean;
  approval_event_type: 'approve' | 'permit';
  approval_amount: number;
  unlimited_basis: TokenApprovalUnlimitedBasis;
  approval_timestamp: string;
  approval_transaction_hash: string | null;
  approval_block_number: number | null;
  status: TokenApprovalCorrelationStatus;
  /** true = confirmed by a matched transferFrom; false = confirmed absent; null = cannot
   * be ruled out (an unattributed transferFrom exists for this owner) - never guessed. */
  used: boolean | null;
  revoked: boolean;
  revocation_timestamp: string | null;
  revocation_transaction_hash: string | null;
  seconds_to_revocation: number | null;
  transfer_from_count: number;
  total_amount_transferred: number;
  first_use_timestamp: string | null;
  time_to_first_use_seconds: number | null;
  first_transfer_from: TokenApprovalTransferSummary | null;
  last_transfer_from: TokenApprovalTransferSummary | null;
  receiving_destinations: string[];
  transaction_hashes: TokenApprovalTransactionHashes;
}

export interface TokenApprovalUnattributedTransfer {
  owner: string;
  spender: string | null;
  recipient: string | null;
  token_address: string | null;
  amount: number;
  timestamp: string;
  transaction_hash: string | null;
  block_number: number | null;
  reason: string;
}

export interface TokenApprovalDataCompleteness {
  event_type_declared: boolean;
  token_address_declared: boolean;
  owner_address_declared: boolean;
  spender_address_declared: boolean;
  is_unlimited_declared: boolean;
  block_number_declared: boolean;
  permit_fields_declared: boolean;
  notes: string[];
}

/** One entry of GET .../token-approval-correlation's `groups[]` - the (owner, spender,
 * token) grant relationship, carrying the forensic risk indicators (see
 * TOKEN-APPROVAL-IMPLEMENTATION.md #15). Only the fields this page actually reads are
 * typed here - the backend's group object carries more (approvals[], linked_transfers[],
 * ...), already covered by TokenApprovalCorrelationEntry for this page's purposes. */
export interface TokenApprovalGroup {
  owner: string;
  spender: string;
  token_address: string | null;
  token_identified: boolean;
  current_status: 'active' | 'revoked';
  spender_known: boolean;
  spender_multi_owner: { distinct_owner_count: number } | null;
  risk_indicators: TokenApprovalRiskIndicator[];
  risk_score: number;
  risk_level: TokenApprovalRiskLevel;
}

export interface TokenApprovalCorrelationResult {
  case_id: string;
  evidence: string | null;
  address: string | null;
  generated_at: string;
  correlation_count: number;
  correlations: TokenApprovalCorrelationEntry[];
  groups: TokenApprovalGroup[];
  unattributed_transfers: TokenApprovalUnattributedTransfer[];
  data_completeness: TokenApprovalDataCompleteness;
  unlimited_threshold: number;
  rapid_use_seconds: number;
  large_amount_threshold: number;
  multiple_transfer_threshold: number;
  long_active_period_seconds: number;
  disclaimer: string;
}
