import logging
import re
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from app.api.auth import router as auth_router
from app.api.documents import router as documents_router
from app.api.organizations import router as organizations_router
from app.api.sources import router as sources_router
from app.body_limit import BodyLimitMiddleware
from app.errors import DocumentProcessingError
from app.health import router as health_router
from app.logging_config import configure_logging, request_id_var
from app.observability import configure_error_tracking, latency, metrics, requests
from app.settings import get_settings


configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    get_settings().validate_runtime()
    configure_error_tracking()
    yield


app = FastAPI(
    title="Sentineth AI",
    description="Organizational Intelligence Platform",
    version="0.3.0",
    lifespan=lifespan,
)
app.add_middleware(BodyLimitMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=get_settings().allowed_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().allowed_origins,
    allow_methods=["GET", "POST", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID", "Location", "Retry-After"],
)


@app.middleware("http")
async def log_request(request: Request, call_next):
    supplied = request.headers.get("x-request-id", "")
    request_id = supplied if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", supplied) else uuid4().hex
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        logger.error("Request failed", extra={"error_type": type(exc).__name__})
        if get_settings().sentry_dsn.get_secret_value():
            import sentry_sdk

            sentry_sdk.capture_exception(exc)
        response = JSONResponse({"detail": "Internal server error"}, status_code=500)
    try:
        route = getattr(request.scope.get("route"), "path", "unmatched")
        method = (
            request.method
            if request.method in {"GET", "POST", "DELETE", "PATCH", "PUT", "OPTIONS", "HEAD"}
            else "OTHER"
        )
        duration = time.perf_counter() - started
        requests.labels(method, route, str(response.status_code)).inc()
        latency.labels(method, route).observe(duration)
        identity = getattr(request.state, "actor", None)
        actor_context = (
            {
                "actor_type": identity.actor_type,
                "actor_id": str(identity.actor_id),
                "organization_id": str(identity.organization_id)
                if identity.organization_id
                else None,
            }
            if identity
            else {}
        )
        logger.info(
            "request completed",
            extra=actor_context
            | {
                "method": method,
                "path": route,
                "status_code": response.status_code,
                "duration_ms": round(duration * 1000, 2),
            },
        )
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        request_id_var.reset(token)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # FastAPI's default includes raw inputs, which can contain passwords/tokens.
    return JSONResponse(
        {
            "detail": [
                {key: error[key] for key in ("type", "loc", "msg") if key in error}
                for error in exc.errors()
            ]
        },
        status_code=422,
    )


@app.exception_handler(DocumentProcessingError)
async def processing_error(request, exc):
    logger.warning("Request rejected", extra={"error_code": exc.code})
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"error_code": exc.code, "message": str(exc)}},
        headers={"Retry-After": "60"} if exc.status_code == 429 else None,
    )


@app.exception_handler(LookupError)
async def missing_document(request, exc):
    return JSONResponse(status_code=404, content={"detail": "Document not found."})


app.include_router(auth_router)
app.include_router(organizations_router)
app.include_router(documents_router)
app.include_router(sources_router)
app.include_router(health_router)
app.add_api_route("/metrics", metrics, methods=["GET"], include_in_schema=False)


@app.get("/")
def root():
    return {"name": "Sentineth AI", "status": "online", "version": "0.3.0"}
