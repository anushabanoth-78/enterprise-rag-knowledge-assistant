import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import admin, auth, chat, documents
from app.core import metrics
from app.core.config import settings

structlog.configure(processors=[
    structlog.contextvars.merge_contextvars,
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.processors.JSONRenderer(),
])
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.assert_safe_for_production()  # refuse to boot with default secrets in production
    yield  # schema is managed by Alembic (`alembic upgrade head`), not create_all


app = FastAPI(title="Enterprise AI Knowledge Assistant", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    rid = uuid.uuid4().hex[:12]
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=rid)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled_error", path=request.url.path)  # never logs bodies, headers or tokens
        response = JSONResponse({"detail": "Internal server error", "request_id": rid}, status_code=500)
    ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = rid
    log.info("request", method=request.method, path=request.url.path, status=response.status_code,
             ms=round(ms, 1))
    if request.url.path.startswith("/api"):
        metrics.record_request(ms, response.status_code >= 500)
    return response


# Added last so it is the outermost layer: CORS headers are present even on error responses.
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                   allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                   allow_headers=["Authorization", "Content-Type"], expose_headers=["X-Request-ID"])


@app.get("/health", tags=["ops"])
def health():
    return {"status": "ok"}


@app.get("/health/ready", tags=["ops"])
def ready():
    """Readiness: can this instance reach Postgres and Redis?"""
    from app.db.repository import engine
    checks = {}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001
        checks["database"] = "unavailable"
    try:
        metrics._redis.ping()
        checks["redis"] = "ok"
    except Exception:  # noqa: BLE001
        checks["redis"] = "unavailable"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ok" if ok else "degraded", **checks}, status_code=200 if ok else 503)


api = APIRouter(prefix="/api")
api.include_router(auth.router)
api.include_router(documents.router)
api.include_router(chat.router)
api.include_router(admin.router)
app.include_router(api)
