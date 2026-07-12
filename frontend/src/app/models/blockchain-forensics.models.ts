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