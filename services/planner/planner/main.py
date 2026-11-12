"""FastAPI app factory. Run with: uvicorn planner.main:create_app --factory"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import service
from .config import Settings, configure_logging
from .models import PlanRequest
from .service import PlanIntegrityError, UnknownGallery
from .store import (
    MuseumNotFound,
    MuseumRepository,
    MuseumUnavailable,
    Store,
    StoreError,
    build_store,
)

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _error(status: int, code: str, message: str, details: list[str] | None = None) -> JSONResponse:
    error: dict[str, object] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return JSONResponse(status_code=status, content={"error": error})


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            f"{'.'.join(str(p) for p in err.get('loc', ()))}: {err.get('msg', 'invalid')}"
            for err in exc.errors()
        ]
        return _error(422, "invalid_request", "The request failed validation.", details)

    @app.exception_handler(MuseumNotFound)
    async def museum_not_found(_: Request, exc: MuseumNotFound) -> JSONResponse:
        return _error(404, "museum_not_found", f"No plannable museum with id '{exc}' has been ingested.")

    @app.exception_handler(UnknownGallery)
    async def unknown_gallery(_: Request, exc: UnknownGallery) -> JSONResponse:
        return _error(
            422,
            "unknown_gallery",
            f"Gallery '{exc}' is not in this museum's floor map, so the plan cannot start there.",
        )

    @app.exception_handler(StoreError)
    async def store_error(_: Request, exc: StoreError) -> JSONResponse:
        log.error("store read failed: %s", exc)
        return _error(503, "store_unavailable", "Museum data is temporarily unavailable. Try again shortly.")

    @app.exception_handler(MuseumUnavailable)
    async def museum_unavailable(_: Request, exc: MuseumUnavailable) -> JSONResponse:
        return _error(503, "store_unavailable", f"Data for museum '{exc}' could not be loaded. Try again later.")

    @app.exception_handler(PlanIntegrityError)
    async def integrity(_: Request, exc: PlanIntegrityError) -> JSONResponse:
        # Refuse to answer rather than return a plan whose access claims are false.
        # The contract's Error.code enum has no internal-error value; see the service README.
        log.critical("plan integrity check failed: %s", exc)
        return _error(500, "internal_error", "The plan failed an internal safety check and was not returned.")


def create_app(store: Store | None = None) -> FastAPI:
    configure_logging()
    settings = Settings.from_env()
    repo = MuseumRepository(store if store is not None else build_store(settings), settings.cache_ttl_seconds)

    app = FastAPI(
        title="Museum Planner API",
        version="1.0.0",
        summary="Builds a museum visit plan from interests, time, and what the visitor's body can do that day.",
    )
    _register_error_handlers(app)

    @app.get("/healthz", tags=["ops"], operation_id="getHealth")
    def get_health():
        return {"status": "ok"}

    @app.get("/api/museums", tags=["museums"], operation_id="listMuseums")
    def list_museums():
        return service.list_museums(repo)

    @app.get("/api/museums/{museum_id}", tags=["museums"], operation_id="getMuseum")
    def get_museum(museum_id: str):
        return service.museum_detail(repo.get(museum_id))

    @app.post("/api/plan", tags=["planning"], operation_id="createPlan")
    def create_plan(body: PlanRequest):
        return service.create_plan(repo.get(body.museum_id), body)

    # Registered last so API routes always win over the static catch-all.
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
