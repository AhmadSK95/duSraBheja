"""API route registration."""

from fastapi import APIRouter

from src.api.routes.brain import router as brain_router
from src.api.routes.dashboard import api_router as dashboard_api_router
from src.api.routes.dashboard import router as dashboard_router

router = APIRouter()
router.include_router(dashboard_router)
router.include_router(brain_router)
router.include_router(dashboard_api_router)
