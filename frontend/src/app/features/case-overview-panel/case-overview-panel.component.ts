import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, Output } from '@angular/core';

import { SettingsService } from '../../core/services/settings.service';
import { InvestigatorLink, InvestigatorNote } from '../../core/models/shared.models';

/** Compact "Case Overview / Investigator Case" summary for the currently selected
 * investigation (CASE-MANAGEMENT-IMPLEMENTATION.md §17). Purely a summary + drill-down of
 * investigator-generated information - notes, pinned addresses, investigator links. NO
 * analytics, NO charts. Analysis stays on the Graph / Taint / Pathfinding / Behavioral /
 * DEX pages; this is only the investigator's workspace overview.
 *
 * Presentational: the parent (graph page) owns the data and the actions.
 */
@Component({
  selector: 'app-case-overview-panel',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './case-overview-panel.component.html',
  styleUrl: './case-overview-panel.component.scss',
})
export class CaseOverviewPanelComponent {
  @Input({ required: true }) investigationName!: string;
  @Input() description: string | null = null;
  @Input() notes: InvestigatorNote[] = [];
  @Input() pinnedAddresses: string[] = [];
  @Input() links: InvestigatorLink[] = [];
  /** Node ids present in the graph currently on screen - used only to flag / disable the
   * "na graf" jump for an address that is not in this view. */
  @Input() graphAddresses: string[] = [];

  @Output() readonly focusNode = new EventEmitter<string>();
  @Output() readonly unpinNode = new EventEmitter<string>();
  @Output() readonly showLink = new EventEmitter<InvestigatorLink>();

  protected open: 'notes' | 'pins' | 'links' | null = null;

  constructor(public readonly settings: SettingsService) {}

  protected t(sr: string, en: string): string {
    return this.settings.lang() === 'sr' ? sr : en;
  }

  protected toggle(category: 'notes' | 'pins' | 'links'): void {
    this.open = this.open === category ? null : category;
  }

  protected inGraph(address: string | null): boolean {
    return !!address && this.graphAddresses.includes(address);
  }

  protected noteTargetId(note: InvestigatorNote): string {
    return note.target_type === 'address' ? note.address ?? '' : note.tx_id ?? '';
  }
}
