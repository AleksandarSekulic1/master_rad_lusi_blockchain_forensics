import { KnownEntityCategory } from '../../core/models/shared.models';

export interface KnownEntity {
  name: string;
  category: KnownEntityCategory;
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
