import { Case } from '../../core/models/shared.models';

/** One evidence file's contribution to the combined case analysis (backend
 * app/exports/service.py :: _evidence_contribution). */
export interface EvidenceContribution {
  file_name: string;
  rows: number;
  total_amount: number;
  addresses_touched: number;
  high_risk_addresses: number;
  blacklisted_addresses: number;
}

/** One audit-log row as embedded in the case report context (a subset of ActivityLogEntry
 * - only the fields the CSV/PDF report has ever shown). */
export interface CaseReportAuditEntry {
  timestamp?: string | null;
  action?: string | null;
  file_name?: string | null;
  user?: string | null;
  case_id?: string | null;
  sha256?: string | null;
}

/** Full case analysis context returned by GET /exports/cases/{id}/report-context - the
 * frontend renders its own signed, bilingual triage PDF from this. Mirrors
 * app/exports/service.py :: build_case_export_context. */
export interface CaseReportContext {
  case: Case;
  rows: number;
  nodes: number;
  edges: number;
  summary: {
    blacklisted_nodes?: number;
    high_risk_nodes?: number;
    clusters?: number;
    [key: string]: unknown;
  };
  audit_entries: CaseReportAuditEntry[];
  evidence_contributions: EvidenceContribution[];
  generated_at: string;
}
