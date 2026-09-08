# Case Management / Investigator Layer — Implementation Log

Status: **Steps 1, 3, 4, 5 & 6 done.** Backend: investigation-case container; investigator
notes on addresses/nodes **and** transactions/edges; investigator links (suspected
off-chain relations between two addresses). Frontend: a "Pin node" action on the graph
page (step 5). All five investigator-layer capabilities from the analysis now have a
backend; frontend for notes and links is still pending.
Date started: 2026-09-08

This file is the running implementation log for the new *Case Management / Investigator
Layer*.

- **§1–§9** — findings of the codebase analysis done before any implementation.
- **§10** — Step 1 implementation log: the investigation-case container.
- **§11** — Step 3 implementation log: investigator notes on blockchain addresses/nodes.
- **§12** — Step 4 implementation log: extending notes to transactions/edges.
- **§13** — Step 5 implementation log: "Pin node" (frontend, reuses the fcose layout).
- **§14** — Step 6 implementation log: investigator links (suspected off-chain relations).

---

## 0. Goal (recap)

Add an "investigator layer" on top of the existing forensic tooling that lets an
investigator:

1. Add notes to addresses / graph nodes.
2. Add notes to transactions / graph edges.
3. Pin / fix important nodes on the graph.
4. Manually link two addresses based on **off-chain** evidence.
5. Keep these investigator conclusions **clearly separated** from on-chain facts.

The hard requirement running through all five points is **separation**: nothing the
investigator asserts may be mixed into, or mistaken for, data derived from the imported
blockchain evidence.

---

## 1. Project shape (what exists today)

### 1.1 Repository layout

```
master_rad_lusi_blockchain_forensics/
├── backend/            FastAPI (Python), no ORM, no SQL — JSON/JSONL files on disk
│   └── app/
│       ├── main.py                 app factory, CORS, router mount at /api/v1
│       ├── paths.py                REPO_ROOT / DATA_DIR / RAW_DIR / CASES_DIR / LOGS_DIR
│       ├── security.py             JWT encode/decode, password hashing
│       ├── api/
│       │   ├── router.py           includes every sub-router, attaches auth deps
│       │   ├── deps.py             get_current_user -> {id, username, role}; require_admin
│       │   └── routes/             auth, cases, graph, analytics, onchain, addresses,
│       │                           upload, custody, exports, reports, activity_log,
│       │                           users(admin), tests(admin)
│       ├── services/               case_management, report_registry, user_management,
│       │                           onchain_ingestion, address_enrichment
│       ├── analytics/              ingestion, graph_building, case_graph, path_finding,
│       │                           behavioral_analysis, dex_swap_analysis,
│       │                           seed_suggestion, timezone_heuristics, plugins/*
│       ├── evidence/               audit_log, custody_log, custody_evidence_log,
│       │                           tx_identity, hashing
│       └── exports/                service (case CSV/PDF/GraphML/GEXF), custody_report,
│                                   custody_evidence_report, custody_pdf_common, pdf_fonts
├── frontend/           Angular 18, standalone components, cytoscape graphs
│   └── src/app/
│       ├── app.routes.ts           lazy standalone routes + auth/admin/guest guards
│       ├── app.config.ts           provideRouter + provideHttpClient(authInterceptor)
│       ├── app.component.html      top nav bar (one <a routerLink> per feature)
│       ├── core/
│       │   ├── cytoscape-setup.ts  ensureCytoscapeExtensionsRegistered() (fcose etc.)
│       │   ├── services/           api.service, auth.service, analysis-state.service
│       │   ├── guards/             auth.guard (authGuard / adminGuard / guestGuard)
│       │   ├── interceptors/       auth.interceptor (Bearer token + 401 -> /login)
│       │   └── components/signature-pad/   reusable canvas signature
│       ├── models/blockchain-forensics.models.ts   ALL shared TS interfaces
│       └── features/              dashboard, cases, graph-visualization, taint-analysis,
│                                  pathfinding, behavioral-analysis, dex-swap-analysis,
│                                  report-export, report-verification, activity-log,
│                                  custody-log, custody-access-dialog, admin, auth, tests
├── data/               cases/, raw/, report_registry.json, users.json, test_scenarios.json
└── logs/               audit_log.jsonl, custody_log.jsonl, custody_evidence_log.jsonl
```

### 1.2 Persistence model — **there is no database**

Everything is flat files:

| Data | Location | Shape |
|---|---|---|
| Case index | `data/cases/index.json` | `{ "cases": [ CaseSummary, ... ] }` |
| Case record | `data/cases/<case_id>/case.json` | full case + `evidence[]` array |
| Stored evidence files | `data/cases/<case_id>/evidence/*.csv` and `data/raw/*.csv` | raw CSV |
| Signed reports | `data/report_registry.json` | `[ ReportRegistryEntry, ... ]` |
| Users | `data/users.json` | `[ user, ... ]` |
| App-wide activity log | `logs/audit_log.jsonl` | append-only, one JSON object per line |
| Per-transaction chain of custody | `logs/custody_log.jsonl` | append-only, `scope:"transaction"` |
| Per-evidence-file chain of custody | `logs/custody_evidence_log.jsonl` | append-only, `scope:"evidence_file"` |

Read/write helpers are tiny and repeated per module (`_read_json` / `_write_json` in
`case_management.py`; `open(path, 'a')` + `json.dumps(...)+'\n'` in the custody logs).
Any new persistence must follow one of these two existing shapes.

### 1.3 Auth / roles

- JWT bearer token, stored in `localStorage` by `auth.service.ts`, attached by
  `auth.interceptor.ts`.
- `get_current_user` (backend) returns `{ id, username, role }`. Roles are `admin` and
  `analyst`.
- `api/router.py` mounts most routers with `dependencies=[Depends(get_current_user)]`
  (any active, non-blocked user). `users` and `tests` routers additionally require
  `require_admin`.
- Precedent for "readable by everyone, writes scoped by identity": `activity_log.py`
  narrows non-admins to their own rows; the `custody` router is open to any authenticated
  user (see `LANAC-DOKAZA.md` §6).

---

## 2. The graph: node / edge / transaction data structures

### 2.1 The graph is **derived, never stored**

`build_case_graph(evidence_paths)` (`app/analytics/case_graph.py`) does, on **every**
request:

```
clean each evidence CSV  ->  concat into one DataFrame  ->  build_transaction_graph()
```

`build_transaction_graph(df)` (`app/analytics/graph_building.py`) returns a
`networkx.DiGraph`:

- **Node** — id = the address **string, verbatim** (case-sensitive, no normalisation).
  Base attributes: `address`, `label`, and (added by `_annotate_node_flow_totals`)
  `total_received`, `total_sent`, `net_flow`.
  Analytics plugins later write more attributes onto the same nodes: `blacklist_flag`,
  `risk_score`, `cluster_id`, `cluster_size`, `taint_percentage`, `is_taint_seed`,
  `taint_by_source`, `peel_chain_flag/role/step`, `chain_hop_flag/type`, `anomaly_flag`,
  etc.
- **Edge** — one directed edge per `(sender, recipient)` **pair**, not per transaction
  (it is a `DiGraph`, not a `MultiDiGraph`). Attributes: `weight`, `total_amount`,
  `transaction_count`, `first_seen`, `last_seen`, and `transactions` — a list of
  `{ amount, timestamp, metadata }` (the individual transfers that were folded onto this
  edge). `metadata` is where a CSV's `tx_hash`/`hash` column lands after ingestion.
- `transaction_graph_to_node_link_json(graph)` serialises to
  `{ directed, multigraph, graph, nodes[], links[] }`; each node carries `id`, each link
  carries `source` + `target`.

### 2.2 Stable identity for a single transaction

`app/evidence/tx_identity.py::transaction_id(row, evidence_stored_name)`:

- if the row has a real tx hash (`metadata`) → that hash **is** the id;
- otherwise → `"row-" + sha256(sender|recipient|amount|timestamp|evidence_stored_name)[:16]`.

This is exactly how the chain-of-custody feature keys a transaction across separate runs.
**Edge notes should reuse this function** so a note keyed today still points at the same
transfer next week.

### 2.3 Frontend types

All shared interfaces live in one file:
`frontend/src/app/models/blockchain-forensics.models.ts` — `GraphNodeData`,
`GraphLinkData`, `NodeLinkGraphResponse`, `AnalyticsResponse`, `CaseSummary`, `Case`,
`EvidenceEntry`, `TransactionCustodyEntry`, `CustodyChain`, `ActivityLogEntry`, etc.
`GraphNodeData` and `GraphLinkData` both end with `[key: string]: unknown`, so extra
server-side fields flow through untyped.

### 2.4 Graph rendering (frontend)

- cytoscape + `cytoscape-fcose` + `cytoscape-layout-utilities`, registered once via
  `core/cytoscape-setup.ts::ensureCytoscapeExtensionsRegistered()`.
- **Each graph page builds its own cytoscape instance** from the node-link JSON. There is
  **no shared graph component**. The stated project convention (comment in
  `pathfinding.component.ts`) is *"look consistent, stay independent"* between analysis
  pages — `graph-visualization.component.ts` is used as a visual reference, never imported
  by the others.
- Pages with a cytoscape graph: `graph-visualization` (main), `taint-analysis`,
  `pathfinding`. Pages without: `behavioral-analysis` (heatmap), `dex-swap-analysis`
  (event list), `dashboard` (embeds `GraphVisualizationComponent`).
- **Layout / repositioning:** every render calls
  `cytoscape({ ..., layout: { name: 'fcose', randomize: true, animate: false, fit: true }})`.
  There is **no preset layout, no saved coordinates, no `node.lock()`, no `grabbable`
  handling, no `dragfree` listener, and no persistence of positions anywhere**. "Fit whole
  graph" = `cy.fit()`. Consequence: the layout is different on every load. This is the
  single biggest architectural gap for requirement #3 ("pin/fix nodes").
- **Overlay mechanism already exists** — the DEX-swap overlay in
  `graph-visualization.component.ts` (`loadDexSwapOverlay` → `renderSwapOverlay` →
  `buildSwapEdgeElements`, `toggleDexSwapOverlay`, and the `edge.swap-edge` /
  `edge.swap-*` cytoscape styles) adds/removes extra dashed edges **without re-running the
  layout**. This is a ready-made template for rendering manual off-chain links and for a
  "show/hide investigator layer" toggle.
