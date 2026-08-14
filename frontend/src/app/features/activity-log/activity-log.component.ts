import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ActivityReportOptions, ApiService } from '../../core/services/api.service';
import { AuthService } from '../../core/services/auth.service';
import { ActivityLogEntry, ActivityPeriodMode } from '../../models/blockchain-forensics.models';

/** How each raw `action` string is presented: a short human label, a one-word group used
 * for the coloured tag, and an icon. Unknown/new actions fall back to the raw string
 * rather than being hidden - a log that silently drops entries it doesn't recognise would
 * be worse than useless in a forensic context. */
interface ActionPresentation {
  label: string;
  group: 'evidence' | 'analysis' | 'case' | 'test' | 'report' | 'custody' | 'other';
  icon: string;
}

/** What an action was performed ON. Most actions belong to a case, but some (correctness
 * tests, path finding on a raw CSV) genuinely have no case - those get their own scope
 * label instead of an empty dash, so the column never looks like missing data. */
interface ScopeInfo {
  label: string;
  sub: string | null;
  kind: 'case' | 'scope' | 'none';
}

@Component({
  selector: 'app-activity-log',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './activity-log.component.html',
  styleUrl: './activity-log.component.scss',
})
export class ActivityLogComponent implements OnInit, OnDestroy {
  protected entries: ActivityLogEntry[] = [];
  protected availableUsers: string[] = [];
  protected scope: 'all' | 'self' = 'self';
  protected selectedUser = '';
  protected isLoading = false;
  protected errorMessage: string | null = null;
  protected lastRefreshed: Date | null = null;
  protected autoRefreshEnabled = true;
  /** Which rows have their raw `details` JSON expanded - keyed by the entry timestamp,
   * which is unique enough in practice (two actions in the same microsecond by the same
   * user aren't a case worth designing around). */
  protected expandedRows = new Set<string>();

  // --- Report export ---
  protected isReportPanelOpen = false;
  protected periodMode: ActivityPeriodMode = 'all';
  protected reportDay = '';
  protected reportFrom = '';
  protected reportTo = '';
  protected reportUsers = new Set<string>();
  protected reportActiveUsers: string[] = [];
  protected reportCount: number | null = null;
  protected reportPeriodLabel = '';
  protected isCountingReport = false;
  protected isDownloadingReport = false;
  protected reportError: string | null = null;

  private refreshTimer: ReturnType<typeof setInterval> | null = null;
  private static readonly AUTO_REFRESH_MS = 20_000;

  private static readonly ACTION_PRESENTATION: Record<string, ActionPresentation> = {
    csv_upload: { label: 'Otpremljena CSV evidencija', group: 'evidence', icon: '⬆' },
    analytics_run: { label: 'Pokrenuta analiza', group: 'analysis', icon: '⚙' },
    path_finding: { label: 'Pretraga putanja', group: 'analysis', icon: '↝' },
    case_created: { label: 'Kreiran slučaj', group: 'case', icon: '＋' },
    case_status_changed: { label: 'Promenjen status slučaja', group: 'case', icon: '⇄' },
    case_deleted: { label: 'Obrisan slučaj', group: 'case', icon: '✕' },
    test_suite_run: { label: 'Pokrenuti sistemski testovi', group: 'test', icon: '✓' },
    test_scenarios_run: { label: 'Pokrenuti validacioni scenariji', group: 'test', icon: '✓' },
    test_scenario_created: { label: 'Kreiran validacioni scenario', group: 'test', icon: '＋' },
    test_scenario_updated: { label: 'Izmenjen validacioni scenario', group: 'test', icon: '✎' },
    test_scenario_deleted: { label: 'Obrisan validacioni scenario', group: 'test', icon: '✕' },
    activity_report_exported: { label: 'Izvezen izveštaj aktivnosti', group: 'report', icon: '⭳' },
    custody_pdf_exported: { label: 'Izvezen lanac dokaza (PDF)', group: 'custody', icon: '🖉' },
  };

