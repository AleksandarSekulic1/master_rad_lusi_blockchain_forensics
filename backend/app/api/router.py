from fastapi import APIRouter, Depends

from app.api.deps import get_current_user, require_admin
from app.api.routes.activity_log import router as activity_log_router
from app.api.routes.addresses import router as addresses_router
from app.api.routes.analytics import router as analytics_router
from app.api.routes.auth import router as auth_router
from app.api.routes.cases import router as cases_router
from app.api.routes.exports import router as exports_router
from app.api.routes.graph import router as graph_router
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