- The **node inspector** is `graph-visualization.component.html` →
  `<aside class="node-inspector">` (a `<dl>` of node facts). `taint-analysis` additionally
  has an **edge details panel** (`selectedEdgeDetails`: `{ source, target, totalAmount,
  transactions: EdgeTransactionDetail[] }`, built by `buildEdgeDetails(edge)`), plus
  `cy.on('tap', 'edge', ...)`. `graph-visualization` currently has no edge click handler.

---

## 3. Existing analyses (relevant surface, and how they touch the graph)

| Analysis | Backend | Frontend | Custody-gated? | Notes |
|---|---|---|---|---|
| **Graph Analysis** | `GET /cases/{id}/graph` (plain, no risk colours), `POST /cases/{id}/analytics/run` (adds plugin colours) | `features/graph-visualization/` | `analytics/run` yes, plain graph no | plain graph auto-loads on case/evidence select; risk colouring needs the custody dialog |
| **Taint Analysis** | `POST /cases/{id}/analytics/run` → `analytics.taint_analysis` (plugin in `plugins/taint_analysis.py`) | `features/taint-analysis/` | yes | own cytoscape graph, seed picking, timeline scrubber, edge-details panel, signed PDF export |
| **Pathfinding** | `POST /cases/{id}/pathfinding` (BFS, `app/analytics/path_finding.py`) + legacy `POST /graph/path-finding` | `features/pathfinding/` | yes | own cytoscape graph, path highlight, signed PDF export |
| **Behavioral / Time-of-Day** | `GET /cases/{id}/behavioral-analysis` (`app/analytics/behavioral_analysis.py` + `timezone_heuristics.py`) | `features/behavioral-analysis/` | no (read-only) | heatmap, per-address, no graph |
| **DEX Swap** | `GET /cases/{id}/dex-swap-analysis` (passive) + `POST .../dex-swap-analysis/run` (deliberate) | `features/dex-swap-analysis/` + overlay in graph page | run: yes | heuristic; drawn as an overlay on the main graph |

All case-scoped analysis pages share the same page skeleton: read `activeCase` from
`AnalysisStateService.selectedCase$`, an evidence-file `<select>` (`?evidence=<stored_name>`
scopes any of the endpoints to one file, otherwise combined), then their own panel.

---

## 4. Cross-cutting infrastructure the new layer will lean on

### 4.1 `AnalysisStateService` (`core/services/analysis-state.service.ts`)

`BehaviorSubject`-based store, `providedIn: 'root'`. Streams: `upload$`, `graph$`,
`analytics$`, `selectedNode$`, `selectedCase$`, plus `*Snapshot` getters and
`ensureValidSelectedNode(nodes)`. This is the glue that lets the graph page and (say) the
report page agree on "the current case / current graph / selected node". The investigator
overlay should get a stream here too (`investigatorOverlay$`).

### 4.2 `ApiService` (`core/services/api.service.ts`)

One class, one method per endpoint, returns `Observable<T>`; base url from
`environment.apiUrl` (`http://localhost:8000`). New endpoints get new methods here.

### 4.3 Audit log — `app/evidence/audit_log.py::write_audit_log(...)`

Append-only `logs/audit_log.jsonl`. Called by essentially every state-changing route
(`case_created`, `analytics_run`, `path_finding`, `report_signed`, `custody_pdf_exported`,
…). It stores `case_name` next to `case_id` on purpose (so renames/deletes can't rewrite
history) and takes a free-form `details` dict. **Every investigator write should call
this** (`investigator_note_added`, `investigator_note_edited`, `investigator_link_added`,
`investigator_pin_set`, …) — it is the app-wide convention and it automatically flows into
the Activity Log page + the activity report.

### 4.4 Chain of custody — the closest existing precedent to "a separate layer"

`LANAC-DOKAZA.md` + `app/evidence/custody_log.py` / `custody_evidence_log.py` +
`app/api/routes/custody.py` + `features/custody-log/`. Properties worth copying wholesale:

- a **separate append-only JSONL store**, never written into `case.json` or the graph;
- keyed by `(case_id, tx_id)` / `(case_id, evidence_stored_name)`;
- each row **self-describes** its granularity with a stamped `"scope"` field;
- a **derived read model** folds the log into "current view" objects
  (`custody_chain_for_transaction`, `list_case_transactions`);
- its **own routes** under `/cases/{id}/custody/...`, its **own page** (`/lanac-dokaza`),
  its **own PDF exports**;
- tests isolate the store with `monkeypatch.setattr(module, '_..._path', lambda: tmp_path / '...')`
  (see `backend/tests/test_custody_log.py`, `test_report_registry.py`).

### 4.5 Reusable frontend pieces

| Piece | Path | Reuse for |
|---|---|---|
| `SignaturePadComponent` | `core/components/signature-pad/` | *if* a manual off-chain link needs a signed declaration |
| `CustodyAccessDialogComponent` | `features/custody-access-dialog/` | template for a modal with `@Input`/`@Output`, suggestions, declaration checkbox |
| DEX-swap overlay code path | `graph-visualization.component.ts` | add/remove manual-link edges + layer toggle without re-layout |
| node inspector `<aside class="node-inspector">` | `graph-visualization.component.html` | host the per-node notes + "pin" control |
| taint edge-details panel + `cy.on('tap','edge')` | `taint-analysis.component.ts` | pattern for an edge inspector on the main graph |
| evidence `<select>` + `activeCase` wiring | any case-scoped feature component | the new Case Management page skeleton |
| `_section_title` / `_draw_table` | `app/exports/service.py` | an "Investigator conclusions" section in the case PDF/CSV |
| server merges display fields into node-link JSON | `app/api/routes/graph.py::enrich_node_metadata` | precedent for attaching `investigator_*` fields to nodes/links |

---

## 5. Where the new functionality should be implemented

### 5.1 Backend

**New store** — one per-case, append-only event log, folded into a read model
(mirrors `custody_log.py`; append-only preferred over a rewritten JSON blob because the
forensic framing wants an immutable history of who concluded what and when, and because
flat-file rewrites race under concurrent editors):

- `app/investigator/case_notes.py` (or `app/services/case_notes.py`) — store + read model.
  - suggested file: `logs/investigator_log.jsonl` (app-wide, `case_id` on every row,
    `"scope"` ∈ `node_note | edge_note | pin | manual_link`) **or**
    `data/cases/<case_id>/investigator_log.jsonl` (per-case, matches `case.json` locality).
  - event kinds: `note_added`, `note_edited`, `note_deleted`, `pin_set`, `pin_cleared`,
    `manual_link_added`, `manual_link_deleted`.
  - derived read model: `investigator_overlay(case_id)` →
    ```
    {
      case_id,
      node_notes:   { "<address>":  [ Note, ... ] },
      edge_notes:   { "<tx_id or edgeKey>": [ Note, ... ] },
      pinned_nodes: { "<address>": { x?, y?, pinned_by, pinned_at } },
      manual_links: [ ManualLink, ... ]
    }
    ```
  - `Note` = `{ id, author, text, created_at, updated_at, deleted? }`.
  - `ManualLink` = `{ id, source_address, target_address, relationship_type, direction,
    rationale, evidence_ref?, created_by, created_at }` where `relationship_type` ∈
    `same_entity | controls | associated_with | off_chain_payment | other` and `direction`
    ∈ `directed | undirected`.
  - `tx_identity.transaction_id` reused verbatim for the edge-note key; a fallback
    `edgeKey = f"{source} {target}"` for notes attached to an aggregated edge rather
    than one transfer.

**New router** — `app/api/routes/investigator.py`, prefix `/cases/{case_id}/investigator`,
mounted in `api/router.py` under the plain `authenticated` dependency list (any analyst,
same as the custody router):

| Method + path | Purpose |
|---|---|
| `GET  /cases/{id}/investigator` | full overlay for the case |
| `POST /cases/{id}/investigator/nodes/{address}/notes` | add a node note |
| `PATCH/DELETE .../nodes/{address}/notes/{note_id}` | edit / delete (author or admin) |
| `POST .../edges/{edge_key}/notes` + `PATCH`/`DELETE` | edge notes |
| `PUT  /cases/{id}/investigator/pins/{address}` | pin (optional `{x,y}` body) |
| `DELETE /cases/{id}/investigator/pins/{address}` | unpin |
| `GET/POST /cases/{id}/investigator/manual-links`, `DELETE .../manual-links/{id}` | off-chain links |

Every write → `write_audit_log(action='investigator_*', case_id=..., user=...)`.

**Optional, additive** merges (keep namespaced, never overwrite plugin/on-chain attrs):

- `GET /cases/{id}/graph` and `POST /cases/{id}/analytics/run` may attach
  `payload['investigator']` (the whole overlay) and/or per-node
  `investigator_note_count`, and append manual links to `payload['links']` **only** with
  `data.investigator_manual_link === true` + no `amount`/`total_amount`.
  Preferred: return the overlay as its **own top-level block** and let the frontend merge,
  so the graph payload proper stays 100% on-chain-derived.
- `app/exports/service.py` — new "Investigator conclusions" section in the case PDF/CSV;
  in GraphML/GEXF, emit manual links as edges tagged `edge_kind="manual_offchain"` (and
  node notes as a `note_count` attribute) so downstream tools never treat them as
  transactions.

**Tests** — `backend/tests/test_case_notes.py` in the style of `test_custody_log.py`
(isolate the store path via `monkeypatch`).

### 5.2 Frontend

- **Models** (`models/blockchain-forensics.models.ts`): `InvestigatorNote`,
  `InvestigatorPin`, `ManualLink`, `CaseInvestigatorOverlay`.
- **`ApiService`**: `getInvestigatorOverlay`, `addNodeNote` / `updateNodeNote` /
  `deleteNodeNote`, `addEdgeNote` / …, `pinNode(caseId, address, pos?)` / `unpinNode`,
  `listManualLinks` / `addManualLink` / `deleteManualLink`.
