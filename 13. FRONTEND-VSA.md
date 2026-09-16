# Prelazak frontend-a na Vertical Slice arhitekturu

Isti princip kao `VSA-REFAKTORING.md` (backend), primenjen na Angular frontend: umesto
nekoliko "god fajlova" koje deli cela aplikacija, svaki `features/<naziv>/` slice sad ima
**svoju** HTTP-servisnu klasu i **svoje** tipove, a samo istinski međusobno deljeno ostaje
u `core/` (shared kernel).

## Dijagnoza (stanje pre)

| Fajl | Veličina | Problem |
|---|---|---|
| `core/services/api.service.ts` | 652 linija, 58+ metoda | Jedan fasad za CEO REST API (taint, pathfinding, dex-swap, token-approval, cases, auth, users, custody, exports...) — ista bolest kao stari `app/api/routes/cases.py` na backend-u pre njegovog VSA refaktoringa |
| `models/blockchain-forensics.models.ts` | 1112 linija, 102 tipa | Jedan fajl sa svim tipovima cele aplikacije |
| `core/services/analysis-state.service.ts` | raste bez granice | Jedan globalni state servis kojem svaka nova funkcionalnost dodaje sopstvenu, nepovezanu odgovornost |

Ovaj refaktoring rešava prva dva reda (servis + tipovi). Treći (state servis) i "god
komponente" (`taint-analysis.component.ts` — 3094 linije, `graph-visualization.component.ts`
— 2481, `token-approval.component.ts` — 2225) su namerno **ostavljeni za kasnije** — vidi
odeljak "Šta NIJE urađeno" na dnu.

## Ciljna struktura

```
src/app/
├── core/                              — shared kernel (deljena infrastruktura)
│   ├── models/
│   │   └── shared.models.ts           — 45 tipova koje koristi 2+ feature-a
│   ├── services/
│   │   ├── case-data.api.ts           — CaseDataApiService, 11 metoda koje deli 2+ feature-a
│   │   ├── analysis-state.service.ts  — reaktivni state (BehaviorSubject), nepromenjeno
│   │   ├── auth.service.ts            — nepromenjeno
│   │   └── settings.service.ts        — nepromenjeno
│   ├── guards/, interceptors/, components/  — nepromenjeno
│   └── cytoscape-setup.ts             — nepromenjeno
│
└── features/<naziv>/                  — svaki slice vlasnik svog dela
    ├── <naziv>.component.ts/html/scss — nepromenjeno (logika UNUTRA nije dirana)
    ├── <naziv>.api.ts                 — NOVO: <Naziv>ApiService, samo HTTP pozivi koje TAJ slice koristi
    └── <naziv>.models.ts              — NOVO: samo tipovi koje TAJ slice koristi (ako ih ima)
```

**Pravilo razdvajanja (i za metode i za tipove):** korišćeno u 2+ feature-a → `core/`
(shared kernel). Korišćeno u tačno jednom → tamo, u sopstvenom `<naziv>.api.ts` /
`<naziv>.models.ts`. Isti princip kao `app/shared/` + `app/services/` na backend-u.

## Šta je gde (finalna mapa)

### `core/services/case-data.api.ts` — `CaseDataApiService` (deljeno, 11 metoda)

`getCase`, `getCaseGraph`, `listCases`, `runCaseAnalytics`, `registerReport`,
`enrichAddress`, `getDexSwapAnalysis`, `getTokenApprovalCorrelation`, `getInvestigatorNotes`,
`getInvestigatorLinks`, `getInvestigatorPins` — svaka od ovih se koristi na **3 do 8**
različitih stranica (npr. `getCaseGraph` čita 8 različitih feature-a), pa ne pripada
nijednom pojedinačnom slice-u.

### `core/models/shared.models.ts` (deljeno, 45 tipova)

Isti princip: `GraphNodeData`, `NodeLinkGraphResponse`, `CaseSummary`, `Case`,
`EvidenceEntry`, `TaintAnalysisResult`, `TransactionCustodyEntry`, ceo investigator-layer
klaster (`InvestigatorLink*`, `InvestigatorNote*`, `PinnedNode*`, `Investigation`), auth
klaster (`AuthUser`, `LoginRequest/Response`, `UserRole/Status`), `AnalyticsResponse`,
`UploadCsvResponse` (treba i `AnalysisStateService`-u), `DexSwapAnalysisResult` +
`TokenApprovalCorrelationResult` (treba shared metodama iznad da vrate nešto), itd.

### Po feature-u — sopstveni `<naziv>.api.ts` (17 slice-ova, 54 metode ukupno)

