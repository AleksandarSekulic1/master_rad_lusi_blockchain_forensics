import { CommonModule } from '@angular/common';
import { Component, ElementRef, OnDestroy, OnInit, ViewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ActivityLogApiService } from './activity-log.api';
import { ActivityReportOptions } from './activity-log.api';
import { AuthService } from '../../core/services/auth.service';
import { AppLang, SettingsService } from '../../core/services/settings.service';
import { ActivityLogEntry, ActivityPeriodMode } from './activity-log.models';
import { SignaturePadComponent } from '../../core/components/signature-pad/signature-pad.component';

/** How each raw `action` string is presented: a short human label, a one-word group used
 * for the coloured tag, and an icon. Unknown/new actions fall back to the raw string
 * rather than being hidden - a log that silently drops entries it doesn't recognise would
 * be worse than useless in a forensic context. */
interface ActionPresentation {
  label: [sr: string, en: string];
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
  imports: [CommonModule, FormsModule, SignaturePadComponent],
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

  // --- Pagination: client-side, over the already-fetched `entries` (the API itself caps
  // at 200 rows per getActivityLog's own `limit`) - 20/page by default, a common table
  // default that keeps one page well within a single screen without paging too often. ---
  protected readonly pageSizeOptions = [10, 20, 50] as const;
  protected pageSize: (typeof this.pageSizeOptions)[number] = 20;
  protected currentPage = 1;
  /** Anchor right above the table - scrolled into view on every page change, so flipping
   * to page 2 doesn't leave the analyst looking at the (now different) pagination bar at
   * the bottom with no idea what changed above. */
  @ViewChild('tableTop') private tableTopRef?: ElementRef<HTMLElement>;

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

  // --- Signing dialog for the PDF export (see activity_report.py's own docstring for why
  // the PDF stays server-built, unlike every other signed report in this app) - CSV export
  // above stays a plain, unsigned download since it's raw data, not a presentation
  // document. Same shape as report-export.component.ts's own signing dialog. ---
  @ViewChild(SignaturePadComponent) private signaturePad?: SignaturePadComponent;
  protected isSigningOpen = false;
  protected reportLang: AppLang = 'sr';
  protected declarationAccepted = false;
  protected isExportingSignedPdf = false;
  protected signatureError: string | null = null;

  private refreshTimer: ReturnType<typeof setInterval> | null = null;
  private static readonly AUTO_REFRESH_MS = 20_000;

  private static readonly ACTION_PRESENTATION: Record<string, ActionPresentation> = {
    csv_upload: { label: ['Otpremljena CSV evidencija', 'Uploaded CSV evidence'], group: 'evidence', icon: '⬆' },
    // Backend automatically splits a multi-currency upload into one evidence file per
    // currency (see upload.py's _split_and_store_by_currency) - each resulting file logs
    // its own entry with this action, rather than the plain csv_upload one.
    csv_upload_split: { label: ['Razdvojena CSV evidencija (po valuti)', 'CSV evidence split (by currency)'], group: 'evidence', icon: '⇉' },
    analytics_run: { label: ['Pokrenuta analiza', 'Ran analysis'], group: 'analysis', icon: '⚙' },
    path_finding: { label: ['Pretraga putanja', 'Pathfinding search'], group: 'analysis', icon: '↝' },
    dex_swap_analysis_run: { label: ['Pokrenuta DEX swap analiza', 'Ran DEX swap analysis'], group: 'analysis', icon: '⇌' },
    behavioral_analysis_run: { label: ['Pokrenuta bihevioralna analiza', 'Ran behavioral analysis'], group: 'analysis', icon: '◔' },
    token_approval_analysis_run: { label: ['Pokrenuta Token Approval analiza', 'Ran Token Approval analysis'], group: 'analysis', icon: '🔑' },
    sybil_analysis_run: { label: ['Pokrenuta Sybil & Bot Network analiza', 'Ran Sybil & Bot Network analysis'], group: 'analysis', icon: '👥' },
    case_created: { label: ['Kreiran slučaj', 'Case created'], group: 'case', icon: '＋' },
    case_status_changed: { label: ['Promenjen status slučaja', 'Case status changed'], group: 'case', icon: '⇄' },
    case_deleted: { label: ['Obrisan slučaj', 'Case deleted'], group: 'case', icon: '✕' },
    test_suite_run: { label: ['Pokrenuti sistemski testovi', 'Ran system tests'], group: 'test', icon: '✓' },
    test_scenarios_run: { label: ['Pokrenuti validacioni scenariji', 'Ran validation scenarios'], group: 'test', icon: '✓' },
    test_scenario_created: { label: ['Kreiran validacioni scenario', 'Validation scenario created'], group: 'test', icon: '＋' },
    test_scenario_updated: { label: ['Izmenjen validacioni scenario', 'Validation scenario updated'], group: 'test', icon: '✎' },
    test_scenario_deleted: { label: ['Obrisan validacioni scenario', 'Validation scenario deleted'], group: 'test', icon: '✕' },
    activity_report_exported: { label: ['Izvezen izveštaj aktivnosti', 'Activity report exported'], group: 'report', icon: '⭳' },
    custody_pdf_exported: { label: ['Izvezen lanac dokaza (PDF)', 'Chain of custody exported (PDF)'], group: 'custody', icon: '🖉' },
    report_signed: { label: ['Izvezen potpisan izveštaj (PDF)', 'Signed report exported (PDF)'], group: 'report', icon: '🖋' },
  };

