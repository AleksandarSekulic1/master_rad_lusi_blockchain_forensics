import { OnchainNetwork, TransactionPreviewRow } from '../../core/models/shared.models';

export type OnchainMode = 'address_history' | 'tx_single' | 'tx_expand_sender';

export interface FetchOnchainRequest {
  query: string;
  network: OnchainNetwork;
  case_id: string;
  mode: OnchainMode;
}

/** POST /onchain/preview - read-only counterpart of FetchOnchainRequest: no case_id,
 * because nothing gets stored as evidence. */
export interface PreviewOnchainRequest {
  query: string;
  network: OnchainNetwork;
  mode: OnchainMode;
}

export interface PreviewOnchainResult {
  resolved_query: string;
  mode: string;
  /** Present only when the query resolved through a transaction hash. */
  transaction: TransactionPreviewRow | null;
  /** How many transactions a matching /onchain/fetch call would pull in. */
  total_transactions: number;
}

/** POST /bitcoin/fetch - separate slice from Ethereum's FetchOnchainRequest by design
 * (see BITCOIN-UTXO-PLAN.md): no network/mode fields since v1 only supports mainnet
 * address history, never a single transaction by hash. */
export interface FetchBitcoinRequest {
  address: string;
  case_id: string;
}
