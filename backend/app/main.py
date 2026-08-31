"""
FastAPI entrypoint for the HospitalityOS platform.

One app, many modules: platform-wide concerns (health, CORS) live here, and
each module under app/modules/ contributes its own router, mounted below.
Real routes land per-slice inside those modules starting Stage 3.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import from_url as redis_from_url
from sqlalchemy import text

from app.core.config import settings
from app.core.db import engine
from app.modules.receptionist.api import router as receptionist_router
from app.modules.receptionist.api import staff_router
from app.platform.api import auth_router
from app.platform.api import router as platform_router

app = FastAPI(title="HospitalityOS API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    """Liveness check — process is up. No dependency checks."""
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready():
    """Readiness check — confirms Postgres and Redis are actually reachable."""
    checks: dict[str, str] = {}

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 — surface any connectivity failure
        checks["database"] = f"error: {exc}"

    try:
        redis_client = redis_from_url(settings.redis_url)
        await redis_client.ping()
        await redis_client.aclose()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc}"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(status_code=200 if all_ok else 503, content=checks)


# --- routers ------------------------------------------------------------
# Auth first: everything below it is gated by what it issues.
app.include_router(auth_router, prefix="/api")
# Platform next (shared resources), then one line per module.
app.include_router(platform_router, prefix="/api")
app.include_router(receptionist_router, prefix="/api")
app.include_router(staff_router, prefix="/api")
