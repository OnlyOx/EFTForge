from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from models_builds import BuildRating, BuildVote, PublicBuild
from models_ratings import AttachmentRating, AttachmentVote
from routers.shared import (
    get_builds_db,
    get_client_id_hash,
    get_optional_client_id_hash,
    get_ratings_db,
    require_admin,
    validate_item_id,
)

router = APIRouter()


# ---------------------------------------------------
# Attachment Ratings
# ---------------------------------------------------


@router.get("/ratings/attachments/bulk")
def get_bulk_ratings(ids: str, x_client_id: str = Header(None), db: Session = Depends(get_ratings_db)):
    if len(ids) > 4000:
        raise HTTPException(status_code=413, detail="ids parameter too long")
    raw_ids = [i.strip() for i in ids.split(",") if i.strip()]
    if not raw_ids:
        raise HTTPException(status_code=400, detail="ids parameter is required")
    if len(raw_ids) > 200:
        raise HTTPException(status_code=400, detail="Too many ids (max 200)")
    for item_id in raw_ids:
        validate_item_id(item_id)
    unique_ids = list(dict.fromkeys(raw_ids))  # deduplicate, preserve order

    # use client token hash for vote lookup; fall back to no user_vote if absent
    client_hash = get_optional_client_id_hash(x_client_id)

    rating_rows = db.query(AttachmentRating).filter(AttachmentRating.item_id.in_(unique_ids)).all()
    ratings_map = {r.item_id: r for r in rating_rows}

    votes_map = {}
    if client_hash:
        vote_rows = (
            db.query(AttachmentVote)
            .filter(
                AttachmentVote.item_id.in_(unique_ids),
                AttachmentVote.ip_hash == client_hash,
            )
            .all()
        )
        votes_map = {v.item_id: v.vote for v in vote_rows}

    result = {}
    for item_id in unique_ids:
        r = ratings_map.get(item_id)
        result[item_id] = {
            "likes": r.like_count if r else 0,
            "dislikes": r.dislike_count if r else 0,
            "user_vote": votes_map.get(item_id),
        }

    return {"ratings": result}


@router.post("/ratings/attachments/{item_id}/vote")
def post_vote(
    item_id: str,
    vote: str = Body(..., embed=True),
    x_client_id: str = Header(None),
    db: Session = Depends(get_ratings_db),
):
    validate_item_id(item_id)
    if vote not in ("like", "dislike"):
        raise HTTPException(status_code=422, detail='vote must be "like" or "dislike"')

    ip_hash = get_client_id_hash(x_client_id)

    existing = (
        db.query(AttachmentVote)
        .filter(
            AttachmentVote.item_id == item_id,
            AttachmentVote.ip_hash == ip_hash,
        )
        .first()
    )

    if existing is None:
        db.add(AttachmentVote(item_id=item_id, ip_hash=ip_hash, vote=vote))
        _upsert_rating(db, item_id, like_delta=1 if vote == "like" else 0, dislike_delta=1 if vote == "dislike" else 0)
        result_vote = vote
    elif existing.vote == vote:
        db.delete(existing)
        _upsert_rating(
            db, item_id, like_delta=-1 if vote == "like" else 0, dislike_delta=-1 if vote == "dislike" else 0
        )
        result_vote = None
    else:
        existing.vote = vote
        existing.created_at = datetime.now(timezone.utc)
        like_d = 1 if vote == "like" else -1
        dislike_d = 1 if vote == "dislike" else -1
        _upsert_rating(db, item_id, like_delta=like_d, dislike_delta=dislike_d)
        result_vote = vote

    db.commit()

    rating = db.query(AttachmentRating).filter(AttachmentRating.item_id == item_id).first()
    return {
        "likes": rating.like_count if rating else 0,
        "dislikes": rating.dislike_count if rating else 0,
        "user_vote": result_vote,
    }


@router.delete("/ratings/attachments/{item_id}/vote")
def delete_vote(item_id: str, x_client_id: str = Header(None), db: Session = Depends(get_ratings_db)):
    validate_item_id(item_id)
    ip_hash = get_client_id_hash(x_client_id)

    existing = (
        db.query(AttachmentVote)
        .filter(
            AttachmentVote.item_id == item_id,
            AttachmentVote.ip_hash == ip_hash,
        )
        .first()
    )

    if existing:
        old_vote = existing.vote
        db.delete(existing)
        _upsert_rating(
            db, item_id, like_delta=-1 if old_vote == "like" else 0, dislike_delta=-1 if old_vote == "dislike" else 0
        )
        db.commit()

    rating = db.query(AttachmentRating).filter(AttachmentRating.item_id == item_id).first()
    return {
        "likes": rating.like_count if rating else 0,
        "dislikes": rating.dislike_count if rating else 0,
        "user_vote": None,
    }


@router.delete("/admin/ratings/attachments/{item_id}")
def admin_clear_rating(
    item_id: str, request: Request, x_admin_key: str = Header(None), db: Session = Depends(get_ratings_db)
):
    require_admin(request, x_admin_key)
    validate_item_id(item_id)

    db.query(AttachmentVote).filter(AttachmentVote.item_id == item_id).delete()
    db.query(AttachmentRating).filter(AttachmentRating.item_id == item_id).delete()
    db.commit()

    return {"cleared": True, "item_id": item_id}


