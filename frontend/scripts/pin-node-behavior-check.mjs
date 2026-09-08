/**
 * Smoke check for the investigator "Pin node" behaviour (CASE-MANAGEMENT-IMPLEMENTATION.md
 * §13). Runs headless against the SAME cytoscape + cytoscape-fcose the app bundles, and
 * faithfully mirrors graph-visualization.component.ts::renderGraph():
 *
 *   every re-layout builds a FRESH `cytoscape({ layout: {...fcose...}, ... })` - here with
 *   `fixedNodeConstraint` derived from the component's `pinnedNodePositions` map - and then
 *   runs `reapplyPinnedNodes()` (element.unlock() -> element.position(pos) -> element.lock()).
 *
 * There is no `ng test` runner wired up in this project, so this is a plain
 * `node frontend/scripts/pin-node-behavior-check.mjs` smoke check, in the spirit of
 * backend/scripts/smoke_*.py.
 *
 * Verifies:
 *   - a plain fcose layout works;
 *   - with nothing pinned the layout config and behaviour are byte-for-byte the old ones
 *     (no fixedNodeConstraint key, nothing locked, randomized re-layout still active);
 *   - a pinned node stays EXACTLY at its pinned position across one and repeated
 *     re-layouts, while the rest of the graph is laid out around it;
 *   - unpinning releases the node (unlocked, unmarked, laid out again).
 */
import cytoscape from 'cytoscape';
import fcose from 'cytoscape-fcose';

cytoscape.use(fcose);

const NODE_IDS = ['0xAAA', '0xABC', '0xBBB', '0xCCC', '0xDDD', '0xEEE', '0xFFF', '0xGGG'];
const EDGES = [
  ['0xAAA', '0xABC'], ['0xABC', '0xBBB'], ['0xBBB', '0xCCC'], ['0xCCC', '0xDDD'],
  ['0xDDD', '0xEEE'], ['0xABC', '0xEEE'], ['0xEEE', '0xFFF'], ['0xFFF', '0xGGG'],
  ['0xGGG', '0xAAA'], ['0xABC', '0xGGG'],
];

const buildElements = () => [
  ...NODE_IDS.map((id) => ({ data: { id } })),
  ...EDGES.map(([source, target], i) => ({ data: { id: `e${i}`, source, target } })),
];

// Exactly the component's layout config (nodeCount <= 60 branch), + fixedNodeConstraint.
const layoutOptions = (fixedNodeConstraint) => ({
  name: 'fcose',
  quality: 'default',
  randomize: true,
  animate: false,
  fit: true,
  padding: 60,
  nodeSeparation: 100,
  nodeRepulsion: 6000,
  idealEdgeLength: 80,
  ...(fixedNodeConstraint && fixedNodeConstraint.length ? { fixedNodeConstraint } : {}),
});

/** One renderGraph() pass: fresh instance + fixedNodeConstraint + reapplyPinnedNodes(). */
function renderGraph(pinnedNodePositions) {
  const presentNodeIds = new Set(NODE_IDS);
  const fixedNodeConstraint = [...pinnedNodePositions.entries()]
    .filter(([id]) => presentNodeIds.has(id))
    .map(([nodeId, position]) => ({ nodeId, position }));

  const cy = cytoscape({
    headless: true,
    styleEnabled: true,
    elements: buildElements(),
    layout: layoutOptions(fixedNodeConstraint),
  });

  for (const [id, position] of pinnedNodePositions) {
    const el = cy.$id(id);
    if (el.empty()) continue;
    el.unlock();
    el.position({ ...position });
    el.lock();
    el.addClass('pinned');
  }
  return cy;
}

const snapshot = (cy) => Object.fromEntries(cy.nodes().map((n) => [n.id(), { ...n.position() }]));
const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

const results = [];
const check = (name, cond, detail = '') => {
  results.push({ name, ok: !!cond });
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? '  -- ' + detail : ''}`);
};

// 1) plain layout
const cy1 = renderGraph(new Map());
const pos1 = snapshot(cy1);
check(
  'plain fcose layout gives finite, distinct positions for every node',
  cy1.nodes().every((n) => Number.isFinite(n.position().x) && Number.isFinite(n.position().y)) &&
    new Set(Object.values(pos1).map((p) => `${Math.round(p.x)},${Math.round(p.y)}`)).size === NODE_IDS.length,
);

// 2) existing behaviour with NO pins is untouched
check(
  'no-pin render passes the original layout config (no fixedNodeConstraint key)',
  !('fixedNodeConstraint' in layoutOptions([])) && !('fixedNodeConstraint' in layoutOptions(undefined)),
);
const cy2 = renderGraph(new Map());
const pos2 = snapshot(cy2);
check(
  'with nothing pinned, no node is locked and none carries .pinned',
  cy2.nodes().filter((n) => n.locked()).length === 0 && cy2.nodes('.pinned').length === 0,
);
const layouts = [pos1, pos2, ...Array.from({ length: 4 }, () => snapshot(renderGraph(new Map())))];
const distinctArrangements = new Set(
  layouts.map((p) => NODE_IDS.map((id) => `${Math.round(p[id].x)},${Math.round(p[id].y)}`).join('|')),
).size;
check(
  'randomized re-layout is still active for non-pinned graphs (fresh layouts vary)',
  distinctArrangements >= 2,
  `${distinctArrangements}/6 fresh no-pin layouts were distinct`,
);

// 3) pin 0xABC at a chosen position, re-layout -> it must land exactly there
const PIN_ID = '0xABC';
const pinnedAt = { x: 123.5, y: -77.25 };
const pinned = new Map([[PIN_ID, pinnedAt]]);

const cy3 = renderGraph(pinned);
const pos3 = snapshot(cy3);
check(
  'pinned node is at its pinned position after re-layout',
  dist(pinnedAt, pos3[PIN_ID]) < 1e-6,
  `off by ${dist(pinnedAt, pos3[PIN_ID]).toExponential(2)} px`,
);
check('pinned node is locked', cy3.$id(PIN_ID).locked() === true);
check('pinned node carries the .pinned class', cy3.$id(PIN_ID).hasClass('pinned') === true);
check(
  'the other nodes are still laid out (finite positions, none stuck on the pin)',
  NODE_IDS.filter((id) => id !== PIN_ID && Number.isFinite(pos3[id].x) && Number.isFinite(pos3[id].y)).length ===
    NODE_IDS.length - 1 && NODE_IDS.every((id) => id === PIN_ID || dist(pos3[id], pinnedAt) > 1),
);

// 4) another independent re-layout, pin still set -> still exactly put
const cy4 = renderGraph(pinned);
check(
  'pinned node stays put across a second, independent re-layout',
  dist(pinnedAt, cy4.$id(PIN_ID).position()) < 1e-6,
);

// 5) unpin -> fresh re-layout, node is unlocked, unmarked, free to move
const cy5 = renderGraph(new Map());
check('after unpin the node is not locked', cy5.$id(PIN_ID).locked() === false);
check('after unpin the node has no .pinned class', cy5.$id(PIN_ID).hasClass('pinned') === false);
check(
  'after unpin the node is placed by the layout again (not at the old pinned spot)',
  dist(pinnedAt, cy5.$id(PIN_ID).position()) > 1,
  `now ${dist(pinnedAt, cy5.$id(PIN_ID).position()).toFixed(1)} px away`,
);

console.log('');
const failed = results.filter((r) => !r.ok).length;
console.log(failed === 0 ? `ALL ${results.length} CHECKS PASSED` : `${failed} CHECK(S) FAILED`);
process.exit(failed === 0 ? 0 : 1);
