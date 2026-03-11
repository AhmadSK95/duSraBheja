"""API route registration."""

from fastapi import APIRouter

from src.api.routes.brain import router as brain_router

router = APIRouter()
router.include_router(brain_router)