- **`AnalysisStateService`**: `investigatorOverlay$` + `setInvestigatorOverlay(...)` +
  `refreshInvestigatorOverlay(caseId)`; refresh on `selectedCase$` change (same place the
  graph page already reloads on case change).
- **New feature area** `features/case-management/` (route `/case-management`, add to
  `app.routes.ts` + a nav link in `app.component.html`):
  - a "case notebook" listing every note / pin / manual link for the active case, with
    author + timestamps + edit/delete, grouped by target (address / transaction /
    off-chain link). Reuses the `activeCase` + evidence-`<select>` skeleton.
  - a form to create a manual off-chain link (two address inputs, relationship type,
    rationale, optional evidence reference).
- **Main graph page** (`features/graph-visualization/`) — the integration point:
  - node inspector: an "Istražiteljski zaključci / beleške" sub-panel (list + add box) and
    a **Pin / Otkači** toggle button, visually distinct (amber "conclusion" styling vs the
    blue on-chain `<dl>`).
  - add `cy.on('tap', 'edge')` + a small edge-details panel (borrow from taint page) so
    edge notes have a home.
  - **pins**: after `fcose` runs, apply saved `{x,y}` for pinned nodes, `node.lock()` +
    `.addClass('pinned')` (distinct badge), make nodes `grabbable`, and on `dragfree`
    call `pinNode(caseId, id, node.position())`. This is a real change to `renderGraph()`
    (from "always random" to "random for unpinned, fixed for pinned").
  - **manual links**: render via a new overlay method modelled on `renderSwapOverlay()` —
    `classes: 'manual-link'`, solid distinct colour, no amount label, arrow only when
    `direction === 'directed'`, label e.g. `OFF-CHAIN: same_entity`.
  - **layer toggle** + **legend entry** mirroring the DEX-swap overlay toggle
    ("Prikaži istražiteljski sloj").
- First pass targets the **main graph page only** (per the "stay independent" convention);
  taint / pathfinding graphs can adopt the overlay later.

---

## 6. Reusable components / endpoints (summary answer)

**Backend, reuse directly:**
`app/evidence/custody_log.py` (store + read-model template), `tx_identity.transaction_id`
(edge/tx key), `audit_log.write_audit_log` (log every write),
`case_management._read_json/_write_json/_case_dir` (if a per-case JSON file is chosen),
`api/deps.get_current_user` (author identity), `exports/service._section_title/_draw_table`
(report section), `api/routes/graph.enrich_node_metadata` (precedent for merging fields).

**Frontend, reuse directly:**
`AnalysisStateService` (add a stream), `ApiService` (add methods), the DEX-swap overlay
code path in `graph-visualization.component.ts` (manual links + toggle),
`<aside class="node-inspector">` markup (host notes/pin), taint page's
`cy.on('tap','edge')` + `selectedEdgeDetails` (edge inspector), the `activeCase` +
evidence-`<select>` skeleton (new page), `SignaturePadComponent` /
`CustodyAccessDialogComponent` (only if a signed declaration is wanted on manual links).

**Endpoints that already return what the overlay must line up against:**
`GET /cases/{id}` (evidence list), `GET /cases/{id}/graph` (node ids = address strings,
link `source`/`target`), `POST /cases/{id}/analytics/run` (same shape + plugin attrs).

---

## 7. Backend models / APIs that will probably be needed

- **New service/module** `case_notes` (append-only event log + `investigator_overlay()`
  read model) — no schema migration, just a new JSONL file + helpers.
- **New router** `investigator.py` under `/cases/{case_id}/investigator` (see §5.1 table),
  mounted with the standard authenticated dependency.
- **New audit-log actions**: `investigator_note_added` / `_edited` / `_deleted`,
  `investigator_pin_set` / `_cleared`, `investigator_manual_link_added` / `_deleted`.
- **New Pydantic request models** in that router: `NodeNoteRequest { text }`,
  `EdgeNoteRequest { text }`, `PinRequest { x?: float, y?: float }`,
  `ManualLinkRequest { source_address, target_address, relationship_type, direction,
  rationale, evidence_ref? }`.
- **Additive response fields** (optional): `investigator` block on the graph/analytics
  responses; `edge_kind="manual_offchain"` + `note_count` in GraphML/GEXF; an
  "Investigator conclusions" section in the case CSV/PDF.
- **New TS interfaces** + **`ApiService` methods** + **`AnalysisStateService` stream** as
  in §5.2.
- **New tests**: `backend/tests/test_case_notes.py` (store isolation via `monkeypatch`).