  constructor(
    private readonly api: ApiService,
    protected readonly auth: AuthService,
  ) {}

  ngOnInit(): void {
    this.loadEntries();
    this.startAutoRefresh();
  }

  ngOnDestroy(): void {
    this.stopAutoRefresh();
  }

  get isAdmin(): boolean {
    return this.auth.isAdmin;
  }

  loadEntries(): void {
    this.isLoading = true;
    this.api.getActivityLog({ user: this.selectedUser || null }).subscribe({
      next: (response) => {
        this.entries = response.entries;
        this.availableUsers = response.available_users;
        this.scope = response.scope;
        this.lastRefreshed = new Date();
        this.errorMessage = null;
        this.isLoading = false;
      },
      error: () => {
        this.isLoading = false;
        this.errorMessage = 'Neuspešno učitavanje loga aktivnosti.';
      },
    });
  }

  onUserFilterChange(): void {
    this.loadEntries();
  }

  toggleAutoRefresh(): void {
    this.autoRefreshEnabled = !this.autoRefreshEnabled;
    if (this.autoRefreshEnabled) {
      this.startAutoRefresh();
    } else {
      this.stopAutoRefresh();
    }
  }

  toggleDetails(entry: ActivityLogEntry): void {
    if (this.expandedRows.has(entry.timestamp)) {
      this.expandedRows.delete(entry.timestamp);
    } else {
      this.expandedRows.add(entry.timestamp);
    }
  }

  isExpanded(entry: ActivityLogEntry): boolean {
    return this.expandedRows.has(entry.timestamp);
  }

  hasDetails(entry: ActivityLogEntry): boolean {
    return entry.details != null && Object.keys(entry.details).length > 0;
  }

  presentation(action: string): ActionPresentation {
    const known = ActivityLogComponent.ACTION_PRESENTATION[action];
    if (known) {
      return known;
    }
    // On-chain fetches encode network+mode into the action name
    // (onchain_fetch_mainnet_address), so they're matched by prefix rather than listed
    // one row per combination.
    if (action.startsWith('onchain_fetch')) {
      return { label: 'Povučene transakcije sa blockchain-a', group: 'evidence', icon: '⛓' };
    }
    return { label: action, group: 'other', icon: '•' };
  }

  /** What the action was performed on. Test actions and path finding have no case by
   * design, so they show their own scope rather than an empty cell. */
  entryScope(entry: ActivityLogEntry): ScopeInfo {
    if (entry.case_id) {
      return { label: entry.case_name || '', sub: entry.case_id, kind: 'case' };
    }
    if (entry.action.startsWith('test_')) {
      return { label: 'Testovi', sub: 'provera ispravnosti', kind: 'scope' };
    }
    if (entry.action === 'path_finding') {
      return { label: 'Graf', sub: entry.file_name, kind: 'scope' };
    }
    if (entry.action === 'activity_report_exported') {
      return { label: 'Izveštaj', sub: 'izvoz aktivnosti', kind: 'scope' };
    }
    return { label: '', sub: null, kind: 'none' };
  }

  /** "10.08.2026." from an ISO date, for the period a report was generated for. */
  private dmy(value: unknown): string {
    const text = String(value ?? '');
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
    return match ? `${match[3]}.${match[2]}.${match[1]}.` : text;
  }

  /** Which time window an exported report covered - the whole point of recording the
   * export is being able to tell two reports apart later. */
  private reportPeriodText(from: unknown, to: unknown): string {
    if (!from && !to) {
      return 'sve aktivnosti';
    }
    if (from && to && from === to) {
      return `jedan dan: ${this.dmy(from)}`;
    }
    if (from && to) {
      return `${this.dmy(from)} – ${this.dmy(to)}`;
    }
    return from ? `od ${this.dmy(from)}` : `do ${this.dmy(to)}`;
  }

