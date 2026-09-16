from fastapi import APIRouter, Depends

from app.api.deps import get_current_user, require_admin
from app.features.activity_log.router import router as activity_log_router
from app.features.addresses.router import router as addresses_router
from app.features.analytics.router import router as analytics_router
from app.features.auth.router import router as auth_router
from app.features.case_analytics_run.router import router as case_analytics_run_router
from app.features.case_behavioral_analysis.router import router as case_behavioral_analysis_router
from app.features.case_dex_swap_analysis.router import router as case_dex_swap_analysis_router
from app.features.case_graph.router import router as case_graph_router
from app.features.case_graph_search.router import router as case_graph_search_router
from app.features.case_management.router import router as case_management_router
from app.features.case_pathfinding.router import router as case_pathfinding_router
from app.features.case_seed_suggestion.router import router as case_seed_suggestion_router
from app.features.case_token_approval_analysis.router import router as case_token_approval_analysis_router
from app.features.custody.router import router as custody_router
from app.features.exports.router import router as exports_router
from app.features.graph.router import router as graph_router
from app.features.investigation_links.router import router as investigation_links_router
from app.features.investigation_management.router import router as investigations_router
from app.features.investigation_notes.router import router as investigation_notes_router
from app.features.investigation_pins.router import router as investigation_pins_router
from app.features.onchain.router import router as onchain_router
from app.features.reports.router import router as reports_router
from app.features.test_suite.router import router as tests_router
from app.features.upload.router import router as upload_router
from app.features.users.router import router as users_router


api_router = APIRouter()

# Public: login and password-reset must work without a token.
api_router.include_router(auth_router)

# Everything else requires an authenticated, non-blocked user.
authenticated = [Depends(get_current_user)]
api_router.include_router(analytics_router, dependencies=authenticated)
api_router.include_router(graph_router, dependencies=authenticated)
api_router.include_router(upload_router, dependencies=authenticated)
# The `Case` (evidence container) feature, split into one slice per capability rather than
# one 1000+ line router - see VSA-REFAKTORING.md. All eight share the '/cases' prefix and
# only ever READ a case through app.shared.case_access, so mounting them together here is
# equivalent to the single router this used to be.
api_router.include_router(case_management_router, dependencies=authenticated)
api_router.include_router(case_graph_router, dependencies=authenticated)
# Napredna pretraga preko Neo4j-a (pilot, opciono - vidi PREDLOG-GRAF-SUBP.md). Vraća 503
# ako Neo4j nije pokrenut; ne utiče ni na jednu drugu rutu.
api_router.include_router(case_graph_search_router, dependencies=authenticated)
api_router.include_router(case_behavioral_analysis_router, dependencies=authenticated)
api_router.include_router(case_dex_swap_analysis_router, dependencies=authenticated)
api_router.include_router(case_token_approval_analysis_router, dependencies=authenticated)
api_router.include_router(case_seed_suggestion_router, dependencies=authenticated)
api_router.include_router(case_analytics_run_router, dependencies=authenticated)
api_router.include_router(case_pathfinding_router, dependencies=authenticated)
# Investigator layer container (pinned nodes / off-chain links attach here later too).
# Separate entity from the evidence Case above; same "any authenticated user" access.
api_router.include_router(investigations_router, dependencies=authenticated)
# Investigator notes on addresses/nodes - observations, kept out of the transaction graph.
api_router.include_router(investigation_notes_router, dependencies=authenticated)
# Investigator links - suspected off-chain relations between two addresses. A separate
# forensic layer; the blockchain graph edges are never touched.
api_router.include_router(investigation_links_router, dependencies=authenticated)
# Pinned nodes - addresses the investigator fixed on the graph; persisted per investigation.
api_router.include_router(investigation_pins_router, dependencies=authenticated)
# Chain of custody per transaction - readable by any logged-in user (analyst or admin),
# same as the case data it describes access to.
api_router.include_router(custody_router, dependencies=authenticated)
api_router.include_router(exports_router, dependencies=authenticated)
api_router.include_router(onchain_router, dependencies=authenticated)
api_router.include_router(addresses_router, dependencies=authenticated)
# Readable by any logged-in user, but the route itself narrows non-admins to their own
# entries (see activity_log.get_activity_log) rather than relying on an admin-only gate.
api_router.include_router(activity_log_router, dependencies=authenticated)
api_router.include_router(reports_router, dependencies=authenticated)

# User administration is admin-only.
api_router.include_router(users_router, dependencies=[Depends(require_admin)])
# So is the correctness test suite - each route also declares require_admin itself, since
# it needs the caller's identity for the audit log anyway.
api_router.include_router(tests_router, dependencies=[Depends(require_admin)])
