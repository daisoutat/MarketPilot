"""MarketPilot API — Phase 1 bootstrap + dashboard.

Endpoints:
  GET  /healthz             liveness (always 200 when process runs)
  GET  /healthz/ready       readiness (database goes/no-go, used by ALB)
  GET  /api/v1/me           current user from Cognito (or dev mode)
  GET  /api/v1/dashboard/*  KPI summary, series, orders feed, sync status
  GET/POST/PATCH /api/v1/alerts*  alerts + detection rules (Phase 4)
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from common.config import get_settings
from common.db import init_db, ping
from .auth import get_current_user
from .routers import alerts, analytics, chat, dashboard

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.database_url or settings.db_secret_arn:
        init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description="US & Canadian Amazon markets — seller analytics + market research",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard.router, prefix="/api/v1")
app.include_router(alerts.router, prefix="/api/v1")
app.include_router(analytics.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "app": settings.app_name, "version": app.version}


@app.get("/healthz/ready")
async def ready() -> dict:
    db_ok = await ping() if (settings.database_url or settings.db_secret_arn) else False
    return {"status": "ok" if db_ok else "degraded", "database": db_ok}


@app.get("/api/v1/me")
async def me(user: dict = Depends(get_current_user)) -> dict:
    return {"user": user, "server_time": datetime.now(timezone.utc).isoformat()}