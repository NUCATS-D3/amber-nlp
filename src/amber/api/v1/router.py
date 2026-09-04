"""API v1 router composition."""

from fastapi import APIRouter

from amber.api.v1.routers import admin, annotation, extraction

router = APIRouter()
router.include_router(admin.router, prefix="/admin", tags=["admin"])
router.include_router(extraction.router, prefix="/extraction", tags=["extraction"])
router.include_router(annotation.router, prefix="/annotation", tags=["annotation"])
