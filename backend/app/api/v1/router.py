from fastapi import APIRouter

from app.api.v1.routes.analytics import router as analytics_router
from app.api.v1.routes.cases import router as cases_router
from app.api.v1.routes.exports import router as exports_router
from app.api.v1.routes.graph import router as graph_router
from app.api.v1.routes.upload import router as upload_router


api_router = APIRouter()
api_router.include_router(analytics_router)
api_router.include_router(graph_router)
api_router.include_router(upload_router)
api_router.include_router(cases_router)
api_router.include_router(exports_router)
