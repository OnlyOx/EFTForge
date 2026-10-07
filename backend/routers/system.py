"""Health, sync status, data version, and the hyperactive sync admin switch."""

import json
import os
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from config import RUNTIME_DIR
from routers.shared import get_db, require_admin
from services import sync

router = APIRouter()


# Recorded once at process start - clients use this to detect a backend restart
# and bypass their local update-check TTL so a fresh deploy is noticed immediately.
SERVER_START_TIME = int(time.time())


# ---------------------------------------------------
# Health check
# ---------------------------------------------------


@router.api_route("/health", methods=["GET", "HEAD"])
def health_check(request: Request, db: Session = Depends(get_db)):
    """Liveness + basic DB connectivity check for load balancers / monitoring."""
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"DB unavailable: {exc}")
    return {"status": "ok", "started": SERVER_START_TIME}


_LAST_SYNC_FILE = os.path.join(RUNTIME_DIR, "last_sync.json")


@router.get("/sync-status")
def get_sync_status():
    """Public endpoint - lets clients show a notice when a background sync is in progress,
    and when the tarkov.dev data was last successfully synced (cron, hyperactive mode,
    or a local dev background sync all write last_sync.json via sync_tarkov_dev.py)."""
    running = sync.is_sync_running()
    last_synced_at = None
    if os.path.exists(_LAST_SYNC_FILE):
        try:
            with open(_LAST_SYNC_FILE, "r", encoding="utf-8") as f:
                last_synced_at = json.load(f).get("last_synced_at")
        except (OSError, ValueError):
            pass
    return {"sync_running": running, "last_synced_at": last_synced_at}


@router.get("/data-version")
def get_data_version():
    """The token the frontend adds to catalog requests as ?dv= so the CDN can cache
    them until the next sync or deploy. Null while a sync runs: clients then send
    unversioned requests, which are never cached."""
    return JSONResponse({"version": sync.data_version()}, headers={"Cache-Control": "no-store"})


_DEV_SYNC_NOTICE_FILE = os.path.join(RUNTIME_DIR, "dev_sync_notice.json")


@router.get("/dev/sync-notice")
def get_dev_sync_notice():
    """Local-dev only: reset.py's background sync writes this file when it finds
    new data. Only reset.py's non-prod branch ever creates it, so in production
    this file never exists and the endpoint is a permanent no-op."""
    if not os.path.exists(_DEV_SYNC_NOTICE_FILE):
        return {"changed": False}
    try:
        os.remove(_DEV_SYNC_NOTICE_FILE)
    except OSError:
        pass
    return {"changed": True}


# ---------------------------------------------------
# Admin - Hyperactive Sync Mode
# ---------------------------------------------------


@router.get("/admin/hyperactive-mode")
def admin_get_hyperactive_mode(
    request: Request,
    x_admin_key: str = Header(None),
):
    require_admin(request, x_admin_key)
    return {
        "hyperactive_mode": sync.hyperactive_mode,
        "sync_running": sync.sync_running,
        "last_sync_at": sync.last_sync_at,
        "sync_interval_seconds": sync.SYNC_INTERVAL_HYPERACTIVE_SECS,
    }


@router.post("/admin/hyperactive-mode")
async def admin_set_hyperactive_mode(
    request: Request,
    enabled: bool = Body(...),
    x_admin_key: str = Header(None),
):
    require_admin(request, x_admin_key)
    sync.hyperactive_mode = enabled
    if enabled:
        open(sync.HYPERACTIVE_LOCK_FILE, "w").close()
        sync.sync_trigger.set()  # wake the background loop for an immediate sync
    else:
        if os.path.exists(sync.HYPERACTIVE_LOCK_FILE):
            os.remove(sync.HYPERACTIVE_LOCK_FILE)
        sync.sync_trigger.set()  # wake the background loop so it sees mode=False
    return {"hyperactive_mode": enabled}
