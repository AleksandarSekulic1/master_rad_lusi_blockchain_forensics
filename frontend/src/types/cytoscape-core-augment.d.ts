// cytoscape-svg (registered in app/core/cytoscape-setup.ts) adds cy.svg(...) to the core
// instance - not part of @types/cytoscape's own typings (which only know about the
// built-in .png()/.jpg()). The `import` below is what makes this file a MODULE, so the
// `declare module 'cytoscape'` block AUGMENTS the real, already-typed 'cytoscape' module
// instead of replacing it with an empty one (a bare `declare module 'cytoscape' {...}` in
// a file with no import/export of its own is a fresh global ambient declaration, which
// would shadow every existing method - .png(), .on(), .nodes()... - see git history).
import 'cytoscape';

declare module 'cytoscape' {
  interface Core {
    svg(options?: { full?: boolean; scale?: number; bg?: string; output?: 'base64' | 'base64uri' }): string;
  }
}
