from fastapi import APIRouter

from app.api.routes.analytics import router as analytics_router
from app.api.routes.cases import router as cases_router
from app.api.routes.exports import router as exports_router
from app.api.routes.graph import router as graph_router
from app.api.routes.upload import router as upload_router


api_router = APIRouter()
api_router.include_router(analytics_router)
api_router.include_router(graph_router)
api_router.include_router(upload_router)
api_router.include_router(cases_router)
api_router.include_router(exports_router)
