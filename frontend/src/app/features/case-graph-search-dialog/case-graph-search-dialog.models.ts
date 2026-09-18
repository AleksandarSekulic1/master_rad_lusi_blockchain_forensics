/** One address reachable from the queried address, `hops` TRANSACTED steps away (the
 * shortest of possibly several paths - see GraphNeighborhoodResult). */
export interface GraphNeighbor {
  address: string;
  hops: number;
}

/** Result of the graph-db pilot endpoint (GET /cases/{id}/graph-search/neighborhood) -
 * "every address connected to `address` within `max_hops` steps", answered by a Cypher
 * query against Neo4j rather than a hand-rolled bounded BFS. Additive/optional: the
 * backend returns HTTP 503 (surfaced to the caller as an error, not this shape) when
 * Neo4j isn't running - see PREDLOG-GRAF-SUBP.md. */
export interface CaseGraphNeighborhoodResult {
  case_id: string;
  evidence?: string | null;
  address: string;
  max_hops: number;
  transactions_indexed: number;
  neighbors: GraphNeighbor[];
  disclaimer: string;
}
