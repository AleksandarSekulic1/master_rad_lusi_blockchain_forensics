from fastapi import APIRouter, Depends

from app.api.deps import get_current_user, require_admin
from app.api.routes.activity_log import router as activity_log_router
from app.api.routes.addresses import router as addresses_router
from app.api.routes.analytics import router as analytics_router
from app.api.routes.auth import router as auth_router
from app.api.routes.cases import router as cases_router
from app.api.routes.custody import router as custody_router
from app.api.routes.exports import router as exports_router
from app.api.routes.graph import router as graph_router
from app.api.routes.investigation_links import router as investigation_links_router
from app.api.routes.investigation_notes import router as investigation_notes_router
from app.api.routes.investigations import router as investigations_router
from app.features.investigation_pins.router import router as investigation_pins_router
from app.api.routes.onchain import router as onchain_router
from app.api.routes.reports import router as reports_router
from app.api.routes.tests import router as tests_router
from app.api.routes.upload import router as upload_router
from app.api.routes.users import router as users_router


api_router = APIRouter()

# Public: login and password-reset must work without a token.
api_router.include_router(auth_router)

# Everything else requires an authenticated, non-blocked user.
authenticated = [Depends(get_current_user)]
api_router.include_router(analytics_router, dependencies=authenticated)
api_router.include_router(graph_router, dependencies=authenticated)
api_router.include_router(upload_router, dependencies=authenticated)
api_router.include_router(cases_router, dependencies=authenticated)
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
