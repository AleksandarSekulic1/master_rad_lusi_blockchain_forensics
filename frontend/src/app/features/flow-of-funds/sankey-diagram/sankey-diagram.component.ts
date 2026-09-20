import { CommonModule } from '@angular/common';
import {
  AfterViewChecked,
  Component,
  ElementRef,
  EventEmitter,
  Input,
  OnChanges,
  OnDestroy,
  Output,
  SimpleChanges,
  ViewChild,
} from '@angular/core';

import { sankey, sankeyLinkHorizontal, SankeyLink, SankeyNode } from 'd3-sankey';

import { SettingsService } from '../../../core/services/settings.service';

/** Plain data the parent hands in - already reduced to whichever aggregation level/asset
 * filter is currently selected. Kept deliberately generic (no AggregatedFlow import) so
 * this component has no idea what a "flow" means - it only knows how to lay out and draw
 * a weighted directed graph, same separation as cytoscape-based pages keep between the
 * graph library and the domain model. */
export interface SankeyDiagramNode {
  id: string;
  label: string;
  /** BFS hop level from a seed - drives the column (x) position, not d3-sankey's own
   * topology-derived depth, so columns always mean "hops from the source" (see
   * flow-of-funds.component.ts's deriveSankeyLevels). */
  level: number;
  isSeed: boolean;
  entityCategory: string | null;
}

export interface SankeyDiagramLink {
  source: string;
  target: string;
  value: number;
  asset: string;
  /** Opaque payload handed back unchanged on (linkSelected) - the parent's AggregatedFlow. */
  flow: unknown;
}

interface NodeExtra {
  id: string;
  label: string;
  level: number;
  isSeed: boolean;
  entityCategory: string | null;
  [key: string]: unknown;
}

interface LinkExtra {
  asset: string;
  flow: unknown;
  linkId: string;
  [key: string]: unknown;
}

type LayoutNode = SankeyNode<NodeExtra, LinkExtra>;
type LayoutLink = SankeyLink<NodeExtra, LinkExtra>;

const MIN_ZOOM = 0.4;
const MAX_ZOOM = 4;
const ZOOM_STEP = 1.2;
const COLUMN_WIDTH = 220;
const ROW_HEIGHT = 30;

/** Interactive Sankey diagram: node columns are fixed to the caller-supplied BFS level (not
 * d3-sankey's own topology heuristic), link width is proportional to value (d3-sankey's
 * standard behaviour - nothing extra needed for that requirement), color is categorical by
 * `asset` (ETH/BTC/other), and every link is clickable (emits its own `flow` payload back
 * unchanged so the parent can show the transactions behind it). Zoom/pan is a small,
 * dependency-free pan+wheel-zoom implemented directly on an SVG transform - the app has no
 * d3-zoom (or any zoom library) anywhere else, so this stays consistent with the project's
 * "no unnecessary dependency" default rather than pulling one in for a handful of lines. */
@Component({
  selector: 'app-sankey-diagram',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './sankey-diagram.component.html',
  styleUrl: './sankey-diagram.component.scss',
})
export class SankeyDiagramComponent implements OnChanges, AfterViewChecked, OnDestroy {
  /** Exposed so the template can clamp stroke-width/rect-height without a wrapper method
   * for every call site. */
  protected readonly Math = Math;

  @Input() nodes: SankeyDiagramNode[] = [];
  @Input() links: SankeyDiagramLink[] = [];
  @Input() emptyMessage = '';

  @Output() readonly linkSelected = new EventEmitter<SankeyDiagramLink | null>();

  @ViewChild('svgRoot', { static: true }) private svgRootRef!: ElementRef<SVGSVGElement>;
  /** .sankey-canvas only exists once there's data (behind *ngIf), so this ref comes and goes
   * - measured (via ngAfterViewChecked, below) to size the SVG viewBox to whatever space the
   * page actually gives this component, instead of a fixed guess. Without this,
   * `preserveAspectRatio="xMidYMid meet"` was fitting a ~960x480 viewBox into a much wider
   * real container and letterboxing it down to a small, hard-to-read picture surrounded by a
   * lot of empty space. */
  @ViewChild('canvasWrap') private canvasWrapRef?: ElementRef<HTMLDivElement>;
  private observedElement: HTMLDivElement | null = null;
  private resizeObserver?: ResizeObserver;
  private containerWidth = 0;
  private containerHeight = 0;