No changes required to: auth, the graph builder itself, the analytics plugins, the
existing custody logs, `report_registry` (unless conclusions are put into signed reports —
see risk #4).

---

## 8. Risks & conflicts with the current architecture

1. **No persisted graph layout / node positions.** Every render runs `fcose` with
   `randomize: true`; nothing is saved. "Pinning" is only meaningful if we also persist
   coordinates and change `renderGraph()` to seed saved positions + `lock()` pinned nodes
   and layout only the rest. If pins must be visible on the taint / pathfinding graphs
   too, that change has to be repeated in each (they deliberately don't share code).
   *Mitigation:* start with pins as a **boolean** ("keep this node visible / flagged as
   important") and treat stored `{x,y}` as optional; full "fixed coordinate" pinning is a
   follow-up once `renderGraph()` is reworked.

2. **Graph nodes/edges have no stable server key beyond the raw address string / derived
   tx id.** Address ids are **case-sensitive and un-normalised** everywhere
   (`path_finding`, `behavioral_analysis` do exact matches). Investigator keys MUST use
   the identical (non-)normalisation or they silently won't line up. Edge notes must key
   off `tx_identity.transaction_id`, with a documented fallback for notes on an aggregated
   edge.

3. **The graph is rebuilt from CSV per request and can be evidence-scoped.** An address or
   edge that a note/pin/link refers to may not be present in the currently displayed graph
   (different evidence filter, or evidence changed since the note was made). The overlay is
   **case-level**; the UI must (a) still list such notes on the Case Management page and
   (b) not crash the graph overlay when a referenced node is absent (the DEX overlay
   already guards this with a `nodeIds.has(...)` check — copy that).

4. **Separation must be enforced at every layer, not just visually.**
   - *Data:* separate store; never written into `case.json`, the DataFrame, or the graph
     builder.
   - *API:* separate endpoints; if merged into a graph response, a namespaced
     (`investigator_*`) additive block only.
   - *UI:* distinct colour + iconography + explicit "ISTRAŽITELJSKI SLOJ / ZAKLJUČAK"
     labelling, its own legend entry, toggleable off.
   - *Exports:* a clearly-headed separate section; manual links tagged as non-transaction
     edges in GraphML/GEXF.
   - *Signed reports:* investigator conclusions are **mutable**, the `report_registry`
     content hash assumes immutable inputs. If conclusions go into a signed PDF, either
     snapshot them into the hashed `content` at sign time, or keep them out and label them
     "as of export". Decide explicitly.

5. **Concurrency / multi-user.** Flat files, no locking. A rewritten per-case JSON blob
   can lose a concurrent editor's write (the existing `case.json` has the same weakness).
   An **append-only JSONL** with a fold-to-current read model tolerates concurrent appends
   far better — another reason to follow the custody-log shape rather than a single JSON
   object.

6. **Authorship / edit rights.** Custody pages are readable by everyone; activity log
   scopes non-admins to their own rows. Need a decision: recommend **anyone can read and
   add; edit/delete restricted to the note's author or an admin**; always stamp `author`
   and always `write_audit_log`.

7. **Closed cases.** `require_open_case` blocks new *evidence* on closed cases but not
   analysis. Decide whether investigator notes can be added to a closed case (reading must
   always work). Leaning: allow adding (an investigator's conclusions often land after a
   case is administratively closed), but surface the closed status in the UI.

8. **Manual-link semantics vs. on-chain edges.** On-chain edges mean "money moved,
   sender→recipient". An off-chain link ("same person", "known associate") is frequently
   **undirected** and carries no amount. The model needs `relationship_type` + `direction`
   + free-text `rationale` (+ optional `evidence_ref`), and the renderer must make it
   impossible to read as a value transfer (no amount, distinct style, arrow only if
   directed).

9. **`GraphNodeData` / `GraphLinkData` are open (`[key: string]: unknown`) and are spread
   verbatim into cytoscape `data`.** Any server-side field added to the node-link JSON
   flows straight into the graph elements. Safe, but name investigator fields
   deliberately (`investigator_note_count`, `investigator_manual_link`) to avoid colliding
   with plugin attributes.

10. **Nav bar is already ~13 links.** Adding "Case management" is fine but the header is
    getting crowded; consider grouping later (out of scope for this feature).

11. **Frontend has no unit-test culture in these feature components** (there's a
    `src/app/features/tests/` *page*, and a backend pytest suite, but the Angular
    components aren't covered by specs). New backend logic should ship with pytest in the
    `test_custody_log.py` style; frontend verification will be manual, matching the rest of
    the app.

12. **`data/report_registry.json` is currently open in the editor** — unrelated to this
    task. The investigator layer should not touch it unless decision #4 says conclusions
    go into signed reports.

---

## 9. Open decisions to confirm before implementation

- Store location: app-wide `logs/investigator_log.jsonl` vs per-case
  `data/cases/<id>/investigator_log.jsonl`. *(Leaning: per-case, matches `case.json`
  locality and makes case deletion clean up automatically.)*
- Pin = boolean flag first, or fixed `{x,y}` coordinates from day one.
- Do manual off-chain links require a signed declaration (`SignaturePadComponent`) like
  custody access / report export, or just a typed rationale?
- Do investigator conclusions appear in the exported case report / signed report, and if
  so are they part of the verification hash?
- Edit/delete rights: author-or-admin (recommended) vs. any analyst.
- Which graphs get the overlay in v1: main graph page only (recommended) vs. taint +
  pathfinding too.

---

*End of analysis entry. No source files were modified.*

---

## 10. Step 1 — investigation case container (backend data model)

Date: 2026-09-08. Scope of this step: **only** the backend container entity that will
later hold investigator notes, pinned nodes and off-chain address links. No frontend. No
notes / pins / links. No change to any Graph / Taint / Pathfinding / Behavioral / DEX
algorithm.

### 10.1 Design decisions taken

- **A new, independent entity — `InvestigationCase` — separate from the evidence `Case`.**
  The evidence `Case` (`app/services/case_management.py`) owns imported on-chain facts,
  currency validation, open/closed status, and the chain of custody. The investigation
  case owns investigator-generated interpretation. They are kept in separate code
  (`app/investigations/`) and separate storage (`data/investigations/`), which is the
  concrete form of the "keep conclusions separated from on-chain facts" requirement.
- **No link to an evidence `Case` in this step.** Notes / pins / links key off address
  strings and stable transaction ids, which exist independently of any particular graph
  render, so the container does not need an evidence-case foreign key yet. If a link is
  wanted later it is an additive nullable field, no migration.
- **Same conventions as the rest of the backend:** flat-file JSON (no DB), 12-char hex id
  (`uuid4().hex[:12]`), UTC ISO-8601 timestamps, an `index.json` + per-entity directory
  layout copied from `case_management.py`, a function-module service, Pydantic v2 request
  models, `FileNotFoundError`-based "not found" signalling mapped to HTTP 404 in the
  route, and an `write_audit_log(...)` call on every write.
- **Layered on purpose** (the task asked for models / repositories / services as distinct
  things): `models.py` (entities + validation) → `repository.py` (pure file I/O, dicts
  only) → `service.py` (ids, timestamps, index sync, model⇄dict) → `routes/investigations.py`
  (HTTP + audit log).
- **Audit logging included** because every state-changing route in this project writes to
  `logs/audit_log.jsonl`; omitting it would be the inconsistent choice. New actions:
  `investigation_case_created` / `_updated` / `_deleted`.

### 10.2 Files created

| File | Purpose |
|---|---|
| `backend/app/investigations/__init__.py` | new package marker (empty, like every other package `__init__` in this project) |
| `backend/app/investigations/models.py` | Pydantic v2 models: `InvestigationCase` (persisted entity), `InvestigationCaseCreate` and `InvestigationCaseUpdate` (request bodies, with validators that trim `name`/`description` and reject a blank `name`). Helpers `utc_now_iso()` and `new_investigation_id()`. |
| `backend/app/investigations/repository.py` | Storage layer. Reads/writes `data/investigations/index.json` and `data/investigations/<id>/investigation.json`; `load_index` / `save_index` (keeps newest-updated first), `read_record` / `write_record` / `delete_record` / `exists`. Pure dict I/O, no validation. |
| `backend/app/investigations/service.py` | Orchestration. `list_investigations`, `get_investigation`, `create_investigation`, `update_investigation` (partial, bumps `updated_at`, never touches `created_at`), `delete_investigation`. Converts between `InvestigationCase` models and stored dicts, and keeps the index in sync. Defines `InvestigationCaseNotFoundError(FileNotFoundError)`. |
| `backend/app/api/routes/investigations.py` | `APIRouter(prefix='/investigations')` with list / create / detail / update / delete. Each write also calls `write_audit_log`. `InvestigationCaseNotFoundError` → HTTP 404. |
| `backend/tests/test_investigation_management.py` | 11 tests over the service layer (create → unique id + equal timestamps, name trimming, blank-name rejection, get, list ordering, 404s, update advances only `updated_at`, description clearing, delete removes record + index entry). Isolated from the real store via `monkeypatch.setattr(repository, '_root', ...)`, same pattern as `test_report_registry.py`. |

### 10.3 Files modified

| File | Change | Why |
|---|---|---|
| `backend/app/paths.py` | added `INVESTIGATIONS_DIR = DATA_DIR / 'investigations'` next to `CASES_DIR` | central path constants are defined here for every other store; the investigator container gets its own tree so its data never shares a directory with imported on-chain evidence |
| `backend/app/api/router.py` | `import ... investigations_router`; `api_router.include_router(investigations_router, dependencies=authenticated)` right after the `cases_router` include | expose the new routes under `/api/v1`, with the same "any authenticated, non-blocked user" access the `cases` router uses |

Nothing else was touched. No analytics / graph / taint / pathfinding / behavioral / DEX
code, no existing routes, no existing models, no existing tests.

### 10.4 Current model / storage structure

**Entity — `InvestigationCase`** (`app/investigations/models.py`):

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | 12-char hex, generated (`uuid4().hex[:12]`), unique, stable — the anchor notes/pins/links will reference later |
| `name` | `str` | required, 1–200 chars, trimmed |
| `description` | `str \| null` | optional, ≤ 5000 chars, trimmed; blank ⇒ stored as `null` |
| `created_at` | `str` | UTC ISO-8601, set once at creation, never changed afterwards |
| `updated_at` | `str` | UTC ISO-8601, equals `created_at` at creation, advanced on every successful edit |

**Request models:** `InvestigationCaseCreate { name, description? }`,
`InvestigationCaseUpdate { name?, description? }` (partial — only fields present in the
body are applied; `description: ""` clears it).

**On disk** (no database — flat JSON, mirrors `app/services/case_management.py`):

```
data/investigations/
├── index.json                         { "investigations": [ {id,name,description,created_at,updated_at}, ... ] }
│                                        sorted by updated_at, newest first
└── <investigation_id>/
    └── investigation.json             the full InvestigationCase record
        (notes.jsonl / pinned_nodes.json / links.jsonl will be added in this
         same per-investigation directory in later steps)
```

`logs/audit_log.jsonl` (existing app-wide log) gains rows with
`action ∈ { investigation_case_created, investigation_case_updated, investigation_case_deleted }`,
each carrying `user` and `details.investigation_id`.

### 10.5 API endpoints introduced

All under `/api/v1`, all require a valid bearer token (mounted with the shared
`get_current_user` dependency), all JSON.

| Method & path | Body | Success | Errors | Notes |
|---|---|---|---|---|
| `GET /api/v1/investigations` | — | `200 { "investigations": [InvestigationCase, ...] }` | `401` | newest-updated first |
| `POST /api/v1/investigations` | `{ "name": str, "description"?: str }` | `200 InvestigationCase` | `401`, `422` (blank/too-long name) | audit: `investigation_case_created` |
| `GET /api/v1/investigations/{id}` | — | `200 InvestigationCase` | `401`, `404` | |
| `PATCH /api/v1/investigations/{id}` | `{ "name"?: str, "description"?: str }` | `200 InvestigationCase` | `401`, `404`, `422` | partial update; advances `updated_at` only; audit: `investigation_case_updated` |
| `DELETE /api/v1/investigations/{id}` | — | `204` no content | `401`, `404` | removes the record + index entry; audit: `investigation_case_deleted` |

### 10.6 Build / verification performed

- `python -c "from app.main import app; app.openapi()"` — app imports, schema builds; the
  five routes above appear under `/api/v1/investigations`.
- `pytest backend/tests/test_investigation_management.py` — **11 passed**.
- `pytest backend/tests` (whole suite) — **197 passed** (was 186; +11 new), 0 failures.
  The only warning is the pre-existing `datetime.utcnow()` deprecation in
  `graph_building.py`, untouched by this step.
- End-to-end HTTP smoke via `TestClient` (login → list → create → detail → patch → list →
  404 → 422 on blank name → 401 without token → delete → 404): all as expected, and the
  three audit-log actions were written.

### 10.7 Not done yet (as of step 1)

- Frontend (models, `ApiService` methods, `AnalysisStateService` stream, a Case Management
  page, graph integration).
- Investigator **notes** on addresses → done in step 3, see §11. Notes on
  transactions / edges still pending.
- **Pinned nodes**.
- Manual **off-chain links** between addresses.
- Optional: link an `InvestigationCase` to an evidence `Case`; a summary projection with
  child-collection counts; an "Investigator conclusions" section in the case exports.

---

## 11. Step 3 — investigator notes on blockchain addresses / nodes (backend)

Date: 2026-09-08. Scope: the backend entity/model, repository, service, API and validation
for a **textual investigator note attached to one address**. No frontend. No notes on
transactions/edges yet. No pinned nodes, no links. No change to any Graph / Taint /
Pathfinding / Behavioral / DEX code.

Example the feature covers:

> Address `0xABC…` — Note: *"Sumnja se da je ovo cold wallet. Čeka se dodatna provera."*

### 11.1 Design decisions taken

- **A note belongs to an investigation case (step 1), not to the evidence `Case`.** The
  "case ID" on a note is the `investigation_id`. Notes live only inside the investigator
  layer tree (`data/investigations/<investigation_id>/notes.json`) — never in
  `data/cases/`, never in the transaction graph, never in any analysis output. That is the
  concrete form of *"notes must not be mixed with blockchain facts"*.
- **The address is stored and matched VERBATIM** — surrounding whitespace trimmed, letter
  case preserved (never lower-cased). Graph node ids in this project are the raw address
  strings from the evidence and are compared case-sensitively
  (`app/analytics/path_finding.py`, `app/analytics/behavioral_analysis.py`), so a note
  only lines up with the node it is about if it keeps the exact same spelling. See §11.5.
- **The address on a note is immutable.** `PATCH` changes only `text`. Re-pointing a note
  at a different address would make its `created_at` / author meaningless as a record of
  one observation — delete and create a new one instead.
- **The creator is recorded (`author`), matching the existing architecture** — the custody
  log stores `user`, `report_registry` stores `analyst`, `case.json` stores `analyst`,
  the audit log stores `user`. `author` is set from the authenticated user, not from the
  request body, and is immutable. Edit/delete attribution is the activity log's job (same
  as everywhere else in this project — no record tracks its own editor).
- **Storage is a mutable JSON list per investigation**, edited in place — same shape as
  `case.json` / `users.json`. (The append-only-JSONL style used by the custody logs was
  considered — §4.4 / §8 risk 5 — but that fits immutable audit records; notes have
  first-class `update` and `delete` and a moving `updated_at`, so an in-place record is
  the honest model. The immutable who-did-what history is the `audit_log.jsonl` entries.)
- **Multiple notes per address are allowed** — a running commentary, newest first.
- **Same layering and conventions as step 1**: `notes_models.py` (entities + validators)
  → `notes_repository.py` (pure dict file I/O) → `notes_service.py` (ids, timestamps,
  parent-exists check) → `routes/investigation_notes.py` (HTTP + `write_audit_log`).
  `FileNotFoundError` subclasses signal "not found" and become HTTP 404 in the route.
- **Cascade delete for free**: notes sit inside the per-investigation directory, and step
  1's `delete_investigation` already `rmtree`s that directory — deleting an investigation
  removes its notes with it (covered by a test).

### 11.2 Files created

| File | Purpose |
|---|---|
| `backend/app/investigations/notes_models.py` | Pydantic v2 models: `InvestigatorNote` (persisted), `InvestigatorNoteCreate` (`address` + `text`), `InvestigatorNoteUpdate` (`text` only). Validators trim `address`/`text` and reject blank values; `address` case is never changed. Constants `ADDRESS_MAX_LENGTH = 256`, `NOTE_TEXT_MAX_LENGTH = 10_000`. Helpers `utc_now_iso()`, `new_note_id()`. |
| `backend/app/investigations/notes_repository.py` | Storage: one file `data/investigations/<investigation_id>/notes.json` shaped `{ "notes": [ … ] }`. `load_notes` / `save_notes`, pure dict I/O. Locates the file via the new public `repository.investigation_dir()` helper, so redirecting the investigations root in tests also redirects the notes. |
| `backend/app/investigations/notes_service.py` | Orchestration: `list_notes(investigation_id, address=None)`, `get_note`, `create_note(…, author=…)`, `update_note`, `delete_note`. Every call first runs step 1's `get_investigation(...)` (→ `InvestigationCaseNotFoundError` → 404). `create` stamps `created_at == updated_at`; `update` moves `updated_at` only and never touches `created_at` / `id` / `address` / `author`. Defines `InvestigatorNoteNotFoundError(FileNotFoundError)`. |
| `backend/app/api/routes/investigation_notes.py` | `APIRouter(prefix='/investigations/{investigation_id}/notes')` — list (with optional `?address=`), create, get-one, update, delete. Each write calls `write_audit_log` (`investigator_note_created` / `_updated` / `_deleted`, with `details = { investigation_id, note_id, address }`). Missing investigation **or** missing note → HTTP 404. |
| `backend/tests/test_investigator_notes.py` | 17 service-layer tests: create (id/author/case-id/timestamps), address trimmed but case preserved, blank text/address rejected, create against unknown investigation → 404-class error, filter by address, list-all, newest-first ordering, update advances only `updated_at`, update persists, update/delete unknown note → error, delete removes only that note, notes scoped per investigation, deleting the investigation removes its notes. Isolated via `monkeypatch.setattr(repository, '_root', …)` (one patch covers records + notes). |

### 11.3 Files modified

| File | Change | Why |
|---|---|---|
| `backend/app/investigations/repository.py` | added public `investigation_dir(investigation_id) -> Path` (thin wrapper over the existing private `_investigation_dir`) | give child-collection modules (notes now; pins/links later) one place to resolve the per-investigation directory instead of each re-deriving the layout; keeps test isolation to a single monkeypatch point |
| `backend/app/api/router.py` | `import … investigation_notes_router`; `include_router(investigation_notes_router, dependencies=authenticated)` right after the investigations router | expose the note routes under `/api/v1`, same "any authenticated user" access as the rest of the investigator layer |

Nothing else was touched. No analytics / graph / taint / pathfinding / behavioral / DEX
code; no existing routes, models or tests.

### 11.4 Model / storage structure

**Entity — `InvestigatorNote`** (`app/investigations/notes_models.py`):

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | 12-char hex, generated, unique within the investigation |
| `investigation_id` | `str` | the "case ID" — the step-1 `InvestigationCase` this note belongs to (never the evidence `Case`) |
| `address` | `str` | the address / graph-node identifier, 1–256 chars, whitespace-trimmed, **case preserved** |
| `text` | `str` | the observation, 1–10 000 chars, trimmed, non-blank |
| `author` | `str` | username of the creator; set server-side from the auth token; immutable |
| `created_at` | `str` | UTC ISO-8601, set once |
| `updated_at` | `str` | UTC ISO-8601, equals `created_at` on create, advances on every edit |

**Request models:** `InvestigatorNoteCreate { address, text }`,
`InvestigatorNoteUpdate { text }` (text required — it is the only editable field).

**On disk** (no database; mutable JSON, same style as `case.json`):

```
data/investigations/<investigation_id>/
├── investigation.json     (step 1)
└── notes.json             { "notes": [ { id, investigation_id, address, text,
                                          author, created_at, updated_at }, ... ] }
```

`logs/audit_log.jsonl` gains rows with
`action ∈ { investigator_note_created, investigator_note_updated, investigator_note_deleted }`,
each carrying `user` and `details = { investigation_id, note_id, address }`.

### 11.5 How a note is associated with an address

- A note carries the address as a **plain string** in its `address` field. There is no
  foreign key to a graph node, because the graph is rebuilt from evidence on every request
  and has no persistent node table (see §2.1).
- The association key is the pair **`(investigation_id, address)`**. `GET …/notes?address=X`
  returns every note whose `address` equals `X` exactly.
- Matching is **exact and case-sensitive**, after trimming surrounding whitespace only.
  This is deliberate: graph node ids are the raw address strings from the imported CSV and
  are compared without normalisation elsewhere in the codebase, so a note attaches to the
  right node only if it stores the identical spelling. A frontend that lets the
  investigator click a node will pass that node's id straight through.
- A note may reference an address that is **not currently in any graph view** (a different
  evidence-file filter, or evidence imported/removed since) — the note is independent of
  graph state and is still returned by its investigation + address.
- Notes are **never** written into the evidence case, the `networkx` graph, the node-link
  JSON, or any analysis result. They are read only through the
  `/investigations/{id}/notes` endpoints and stored only under `data/investigations/`.

### 11.6 API endpoints introduced

All under `/api/v1`, all require a valid bearer token, all JSON.

| Method & path | Body | Success | Errors | Notes |
|---|---|---|---|---|
| `GET /api/v1/investigations/{investigation_id}/notes` | — (optional `?address=<exact>`) | `200 { "investigation_id", "address", "notes": [InvestigatorNote, …] }` | `401`, `404` (investigation) | with `address`: only that address's notes; without: every note in the investigation. Newest-created first. |
| `POST /api/v1/investigations/{investigation_id}/notes` | `{ "address": str, "text": str }` | `200 InvestigatorNote` | `401`, `404` (investigation), `422` (blank/too-long `address` or `text`) | `author` taken from the token; `created_at == updated_at`. Audit: `investigator_note_created`. |
| `GET /api/v1/investigations/{investigation_id}/notes/{note_id}` | — | `200 InvestigatorNote` | `401`, `404` (investigation or note) | |
| `PATCH /api/v1/investigations/{investigation_id}/notes/{note_id}` | `{ "text": str }` | `200 InvestigatorNote` | `401`, `404`, `422` | changes `text` only; advances `updated_at`; `id`/`address`/`author`/`created_at` unchanged. Audit: `investigator_note_updated`. |
| `DELETE /api/v1/investigations/{investigation_id}/notes/{note_id}` | — | `204` no content | `401`, `404` | Audit: `investigator_note_deleted`. |

### 11.7 Build / verification performed

- `from app.main import app; app.openapi()` — app imports, schema builds; the five note
  routes appear under `/api/v1/investigations/{investigation_id}/notes`.
- `pytest backend/tests/test_investigator_notes.py` — **17 passed**.
- `pytest backend/tests` (whole suite) — **214 passed** (was 197; +17 new), 0 failures.
  Only the pre-existing `datetime.utcnow()` deprecation warning in `graph_building.py`
  (untouched).
- End-to-end HTTP smoke via `TestClient` (login → empty list → create with padded
  address/text → three notes across two addresses → filter by address → list all → patch →
  get one → `422` blank text → `404` unknown investigation → `404` unknown note → `401`
  no token → delete → `404` → delete investigation → notes now `404`): all as expected;
  the five audit actions were written.

### 11.8 Not done yet (next steps)

- Investigator notes on **transactions / edges** → done in step 4, see §12.
- **Pinned nodes** and manual **off-chain links** between addresses.
- Author-or-admin restriction on editing/deleting someone else's note (currently any
  authenticated user can; deferred decision from §8 risk 6).
- Frontend.

---

## 12. Step 4 — notes on transactions / edges (backend)

Date: 2026-09-08. Scope: extend the step-3 note feature so a note can target **either** an
address/node **or** a transaction/edge, using the project's **existing** transaction
identifier. No new identification mechanism. No frontend. No pinned nodes / links. No
change to any Graph / Taint / Pathfinding / Behavioral / DEX code.

Example the feature now also covers:

> Transaction `0x123…` — Note: *"Transfer appears to be related to the initial laundering stage."*

### 12.1 Design decisions taken

- **One note = one target, of one of two kinds.** A note is attached to exactly one of
  `address` or `tx_id`; the kind is recorded explicitly in a new `target_type` field
  (`"address"` | `"transaction"`). Enforced by a model validator ("exactly one target")
  and stored on every note, so the two kinds stay logically distinguishable even when the
  `notes.json` file is read on its own.
- **The transaction identifier is the project's existing `tx_id`** — *not* a new scheme.
  It is the same identity the chain of custody uses (`app/evidence/tx_identity.py::transaction_id`):
  the transaction hash when the evidence carries one, otherwise the deterministic
  `row-<sha256(sender|recipient|amount|timestamp|evidence)[:16]>` fallback. This layer
  never *computes* it (it has no transaction row) — it stores and matches whatever id
  string the caller passes, verbatim, exactly the way it already treats an `address`. The
  code does not import `tx_identity`; it only documents that `tx_id` means that.
- **Additive, backward compatible.** The step-3 wire contract still works unchanged:
  `POST …/notes { "address", "text" }` and `GET …/notes?address=…` behave exactly as
  before and yield `target_type: "address"`. The model field `address` changed from
  *required* to *optional* internally, but a body with `address` + `text` still validates.
- **The target is immutable.** `PATCH` still changes only `text`; `target_type` /
  `address` / `tx_id` cannot be edited (re-pointing a note would make its `created_at` /
  `author` meaningless — delete and recreate).
- **Explicit discriminator removes any ambiguity.** Filtering is scoped by `target_type`,
  so the same literal string used once as an address and once as a `tx_id` produces two
  independent notes that never cross over in retrieval (covered by a test).
- **Same layering / conventions** as steps 1 & 3. Storage shape unchanged (one mutable
  `notes.json` list per investigation) — the new keys are just more fields on each row.
- **Legacy tolerance (defensive only).** `notes_service._coerce_row` fills in
  `target_type` / `tx_id` for any note row written before this step; since there is no
  persisted step-3 data, this only matters in principle — an old row loads as the address
  note it always was.

### 12.2 Files modified

No new files. No new routes registered (the note routes already existed).

| File | Change | Purpose |
|---|---|---|
| `backend/app/investigations/notes_models.py` | `InvestigatorNote` gains `target_type: "address" \| "transaction"` and `tx_id: str \| None` (added next to `address`), a `model_validator` that keeps `target_type` consistent with which id is set, and a `target_id` convenience property. `InvestigatorNoteCreate` now takes `address?` **and** `tx_id?` (each trimmed, blank→None) with a `model_validator` requiring **exactly one**; it exposes a derived `target_type`. New `TX_ID_MAX_LENGTH = 256` and `NoteTargetType` alias. `InvestigatorNoteUpdate` unchanged (`text` only). | model the two note kinds with an explicit discriminator; validate "exactly one target" |
| `backend/app/investigations/notes_service.py` | `create_note` sets `target_type` / `address` / `tx_id` from the request. `list_notes` signature is now `list_notes(investigation_id, *, address=None, tx_id=None, target_type=None)` — filter by exact address (address notes only), by exact `tx_id` (transaction notes only), or by kind; none → all. Added `_coerce_row` for legacy rows; `_load_models` runs it. | retrieval by transaction/edge and by kind; keep the two kinds from crossing over |
| `backend/app/api/routes/investigation_notes.py` | `GET …/notes` gains `tx_id` and `target_type` query params; rejects **>1** filter with `400`, and an unknown `target_type` value with `400`; the response echoes `address` / `tx_id` / `target_type`. `POST` request-model change is automatic (Pydantic returns `422` for zero or two targets). Audit-log `details` for create/update/delete now carry `target_type`, `address` and `tx_id` (via a shared `_audit_details` helper). | expose retrieval-by-transaction; validate filter combinations; keep the activity log self-describing |
| `backend/tests/test_investigator_notes.py` | +8 tests: transaction-note create sets `target_type`/`tx_id`, `tx_id` trimmed & case-preserved, "exactly one target" rejection (neither / both), filter by `tx_id`, update+delete on a transaction note, same string as address vs `tx_id` stays distinct, filter by `target_type`, list-all returns both kinds each tagged. Existing address-note tests unchanged except one extra assertion (`target_type == 'address'`, `tx_id is None`). | lock in the new behaviour and the node/edge separation |

Nothing else touched — no analytics / graph / taint / pathfinding / behavioral / DEX code,
no other routes, `router.py` / `paths.py` / `repository.py` / step-1 files all unchanged.

### 12.3 How node notes and edge/transaction notes are represented

Both kinds are the **same `InvestigatorNote` record** in the **same store** (one
`data/investigations/<investigation_id>/notes.json` list). They differ only in three
fields:

| | node note | transaction / edge note |
|---|---|---|
| `target_type` | `"address"` | `"transaction"` |
| `address` | the graph-node id string | `null` |
| `tx_id` | `null` | the transaction id string (tx hash, or `row-…` fallback — the chain-of-custody identity) |

Every other field is identical in meaning: `id`, `investigation_id` (the "case ID"),
`text`, `author`, `created_at`, `updated_at`.

Full persisted shape:

```jsonc
// data/investigations/<investigation_id>/notes.json
{
  "notes": [
    { "id": "…", "investigation_id": "…", "target_type": "address",
      "address": "0xABC…", "tx_id": null,
      "text": "Suspected cold wallet, pending confirmation.",
      "author": "marko", "created_at": "…", "updated_at": "…" },

    { "id": "…", "investigation_id": "…", "target_type": "transaction",
      "address": null, "tx_id": "0x123…",
      "text": "Transfer appears related to the initial laundering stage.",
      "author": "marko", "created_at": "…", "updated_at": "…" }
  ]
}
```

**Association / matching** (extends §11.5):

- A node note is keyed by `(investigation_id, target_type="address", address)`; a
  transaction note by `(investigation_id, target_type="transaction", tx_id)`.
- `tx_id` is the project's existing transaction identity (see
  `app/evidence/tx_identity.py`), stored as an opaque string and matched **exactly**
  (whitespace-trimmed, case preserved) — identical treatment to `address`, and identical
  to how `custody_log` stores `tx_id`.
- Matching is always scoped to the matching `target_type`, so `?address=X` never returns a
  transaction note and `?tx_id=X` never returns a node note, even when `X` is the same
  string in both.
- Neither kind is ever written into the evidence case, the `networkx` graph, the node-link
  JSON, or any analysis result — read only via `/investigations/{id}/notes`, stored only
  under `data/investigations/`.

### 12.4 API — what changed

Same five endpoints as §11.6. Deltas:

| Endpoint | Change |
|---|---|
| `POST /api/v1/investigations/{id}/notes` | body is now `{ "address"?: str, "tx_id"?: str, "text": str }` — **exactly one** of `address` / `tx_id` (zero or both → `422`). Response `InvestigatorNote` now includes `target_type` and `tx_id`. `POST { address, text }` still works exactly as in step 3. |
| `GET /api/v1/investigations/{id}/notes` | new optional query params `tx_id` (exact transaction id) and `target_type` (`address` \| `transaction`); **at most one** of `address` / `tx_id` / `target_type` (more → `400`; bad `target_type` value → `400`). Response body now `{ investigation_id, address, tx_id, target_type, notes }`. |
| `GET/PATCH/DELETE …/notes/{note_id}` | unchanged behaviour; responses now carry `target_type` / `tx_id`. |
| audit log | `investigator_note_created` / `_updated` / `_deleted` `details` now `{ investigation_id, note_id, target_type, address, tx_id }`. |

Retrieval by transaction/edge:

```
GET /api/v1/investigations/{id}/notes?tx_id=0x123…      -> notes on that transaction
GET /api/v1/investigations/{id}/notes?target_type=transaction   -> every edge note
GET /api/v1/investigations/{id}/notes?target_type=address       -> every node note
GET /api/v1/investigations/{id}/notes                           -> all, each with target_type
```

### 12.5 Build / verification performed

- `from app.main import app; app.openapi()` — app imports; `GET …/notes` now advertises
  `address`, `tx_id`, `target_type` query params; `InvestigatorNoteCreate` props
  `[address, tx_id, text]`; `InvestigatorNote` props include `target_type`, `tx_id`.
- `pytest backend/tests/test_investigator_notes.py` — **25 passed** (17 → +8).
- `pytest backend/tests` (whole suite) — **222 passed** (was 214; +8 new), 0 failures.
  Only the pre-existing `datetime.utcnow()` deprecation warning in `graph_building.py`
  (untouched).
- End-to-end HTTP smoke via `TestClient`: create an address note and a transaction note
  (padded `tx_id` trimmed); retrieve by `address`, by `tx_id`, by `target_type`, and all;
  `422` for zero / two targets; `400` for two filters / bad `target_type`; `PATCH` a
  transaction note keeps `tx_id` + `target_type`, changes `text`; `DELETE` → `204`; audit
  rows carry `target_type`. All as expected.

### 12.6 Not done yet (as of step 4)

- **Pinned nodes** → done in step 5, see §13. Manual **off-chain links** between addresses
  still pending.
- Author-or-admin restriction on editing/deleting someone else's note (§8 risk 6).
- Optional: an aggregated-edge (`source→target` pair) note target, distinct from a single
  transaction — only the transaction-level `tx_id` is supported now, matching the chain of
  custody's granularity.
- Frontend for notes (a Case Management page; graph integration).

---

## 13. Step 5 — "Pin node" (frontend, reuses the fcose layout)

Date: 2026-09-08. First frontend work in this effort. Scope: let an investigator mark a
graph node as important and keep it **fixed in place when the graph is re-laid out**, from
the existing node details panel. **No backend, no new API, no persistence.** No change to
any Graph / Taint / Pathfinding / Behavioral / DEX algorithm.

### 13.1 Inspection — how graph layout & node positions work today

(Confirmed against `frontend/src/app/features/graph-visualization/graph-visualization.component.ts`,
unchanged since commit `fd20888`.)

- The graph page builds its cytoscape instance in `renderGraph()`, which **destroys and
  recreates `this.cy`** on every `graph$` emission — i.e. on case select, evidence-file
  switch, and "Analiziraj graf".
- Layout is **cytoscape-fcose** (`{ name: 'fcose', quality: 'default', randomize: true,
  animate: false, fit: true, … }`), passed as `… as any`. `cytoscape-fcose@2.2.0` and
  `cytoscape-layout-utilities` are already registered once via
  `core/cytoscape-setup.ts::ensureCytoscapeExtensionsRegistered()`.
- **Nothing about node positions is persisted anywhere.** `randomize: true` means every
  render produces a fresh, different arrangement. There was no `node.lock()`, no
  `grabbable` handling, no `dragfree`/`free` listener, and no `preset` layout.
- Nodes are draggable already (cytoscape's default `grabbable: true`); the app just never
  did anything with a dragged position.
- `applyVisibilityFilters()` (timeline / dead-end / funding-source filters) and
  `renderSwapOverlay()` (DEX overlay) mutate the existing `cy` **without** re-running the
  layout.
- **cytoscape-fcose natively supports fixed positions**: a `fixedNodeConstraint` layout
  option — `[{ nodeId, position: { x, y } }]` — keeps the listed nodes exactly where
  stated and arranges every other node around them. `cytoscape@3.34` also has
  `node.lock()` (immune to layout moves and to dragging) and node `underlay-*` styling.

**Conclusion:** the "stay fixed on re-layout" requirement is met entirely by fcose's own
`fixedNodeConstraint` + `node.lock()`. No new positioning system is introduced.

### 13.2 Design decisions

- **Reuse fcose, don't replace it.** On pin, the node's *current* position (wherever fcose
  put it, or wherever it was dragged) is captured into a component `Map`. Every subsequent
  `renderGraph()` feeds that map into fcose's `fixedNodeConstraint` **and** re-`lock()`s
  the node and re-adds a `.pinned` class. Non-pinned nodes are laid out by the same fcose
  call, exactly as before.
- **No re-layout on the pin click itself.** `togglePinSelectedNode()` only locks/unlocks
  and toggles the class + map entry on the live `cy`; the rest of the graph is left
  untouched. A re-layout only happens on the events that already caused one.
- **Non-pinned behaviour is byte-for-byte unchanged.** When the pin map is empty, the
  `fixedNodeConstraint` key is *not* added to the layout options at all, so the config
  object is identical to the pre-step-5 one (verified — see §13.5).
- **No backend persistence — deliberately.** The instruction was to persist "only if
  persistence is necessary in the current architecture". It is not:
  - Nothing about graph *presentation* is persisted in this project — fcose re-randomizes
    every load by design, and the graph itself is recomputed from evidence on every
    request. A component-held pin map is *consistent* with that.
  - The stated goal ("pinned nodes remain at their position when the graph is re-laid
    out") is fully delivered by fcose + a component `Map`: a pin survives every **in-page**
    re-layout (case switch, evidence switch, "Analiziraj graf", DEX overlay refresh) for
    as long as the `/graph` page stays open.
  - It does **not** survive navigating away from `/graph` or a full reload. Making it
    survive that would require the graph page to be scoped to an *investigation* (steps
    1–4) — i.e. an investigation picker on the graph page — which is exactly the "large
    new UI section" the task rules out. The investigator-layer container is where such a
    pin store would live (`data/investigations/<id>/pinned_nodes.json`) if that's wanted
    later; noted, not built.
- **Pin action lives in the existing node details panel**, as asked — one small
  `.pin-controls` row under the node's address `<h3>`, not a new section.
- **Visual indication** is additive-only, chosen so it never overrides an existing cue:
  on the canvas, `node.pinned` adds a **gold double border** + a soft **gold underlay
  glow** (`underlay-*` is unused by any other node rule; the cluster ring uses `outline-*`
  and the risk/blacklist cues use `background-color`/shape — all untouched). In the panel,
  a `📌 ZAKAČENO` badge + the toggle button's lit `.active` state. One legend row added.

### 13.3 Files changed

**No new source files. No backend changes. No API changes.**

| File | Change | Purpose |
|---|---|---|
| `frontend/src/app/features/graph-visualization/graph-visualization.component.ts` | New private `pinnedNodePositions: Map<string, {x,y}>`. New `get isSelectedNodePinned`, `get pinnedNodeCount`, `togglePinSelectedNode()`, `private reapplyPinnedNodes()`. In `renderGraph()`: build `fixedNodeConstraint` from the map (only for node ids present in the current graph), spread it into the fcose `layout` options **only when non-empty**, and call `reapplyPinnedNodes()` right after the new `cy` is built. New `node.pinned` cytoscape style rule (gold double border + gold underlay), placed just before `node:selected`. | the whole feature |
| `frontend/src/app/features/graph-visualization/graph-visualization.component.html` | In the node-details `<aside>`, a `.pin-controls` row after `<h3>`: a `📌 ZAKAČENO` badge (when pinned) and a `📌 Zakači čvor` / `📌 Otkači čvor` button bound to `togglePinSelectedNode()` / `isSelectedNodePinned`. One extra `.legend-item` for the pinned marker. | the pin/unpin action + visual/legend |
| `frontend/src/app/features/graph-visualization/graph-visualization.component.scss` | `.pin-controls`, `.pin-badge`, `.pin-toggle`, `.ghost-button.pin-toggle.active` (gold lit state), `.legend-swatch.pinned`. ~35 lines, all new selectors — nothing existing restyled. | styling for the above |
| `frontend/scripts/pin-node-behavior-check.mjs` *(new)* | Headless `node` smoke check (no `ng test` runner exists in this project — mirrors `backend/scripts/smoke_*.py`). Drives the real cytoscape + cytoscape-fcose exactly as `renderGraph()` does and asserts the pin behaviour (see §13.5). | reproducible verification |

### 13.4 Behaviour

- **Pin** (button in node details): `pinnedNodePositions.set(id, {…node.position()})`,
  `node.lock()`, `node.addClass('pinned')`. The panel shows `📌 ZAKAČENO`; the button
  becomes "Otkači čvor" and lit.
- **Unpin**: `pinnedNodePositions.delete(id)`, `node.unlock()`,
  `node.removeClass('pinned')`. The node rejoins normal layout on the next re-layout.
- **Re-layout** (case/evidence switch, "Analiziraj graf"): `renderGraph()` passes
  `fixedNodeConstraint: [{nodeId, position}, …]` for every still-present pinned node, so
  fcose places all other nodes around them; then `reapplyPinnedNodes()` snaps each pinned
  node exactly onto its stored position and re-locks it.
- A pin whose node is **not in the current graph** (hidden by the evidence filter) is kept
  in the map and re-applies if that node reappears.
- Pins are **per component instance**: leaving `/graph` (component destroyed) or reloading
  clears them. Documented limitation (§13.2).

### 13.5 Testing performed

- **Frontend build** — `ng build --configuration development` before and after: both
  succeed, **0 errors, 0 warnings**; bundle sizes unchanged.
- **Backend suite** — `pytest backend/tests` → **222 passed** (no backend files touched;
  run as a regression guard).
- **Graph / pin / re-layout behaviour** — `node frontend/scripts/pin-node-behavior-check.mjs`,
  run repeatedly, **all 12 checks pass**:
  - plain fcose layout gives finite, distinct positions for every node;
  - with nothing pinned: the layout config has **no** `fixedNodeConstraint` key, **no**
    node is locked, **no** node has `.pinned`, and repeated fresh layouts still vary
    (randomized re-layout intact);
  - a pinned node is at its pinned position after re-layout **to < 1e-6 px** (i.e. exactly),
    is `locked()`, and carries `.pinned`, while the other nodes are still laid out (finite
    positions, none collapsed onto the pin);
  - the pinned node stays exactly put across a **second, independent** re-layout;
  - after unpin: the node is no longer `locked()`, has no `.pinned`, and is placed by the
    layout again (tens–hundreds of px from the old pinned spot).
  - Note: on an 8-node graph fcose occasionally reproduces an identical arrangement
    between two runs — expected fcose determinism on a trivial graph, unrelated to
    pinning; the check accounts for it by looking at a spread of runs.

### 13.6 Not done yet (as of step 5)

- Manual **off-chain links** between addresses → done in step 6, see §14.
- Frontend for investigator **notes** and **links** (backend-only so far).
- If cross-navigation / cross-reload pin persistence is wanted: scope the graph page to an
  investigation and store pins under `data/investigations/<id>/pinned_nodes.json` via the
  investigator layer.
- Optional: let a pinned node be dragged to fine-tune its position (currently `lock()`
  disables dragging — re-position by unpin → drag → re-pin).

---

## 14. Step 6 — investigator links (suspected off-chain relations)

Date: 2026-09-08. Scope: backend model / repository / service / API / validation for a
manually recorded **suspected relation** between two blockchain addresses, based on
**off-chain** evidence. No frontend. **No change to the blockchain graph, its edges, or
any Graph / Taint / Pathfinding / Behavioral / DEX code.**

Example the feature records:

> Address A `0xABC…` — Address B `0xDEF…`
> Reason: *"IP address from server logs connects both addresses."*
> Evidence: *"Server log #42"* — Confidence: **High**

### 14.1 Design decisions

- **It is a *suspected relation*, never a proven fact.** Enforced structurally, not just
  in prose:
  - the entity is `InvestigatorLink`; the route is `…/links`; the audit actions are
    `investigator_link_*`; every list response carries a `disclaimer` string ("… suspected
    relation / investigator association … NIJE dokazana blockchain činjenica i NIJE grana
    transakcionog grafa") — the same "ship the disclaimer with the data" pattern the DEX
    swap and behavioral endpoints already use;
  - there is **no `relationship_type` enum**. The model deliberately does not offer values
    like `same_owner` / `same_person` / `proven` (which the earlier analysis §5.1 had
    sketched). The *nature* of the suspected relation is the investigator's own free-text
    `reason` / `evidence`. If they want to assert "same person", they write exactly that
    there — which the spec explicitly allows — and it is then plainly their statement, not
    a system label;
  - `confidence` is `Low` / `Medium` / `High` only — never "Certain" / "Proven".
- **Undirected association.** The spec: *directional only if the existing investigation
  model requires direction*. The step-1 `InvestigationCase` is a bare container and the
  notes (steps 3–4) attach to a single address or a single transaction — **nothing in the
  model consumes a from/to ordering**. So a link is undirected: the record carries an
  explicit `directed: false`, `source_address` / `target_address` are just "the two
  addresses" with no significance to their order, and retrieval by address matches
  **either** endpoint. (`directed` is a stored field, not settable via the API, so
  `links.json` is self-describing and a directed variant could be added later without a
  migration.)
- **Additional forensic layer, blockchain graph untouched.** Links live only in
  `data/investigations/<id>/links.json`. `links_repository.py` / `links_service.py` /
  `investigation_links.py` import **nothing** from `app.analytics` / graph code. Links are
  never merged into `build_transaction_graph`, the node-link JSON, `case_graph`, or the
  case exports.
- **Same conventions and layering as steps 1/3/4.** 12-hex id, UTC ISO timestamps,
  `author` from the auth token (immutable; edits attributed via the activity log),
  `updated_at` that advances on edit and never touches `created_at`, mutable per-file JSON
  list, `FileNotFoundError` subclass → HTTP 404, `write_audit_log` on every write, and the
  `models → repository → service → route` split. Retrieval identifiers (`source_address`,
  `target_address`, the `?address=` filter) are matched **exactly**, whitespace-trimmed,
  case preserved — identical to how notes treat `address`.
- **All create fields required.** `source_address`, `target_address`, `reason`,
  `evidence`, `confidence` — an off-chain association with no stated reason or evidence
  reference is not worth recording. `source_address == target_address` (after trim) is
  rejected (`422`).
- **Editable: `reason` / `evidence` / `confidence`** (refine wording, or raise/lower
  confidence as more off-chain evidence arrives). The two addresses are immutable —
  re-pointing a link makes a different association, so delete + create.
- **Duplicates allowed.** Two links between the same pair are legitimate (two independent
  pieces of off-chain evidence), so no dedupe / reverse-pair check.

### 14.2 Files created

| File | Purpose |
|---|---|
| `backend/app/investigations/links_models.py` | Pydantic v2 models. `InvestigatorLink` (persisted): `id`, `investigation_id`, `source_address`, `target_address`, `directed` (always `False`), `reason`, `evidence`, `confidence`, `author`, `created_at`, `updated_at`, plus an `involves(address)` helper for the undirected match. `InvestigatorLinkCreate` (all 5 fields required; validators trim & reject blank; a model validator rejects equal endpoints). `InvestigatorLinkUpdate` (`reason?` / `evidence?` / `confidence?`). `LinkConfidence = Literal['Low','Medium','High']`, `LINK_CONFIDENCE_VALUES`, `ADDRESS_MAX_LENGTH=256`, `REASON_MAX_LENGTH=5000`, `EVIDENCE_MAX_LENGTH=2000`. |
| `backend/app/investigations/links_repository.py` | Storage: one file `data/investigations/<id>/links.json` shaped `{ "links": [ … ] }`. `load_links` / `save_links`, pure dict I/O. Locates the file via `repository.investigation_dir()` (added in step 4), so redirecting the investigations root in tests also redirects the links. |
| `backend/app/investigations/links_service.py` | Orchestration: `list_links(investigation_id, *, address=None)`, `get_link`, `create_link(…, author=…)`, `update_link`, `delete_link`. Every call first runs step 1's `get_investigation(...)` (→ `InvestigationCaseNotFoundError` → 404). `create` stamps `created_at == updated_at`; `update` is partial (`exclude_unset`), moves `updated_at` only, and is a no-op on an empty body. Defines `InvestigatorLinkNotFoundError(FileNotFoundError)`. Imports nothing from analytics/graph. |
| `backend/app/api/routes/investigation_links.py` | `APIRouter(prefix='/investigations/{investigation_id}/links')` — list (with optional `?address=`), create, get-one, update, delete. List response carries a fixed `disclaimer`. Each write calls `write_audit_log` (`investigator_link_created` / `_updated` / `_deleted`, `details = { investigation_id, link_id, source_address, target_address, confidence }`). Missing investigation **or** missing link → HTTP 404; `confidence` outside Low/Medium/High or equal endpoints → `422` (Pydantic). |
| `backend/tests/test_investigator_links.py` | 20 service-layer tests: all minimum fields on create + `directed is False` + equal timestamps; addresses trimmed, case preserved; blank fields rejected; equal endpoints rejected; confidence must be exactly Low/Medium/High; create against unknown investigation → 404-class; **undirected retrieval — a link is found from either endpoint** and not from an unrelated address; list-all; newest-first ordering; update changes reason/evidence/confidence and advances only `updated_at`; empty update is a no-op; update/delete unknown link → error; delete removes only that link; links scoped per investigation; deleting the investigation removes its links; links stored in their own `links.json` (no `notes.json` created). Isolated via `monkeypatch.setattr(repository, '_root', …)`. |

### 14.3 Files modified

| File | Change | Why |
|---|---|---|
| `backend/app/api/router.py` | `import … investigation_links_router`; `include_router(investigation_links_router, dependencies=authenticated)` right after the notes router | expose the link routes under `/api/v1`, same "any authenticated user" access as the rest of the investigator layer |

Nothing else was touched. No analytics / graph / taint / pathfinding / behavioral / DEX
code; no other routes, models, or tests; `paths.py` / `repository.py` / step-1/3/4/5
files all unchanged.

### 14.4 Model / storage structure

**Entity — `InvestigatorLink`** (`app/investigations/links_models.py`):

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | 12-char hex, generated, unique within the investigation |
| `investigation_id` | `str` | the "case ID" — the step-1 `InvestigationCase` this link belongs to (never the evidence `Case`) |
| `source_address` | `str` | one of the two addresses, 1–256 chars, trimmed, **case preserved** — order carries no meaning |
| `target_address` | `str` | the other address, same rules; must differ from `source_address` |
| `directed` | `bool` | always `false` — an **undirected** investigator association (not settable via the API) |
| `reason` | `str` | free-text "why", 1–5000 chars, trimmed, non-blank |
| `evidence` | `str` | free-text off-chain evidence / reference, 1–2000 chars, trimmed, non-blank |
| `confidence` | `"Low" \| "Medium" \| "High"` | the investigator's confidence in the suspected relation |
| `author` | `str` | username of the creator; set server-side; immutable |
| `created_at` | `str` | UTC ISO-8601, set once |
| `updated_at` | `str` | UTC ISO-8601, equals `created_at` on create, advances on every edit |

**Request models:** `InvestigatorLinkCreate { source_address, target_address, reason,
evidence, confidence }` (all required); `InvestigatorLinkUpdate { reason?, evidence?,
confidence? }`.

**On disk** (no database; mutable JSON, same style as `case.json` / `notes.json`):

```
data/investigations/<investigation_id>/
├── investigation.json     (step 1)
├── notes.json             (steps 3–4)
└── links.json             { "links": [ { id, investigation_id, source_address,
                                          target_address, directed:false, reason,
                                          evidence, confidence, author,
                                          created_at, updated_at }, ... ] }
```

`logs/audit_log.jsonl` gains rows with
`action ∈ { investigator_link_created, investigator_link_updated, investigator_link_deleted }`,
each carrying `user` and `details = { investigation_id, link_id, source_address,
target_address, confidence }`.

### 14.5 How a link relates two addresses (and why it is not a graph edge)

- A link stores **two plain address strings** (`source_address`, `target_address`) and an
  explicit `directed: false`. There is no reference to a graph node or a graph edge — the
  transaction graph is recomputed from evidence on every request and has no persistent
  edge table (§2.1).
- The association key is the **unordered pair** `{source_address, target_address}` within
  one `investigation_id`. `GET …/links?address=X` returns every link where `X` equals
  `source_address` **or** `target_address` exactly (trimmed, case-sensitive) — you reach
  the association from either side.
- It is **not** an edge in `build_transaction_graph`'s `DiGraph`, not in the node-link
  JSON, not in `case_graph`, not in the GraphML/GEXF/CSV/PDF case exports. A separate
  endpoint set, a separate file, a separate subsystem with no import of graph code. A
  frontend that draws these later must render them as a visually distinct overlay
  (dashed / labelled "investigator link", no amount, no arrow unless `directed`), never
  as a transaction edge — noted for the frontend step.
- A link may reference an address **not present in any current graph view** (different
  evidence filter, or evidence not imported) — it is independent of graph state.
- Deleting the investigation cascades (its directory is `rmtree`-d), removing its links.

### 14.6 API endpoints introduced

All under `/api/v1`, all require a valid bearer token, all JSON.

| Method & path | Body | Success | Errors | Notes |
|---|---|---|---|---|
| `GET /api/v1/investigations/{id}/links` | — (optional `?address=<exact>`) | `200 { investigation_id, address, disclaimer, links: [InvestigatorLink, …] }` | `401`, `404` (investigation) | with `address`: links where it is **either** endpoint (undirected); without: every link. Newest-created first. |
| `POST /api/v1/investigations/{id}/links` | `{ source_address, target_address, reason, evidence, confidence }` | `200 InvestigatorLink` | `401`, `404` (investigation), `422` (blank/too-long field, equal endpoints, `confidence` not Low/Medium/High) | `author` from the token; `directed:false`; `created_at == updated_at`. Audit: `investigator_link_created`. |
| `GET /api/v1/investigations/{id}/links/{link_id}` | — | `200 InvestigatorLink` | `401`, `404` (investigation or link) | |
| `PATCH /api/v1/investigations/{id}/links/{link_id}` | `{ reason?, evidence?, confidence? }` | `200 InvestigatorLink` | `401`, `404`, `422` | edits the given fields only; advances `updated_at`; `id`/addresses/`author`/`created_at`/`directed` unchanged; empty body → unchanged. Audit: `investigator_link_updated`. |
| `DELETE /api/v1/investigations/{id}/links/{link_id}` | — | `204` no content | `401`, `404` | Audit: `investigator_link_deleted`. |

### 14.7 Build / verification performed

- `from app.main import app; app.openapi()` — app imports; the five link routes appear
  under `/api/v1/investigations/{investigation_id}/links`; `InvestigatorLinkCreate`
  requires all 5 fields; `confidence` is the enum `["Low","Medium","High"]`;
  `InvestigatorLink` carries `directed`.
- `pytest backend/tests/test_investigator_links.py` — **20 passed**.
- `pytest backend/tests` (whole suite) — **242 passed** (was 222; +20 new), 0 failures.
  Only the pre-existing `datetime.utcnow()` deprecation warning in `graph_building.py`
  (untouched).
- End-to-end HTTP smoke via `TestClient`, using the spec's example
  (`0xABC`/`0xDEF`, "IP address from server logs…", "Server log #42", High): create (padded
  address trimmed, `directed:false`); **retrieve by address A and by address B — both
  return the link** (undirected), unrelated address returns none; list response contains
  the `disclaimer`; `PATCH` confidence `High → Medium` + reason, addresses unchanged,
  `updated_at` advanced; `422` for equal endpoints / blank reason / missing evidence / bad
  confidence; `404` for unknown investigation / unknown link; `401` without a token;
  `DELETE` → `204` → `404`; deleting the investigation → links `404`; the three audit
  actions were written.

### 14.8 Not done yet (next steps)

- Frontend for investigator **notes** (steps 3–4) and **links** (step 6) — a Case
  Management view, and a distinct, clearly-labelled overlay on the graph for links
  (never drawn as a transaction edge).
- Author-or-admin restriction on editing/deleting another investigator's note/link
  (§8 risk 6).
- Optional: link investigator conclusions into the case report export as a separate,
  clearly-headed "Investigator conclusions (not blockchain facts)" section.