| Feature | Klasa | Metoda |
|---|---|---|
| dashboard | DashboardApiService | uploadCsv, fetchOnchainTransactions, fetchBitcoinTransactions |
| cases | CasesApiService | createCase, deleteCase, removeCaseEvidence, setCaseStatus |
| taint-analysis | TaintAnalysisApiService | getKnownEntities, getSeedSuggestions |
| pathfinding | PathfindingApiService | findCasePath |
| case-graph-search-dialog | CaseGraphSearchDialogApiService | getCaseGraphNeighborhood |
| behavioral-analysis | BehavioralAnalysisApiService | runBehavioralAnalysis |
| dex-swap-analysis | DexSwapAnalysisApiService | runDexSwapAnalysis |
| token-approval | TokenApprovalApiService | runTokenApprovalAnalysis |
| admin | AdminApiService | listUsers, createUser, setUserStatus, generateResetLink, renameUser, deleteUser |
| activity-log | ActivityLogApiService | getActivityReportPreview, downloadActivityReportCsv, signActivityReportPdf, getActivityLog (+ `ActivityReportOptions`) |
| tests | TestsApiService | listSuiteTests, runSuite, listScenarios, createScenario, updateScenario, deleteScenario, runScenarios |
| report-verification | ReportVerificationApiService | verifyReport |
| custody-log | CustodyLogApiService | getCustodyTransactions, getCustodyChain, exportCustodyPdf, getCustodyEvidenceList, getCustodyEvidenceChain, exportCustodyEvidencePdf |
| custody-access-dialog | CustodyAccessDialogApiService | getCustodySuggestions |
| report-export | ReportExportApiService | getCaseReportContext, exportCaseGraphml, exportCaseGexf, exportCaseTransactionsCsv |
| graph-visualization | GraphVisualizationApiService | listInvestigations, createInvestigation, deleteInvestigation, deleteInvestigatorLink, pinInvestigatorNode, unpinInvestigatorNode |
| investigator-node-dialog | InvestigatorNodeDialogApiService | addInvestigatorLink, addInvestigatorNote, updateInvestigatorNote, deleteInvestigatorNote |

### Uklonjen mrtvi kod

4 metode i 5 tipova nisu imali nijedan poziv nigde u frontend-u (stari standalone
`/graph/path-finding` endpoint, zamenjen case-scoped `findCasePath`-om; passivni
`getBehavioralAnalysis`, zamenjen `runBehavioralAnalysis`-om; neiskorišćeni
`exportCaseReportCsv/Pdf`): `findPaths`, `exportCaseReportCsv`, `exportCaseReportPdf`,
`getBehavioralAnalysis`, `AnalyticsRequest`, `PathFindingRequest`, `PathSummary`,
`PathFindingResponse`, `GraphSearchResult`.

## Kako je urađeno (za tehnički deo rada)

Ovo NIJE rađeno ručnim prekucavanjem 652+1112 linija — previše rizično za grešku pri tom
obimu. Napisan je jednokratan Python migracioni skript (obrisan posle upotrebe) koji je:

1. Parsirao originalne fajlove po tačnim granicama svake deklaracije (brace-matching za
   metode/interfejse — obično brojanje `{`/`}` po celom redu je pogrešno kad signatura sama
   sadrži inline object tip pre stvarnog tela, npr. `Observable<{ cases: CaseSummary[] }> {`
   — rešeno oslanjanjem na to da ovaj kod dosledno formatira zatvarajuću vitičastu zagradu
   metode kao samostalan red `  }`).
2. **Validirao rekonstrukciju** — spojio sve izvučene blokove nazad i uporedio sa
   originalnim fajlom red-po-red pre nego što je ijedan novi fajl upisan; ovo je uhvatilo
   dva realna bug-a (dupliran komentar preuzet i od prethodnog i od sledećeg tipa; "section
   header" komentar koji nije bio vezan ni za jedan tip pojedinačno) pre nego što bi
   nezapaženi ušli u finalni kod.
3. Generisao svaki novi `.api.ts`/`.models.ts` sa tačnim import-ima (skeniranjem tela za
   imena tipova koja stvarno postoje u shared kernel-u).
4. Automatski prepisao svih 17 komponenata: `import { ApiService }` → import po servisu koji
   joj treba, `constructor(private readonly api: ApiService)` → jedan parametar po servisu,
   `this.api.metoda(` → `this.<servis>.metoda(`.

**Pronađene i ispravljene greške tokom migracije** (dokaz da je validacija na svakom koraku
bila neophodna, ne kozmetička): pogrešno mapiran `addInvestigatorLink` (trebalo
`investigator-node-dialog`, prvobitno stavljen pod `graph-visualization`); pogrešna putanja
do `environments/environment` (sibling od `src/app/`, ne unutar njega); nekoliko poziva
oblika `this.api\n  .metoda(...)` (prelom u novi red) koje regex za "poziv na istom redu"
nije uhvatio.

**Status:** `npm run build` (produkcioni build, pun TypeScript type-check) prolazi čisto —
samo unapred postojeća CommonJS upozorenja iz `canvg`/`jspdf`/`cytoscape` zavisnosti,
nepovezana sa ovom izmenom. Dev server (`ng serve`, watch mode) kompajlira čisto. Ponašanje
aplikacije nepromenjeno — svaka komponenta i dalje zove iste HTTP endpoint-e na isti način,
samo kroz drugačije organizovane klase.

## Šta NIJE urađeno (namerno, sledeća faza)

- **"God komponente"** — `taint-analysis.component.ts` (3094 linije), `graph-visualization.component.ts`
  (2481), `token-approval.component.ts` (2225) i dalje mešaju rendering + PDF generisanje +
  poslovnu logiku u jednoj klasi. Razbijanje na manje pod-komponente + izdvojene servise
  (npr. `taint-pdf-report.service.ts`) je veći, rizičniji zahvat koji menja unutrašnje
  ponašanje komponenti, ne samo njihovu organizaciju — namerno odvojen od ovog, čisto
  strukturnog koraka.
- **`AnalysisStateService`** ostaje jedan globalni state servis (upload rezultat, graf,
  analitika, izabran čvor, pending seed-ovi za 2 stranice, graph image/svg provideri) — mali
  danas (172 linije), ali kandidat da postane sledeći "god fajl" kako aplikacija raste.