  /** Fixed action order used only to space each action's colour evenly around the hue
   * circle (index * step, not a hash) - deterministic and maximally spread, so any two
   * actions differ by the same amount regardless of how many exist. Every key in
   * ACTION_PRESENTATION must appear here, plus the two synthetic keys for the
   * prefix-matched on-chain fetch and the unrecognised-action fallback. Replaces the old
   * one-colour-per-group scheme (7 colours for ~18 actions, so unrelated actions like
   * "Pokrenuta analiza" and "Pretraga putanja" looked identical) - see presentation(). */
  private static readonly ACTION_HUE_ORDER: readonly string[] = [
    'csv_upload',
    'csv_upload_split',
    'onchain_fetch',
    'analytics_run',
    'path_finding',
    'dex_swap_analysis_run',
    'behavioral_analysis_run',
    'token_approval_analysis_run',
    'sybil_analysis_run',
    'case_created',
    'case_status_changed',
    'case_deleted',
    'test_suite_run',
    'test_scenarios_run',
    'test_scenario_created',
    'test_scenario_updated',
    'test_scenario_deleted',
    'activity_report_exported',
    'custody_pdf_exported',
    'report_signed',
    'other',
  ];

  /** action key -> hue (0-359). Starts at 200 (blue) rather than 0 so the run doesn't
   * open on red, which already means "error/warning" elsewhere on this page. */
  private static readonly ACTION_HUE: Record<string, number> = Object.fromEntries(
    ActivityLogComponent.ACTION_HUE_ORDER.map((key, index) => [
      key,
      Math.round((200 + index * (360 / ActivityLogComponent.ACTION_HUE_ORDER.length)) % 360),
    ]),
  );

  /** Report type (see reports.py's RegisterReportRequest.report_type) -> human label. The
   * backend's own exported PDF/CSV activity report (activity_report.py's
   * _REPORT_TYPE_LABELS) is Serbian-only - this on-screen label is independently
   * translated, so only the exported file itself stays fixed. */
  private static readonly REPORT_TYPE_LABELS: Record<string, [sr: string, en: string]> = {
    taint: ['Taint izveštaj', 'Taint report'],
    pathfinding: ['Pathfinding izveštaj', 'Pathfinding report'],
    dex_swap: ['DEX Swap izveštaj', 'DEX Swap report'],
    behavioral: ['Bihevioralni izveštaj', 'Behavioral report'],
    token_approval: ['Token Approval izveštaj', 'Token Approval report'],
  };

  constructor(
    private readonly activityLogApi: ActivityLogApiService,
    protected readonly auth: AuthService,
    public readonly settings: SettingsService,
  ) {}

