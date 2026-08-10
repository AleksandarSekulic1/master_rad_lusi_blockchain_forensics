import cytoscape from 'cytoscape';
import fcose from 'cytoscape-fcose';
import layoutUtilities from 'cytoscape-layout-utilities';

let registered = false;

/** Registers the cytoscape layout extensions exactly once per app session, no matter
 * which page (graph-visualization, taint-analysis, ...) happens to load first - calling
 * cytoscape.use() a second time for the same extension name throws, and with two
 * separate lazy-loaded routes each wanting these extensions, module-level registration
 * in either component alone can't guarantee "exactly once" on its own. */
export function ensureCytoscapeExtensionsRegistered(): void {
  if (registered) {
    return;
  }
  cytoscape.use(fcose);
  cytoscape.use(layoutUtilities);
  registered = true;
}
