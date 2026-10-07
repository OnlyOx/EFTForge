"""Admin bans and build authors."""

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session

from models_builds import IPBan, PendingNotification, PublicBuild, PublicBuildAuthor
from routers.shared import get_builds_db, require_admin

router = APIRouter()


# ---------------------------------------------------
# Admin - Bans
# ---------------------------------------------------


@router.post("/admin/bans")
def admin_create_ban(
    request: Request,
    x_admin_key: str = Header(None),
    client_id_hash: str = Body(...),
    duration_hours: int | None = Body(default=None),
    reason: str | None = Body(default=None),
    db: Session = Depends(get_builds_db),
):
    require_admin(request, x_admin_key)

    banned_until = None
    if duration_hours is not None:
        banned_until = datetime.now(timezone.utc) + timedelta(hours=duration_hours)

    existing = db.query(IPBan).filter(IPBan.ip_hash == client_id_hash).first()
    if existing:
        existing.banned_at = datetime.now(timezone.utc)
        existing.banned_until = banned_until
        existing.reason = reason
    else:
        db.add(IPBan(ip_hash=client_id_hash, banned_until=banned_until, reason=reason))

    # notify the banned client
    db.add(
        PendingNotification(
            ip_hash=client_id_hash,
            type="ban",
            data_json=json.dumps(
                {
                    "banned_until": banned_until.replace(tzinfo=None).isoformat() + "Z" if banned_until else None,
                    "reason": reason,
                }
            ),
        )
    )

    db.commit()
    return {"banned": True, "client_id_hash": client_id_hash}


@router.delete("/admin/bans/{client_id_hash}")
def admin_delete_ban(
    client_id_hash: str,
    request: Request,
    x_admin_key: str = Header(None),
    db: Session = Depends(get_builds_db),
):
    require_admin(request, x_admin_key)
    deleted = db.query(IPBan).filter(IPBan.ip_hash == client_id_hash).delete()
    if deleted:
        db.add(
            PendingNotification(
                ip_hash=client_id_hash,
                type="unban",
                data_json=json.dumps({}),
            )
        )
    db.commit()
    return {"unbanned": True, "client_id_hash": client_id_hash}


@router.get("/admin/bans")
def admin_list_bans(
    request: Request,
    x_admin_key: str = Header(None),
    db: Session = Depends(get_builds_db),
):
    require_admin(request, x_admin_key)
    bans = db.query(IPBan).all()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return [
        {
            "ip_hash": b.ip_hash,
            "banned_at": b.banned_at.isoformat(),
            "banned_until": b.banned_until.isoformat() + "Z" if b.banned_until else None,
            "is_active": b.banned_until is None or b.banned_until > now,
            "reason": b.reason,
        }
        for b in bans
    ]


# ---------------------------------------------------
# Admin - Authors
# ---------------------------------------------------


@router.post("/admin/authors")
def admin_upsert_author(
    request: Request,
    x_admin_key: str = Header(None),
    id: str = Body(...),
    display_name: str = Body(...),
    avatar_url: str | None = Body(default=None),
    display_name_zh: str | None = Body(default=None),
    db: Session = Depends(get_builds_db),
):
    require_admin(request, x_admin_key)
    existing = db.query(PublicBuildAuthor).filter(PublicBuildAuthor.id == id).first()
    if existing:
        existing.display_name = display_name
        existing.avatar_url = avatar_url
        existing.display_name_zh = display_name_zh
    else:
        db.add(
            PublicBuildAuthor(
                id=id,
                display_name=display_name,
                avatar_url=avatar_url,
                display_name_zh=display_name_zh,
            )
        )
    db.commit()
    return {"ok": True, "id": id}


@router.delete("/admin/authors/{author_id}")
def admin_delete_author(
    author_id: str,
    request: Request,
    x_admin_key: str = Header(None),
    db: Session = Depends(get_builds_db),
):
    require_admin(request, x_admin_key)
    author = db.query(PublicBuildAuthor).filter(PublicBuildAuthor.id == author_id).first()
    if not author:
        raise HTTPException(status_code=404, detail="Author not found.")
    db.query(PublicBuild).filter(PublicBuild.author_id == author_id).update({"author_id": None})
    db.delete(author)
    db.commit()
    return {"ok": True, "id": author_id}


@router.get("/admin/authors")
def admin_list_authors(
    request: Request,
    x_admin_key: str = Header(None),
    db: Session = Depends(get_builds_db),
):
    require_admin(request, x_admin_key)
    authors = db.query(PublicBuildAuthor).all()
    return [
        {
            "id": a.id,
            "display_name": a.display_name,
            "display_name_zh": a.display_name_zh,
            "avatar_url": a.avatar_url,
        }
        for a in authors
    ]
