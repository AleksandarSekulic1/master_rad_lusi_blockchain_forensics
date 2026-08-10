import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../core/services/api.service';
import { AuthService } from '../../core/services/auth.service';
import { ActivityLogEntry } from '../../models/blockchain-forensics.models';

/** How each raw `action` string is presented: a short human label, a one-word group used
 * for the coloured tag, and an icon. Unknown/new actions fall back to the raw string
 * rather than being hidden - a log that silently drops entries it doesn't recognise would
 * be worse than useless in a forensic context. */
interface ActionPresentation {
  label: string;
  group: 'evidence' | 'analysis' | 'case' | 'other';
  icon: string;
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

  private refreshTimer: ReturnType<typeof setInterval> | null = null;
  private static readonly AUTO_REFRESH_MS = 20_000;

  private static readonly ACTION_PRESENTATION: Record<string, ActionPresentation> = {
    csv_upload: { label: 'Otpremljena CSV evidencija', group: 'evidence', icon: '⬆' },
    analytics_run: { label: 'Pokrenuta analiza', group: 'analysis', icon: '⚙' },
    path_finding: { label: 'Pretraga putanja', group: 'analysis', icon: '↝' },
    case_created: { label: 'Kreiran slučaj', group: 'case', icon: '＋' },
    case_status_changed: { label: 'Promenjen status slučaja', group: 'case', icon: '⇄' },
    case_deleted: { label: 'Obrisan slučaj', group: 'case', icon: '✕' },
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

  /** A one-line "what exactly happened" summary built from the action's own details, so
   * the common case is readable without expanding the raw JSON. */
  summary(entry: ActivityLogEntry): string {
    const details = entry.details ?? {};
    switch (entry.action) {
      case 'analytics_run': {
        const seedCount = Number(details['seed_count'] ?? 0);
        const scope = String(details['evidence_scope'] ?? 'combined');
        const scopeText = scope === 'combined' ? 'sva evidencija (kombinovano)' : scope;
        const seedText = seedCount === 1 ? '1 izvor (seed)' : `${seedCount} izvora (seed)`;
        return `${seedText} · ${scopeText}`;
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