  protected layoutNodes: LayoutNode[] = [];
  protected layoutLinks: LayoutLink[] = [];
  protected width = 960;
  protected height = 480;
  protected selectedLinkId: string | null = null;
  protected hoveredLinkId: string | null = null;
  protected hoveredNodeId: string | null = null;
  protected readonly linkPath = sankeyLinkHorizontal<NodeExtra, LinkExtra>();

  protected transform = { x: 0, y: 0, k: 1 };
  protected tooltip: { x: number; y: number; lines: string[] } | null = null;

  constructor(public readonly settings: SettingsService) {}

  /** Tiny inline translator: picks the Serbian or English string for the active language
   * (same pattern as every other page's own t()) - this component has its own copy since
   * it's mounted stand-alone, not passed labels from the parent. */
  protected t(sr: string, en: string): string {
    return this.settings.lang() === 'sr' ? sr : en;
  }

  private isPanning = false;
  private didPan = false;
  private panStart = { x: 0, y: 0 };
  private transformStart = { x: 0, y: 0 };

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['nodes'] || changes['links']) {
      this.rebuildLayout();
    }
  }

  /** Re-checks after every view update whether .sankey-canvas exists (it's behind *ngIf, so
   * it appears/disappears as `nodes`/`links` go from empty to populated and back) and
   * (re)attaches the ResizeObserver to whichever element is actually there right now. */
  ngAfterViewChecked(): void {
    const el = this.canvasWrapRef?.nativeElement ?? null;
    if (el === this.observedElement) {
      return;
    }
    this.resizeObserver?.disconnect();
    this.observedElement = el;
    if (!el || typeof ResizeObserver === 'undefined') {
      return;
    }
    this.resizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) {
        return;
      }
      const { width, height } = entry.contentRect;
      const changedEnough = Math.abs(width - this.containerWidth) > 4 || Math.abs(height - this.containerHeight) > 4;
      this.containerWidth = width;
      this.containerHeight = height;
      if (changedEnough && this.nodes.length > 0 && this.links.length > 0) {
        this.rebuildLayout(/* preserveView */ true);
      }
    });
    this.resizeObserver.observe(el);
  }

  ngOnDestroy(): void {
    this.resizeObserver?.disconnect();
  }

  private rebuildLayout(preserveView = false): void {
    if (!preserveView) {
      this.selectedLinkId = null;
      this.tooltip = null;
      this.linkSelected.emit(null);
      this.transform = { x: 0, y: 0, k: 1 };
    }

    if (this.nodes.length === 0 || this.links.length === 0) {
      this.layoutNodes = [];
      this.layoutLinks = [];
      return;
    }

    const maxLevel = this.nodes.reduce((max, node) => Math.max(max, node.level), 0);
    // Fill whatever space the page actually gives this component (measured via
    // ResizeObserver); the level/row-count minimums only kick in for a diagram too big to
    // fit that space at all, where pan/zoom takes over instead of a squeezed layout.
    this.width = Math.max(this.containerWidth || 0, (maxLevel + 1) * COLUMN_WIDTH, 640);
    this.height = Math.max(this.containerHeight || 0, this.nodes.length * ROW_HEIGHT, 320);

    const sankeyLayout = sankey<NodeExtra, LinkExtra>()
      .nodeId((node) => node.id)
      .nodeWidth(14)
      .nodePadding(20)
      .nodeAlign((node) => node.level)
      .extent([
        [8, 8],
        [this.width - 8, this.height - 8],
      ]);

    const graph = sankeyLayout({
      nodes: this.nodes.map((node) => ({ ...node })),
      links: this.links.map((link, index) => ({
        source: link.source,
        target: link.target,
        value: Math.max(link.value, 1e-9),
        asset: link.asset,
        flow: link.flow,
        linkId: `${link.source}__${link.target}__${link.asset}__${index}`,
      })),
    });

    this.layoutNodes = graph.nodes;
    this.layoutLinks = graph.links;
  }

  /** Which asset color classes actually appear in the current diagram - drives the legend,
   * so it never lists a color that isn't on screen. */
  protected get presentAssetClasses(): string[] {
    const seen = new Set<string>();
    for (const link of this.layoutLinks) {
      seen.add(this.assetClass(link.asset));
    }
    return Array.from(seen);
  }

  protected legendAssetLabel(assetClass: string): string {
    if (assetClass === 'eth') {
      return 'ETH';
    }
    if (assetClass === 'btc') {
      return 'BTC';
    }
    if (assetClass === 'unknown') {
      return this.t('Nepoznato', 'Unknown');
    }
    return this.t('Ostalo', 'Other');
  }

  protected sourceNode(link: LayoutLink): LayoutNode {
    return link.source as LayoutNode;
  }

  protected targetNode(link: LayoutLink): LayoutNode {
    return link.target as LayoutNode;
  }

  protected assetClass(asset: string): string {
    const normalized = (asset || '').toUpperCase();
    if (normalized === 'ETH') {
      return 'eth';
    }
    if (normalized === 'BTC') {
      return 'btc';
    }
    if (normalized === 'UNKNOWN') {
      return 'unknown';
    }
    return 'other';
  }

  protected trackLink(_index: number, link: LayoutLink): string {
    return link.linkId;
  }

  protected trackNode(_index: number, node: LayoutNode): string {
    return node.id;
  }

  protected onLinkEnter(event: MouseEvent, link: LayoutLink): void {
    this.hoveredLinkId = link.linkId;
    this.updateTooltip(event, link);
  }

  protected onLinkMove(event: MouseEvent, link: LayoutLink): void {
    this.updateTooltip(event, link);
  }

  protected onLinkLeave(): void {
    this.hoveredLinkId = null;
    this.tooltip = null;
  }

  private updateTooltip(event: MouseEvent, link: LayoutLink): void {
    const amount = link.value.toLocaleString('en-US', { maximumFractionDigits: 6 });
    this.tooltip = {
      x: event.clientX,
      y: event.clientY,
      lines: [
        `${this.sourceNode(link).label} -> ${this.targetNode(link).label}`,
        `${amount} ${link.asset}`,
      ],
    };
  }

  protected onLinkClick(link: LayoutLink): void {
    if (this.didPan) {
      // A drag-to-pan that happened to end over a link shouldn't also register as a click.
      return;
    }
    if (this.selectedLinkId === link.linkId) {
      this.selectedLinkId = null;
      this.linkSelected.emit(null);
      return;
    }
    this.selectedLinkId = link.linkId;
    this.linkSelected.emit({
      source: this.sourceNode(link).id,
      target: this.targetNode(link).id,
      value: link.value,
      asset: link.asset,
      flow: link.flow,
    });
  }

  // --- zoom (wheel, centered on the cursor) + pan (drag) -------------------------------

  protected onWheel(event: WheelEvent): void {
    event.preventDefault();
    const rect = this.svgRootRef.nativeElement.getBoundingClientRect();
    const cursorX = event.clientX - rect.left;
    const cursorY = event.clientY - rect.top;
    this.zoomAt(cursorX, cursorY, event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP);
  }

  private zoomAt(cx: number, cy: number, factor: number): void {
    const nextScale = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, this.transform.k * factor));
    const ratio = nextScale / this.transform.k;
    this.transform = {
      k: nextScale,
      x: cx - (cx - this.transform.x) * ratio,
      y: cy - (cy - this.transform.y) * ratio,
    };
  }

  protected zoomIn(): void {
    this.zoomAt(this.width / 2, this.height / 2, ZOOM_STEP);
  }

  protected zoomOut(): void {
    this.zoomAt(this.width / 2, this.height / 2, 1 / ZOOM_STEP);
  }

  protected resetView(): void {
    this.transform = { x: 0, y: 0, k: 1 };
  }

  protected onPointerDown(event: PointerEvent): void {
    if (event.button !== 0) {
      return;
    }
    this.isPanning = true;
    this.didPan = false;
    this.panStart = { x: event.clientX, y: event.clientY };
    this.transformStart = { ...this.transform };
    (event.currentTarget as Element).setPointerCapture(event.pointerId);
  }

  protected onPointerMove(event: PointerEvent): void {
    if (!this.isPanning) {
      return;
    }
    const dx = event.clientX - this.panStart.x;
    const dy = event.clientY - this.panStart.y;
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) {
      this.didPan = true;
    }
    this.transform = { ...this.transform, x: this.transformStart.x + dx, y: this.transformStart.y + dy };
  }

  protected onPointerUp(event: PointerEvent): void {
    this.isPanning = false;
    try {
      (event.currentTarget as Element).releasePointerCapture(event.pointerId);
    } catch {
      /* pointer capture already released */
    }
  }

  protected get transformAttr(): string {
    return `translate(${this.transform.x}, ${this.transform.y}) scale(${this.transform.k})`;
  }
}
