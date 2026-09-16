import { OnchainNetwork } from '../../core/models/shared.models';

export type OnchainMode = 'address_history' | 'tx_single' | 'tx_expand_sender';

export interface FetchOnchainRequest {
  query: string;
  network: OnchainNetwork;
  case_id: string;
  mode: OnchainMode;
}

/** POST /bitcoin/fetch - separate slice from Ethereum's FetchOnchainRequest by design
 * (see BITCOIN-UTXO-PLAN.md): no network/mode fields since v1 only supports mainnet
 * address history, never a single transaction by hash. */
export interface FetchBitcoinRequest {
  address: string;
  case_id: string;
}
