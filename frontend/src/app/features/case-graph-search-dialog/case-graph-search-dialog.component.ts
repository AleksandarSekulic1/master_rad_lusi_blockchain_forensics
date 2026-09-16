import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, EventEmitter, Input, OnInit, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { CaseDataApiService } from '../../core/services/case-data.api';
import { CaseGraphSearchDialogApiService } from './case-graph-search-dialog.api';
import { SettingsService } from '../../core/services/settings.service';
import { CaseGraphNeighborhoodResult, GraphNeighbor } from './case-graph-search-dialog.models';

/** One hop distance and every address found at exactly that distance - the shape the
 * template renders (grouped, closest first), derived from the flat `neighbors` list the
 * backend returns. */
interface HopGroup {
  hops: number;
  neighbors: GraphNeighbor[];
}

/** UI for the graph-db pilot (GET /cases/{id}/graph-search/neighborhood) - see
 * PREDLOG-GRAF-SUBP.md. Deliberately a self-contained dialog rather than a routed page:
 * this is an optional, additive capability (Neo4j may not even be running), so it should
 * be reachable from wherever a case is already selected without adding a permanent nav
 * item for a feature that might 503.
 */
@Component({
  selector: 'app-case-graph-search-dialog',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './case-graph-search-dialog.component.html',
  styleUrl: './case-graph-search-dialog.component.scss',
})
export class CaseGraphSearchDialogComponent implements OnInit {
  @Input({ required: true }) caseId!: string;
  @Input() caseName: string | null = null;

  @Output() readonly closed = new EventEmitter<void>();

  protected readonly hopOptions = [1, 2, 3, 4, 5];
  /** Static since only one instance of this dialog is ever mounted at a time (see the
   * `*ngIf` in cases.component.html) - no risk of two dialogs colliding on the same
   * <datalist> id. */
  protected readonly addressListId = 'graphsearch-address-options';

  protected address = '';
  protected maxHops = 2;

  protected isSearching = false;
  protected errorMessage: string | null = null;
  protected result: CaseGraphNeighborhoodResult | null = null;

  /** Addresses already known to appear in this case's evidence (from the same node-link
   * graph the Graf page renders - GET .../graph), offered as <datalist> suggestions so
   * the analyst can pick one instead of retyping it by hand. Manual entry stays fully
   * available either way - this only ever suggests, never restricts. */
  protected knownAddresses: string[] = [];

  constructor(
    private readonly caseData: CaseDataApiService,
    private readonly caseGraphSearchDialogApi: CaseGraphSearchDialogApiService,
    public readonly settings: SettingsService,
  ) {}

  protected t(sr: string, en: string): string {
    return this.settings.lang() === 'sr' ? sr : en;
  }

  ngOnInit(): void {
    this.caseData.getCaseGraph(this.caseId).subscribe({
      next: (graph) => {
        const addresses = new Set(graph.nodes.map((node) => node.id));
        this.knownAddresses = [...addresses].sort();
      },
      // Best-effort convenience only - if it fails (e.g. no evidence yet), the address
      // field still works exactly like a plain text input, just without suggestions.
      error: () => (this.knownAddresses = []),
    });
  }

  /** Composed here (not read from the backend's `result.disclaimer`, which is
   * Serbian-only) so it follows the UI language - same convention as every other
   * analysis's disclaimer in this project (e.g. dex-swap-analysis's PDF export). */
  protected get disclaimerText(): string {
    return this.t(
      'Rezultat je izveden iz iste dokazne evidencije kao i ostatak aplikacije (preko Neo4j grafa, '
        + 'sinhronizovanog pri svakom pozivu) - nije zaseban ili trajniji izvor istine od dokaznog CSV-a '
        + 'i njegovog SHA-256 heša.',
      'The result is derived from the same evidence as the rest of the app (via a Neo4j graph, synchronized '
        + 'on every call) - it is not a separate or more permanent source of truth than the evidence CSV and '
        + 'its SHA-256 hash.',
    );
  }

  protected get groupedByHop(): HopGroup[] {
    if (!this.result) {
      return [];
    }
    const groups = new Map<number, GraphNeighbor[]>();
    for (const neighbor of this.result.neighbors) {
      const list = groups.get(neighbor.hops) ?? [];
      list.push(neighbor);
      groups.set(neighbor.hops, list);
    }
    return [...groups.entries()].sort(([a], [b]) => a - b).map(([hops, neighbors]) => ({ hops, neighbors }));
  }

  search(): void {
    const address = this.address.trim();
    if (!address || this.isSearching) {
      return;
    }

    this.isSearching = true;
    this.errorMessage = null;
    this.result = null;

    this.caseGraphSearchDialogApi.getCaseGraphNeighborhood(this.caseId, address, this.maxHops).subscribe({
      next: (result) => {
        this.result = result;
        this.isSearching = false;
      },
      error: (error: HttpErrorResponse) => {
        this.isSearching = false;
        this.errorMessage = this.describeError(error);
      },
    });
  }

  private describeError(error: HttpErrorResponse): string {
    if (error.status === 503) {
      return this.t(
        'Graf baza (Neo4j) trenutno nije pokrenuta. Pokrenite je sa "docker compose up -d neo4j" da biste koristili ovu mogućnost - ostatak aplikacije radi normalno i bez nje.',
        'The graph database (Neo4j) isn’t running right now. Start it with "docker compose up -d neo4j" to use this feature – the rest of the app works fine without it.',
      );
    }
    if (error.status === 404) {
      return this.t(
        'Ta adresa se ne pojavljuje u evidenciji ovog slučaja.',
        'That address does not appear in this case’s evidence.',
      );
    }
    return this.t('Pretraga nije uspela. Pokušajte ponovo.', 'The search failed. Please try again.');
  }

  close(): void {
    this.closed.emit();
  }
}