  /** A one-line "what exactly happened" summary built from the action's own details, so
   * the common case is readable without expanding the raw JSON. */
  summary(entry: ActivityLogEntry): string {
    const details = entry.details ?? {};
    switch (entry.action) {
      case 'test_suite_run': {
        const total = Number(details['total'] ?? 0);
        const passed = Number(details['passed'] ?? 0);
        const failed = Number(details['failed'] ?? 0);
        const outcome = failed > 0 ? `${failed} palo` : 'sve prošlo';
        return `${passed}/${total} testova prošlo · ${outcome}`;
      }
      case 'test_scenarios_run': {
        const total = Number(details['total'] ?? 0);
        const passed = Number(details['passed'] ?? 0);
        const errors = Number(details['errors'] ?? 0);
        const single = details['scenario_id'] ? 'jedan scenario' : `${total} ${total === 1 ? 'scenario' : 'scenarija'}`;
        const errorText = errors > 0 ? ` · ${errors} sa greškom` : '';
        return `${single} · ${passed}/${total} prošlo${errorText}`;
      }
      case 'test_scenario_created':
      case 'test_scenario_updated':
      case 'test_scenario_deleted':
        return String(details['name'] || details['scenario_id'] || '');
      case 'activity_report_exported': {
        const format = String(details['format'] ?? '').toUpperCase();
        const count = Number(details['entry_count'] ?? 0);
        const period = this.reportPeriodText(details['date_from'], details['date_to']);
        const users = details['users'];
        const usersText = Array.isArray(users) && users.length > 0 ? ` · ${users.join(', ')}` : '';
        return `${format} · ${count} zapisa · ${period}${usersText}`;
      }
      case 'analytics_run': {
        const seedCount = Number(details['seed_count'] ?? 0);
        const scope = String(details['evidence_scope'] ?? 'combined');
        const scopeText = scope === 'combined' ? 'sva evidencija (kombinovano)' : scope;
        const seedText = seedCount === 1 ? '1 izvor (seed)' : `${seedCount} izvora (seed)`;
        let summary = `${seedText} · ${scopeText}`;
        // Only deliberate runs (Taint analiza / "Analiziraj graf") carry this - a passive
        // preview load never writes into the lanac dokaza, so this line is exactly what
        // distinguishes the two at a glance, without opening the raw detalji JSON.
        if (details['custody_recorded']) {
          const txRows = Number(details['custody_transaction_rows'] ?? 0);
          const evidenceFiles = Number(details['custody_evidence_files'] ?? 0);
          summary += ` · lanac dokaza: ${txRows} transakcija, ${evidenceFiles} fajl(ova)`;
        }
        return summary;
      }
      case 'custody_pdf_exported': {
        const scope = details['scope'] === 'transaction' ? 'transakcija' : 'dokazni fajl';
        const target = String(details['tx_id'] ?? details['evidence_stored_name'] ?? '?');
        return `${scope}: ${target} · ${Number(details['entry_count'] ?? 0)} zapisa`;
      }
      case 'path_finding':
        return `${String(details['source_address'] ?? '?')} → ${String(details['target_address'] ?? '?')}`;
      case 'case_status_changed':
        return `${String(details['from'] ?? '?')} → ${String(details['to'] ?? '?')}`;
      case 'csv_upload':
        return String(details['original_name'] ?? entry.file_name ?? '');
      default:
        if (entry.action.startsWith('onchain_fetch')) {
          const query = String(details['query'] ?? '');
          const rows = details['rows_fetched'];
          return rows != null ? `${query} · ${rows} transakcija` : query;
        }
        return entry.file_name ?? '';
    }
  }

  detailPairs(entry: ActivityLogEntry): Array<{ key: string; value: string }> {
    const details = entry.details ?? {};
    return Object.entries(details).map(([key, value]) => ({
      key,
      value: Array.isArray(value) ? (value.length > 0 ? value.join(', ') : '(prazno)') : String(value),
    }));
  }

  trackByEntry(_index: number, entry: ActivityLogEntry): string {
    return `${entry.timestamp}__${entry.action}__${entry.user}`;
  }

  // --- Report export ------------------------------------------------------------------

