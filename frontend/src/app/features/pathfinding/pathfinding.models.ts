/** 'specific_address' is the original (From/To) mode. 'nearest_cex' resolves the
 * destination server-side, from the local known_entities registry - never a guess.
 * 'cash_out_point' is a recognized value but not implemented yet (the backend rejects it
 * with a 400 rather than accepting it and doing nothing useful) - kept here so the UI can
 * list it as a disabled/"coming soon" option without a type error. */
export type PathfindingDestinationMode = 'specific_address' | 'nearest_cex' | 'cash_out_point';

/** Result of the case-scoped Pathfinding Analysis endpoint (POST /cases/{id}/pathfinding)
 * - first version, plain BFS. Deliberately separate from PathFindingResponse above, which
 * describes the older, unrelated standalone /graph/path-finding endpoint (different
 * shape, works off a raw CSV file rather than a case).
 *
 * destination_address/destination_label/message are only ever populated for
 * destination_mode 'nearest_cex' - for 'specific_address' the response is still exactly
 * {found, path, hops}, unchanged since the first version. */
export interface CasePathfindingResult {
  found: boolean;
  path: string[];
  hops: number;
  destination_address?: string | null;
  destination_label?: string | null;
  message?: string | null;
}