  /** Tiny inline translator: picks the Serbian or English string for the active language
   * (same pattern as the other analysis pages' own t()). */
  protected t(sr: string, en: string): string {
    return this.settings.lang() === 'sr' ? sr : en;
  }

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
    this.activityLogApi.getActivityLog({ user: this.selectedUser || null }).subscribe({
      next: (response) => {
        this.entries = response.entries;
        this.availableUsers = response.available_users;
        this.scope = response.scope;
        this.lastRefreshed = new Date();
        this.errorMessage = null;
        this.isLoading = false;
        // Clamp rather than reset to page 1 - this also runs on every 20s auto-refresh,
        // and snapping an analyst reading page 3 back to page 1 every 20s would be worse
        // than just leaving the page number alone when it's still valid.
        this.currentPage = Math.min(this.currentPage, this.totalPages);
      },
      error: () => {
        this.isLoading = false;
        this.errorMessage = this.t('Neuspešno učitavanje loga aktivnosti.', 'Failed to load the activity log.');
      },
    });
  }

  onUserFilterChange(): void {
    this.currentPage = 1;
    this.loadEntries();
  }

  // --- Pagination -----------------------------------------------------------------

  protected get totalPages(): number {
    return Math.max(1, Math.ceil(this.entries.length / this.pageSize));
  }

  protected get pagedEntries(): ActivityLogEntry[] {
    const start = (this.currentPage - 1) * this.pageSize;
    return this.entries.slice(start, start + this.pageSize);
  }

  /** First/last row numbers on the current page (1-based), for "21–40 of 137". */
  protected get pageRangeLabel(): string {
    if (this.entries.length === 0) {
      return '0';
    }
    const start = (this.currentPage - 1) * this.pageSize + 1;
    const end = Math.min(this.entries.length, this.currentPage * this.pageSize);
    return `${start}–${end}`;
  }

  protected setPageSize(size: number): void {
    this.pageSize = size as (typeof this.pageSizeOptions)[number];
    this.currentPage = 1;
    this.scrollToTableTop();
  }

  protected goToPage(page: number): void {
    const next = Math.min(Math.max(1, page), this.totalPages);
    if (next === this.currentPage) {
      return;
    }
    this.currentPage = next;
    this.scrollToTableTop();
  }

  private scrollToTableTop(): void {
    this.tableTopRef?.nativeElement.scrollIntoView({ behavior: 'smooth', block: 'start' });
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

  presentation(action: string): { label: string; group: ActionPresentation['group']; icon: string; hue: number } {
    const known = ActivityLogComponent.ACTION_PRESENTATION[action];
    if (known) {
      return {
        label: this.t(known.label[0], known.label[1]),
        group: known.group,
        icon: known.icon,
        hue: ActivityLogComponent.ACTION_HUE[action] ?? ActivityLogComponent.ACTION_HUE['other'],
      };
    }
    // On-chain fetches encode network+mode into the action name
    // (onchain_fetch_mainnet_address), so they're matched by prefix rather than listed
    // one row per combination.
    if (action.startsWith('onchain_fetch')) {
      return {
        label: this.t('Povučene transakcije sa blockchain-a', 'Fetched transactions from the blockchain'),
        group: 'evidence',
        icon: '⛓',
        hue: ActivityLogComponent.ACTION_HUE['onchain_fetch'],
      };
    }
    return { label: action, group: 'other', icon: '•', hue: ActivityLogComponent.ACTION_HUE['other'] };
  }

  /** What the action was performed on. Test actions and path finding have no case by
   * design, so they show their own scope rather than an empty cell. */
  entryScope(entry: ActivityLogEntry): ScopeInfo {
    if (entry.case_id) {
      return { label: entry.case_name || '', sub: entry.case_id, kind: 'case' };
    }
    if (entry.action.startsWith('test_')) {
      return { label: this.t('Testovi', 'Tests'), sub: this.t('provera ispravnosti', 'correctness check'), kind: 'scope' };
    }
    if (entry.action === 'path_finding') {
      return { label: this.t('Graf', 'Graph'), sub: entry.file_name, kind: 'scope' };
    }
    if (entry.action === 'activity_report_exported') {
      return { label: this.t('Izveštaj', 'Report'), sub: this.t('izvoz aktivnosti', 'activity export'), kind: 'scope' };
    }
    return { label: '', sub: null, kind: 'none' };
  }

  /** "10.08.2026." from an ISO date, for the period a report was generated for. */
  private dmy(value: unknown): string {
    const text = String(value ?? '');
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
    if (!match) {
      return text;
    }
    return this.settings.lang() === 'sr' ? `${match[3]}.${match[2]}.${match[1]}.` : `${match[1]}-${match[2]}-${match[3]}`;
  }

  /** Which time window an exported report covered - the whole point of recording the
   * export is being able to tell two reports apart later. */
  private reportPeriodText(from: unknown, to: unknown): string {
    if (!from && !to) {
      return this.t('sve aktivnosti', 'all activity');
    }
    if (from && to && from === to) {
      return `${this.t('jedan dan', 'one day')}: ${this.dmy(from)}`;
    }
    if (from && to) {
      return `${this.dmy(from)} – ${this.dmy(to)}`;
    }
    return from ? `${this.t('od', 'from')} ${this.dmy(from)}` : `${this.t('do', 'to')} ${this.dmy(to)}`;
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
        const outcome = failed > 0 ? this.t(`${failed} palo`, `${failed} failed`) : this.t('sve prošlo', 'all passed');
        return `${passed}/${total} ${this.t('testova prošlo', 'tests passed')} · ${outcome}`;
      }
      case 'test_scenarios_run': {
        const total = Number(details['total'] ?? 0);
        const passed = Number(details['passed'] ?? 0);
        const errors = Number(details['errors'] ?? 0);
        const single = details['scenario_id']
          ? this.t('jedan scenario', 'one scenario')
          : this.t(`${total} ${total === 1 ? 'scenario' : 'scenarija'}`, `${total} ${total === 1 ? 'scenario' : 'scenarios'}`);
        const errorText = errors > 0 ? ` · ${this.t(`${errors} sa greškom`, `${errors} with errors`)}` : '';
        return `${single} · ${passed}/${total} ${this.t('prošlo', 'passed')}${errorText}`;
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
        return `${format} · ${count} ${this.t('zapisa', 'entries')} · ${period}${usersText}`;
      }
      case 'analytics_run': {
        const seedCount = Number(details['seed_count'] ?? 0);
        const scope = String(details['evidence_scope'] ?? 'combined');
        const scopeText = scope === 'combined' ? this.t('sva evidencija (kombinovano)', 'all evidence (combined)') : scope;
        const seedText =
          seedCount === 1 ? this.t('1 izvor (seed)', '1 seed address') : this.t(`${seedCount} izvora (seed)`, `${seedCount} seed addresses`);
        let summary = `${seedText} · ${scopeText}`;
        // Only deliberate runs (Taint analiza / "Analiziraj graf") carry this - a passive
        // preview load never writes into the lanac dokaza, so this line is exactly what
        // distinguishes the two at a glance, without opening the raw detalji JSON.
        if (details['custody_recorded']) {
          const txRows = Number(details['custody_transaction_rows'] ?? 0);
          const evidenceFiles = Number(details['custody_evidence_files'] ?? 0);
          summary += ` · ${this.t('lanac dokaza', 'chain of custody')}: ${txRows} ${this.t('transakcija', 'transactions')}, ${evidenceFiles} ${this.t('fajl(ova)', 'file(s)')}`;
        }
        return summary;
      }
      case 'dex_swap_analysis_run': {
        const address = String(details['address'] ?? '') || this.t('sve adrese', 'all addresses');
        const scope = String(details['evidence_scope'] ?? 'combined');
        const scopeText = scope === 'combined' ? this.t('sva evidencija (kombinovano)', 'all evidence (combined)') : scope;
        let summary = `${address} · ${scopeText} · ${Number(details['total_events'] ?? 0)} ${this.t('događaja', 'events')}`;
        if (details['custody_recorded']) {
          const txRows = Number(details['custody_transaction_rows'] ?? 0);
          const evidenceFiles = Number(details['custody_evidence_files'] ?? 0);
          summary += ` · ${this.t('lanac dokaza', 'chain of custody')}: ${txRows} ${this.t('transakcija', 'transactions')}, ${evidenceFiles} ${this.t('fajl(ova)', 'file(s)')}`;
        }
        return summary;
      }
      case 'token_approval_analysis_run': {
        const address = String(details['address'] ?? '') || this.t('sve adrese', 'all addresses');
        const scope = String(details['evidence_scope'] ?? 'combined');
        const scopeText = scope === 'combined' ? this.t('sva evidencija (kombinovano)', 'all evidence (combined)') : scope;
        if (details['status'] === 'FAILED') {
          const error = String(details['error'] ?? this.t('nepoznata greška', 'unknown error'));
          return `${this.t('NEUSPEŠNO', 'FAILED')} · ${address} · ${scopeText} · ${error}`;
        }
        let summary = `${address} · ${scopeText} · ${Number(details['correlation_count'] ?? 0)} ${this.t('odobrenja', 'grants')}`;
        if (details['custody_recorded']) {
          const txRows = Number(details['custody_transaction_rows'] ?? 0);
          const evidenceFiles = Number(details['custody_evidence_files'] ?? 0);
          const findings = Number(details['token_approval_findings_recorded'] ?? 0);
          summary +=
            ` · ${this.t('lanac dokaza', 'chain of custody')}: ${txRows} ${this.t('transakcija', 'transactions')}, ` +
            `${evidenceFiles} ${this.t('fajl(ova)', 'file(s)')}, ${findings} ${this.t('TOKEN_APPROVAL nalaza', 'TOKEN_APPROVAL findings')}`;
        }
        return summary;
      }
      case 'sybil_analysis_run': {
        const address = String(details['address'] ?? '') || this.t('sve adrese', 'all addresses');
        const contract = details['contract'] ? ` · ${String(details['contract'])}` : '';
        const scope = String(details['evidence_scope'] ?? 'combined');
        const scopeText = scope === 'combined' ? this.t('sva evidencija (kombinovano)', 'all evidence (combined)') : scope;
        let summary =
          `${address}${contract} · ${scopeText} · ${Number(details['total_clusters'] ?? 0)} ${this.t('klastera', 'clusters')}, ` +
          `${Number(details['addresses_flagged'] ?? 0)} ${this.t('označenih adresa', 'flagged addresses')}`;
        if (details['custody_recorded']) {
          const txRows = Number(details['custody_transaction_rows'] ?? 0);
          const evidenceFiles = Number(details['custody_evidence_files'] ?? 0);
          summary += ` · ${this.t('lanac dokaza', 'chain of custody')}: ${txRows} ${this.t('transakcija', 'transactions')}, ${evidenceFiles} ${this.t('fajl(ova)', 'file(s)')}`;
        }
        return summary;
      }
      case 'custody_pdf_exported': {
        const scope = details['scope'] === 'transaction' ? this.t('transakcija', 'transaction') : this.t('dokazni fajl', 'evidence file');
        const target = String(details['tx_id'] ?? details['evidence_stored_name'] ?? '?');
        return `${scope}: ${target} · ${Number(details['entry_count'] ?? 0)} ${this.t('zapisa', 'entries')}`;
      }
      case 'report_signed': {
        const reportType = String(details['report_type'] ?? '');
        const typeLabelPair = ActivityLogComponent.REPORT_TYPE_LABELS[reportType];
        const typeLabel = typeLabelPair ? this.t(typeLabelPair[0], typeLabelPair[1]) : this.t('Izveštaj', 'Report');
        const code = String(details['verification_code'] ?? '?');
        let extra = '';
        if (reportType === 'taint') {
          extra = ` · ${this.t(
            `${Number(details['tainted_addresses'] ?? 0)} zaprljanih adresa, ${Number(details['cash_out_points'] ?? 0)} tačaka unovčavanja`,
            `${Number(details['tainted_addresses'] ?? 0)} tainted addresses, ${Number(details['cash_out_points'] ?? 0)} cash-out points`,
          )}`;
        } else if (reportType === 'pathfinding') {
          extra = ` · ${this.t(`${Number(details['hops'] ?? 0)} skokova`, `${Number(details['hops'] ?? 0)} hops`)}`;
        } else if (reportType === 'dex_swap') {
          extra = ` · ${Number(details['total_events'] ?? 0)} ${this.t('događaja', 'events')}`;
        } else if (reportType === 'token_approval') {
          extra = ` · ${Number(details['total_approvals'] ?? 0)} ${this.t('odobrenja', 'approvals')}, ${Number(details['potentially_risky_approvals'] ?? 0)} ${this.t('rizičnih', 'risky')}`;
        }
        return `${typeLabel} · ${code}${extra}`;
      }
      case 'path_finding':
        return `${String(details['source_address'] ?? '?')} → ${String(details['target_address'] ?? '?')}`;
      case 'case_status_changed':
        return `${String(details['from'] ?? '?')} → ${String(details['to'] ?? '?')}`;
      case 'csv_upload':
        return String(details['original_name'] ?? entry.file_name ?? '');
      case 'csv_upload_split': {
        const source = String(details['source_file'] ?? entry.file_name ?? '');
        const currency = String(details['currency'] ?? '') || this.t('bez valute', 'no currency');
        return `${source} · ${currency}`;
      }
      default:
        if (entry.action.startsWith('onchain_fetch')) {
          const query = String(details['query'] ?? '');
          const rows = details['rows_fetched'];
          return rows != null ? `${query} · ${rows} ${this.t('transakcija', 'transactions')}` : query;
        }
        return entry.file_name ?? '';
    }
  }

  detailPairs(entry: ActivityLogEntry): Array<{ key: string; value: string }> {
    const details = entry.details ?? {};
    return Object.entries(details).map(([key, value]) => ({
      key,
      value: Array.isArray(value) ? (value.length > 0 ? value.join(', ') : this.t('(prazno)', '(empty)')) : String(value),
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
    this.activityLogApi.getActivityReportPreview(this.reportOptions()).subscribe({
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
        this.reportError = this.t('Neuspešna provera broja zapisa.', 'Failed to check the entry count.');
      },
    });
  }

  /** CSV stays a plain, unsigned download - raw data, not a presentation document. */
  downloadReportCsv(): void {
    this.isDownloadingReport = true;
    this.reportError = null;
    this.activityLogApi.downloadActivityReportCsv(this.reportOptions()).subscribe({
      next: (blob) => {
        this.isDownloadingReport = false;
        const suffix = this.periodMode === 'all' ? 'sve' : this.reportDay || this.reportFrom;
        this.saveBlob(blob, `izvestaj_aktivnosti_${suffix}.csv`);
        // The export is itself a logged action, so the list is no longer current.
        this.loadEntries();
      },
      error: () => {
        this.isDownloadingReport = false;
        this.reportError = this.t('Neuspešno generisanje izveštaja.', 'Failed to generate the report.');
      },
    });
  }

  // --- Signed PDF export -------------------------------------------------------------

  openSigningDialog(): void {
    if (!this.canGenerateReport) {
      return;
    }
    this.isSigningOpen = true;
    this.reportLang = this.settings.lang();
    this.declarationAccepted = false;
    this.signatureError = null;
    this.reportError = null;
    setTimeout(() => this.signaturePad?.clear());
  }

  closeSigningDialog(): void {
    if (this.isExportingSignedPdf) {
      return;
    }
    this.isSigningOpen = false;
  }

  get canSubmitSignature(): boolean {
    return (this.signaturePad?.hasStrokes ?? false) && this.declarationAccepted && !this.isExportingSignedPdf;
  }

  private signatureDeclaration(): string {
    return this.reportLang === 'sr'
      ? 'Potvrđujem da sam izradio ovaj izveštaj aktivnosti u okviru navedenog perioda i da su u njemu prikazane '
        + 'tačno one akcije koje je aplikacija zabeležila u dnevniku. Potpis iznad je moj.'
      : 'I confirm that I produced this activity report for the stated period and that it shows exactly the actions '
        + 'the application recorded in the log. The signature above is mine.';
  }

  confirmSignAndExport(): void {
    if (!this.canSubmitSignature) {
      return;
    }
    this.isExportingSignedPdf = true;
    this.signatureError = null;

    this.activityLogApi
      .signActivityReportPdf(this.reportOptions(), {
        lang: this.reportLang,
        declaration: this.signatureDeclaration(),
        signatureImage: this.signaturePad!.getDataUrl(),
      })
      .subscribe({
        next: (blob) => {
          this.isExportingSignedPdf = false;
          this.isSigningOpen = false;
          const suffix = this.periodMode === 'all' ? 'sve' : this.reportDay || this.reportFrom;
          this.saveBlob(blob, `izvestaj_aktivnosti_${suffix}.pdf`);
          // The export is itself a logged action, so the list is no longer current.
          this.loadEntries();
        },
        error: () => {
          this.isExportingSignedPdf = false;
          this.signatureError = this.t('Neuspešno generisanje PDF izveštaja.', 'Failed to generate the PDF report.');
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
