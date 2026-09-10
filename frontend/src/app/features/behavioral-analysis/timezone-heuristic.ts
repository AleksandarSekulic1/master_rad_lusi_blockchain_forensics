import { TimezoneEstimate } from '../../models/blockchain-forensics.models';

/**
 * TypeScript port of backend/app/analytics/timezone_heuristics.py
 * (`estimate_timezone_compatibility`) - SAME thresholds and region map, so the combined /
 * aggregate view's estimate matches what the backend would return for the same numbers.
 *
 * Pure arithmetic over an already-computed, zero-filled hourly distribution ("00".."23").
 * NEVER a location claim - see `disclaimer`; every string that surfaces this feature must
 * use "is compatible with" phrasing, never "is located in".
 */

const NIGHT_LOCAL_HOURS = [0, 1, 2, 3, 4, 5] as const;
const NIGHT_FRACTION_COMPATIBILITY_THRESHOLD = 0.15;
const MIN_TRANSACTIONS_FOR_ESTIMATE = 8;
const HIGH_CONFIDENCE_NIGHT_FRACTION = 0.05;
const HIGH_CONFIDENCE_MIN_TRANSACTIONS = 20;
const MEDIUM_CONFIDENCE_NIGHT_FRACTION = 0.1;
const MEDIUM_CONFIDENCE_MIN_TRANSACTIONS = 12;

const INSUFFICIENT_DATA_MESSAGE = 'Insufficient data for reliable timezone inference.';

/** Backend keeps this string in Serbian; the component re-derives an English version when
 * the report language is EN (see timezoneDisclaimer). */
const DISCLAIMER =
  'Vremenski obrazac predstavlja heuristički indikator i ne predstavlja dokaz stvarne lokacije vlasnika adrese.';

const REGIONS_BY_UTC_OFFSET: Record<number, readonly string[]> = {
  [-11]: ['Oceania'],
  [-10]: ['Oceania', 'North America'],
  [-9]: ['North America'],
  [-8]: ['North America'],
  [-7]: ['North America'],
  [-6]: ['North America'],
  [-5]: ['North America', 'South America'],
  [-4]: ['South America'],
  [-3]: ['South America'],
  [-2]: ['South America'],
  [-1]: ['Europe', 'Africa'],
  [0]: ['Europe', 'Africa'],
  [1]: ['Europe', 'Africa'],
  [2]: ['Europe', 'Africa', 'Middle East'],
  [3]: ['Africa', 'Middle East', 'Europe'],
  [4]: ['Middle East', 'Asia'],
  [5]: ['Asia'],
  [6]: ['Asia'],
  [7]: ['Asia'],
  [8]: ['Asia', 'Oceania'],
  [9]: ['Asia'],
  [10]: ['Oceania'],
  [11]: ['Oceania'],
  [12]: ['Oceania'],
};

const CANDIDATE_OFFSETS: readonly number[] = Array.from({ length: 24 }, (_, i) => i - 11); // -11..12

function formatUtcOffset(offset: number): string {
  return `UTC${offset >= 0 ? '+' : '-'}${Math.abs(offset)}`;
}

function formatOffsetRange(min: number, max: number): string {
  return min === max ? formatUtcOffset(min) : `${formatUtcOffset(min)} – ${formatUtcOffset(max)}`;
}

function nightFractionForOffset(hourly: Record<string, number>, total: number, offset: number): number {
  const nightUtcHours = new Set(NIGHT_LOCAL_HOURS.map((localHour) => (((localHour - offset) % 24) + 24) % 24));
  let nightCount = 0;
  for (const hour of nightUtcHours) {
    nightCount += hourly[String(hour).padStart(2, '0')] ?? 0;
  }
  return nightCount / total;
}

function confidenceLabel(bestNightFraction: number, total: number): 'Low' | 'Medium' | 'High' {
  if (bestNightFraction <= HIGH_CONFIDENCE_NIGHT_FRACTION && total >= HIGH_CONFIDENCE_MIN_TRANSACTIONS) {
    return 'High';
  }
  if (bestNightFraction <= MEDIUM_CONFIDENCE_NIGHT_FRACTION && total >= MEDIUM_CONFIDENCE_MIN_TRANSACTIONS) {
    return 'Medium';
  }
  return 'Low';
}

export function estimateTimezoneCompatibility(hourly: Record<string, number>, total: number): TimezoneEstimate {
  if (total < MIN_TRANSACTIONS_FOR_ESTIMATE) {
    return { available: false, reason: 'insufficient_transactions', message: INSUFFICIENT_DATA_MESSAGE };
  }

  const scores = new Map<number, number>();
  for (const offset of CANDIDATE_OFFSETS) {
    scores.set(offset, nightFractionForOffset(hourly, total, offset));
  }
  const compatible = CANDIDATE_OFFSETS.filter(
    (offset) => (scores.get(offset) ?? 1) <= NIGHT_FRACTION_COMPATIBILITY_THRESHOLD,
  );
  if (compatible.length === 0) {
    return { available: false, reason: 'no_compatible_offset', message: INSUFFICIENT_DATA_MESSAGE };
  }

  const min = Math.min(...compatible);
  const max = Math.max(...compatible);
  const best = Math.min(...compatible.map((offset) => scores.get(offset) ?? 1));
  const regions = [...new Set(compatible.flatMap((offset) => REGIONS_BY_UTC_OFFSET[offset] ?? []))].sort();

  return {
    available: true,
    utc_offset_min: min,
    utc_offset_max: max,
    utc_offset_range_label: formatOffsetRange(min, max),
    possible_regions: regions,
    confidence: confidenceLabel(best, total),
    disclaimer: DISCLAIMER,
  };
}
