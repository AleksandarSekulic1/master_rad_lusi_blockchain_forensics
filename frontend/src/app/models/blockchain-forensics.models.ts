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
  [key: string]: unknown;
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