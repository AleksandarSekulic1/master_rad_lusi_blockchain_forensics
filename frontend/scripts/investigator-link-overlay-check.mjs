/**
 * Smoke check for the investigator-link graph overlay (CASE-MANAGEMENT-IMPLEMENTATION.md
 * §15). Runs headless against the SAME cytoscape the app bundles and mirrors
 * graph-visualization.component.ts:
 *   - buildInvestigatorLinkEdgeElements(): one `edge.investigator-link` element per link
 *     whose BOTH endpoints are nodes on screen, carrying the full link object;
 *   - renderInvestigatorLinkOverlay(): cy.remove('edge.investigator-link') then cy.add(...)
 *     on the live instance, no re-layout;
 *   - the cytoscape style rules for `edge`, `edge.swap-edge`, `edge.investigator-link`.
 *
 * Verifies: the investigator link is a visually distinct, separate element; real
 * transaction edges are never touched when the overlay is added/removed; a link to an
 * off-graph address is skipped.
 *
 * Run: `node frontend/scripts/investigator-link-overlay-check.mjs`
 */
import cytoscape from 'cytoscape';

// The three edge rules that matter for "visual distinction" (verbatim from the component).
const STYLE = [
  { selector: 'node', style: { 'background-color': '#4f8cff' } },
  {
    selector: 'edge',
    style: { 'line-style': 'solid', 'line-color': '#6ea8fe', 'target-arrow-shape': 'triangle' },
  },
  {
    selector: 'edge.swap-edge',
    style: { 'line-style': 'dashed', 'line-color': '#c084fc', 'target-arrow-shape': 'triangle' },
  },
  {
    selector: 'edge.investigator-link',
    style: {
      'line-style': 'dashed',
      'line-dash-pattern': [4, 4],
      'line-color': '#fb923c',
      'target-arrow-shape': 'none',
      'source-arrow-shape': 'none',
      label: 'data(label)',
    },
  },
];

const TX_EDGES = [
  { data: { id: 't0', source: '0xAAA', target: '0xBBB', label: '#1 · 5.00' } },
  { data: { id: 't1', source: '0xBBB', target: '0xCCC', label: '#2 · 2.00' } },
];
const NODES = ['0xAAA', '0xBBB', '0xCCC'].map((id) => ({ data: { id } }));

// mirrors component.buildInvestigatorLinkEdgeElements()
function buildInvestigatorLinkEdgeElements(links, nodeIds) {
  const out = [];
  links.forEach((link) => {
    if (!nodeIds.has(link.source_address) || !nodeIds.has(link.target_address)) return;
    out.push({
      data: {
        id: `invlink__${link.id}`,
        source: link.source_address,
        target: link.target_address,
        label: `◆ INVESTIGATOR LINK · ${link.confidence}`,
        isInvestigatorLink: true,
        investigatorLink: link,
      },
      classes: `investigator-link investigator-link-${link.confidence.toLowerCase()}`,
    });
  });
  return out;
}

// mirrors component.renderInvestigatorLinkOverlay()
function renderInvestigatorLinkOverlay(cy, links, enabled) {
  cy.remove('edge.investigator-link');
  if (enabled) {
    const nodeIds = new Set(cy.nodes().map((n) => n.id()));
    cy.add(buildInvestigatorLinkEdgeElements(links, nodeIds));
  }
}

const results = [];
const check = (name, cond, detail = '') => {
  results.push({ name, ok: !!cond });
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? '  -- ' + detail : ''}`);
};

const LINKS = [
  {
    id: 'lnk1',
    investigation_id: 'inv1',
    source_address: '0xAAA',
    target_address: '0xCCC',
    directed: false,
    reason: 'IP address from server logs connects both addresses.',
    evidence: 'Server log #42',
    confidence: 'High',
    author: 'marko',
    created_at: '2026-09-08T10:00:00+00:00',
    updated_at: '2026-09-08T10:00:00+00:00',
  },
  // endpoint 0xZZZ is NOT a node -> must be skipped
  {
    id: 'lnk2',
    investigation_id: 'inv1',
    source_address: '0xAAA',
    target_address: '0xZZZ',
    directed: false,
    reason: 'r',
    evidence: 'e',
    confidence: 'Low',
    author: 'marko',
    created_at: '2026-09-08T11:00:00+00:00',
    updated_at: '2026-09-08T11:00:00+00:00',
  },
];

const cy = cytoscape({ headless: true, styleEnabled: true, style: STYLE, elements: [...NODES, ...TX_EDGES] });

const txSnapshot = () =>
  cy.edges('[!isInvestigatorLink]').map((e) => JSON.stringify({ id: e.id(), ...e.data(), isInvestigatorLink: undefined }));
const txBefore = txSnapshot();

// 1) enable overlay
renderInvestigatorLinkOverlay(cy, LINKS, true);

check('exactly one investigator-link edge is added (the off-graph one is skipped)', cy.edges('.investigator-link').length === 1, `${LINKS.length} links in, ${cy.edges('.investigator-link').length} drawn`);
const inv = cy.$id('invlink__lnk1');
check('investigator-link edge carries the full link object + isInvestigatorLink flag', inv.data('isInvestigatorLink') === true && inv.data('investigatorLink')?.reason === LINKS[0].reason && inv.data('investigatorLink')?.confidence === 'High');
check('investigator-link edge connects the two link addresses', inv.data('source') === '0xAAA' && inv.data('target') === '0xCCC');
check('investigator-link label reads "◆ INVESTIGATOR LINK · High"', inv.data('label') === '◆ INVESTIGATOR LINK · High');

// 2) visual distinction: resolved style differs from a real tx edge AND from a swap edge
const txStyle = cy.$id('t0').style();
check('real tx edge is solid with a triangle arrow (unchanged)', txStyle['line-style'] === 'solid' && txStyle['target-arrow-shape'] === 'triangle');
check('investigator-link edge is DASHED', inv.style('line-style') === 'dashed');
check('investigator-link edge has NO arrowheads (undirected association, not a transfer)', inv.style('target-arrow-shape') === 'none' && inv.style('source-arrow-shape') === 'none');
check('investigator-link edge colour (#fb923c) differs from tx (#6ea8fe) and swap (#c084fc)', inv.style('line-color') === 'rgb(251,146,60)' || inv.style('line-color') === '#fb923c', inv.style('line-color'));

// 3) real transaction edges are untouched by adding the overlay
check('no transaction edge was added, removed, or modified by the overlay', JSON.stringify(txSnapshot()) === JSON.stringify(txBefore), `${cy.edges('[!isInvestigatorLink]').length} tx edges (was ${TX_EDGES.length})`);

// 4) toggle OFF removes only the investigator link
renderInvestigatorLinkOverlay(cy, LINKS, false);
check('toggling the overlay off removes the investigator-link edge', cy.edges('.investigator-link').length === 0);
check('...and the transaction edges are still all there, unchanged', JSON.stringify(txSnapshot()) === JSON.stringify(txBefore));

// 5) removing a single link (delete) then re-render drops just that one
renderInvestigatorLinkOverlay(cy, LINKS, true);
const afterDelete = LINKS.filter((l) => l.id !== 'lnk1');
renderInvestigatorLinkOverlay(cy, afterDelete, true);
check('after a link is deleted, re-rendering the overlay drops exactly that edge', cy.$id('invlink__lnk1').empty() && cy.edges('.investigator-link').length === 0);

console.log('');
const failed = results.filter((r) => !r.ok).length;
console.log(failed === 0 ? `ALL ${results.length} CHECKS PASSED` : `${failed} CHECK(S) FAILED`);
process.exit(failed === 0 ? 0 : 1);
