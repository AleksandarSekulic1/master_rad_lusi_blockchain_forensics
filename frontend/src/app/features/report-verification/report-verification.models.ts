import { ReportRegistryEntry } from '../../core/models/shared.models';

export interface ReportVerificationResult {
  /** Whether the verification code exists in the registry at all. */
  found: boolean;
  /** null when no hash was supplied - "not checked" is distinct from "does not match". */
  matches: boolean | null;
  message: string;
  entry: ReportRegistryEntry | null;
}
