/** Trace direction - 'forward' follows money the way it actually moved (sender ->
 * recipient, "where did it go"); 'backward' walks incoming edges instead ("where did it
 * come from"). See backend/app/analytics/flow_of_funds.py. */
export type FlowOfFundsDirection = 'forward' | 'backward';

/** Which of the backend's three aggregation views is currently rendered - never raw,
 * per-transaction rows, always one of these three granularities. */
export type FlowAggregationLevel = 'address' | 'entity' | 'category';

/** One evidence row behind an aggregated flow - present on EVERY flow record (address,
 * entity or category level) so a click can always reach concrete blockchain evidence,
 * never just a bare hash. sender/recipient_address matter most at entity/category
 * granularity, where several different address pairs can be merged into one flow. */
export interface FlowTransactionDetail {
  tx_hash: string | null;
  /** Same id app.evidence.tx_identity.transaction_id computes for this row - identical to
   * the one a chain-of-custody entry for this exact transaction is keyed by, so a click can
   * open "/lanac-dokaza?tx=...&caseId=..." directly (see custody-log.component.ts's own
   * deep-link handling - that page already knows how to open a tx id, nothing new there). */
  tx_id: string;
  amount: number;
  timestamp: string | null;
  sender_address: string;
  recipient_address: string;
}

/** One cross-referenced finding about an address, always tagged with WHICH existing
 * analysis produced it (`type`) - see backend/app/analytics/flow_of_funds_enrichment.py for
 * the full field shape per type. Kept loose (index signature) since each `type` carries
 * different extra fields (a `risk_score` badge has `score`/`band`/`reasons`, a
 * `known_entity` badge has `name`/`category`, etc.) - the component picks fields by `type`. */
export interface NodeAnnotationBadge {
  type: string;
  [key: string]: unknown;
}

/** The three CLEARLY SEPARATED buckets a node's cross-referenced findings are split into -
 * never blended into one list, so a reader never mistakes a model's guess for an
 * established fact (see the enrichment module's own docstring for the exact rule per
 * bucket). */
export interface NodeAnnotationBuckets {
  facts: NodeAnnotationBadge[];
  aggregated: NodeAnnotationBadge[];
  heuristics: NodeAnnotationBadge[];
}

/** One node summary from the backend result - the smallest BFS level at which this
 * address was reached (0 for a seed), used to place Sankey columns without re-deriving
 * reachability client-side. Always address-level, regardless of which aggregation view is
 * displayed (see deriveSankeyLevels in flow-of-funds.component.ts for how entity/category
 * views reuse this). */
export interface FlowOfFundsNode {
  id: string;
  label: string;
  level: number;
  type: 'seed' | 'address';
  entity_name: string | null;
  entity_category: string | null;
}

/** One aggregated flow record - the shape shared by address_flows, entity_flows and
 * category_flows. `source`/`target` are addresses on address_flows, entity names on
 * entity_flows (falling back to the address when none is known), categories on
 * category_flows (same fallback) - see backend/app/analytics/flow_of_funds.py's
 * _collapse_flows. `contributing_addresses`/`source_category`/`target_category` are only
 * ever populated on the two collapsed views. */
export interface AggregatedFlow {
  level: number;
  source: string;
  source_label: string;
  source_entity_name?: string | null;
  source_entity_category?: string | null;
  source_category?: string | null;
  target: string;
  target_label: string;
  target_entity_name?: string | null;
  target_entity_category?: string | null;
  target_category?: string | null;
  asset: string;
  amount: number;
  value: number;
  transaction_count: number;
  distinct_tx_count: number;
  /** True when at least one underlying transaction also paid a DIFFERENT recipient in the
   * very same tx - the same-transaction, multi-output UTXO signature a single Ethereum
   * value transfer never produces (see the backend module docstring). */
  multi_output_same_tx: boolean;
  tx_hashes: string[];
  transactions: FlowTransactionDetail[];
  first_seen: string | null;
  last_seen: string | null;
  contributing_addresses?: { source: string[]; target: string[] };
}

export interface FlowOfFundsResult {
  source_addresses: string[];
  direction: FlowOfFundsDirection;
  max_levels: number;
  levels_reached: number;
  start_time: string | null;
  end_time: string | null;
  flow_count: number;
  truncated: boolean;
  nodes: FlowOfFundsNode[];
  address_flows: AggregatedFlow[];
  entity_flows: AggregatedFlow[];
  category_flows: AggregatedFlow[];
  /** Cross-referenced findings from the rest of the app's existing analyses (Graph's own
   * plugin pipeline, DEX Swap, Token Approval, and - opt-in - Taint/Sybil), keyed by real
   * address (always address-level, even when the Sankey view itself shows entities/
   * categories - see flow-of-funds.component.ts's annotationsForFlow). */
  node_annotations: Record<string, NodeAnnotationBuckets>;
  case_id: string;
  evidence: string | null;
  generated_at: string;
}

/** What the page currently asks the backend for - shared between the passive GET preview
 * and the custody-gated POST /run (see FlowOfFundsApiService). */
export interface FlowOfFundsRequestParams {
  sourceAddresses: string[];
  direction: FlowOfFundsDirection;
  maxLevels: number;
  minAmount?: number;
  maxFlows?: number;
  startTime?: string | null;
  endTime?: string | null;
  /** Opt into the two heavier cross-referenced analyses (see flow_of_funds_enrichment) -
   * both default OFF server-side, so these are only ever sent when explicitly true. */
  includeTaint?: boolean;
  includeSybil?: boolean;
}