def _upsert_rating(db: Session, item_id: str, like_delta: int, dislike_delta: int) -> None:
    """Insert or update the rating summary row atomically."""
    existing = db.query(AttachmentRating).filter(AttachmentRating.item_id == item_id).first()
    if existing is None:
        db.add(
            AttachmentRating(
                item_id=item_id,
                like_count=max(0, like_delta),
                dislike_count=max(0, dislike_delta),
                last_updated=datetime.now(timezone.utc),
            )
        )
    else:
        existing.like_count = max(0, existing.like_count + like_delta)
        existing.dislike_count = max(0, existing.dislike_count + dislike_delta)
        existing.last_updated = datetime.now(timezone.utc)


# ---------------------------------------------------
# Build Ratings
# ---------------------------------------------------


def _validate_build_id_positive(build_id: int) -> None:
    if build_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid build_id")


def _upsert_build_rating(db: Session, build_id: int, like_delta: int, dislike_delta: int) -> None:
    existing = db.query(BuildRating).filter(BuildRating.build_id == build_id).first()
    if existing is None:
        db.add(
            BuildRating(
                build_id=build_id,
                like_count=max(0, like_delta),
                dislike_count=max(0, dislike_delta),
                last_updated=datetime.now(timezone.utc),
            )
        )
    else:
        existing.like_count = max(0, existing.like_count + like_delta)
        existing.dislike_count = max(0, existing.dislike_count + dislike_delta)
        existing.last_updated = datetime.now(timezone.utc)


@router.get("/ratings/builds/bulk")
def get_bulk_build_ratings(ids: str, x_client_id: str = Header(None), db: Session = Depends(get_builds_db)):
    if len(ids) > 4000:
        raise HTTPException(status_code=413, detail="ids parameter too long")
    raw_ids = [i.strip() for i in ids.split(",") if i.strip()]
    if not raw_ids:
        raise HTTPException(status_code=400, detail="ids parameter is required")
    if len(raw_ids) > 200:
        raise HTTPException(status_code=400, detail="Too many ids (max 200)")

    try:
        int_ids = [int(i) for i in raw_ids]
    except ValueError:
        raise HTTPException(status_code=400, detail="ids must be integers")
    unique_ids = list(dict.fromkeys(int_ids))

    client_hash = get_optional_client_id_hash(x_client_id)

    rating_rows = db.query(BuildRating).filter(BuildRating.build_id.in_(unique_ids)).all()
    ratings_map = {r.build_id: r for r in rating_rows}

    votes_map = {}
    if client_hash:
        vote_rows = (
            db.query(BuildVote)
            .filter(
                BuildVote.build_id.in_(unique_ids),
                BuildVote.ip_hash == client_hash,
            )
            .all()
        )
        votes_map = {v.build_id: v.vote for v in vote_rows}

    result = {}
    for build_id in unique_ids:
        r = ratings_map.get(build_id)
        result[str(build_id)] = {
            "likes": r.like_count if r else 0,
            "dislikes": r.dislike_count if r else 0,
            "user_vote": votes_map.get(build_id),
        }

    return {"ratings": result}


@router.post("/ratings/builds/{build_id}/vote")
def post_build_vote(
    build_id: int,
    vote: str = Body(..., embed=True),
    x_client_id: str = Header(None),
    db: Session = Depends(get_builds_db),
):
    _validate_build_id_positive(build_id)
    if vote != "like":
        raise HTTPException(status_code=422, detail='vote must be "like"')

    if not db.query(PublicBuild.id).filter(PublicBuild.id == build_id).first():
        raise HTTPException(status_code=404, detail="Build not found.")

    ip_hash = get_client_id_hash(x_client_id)

    existing = (
        db.query(BuildVote)
        .filter(
            BuildVote.build_id == build_id,
            BuildVote.ip_hash == ip_hash,
        )
        .first()
    )

    if existing is None:
        db.add(BuildVote(build_id=build_id, ip_hash=ip_hash, vote=vote))
        _upsert_build_rating(
            db, build_id, like_delta=1 if vote == "like" else 0, dislike_delta=1 if vote == "dislike" else 0
        )
        result_vote = vote
    elif existing.vote == vote:
        db.delete(existing)
        _upsert_build_rating(
            db, build_id, like_delta=-1 if vote == "like" else 0, dislike_delta=-1 if vote == "dislike" else 0
        )
        result_vote = None
    else:
        existing.vote = vote
        existing.created_at = datetime.now(timezone.utc)
        like_d = 1 if vote == "like" else -1
        dislike_d = 1 if vote == "dislike" else -1
        _upsert_build_rating(db, build_id, like_delta=like_d, dislike_delta=dislike_d)
        result_vote = vote

    db.commit()

    rating = db.query(BuildRating).filter(BuildRating.build_id == build_id).first()
    return {
        "likes": rating.like_count if rating else 0,
        "dislikes": rating.dislike_count if rating else 0,
        "user_vote": result_vote,
    }


@router.delete("/ratings/builds/{build_id}/vote")
def delete_build_vote(build_id: int, x_client_id: str = Header(None), db: Session = Depends(get_builds_db)):
    _validate_build_id_positive(build_id)
    ip_hash = get_client_id_hash(x_client_id)

    existing = (
        db.query(BuildVote)
        .filter(
            BuildVote.build_id == build_id,
            BuildVote.ip_hash == ip_hash,
        )
        .first()
    )

    if existing:
        old_vote = existing.vote
        db.delete(existing)
        _upsert_build_rating(
            db, build_id, like_delta=-1 if old_vote == "like" else 0, dislike_delta=-1 if old_vote == "dislike" else 0
        )
        db.commit()

    rating = db.query(BuildRating).filter(BuildRating.build_id == build_id).first()
    return {
        "likes": rating.like_count if rating else 0,
        "dislikes": rating.dislike_count if rating else 0,
        "user_vote": None,
    }
