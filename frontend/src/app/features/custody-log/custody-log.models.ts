import { SybilCustodyEvidence, TransactionCustodyEntry } from '../../core/models/shared.models';

/** One row of the printed Образац table (Бр./Датум/Име и презиме/Опис радње/Потпис). */
export interface CustodyLogRow extends TransactionCustodyEntry {
  redni_broj: number;
  timestamp: string;
  user: string;
}

/** The full form for one transaction: header fields (which stay editable and reflect the
 * MOST RECENT access) plus every access row, oldest first. */
export interface CustodyChain {
  case_id: string;
  case_name: string | null;
  tx_id: string;
  tx_hash: string | null;
  sender_address: string | null;
  recipient_address: string | null;
  amount: number | null;
  currency: string | null;
  tx_timestamp: string | null;
  evidence_stored_name: string | null;
  evidence_file_name: string | null;
  identifikator_predmeta: string | null;
  identifikator_dokaznog_materijala: string | null;
  proizvodjac: string | null;
  model: string | null;
  serijski_broj: string | null;
  /** Structured Sybil & Bot Network Analysis finding for THIS transaction, if any access
   * to it ever attached one (see backend's custody_chain_for_transaction) - null for every
   * transaction that was never part of a flagged cluster. */
  sybil_evidence?: SybilCustodyEvidence | null;
  entries: CustodyLogRow[];
}

/** One row of the "Lanac dokaza" browsing list - a transaction that has been accessed at
 * least once, without loading its whole access history. */
export interface CustodyTransactionSummary {
  tx_id: string;
  tx_hash: string | null;
  sender_address: string | null;
  recipient_address: string | null;
  amount: number | null;
  currency: string | null;
  tx_timestamp: string | null;
  evidence_file_name: string | null;
  access_count: number;
  last_accessed_at: string | null;
  /** True when ANY access to this transaction identified it as a Sybil & Bot Network
   * finding - a quick "has forensic annotation" flag for the browsing list; the full
   * structured item only appears once the transaction's own chain is opened. */
  has_sybil_evidence?: boolean;
}

// --- Lanac dokaza po dokaznom fajlu (coarser sibling - see LANAC-DOKAZA.md) ---

/** The full form for one EVIDENCE FILE (not one transaction): header fields plus every
 * access row, oldest first. The evidence-level analogue of CustodyChain above. */
export interface CustodyEvidenceChain {
  case_id: string;
  case_name: string | null;
  evidence_stored_name: string;
  evidence_file_name: string | null;
  evidence_sha256: string | null;
  evidence_currency: string | null;
  evidence_row_count: number | null;
  identifikator_predmeta: string | null;
  identifikator_dokaznog_materijala: string | null;
  proizvodjac: string | null;
  model: string | null;
  serijski_broj: string | null;
  entries: CustodyLogRow[];
}

/** One row of the "Lanac dokaza" evidence-level browsing list. */
export interface CustodyEvidenceSummary {
  evidence_stored_name: string;
  evidence_file_name: string | null;
  evidence_sha256: string | null;
  evidence_currency: string | null;
  evidence_row_count: number | null;
  access_count: number;
  last_accessed_at: string | null;
}
