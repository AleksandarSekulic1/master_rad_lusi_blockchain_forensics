/** Busiest single (day, hour) cell in BehavioralAnalysisResult.hour_by_day_distribution -
 * a more specific claim than "most active hour" (summed across all days) or "most active
 * day" (summed across all hours) alone. Null when the address has no timestamped
 * transactions at all. */
export interface BehavioralAnalysisPeakPeriod {
  day: string;
  hour: string;
  count: number;
  label: string;
}

export interface BehavioralAnalysisStats {
  most_active_hour: string | null;
  most_active_hour_count: number;
  most_active_day: string | null;
  most_active_day_count: number;
  peak_period: BehavioralAnalysisPeakPeriod | null;
  total_analyzed_transactions: number;
}

/** Heuristic UTC-offset-range / broad-region compatibility estimate, layered on top of
 * BehavioralAnalysisResult.hourly_distribution. NEVER a location claim - see `disclaimer`,
 * which is present on every `available: true` result and MUST be rendered alongside it.
 * `available: false` covers two distinct backend reasons (too few transactions, or enough
 * transactions but no offset's pattern clears the compatibility bar) that both surface the
 * same `message` - the UI only ever needs to branch on `available`. */
export interface TimezoneEstimate {
  available: boolean;
  /** Only present when `available` is true. */
  utc_offset_min?: number;
  utc_offset_max?: number;
  /** Pre-formatted "UTC+5 – UTC+8" (or "UTC+6" alone when the range is a single offset) - render as-is, do not reformat. */
  utc_offset_range_label?: string;
  /** Broad regions only (continent-level) - never a country, never phrased as "located in". */
  possible_regions?: string[];
  confidence?: 'Low' | 'Medium' | 'High';
  /** Always present when `available` is true - render verbatim beneath the estimate. */
  disclaimer?: string;
  /** Only present when `available` is false - e.g. "Insufficient data for reliable timezone inference." */
  message?: string;
  /** Only present when `available` is false: 'insufficient_transactions' | 'no_compatible_offset'.
   * Lets the UI show a language-specific, reason-specific fallback instead of `message`. */
  reason?: string;
}

/** Result of the case-scoped Behavioral / Time-of-Day Analysis endpoint
 * (GET /cases/{id}/behavioral-analysis) - first version, UTC only, no timezone/continent
 * inference. `hourly_distribution` and `day_of_week_distribution` are always fully
 * zero-filled (all 24 hour keys "00".."23", all 7 day names Monday..Sunday), and
 * `hour_by_day_distribution` is the same 7x24 grid nested by day then hour - exactly what
 * the heatmap renders. */
export interface BehavioralAnalysisResult {
  case_id: string;
  evidence: string | null;
  address: string;
  timezone_estimate: TimezoneEstimate;
  total_transactions: number;
  hourly_distribution: Record<string, number>;
  day_of_week_distribution: Record<string, number>;
  hour_by_day_distribution: Record<string, Record<string, number>>;
  stats: BehavioralAnalysisStats;
  generated_at: string;
}
