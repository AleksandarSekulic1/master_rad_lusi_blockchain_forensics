/** One recorded analyst action (see backend app/evidence/audit_log.py). Everything except
 * timestamp/action/user is optional, because a single log carries several kinds of action:
 * evidence intake has a file + hash but no seed list, an analysis run has a case + seed
 * list but no file, path finding has a file but no case. */
export interface ActivityLogEntry {
  timestamp: string;
  user: string;
  action: string;
  case_id: string | null;
  /** The case's name AS IT WAS when the action happened - kept verbatim rather than
   * resolved from case_id at read time, so renaming or deleting a case can't rewrite
   * history. */
  case_name: string | null;
  file_name: string | null;
  sha256: string | null;
  details: Record<string, unknown> | null;
}

export type ActivityPeriodMode = 'all' | 'day' | 'range';

export interface ActivityReportPreview {
  count: number;
  period: string;
  scope: 'all' | 'self';
  available_users: string[];
  /** Subset of available_users that still exist as accounts; the rest are historical. */
  active_users: string[];
}

export interface ActivityLogResponse {
  entries: ActivityLogEntry[];
  /** "all" for an admin (every account), "self" for everyone else - decided server-side. */
  scope: 'all' | 'self';
  filtered_user: string | null;
  /** Only populated for admins; the roster to offer in the per-user filter. */
  available_users: string[];
}