  toggleReportPanel(): void {
    this.isReportPanelOpen = !this.isReportPanelOpen;
    if (this.isReportPanelOpen) {
      this.refreshReportCount();
    }
  }

  setPeriodMode(mode: ActivityPeriodMode): void {
    this.periodMode = mode;
    this.refreshReportCount();
  }

  toggleReportUser(username: string): void {
    if (this.reportUsers.has(username)) {
      this.reportUsers.delete(username);
    } else {
      this.reportUsers.add(username);
    }
    this.refreshReportCount();
  }

  selectAllReportUsers(): void {
    this.reportUsers.clear();
    this.refreshReportCount();
  }

  get isAllUsersSelected(): boolean {
    return this.reportUsers.size === 0;
  }

  isHistoricalUser(username: string): boolean {
    return this.reportActiveUsers.length > 0 && !this.reportActiveUsers.includes(username);
  }

  /** Empty dates mean "everything"; a single day is expressed as from === to, which is
   * what the backend already treats as one whole local day. */
  private reportOptions(): ActivityReportOptions {
    const users = [...this.reportUsers];
    if (this.periodMode === 'day') {
      return { dateFrom: this.reportDay || null, dateTo: this.reportDay || null, users };
    }
    if (this.periodMode === 'range') {
      return { dateFrom: this.reportFrom || null, dateTo: this.reportTo || null, users };
    }
    return { dateFrom: null, dateTo: null, users };
  }

  get isPeriodIncomplete(): boolean {
    if (this.periodMode === 'day') {
      return !this.reportDay;
    }
    if (this.periodMode === 'range') {
      return !this.reportFrom || !this.reportTo;
    }
    return false;
  }

  get isRangeInverted(): boolean {
    return this.periodMode === 'range' && !!this.reportFrom && !!this.reportTo && this.reportFrom > this.reportTo;
  }

  get canGenerateReport(): boolean {
    return !this.isPeriodIncomplete && !this.isRangeInverted && (this.reportCount ?? 0) > 0 && !this.isDownloadingReport;
  }

  refreshReportCount(): void {
    this.reportError = null;
    if (this.isPeriodIncomplete || this.isRangeInverted) {
      this.reportCount = null;
      return;
    }
    this.isCountingReport = true;
    this.api.getActivityReportPreview(this.reportOptions()).subscribe({
      next: (preview) => {
        this.reportCount = preview.count;
        this.reportPeriodLabel = preview.period;
        this.reportActiveUsers = preview.active_users;
        if (preview.available_users.length > 0) {
          this.availableUsers = preview.available_users;
        }
        this.isCountingReport = false;
      },
      error: () => {
        this.isCountingReport = false;
        this.reportCount = null;
        this.reportError = 'Neuspešna provera broja zapisa.';
      },
    });
  }

  downloadReport(format: 'pdf' | 'csv'): void {
    this.isDownloadingReport = true;
    this.reportError = null;
    this.api.downloadActivityReport(this.reportOptions(), format).subscribe({
      next: (blob) => {
        this.isDownloadingReport = false;
        const suffix = this.periodMode === 'all' ? 'sve' : this.reportDay || this.reportFrom;
        this.saveBlob(blob, `izvestaj_aktivnosti_${suffix}.${format}`);
        // The export is itself a logged action, so the list is no longer current.
        this.loadEntries();
      },
      error: () => {
        this.isDownloadingReport = false;
        this.reportError = 'Neuspešno generisanje izveštaja.';
      },
    });
  }

  private saveBlob(blob: Blob, fileName: string): void {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = fileName;
    link.click();
    URL.revokeObjectURL(url);
  }

  private startAutoRefresh(): void {
    this.stopAutoRefresh();
    this.refreshTimer = setInterval(() => this.loadEntries(), ActivityLogComponent.AUTO_REFRESH_MS);
  }

  private stopAutoRefresh(): void {
    if (this.refreshTimer !== null) {
      clearInterval(this.refreshTimer);
      this.refreshTimer = null;
    }
  }
}
