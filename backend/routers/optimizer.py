"""Optimizer endpoints (Explore mode and its stat range and MOA floor helpers),
mod and default preset lookups, and the backend-only gunsmith endpoints."""

import contextlib
import json
import time
from typing import List

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from models_item_offers import ItemOffer
from models_items import Item
from models_weapon_presets import WeaponDefaultPreset
from optimizer.cancellation import SolveCancelled, SolveStreamingResponse
from optimizer.compat_map import build_compatibility_map
from optimizer.explore_request import ExploreRequest
from optimizer.gunsmith import get_gunsmith_tasks
from optimizer.process_runner import run_gunsmith, run_job, stream_explore
from optimizer.solver import OptimizeParams
from routers.shared import (
    EQUIP_ERGO_MAX,
    EQUIP_ERGO_MIN,
    STRENGTH_LEVEL_MAX,
    STRENGTH_LEVEL_MIN,
    VALID_GAME_MODES,
    cap_list,
    get_client_ip,
    get_db,
    item_category,
    item_name,
    item_short_name,
)
from services.solve_limits import check_solve_rate_limit, solve_slot
from services.solver_cache import OPTIMIZE_CACHE, OPTIMIZE_CACHE_LOCK, solver_cache_generation

router = APIRouter()


# ---------------------------------------------------
# Weapon Optimizer (MILP solver) - MVP: weapon + mods only.
# Presets-as-base, FiR fallback pricing, multi-slot placement variables,
# TrueErgo sweep, Tchebycheff scalarization, and category filters are not
# implemented yet - see optimizer/solver.py's module docstring.
# ---------------------------------------------------

# Sights/scopes and tactical devices should always sort to the top of the mod-
# filter's category groups, regardless of display language - keyed on
# tarkov.dev's raw category ids (stable across locales) rather than the
# localized display name. Item.category_ids carries the item's full ancestor
# chain, so checking for the "Sights" parent id catches every sight/scope leaf
# category (Ironsight, Reflex sight, Scope, ...) without listing each one.
# Within the sight tiers: Scope leads, Ironsight trails (magnified/reflex
# optics are what players actually browse this list for).
_SCOPE_CATEGORY_ID = "55818ae44bdc2dde698b456c"
_SIGHTS_CATEGORY_ID = "5448fe7a4bdc2d6f028b456b"  # "Sights" (parent of all scope/sight leaf categories)
_IRONSIGHT_CATEGORY_ID = "55818ac54bdc2d5b648b456e"
_TACTICAL_DEVICE_CATEGORY_IDS = {
    "55818b084bdc2d5b648b4571",  # Flashlight
    "55818b164bdc2ddc698b456c",  # Comb. tact. device (flashlight/IR-laser combo units)
}


def _category_priority(item) -> int:
    ids = set((item.category_ids or "").split(","))
    if _SCOPE_CATEGORY_ID in ids:
        return 0
    if _IRONSIGHT_CATEGORY_ID in ids:
        return 2
    if _SIGHTS_CATEGORY_ID in ids:
        return 1
    if ids & _TACTICAL_DEVICE_CATEGORY_IDS:
        return 3
    return 4


_OPTIMIZE_CACHE_MAX = 500
MAX_INCLUDE_EXCLUDE_IDS = 100


@router.post("/build/explore")
def build_explore(request: Request, payload: ExploreRequest, db: Session = Depends(get_db)):
    ip = get_client_ip(request)
    check_solve_rate_limit(ip)
    if payload.trader_levels and any(level < 0 or level > 4 for level in payload.trader_levels.values()):
        raise HTTPException(status_code=422, detail="trader_levels values must be between 0 and 4")
    weapon = db.query(Item).filter(Item.id == payload.weapon_id, Item.is_weapon == True).first()  # noqa: E712
    if weapon is None:
        raise HTTPException(status_code=404, detail="Weapon not found")

    # Entered here (not inside _stream) so a busy/already-solving 429 is raised
    # synchronously with a real status code, before the streaming response - whose
    # status is committed to 200 the moment it starts - has begun sending anything.
    slot = solve_slot(ip)
    slot.__enter__()
    released = False

    def release_slot():
        nonlocal released
        if not released:
            released = True
            slot.__exit__(None, None, None)

    def _stream():
        try:
            with contextlib.closing(
                stream_explore(payload.weapon_id, payload.optimize_params(), payload.tradeoff, payload.steps)
            ) as events:
                for event in events:
                    yield f"data: {json.dumps(event)}\n\n"
        except SolveCancelled:
            return
        finally:
            release_slot()

    return SolveStreamingResponse(
        _stream(),
        cleanup=release_slot,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/build/optimize")
def build_optimize(
    request: Request,
    weapon_id: str = Body(...),
    max_price: float | None = Body(default=None),
    min_ergonomics: float | None = Body(default=None),
    max_ergonomics: float | None = Body(default=None),
    max_recoil_v: float | None = Body(default=None),
    max_weight: float | None = Body(default=None),
    min_mag_capacity: int | None = Body(default=None),
    min_sighting_range: float | None = Body(default=None),
    include_items: List[str] = Body(default=[]),
    exclude_items: List[str] = Body(default=[]),
    include_categories: List[List[str]] = Body(default=[]),
    exclude_categories: List[str] = Body(default=[]),
    prevent_overswing: bool = Body(default=False),
    require_suppressor: bool = Body(default=False),
    max_moa: float | None = Body(default=None),
    ergo_weight: float = Body(default=1.0),
    recoil_weight: float = Body(default=1.0),
    price_weight: float = Body(default=0.0),
    trader_levels: dict | None = Body(default=None),
    flea_available: bool = Body(default=True),
    allow_unpriced: bool = Body(default=False),
    player_level: int | None = Body(default=None),
    strength_level: int = Body(default=10),
    equip_ergo_modifier: float = Body(default=0.0),
    use_true_ergo: bool = Body(default=False),
    true_ergo_k: float | None = Body(default=None),
    use_tchebycheff: bool = Body(default=True),
    assume_full_mag: bool = Body(default=True),
    selected_ammo_id: str | None = Body(default=None),
    selected_ubgl_ammo_id: str | None = Body(default=None),
    game_mode: str = Body(default="pvp"),
    db: Session = Depends(get_db),
):
    check_solve_rate_limit(get_client_ip(request))
    if game_mode not in VALID_GAME_MODES:
        raise HTTPException(status_code=422, detail=f"game_mode must be one of {sorted(VALID_GAME_MODES)}")
    if not (STRENGTH_LEVEL_MIN <= strength_level <= STRENGTH_LEVEL_MAX):
        raise HTTPException(
            status_code=422, detail=f"strength_level must be between {STRENGTH_LEVEL_MIN} and {STRENGTH_LEVEL_MAX}"
        )
    if not (EQUIP_ERGO_MIN <= equip_ergo_modifier <= EQUIP_ERGO_MAX):
        raise HTTPException(
            status_code=422, detail=f"equip_ergo_modifier must be between {EQUIP_ERGO_MIN} and {EQUIP_ERGO_MAX}"
        )
    for weight_name, weight_val in (
        ("ergo_weight", ergo_weight),
        ("recoil_weight", recoil_weight),
        ("price_weight", price_weight),
    ):
        if not (0 <= weight_val <= 1):
            raise HTTPException(status_code=422, detail=f"{weight_name} must be between 0 and 1")
    if trader_levels is not None:
        for level in trader_levels.values():
            if not (0 <= level <= 4):
                raise HTTPException(status_code=422, detail="trader_levels values must be between 0 and 4")

    cap_list("include_items", include_items, MAX_INCLUDE_EXCLUDE_IDS)
    cap_list("exclude_items", exclude_items, MAX_INCLUDE_EXCLUDE_IDS)
    cap_list("include_categories", include_categories, MAX_INCLUDE_EXCLUDE_IDS)
    cap_list("exclude_categories", exclude_categories, MAX_INCLUDE_EXCLUDE_IDS)

    weapon = db.query(Item).filter(Item.id == weapon_id, Item.is_weapon == True).first()  # noqa: E712
    if not weapon:
        raise HTTPException(status_code=404, detail="Weapon not found")

    _cache_key = (
        solver_cache_generation(),
        weapon_id,
        max_price,
        min_ergonomics,
        max_ergonomics,
        max_recoil_v,
        max_weight,
        min_mag_capacity,
        min_sighting_range,
        tuple(sorted(include_items)),
        tuple(sorted(exclude_items)),
        tuple(sorted(tuple(sorted(g)) for g in include_categories)),
        tuple(sorted(exclude_categories)),
        prevent_overswing,
        require_suppressor,
        max_moa,
        ergo_weight,
        recoil_weight,
        price_weight,
        tuple(sorted((trader_levels or {}).items())),
        flea_available,
        allow_unpriced,
        player_level,
        strength_level,
        equip_ergo_modifier,
        use_true_ergo,
        true_ergo_k,
        use_tchebycheff,
        assume_full_mag,
        selected_ammo_id,
        selected_ubgl_ammo_id,
        game_mode,
    )
    _solve_start = time.perf_counter()

    with OPTIMIZE_CACHE_LOCK:
        cached = OPTIMIZE_CACHE.get(_cache_key)
    if cached is not None:
        cache_processing_ms = round((time.perf_counter() - _solve_start) * 1000, 3)
        return {
            **cached,
            "metrics": {**cached.get("metrics", {}), "cache_hit": True, "processing_ms": cache_processing_ms},
            "solve_ms": round(cache_processing_ms),
        }

    params = OptimizeParams(
        max_price=max_price,
        min_ergonomics=min_ergonomics,
        max_ergonomics=max_ergonomics,
        max_recoil_v=max_recoil_v,
        max_weight=max_weight,
        min_mag_capacity=min_mag_capacity,
        min_sighting_range=min_sighting_range,
        include_items=include_items or None,
        exclude_items=exclude_items or None,
        include_categories=include_categories or None,
        exclude_categories=exclude_categories or None,
        prevent_overswing=prevent_overswing,
        require_suppressor=require_suppressor,
        max_moa=max_moa,
        ergo_weight=ergo_weight,
        recoil_weight=recoil_weight,
        price_weight=price_weight,
        trader_levels=trader_levels,
        flea_available=flea_available,
        allow_unpriced=allow_unpriced,
        player_level=player_level,
        game_mode=game_mode,
        strength_level=strength_level,
        equip_ergo_modifier=equip_ergo_modifier,
        use_true_ergo=use_true_ergo,
        true_ergo_k=true_ergo_k,
        use_tchebycheff=use_tchebycheff,
        assume_full_mag=assume_full_mag,
        selected_ammo_id=selected_ammo_id,
        selected_ubgl_ammo_id=selected_ubgl_ammo_id,
    )
    with solve_slot(get_client_ip(request)):
        result = run_job("optimize", weapon_id, params)
    result = {**result, "metrics": {**result.get("metrics", {}), "cache_hit": False}}

    if result.get("status") != "timeout":
        with OPTIMIZE_CACHE_LOCK:
            if len(OPTIMIZE_CACHE) >= _OPTIMIZE_CACHE_MAX:
                keys = list(OPTIMIZE_CACHE.keys())
                for k in keys[: len(keys) // 2]:
                    del OPTIMIZE_CACHE[k]
            OPTIMIZE_CACHE[_cache_key] = result

    return {**result, "solve_ms": round((time.perf_counter() - _solve_start) * 1000)}


@router.post("/build/stat-ranges")
def build_stat_ranges(
    request: Request,
    weapon_id: str = Body(...),
    trader_levels: dict | None = Body(default=None),
    flea_available: bool = Body(default=True),
    allow_unpriced: bool = Body(default=False),
    player_level: int | None = Body(default=None),
    game_mode: str = Body(default="pvp"),
    db: Session = Depends(get_db),
):
    """Theoretical [min, max] each hard-constraint stat can reach for this
    weapon, so the optimizer UI can cap each constraint slider to what's
    actually achievable instead of an arbitrary fixed range."""
    if trader_levels is not None:
        for level in trader_levels.values():
            if not (0 <= level <= 4):
                raise HTTPException(status_code=422, detail="trader_levels values must be between 0 and 4")
    if game_mode not in VALID_GAME_MODES:
        raise HTTPException(status_code=422, detail=f"game_mode must be one of {sorted(VALID_GAME_MODES)}")

    weapon = db.query(Item).filter(Item.id == weapon_id, Item.is_weapon == True).first()  # noqa: E712
    if not weapon:
        raise HTTPException(status_code=404, detail="Weapon not found")

    params = OptimizeParams(
        trader_levels=trader_levels,
        flea_available=flea_available,
        allow_unpriced=allow_unpriced,
        player_level=player_level,
        game_mode=game_mode,
    )
    with solve_slot(get_client_ip(request)):
        result = run_job("stat_ranges", weapon_id, params)
    if result["status"] == "error":
        raise HTTPException(status_code=404, detail=result["reason"])
    return result


@router.post("/build/moa-floor")
def build_moa_floor(
    request: Request,
    weapon_id: str = Body(...),
    trader_levels: dict | None = Body(default=None),
    flea_available: bool = Body(default=True),
    allow_unpriced: bool = Body(default=False),
    player_level: int | None = Body(default=None),
    game_mode: str = Body(default="pvp"),
    db: Session = Depends(get_db),
):
    """Exact minimum achievable accuracy_moa for this weapon, via a binary
    search of real solves - slower than GET /build/stat-ranges's LP-relaxation
    estimate, only run when the optimizer's "Exact slider floor" toggle is on."""
    check_solve_rate_limit(get_client_ip(request))
    if trader_levels is not None:
        for level in trader_levels.values():
            if not (0 <= level <= 4):
                raise HTTPException(status_code=422, detail="trader_levels values must be between 0 and 4")
    if game_mode not in VALID_GAME_MODES:
        raise HTTPException(status_code=422, detail=f"game_mode must be one of {sorted(VALID_GAME_MODES)}")

    weapon = db.query(Item).filter(Item.id == weapon_id, Item.is_weapon == True).first()  # noqa: E712
    if not weapon:
        raise HTTPException(status_code=404, detail="Weapon not found")

    params = OptimizeParams(
        trader_levels=trader_levels,
        flea_available=flea_available,
        allow_unpriced=allow_unpriced,
        player_level=player_level,
        game_mode=game_mode,
    )
    with solve_slot(get_client_ip(request)):
        result = run_job("moa_floor", weapon_id, params)
    if result["status"] == "error":
        raise HTTPException(status_code=404, detail=result["reason"])
    return result


@router.get("/build/mods")
def build_mods(weapon_id: str, lang: str = "en", db: Session = Depends(get_db)):
    """Reachable mods for one weapon, for the optimizer's Mod Filter search
    (force-include/exclude a specific item)."""
    weapon = db.query(Item).filter(Item.id == weapon_id, Item.is_weapon == True).first()  # noqa: E712
    if not weapon:
        raise HTTPException(status_code=404, detail="Weapon not found")

    cmap = build_compatibility_map(db, weapon_id)
    if not cmap.reachable_ids:
        return {"mods": []}

    items = db.query(Item).filter(Item.id.in_(cmap.reachable_ids)).all()
    mods = [
        {
            "id": item.id,
            "name": item_name(item, lang),
            "short_name": item_short_name(item, lang),
            "icon": item.icon_link,
            "category": item_category(item, lang),
            "category_priority": _category_priority(item),
        }
        for item in items
    ]
    mods.sort(key=lambda m: m["name"] or "")

    return {"mods": mods}


@router.get("/build/default-preset")
def build_default_preset(weapon_id: str, db: Session = Depends(get_db)):
    """The weapon's factory default preset as a pseudo-item the price panel can price
    exactly like any other item (its cheapest eligible trader offer + id for the flea
    lookup), so the panel can weigh 'buy the bare receiver + parts' against 'buy the
    assembled factory preset' the same way the optimizer's _choose_base does. Returns
    {"preset": null} for weapons that have no default preset (melee, throwables)."""
    row = db.query(WeaponDefaultPreset).filter(WeaponDefaultPreset.weapon_id == weapon_id).first()
    if not row:
        return {"preset": None}
    # Cheapest trader offer, mirroring how sync computes Item.trader_price_rub (same
    # excluded vendors) so the preset prices consistently with every other item row.
    excluded = {"ragman", "ref", "fence", "flea-market"}
    offers = (
        db.query(ItemOffer).filter(ItemOffer.item_id == row.preset_id, ItemOffer.is_flea == False).all()  # noqa: E712
    )
    eligible = [o for o in offers if o.vendor_normalized not in excluded and o.price_rub is not None]
    cheapest = min(eligible, key=lambda o: o.price_rub) if eligible else None
    return {
        "preset": {
            "id": row.preset_id,
            "trader_price_rub": cheapest.price_rub if cheapest else None,
            "trader_vendor": cheapest.vendor_normalized if cheapest else None,
            "trader_min_level": cheapest.trader_level if cheapest else None,
        }
    }


@router.get("/build/gunsmith-tasks")
def build_gunsmith_tasks(lang: str = "en", db: Session = Depends(get_db)):
    return {"tasks": get_gunsmith_tasks(db, lang)}


@router.post("/build/gunsmith-solve")
def build_gunsmith_solve(
    request: Request,
    task_name: str = Body(...),
    trader_levels: dict | None = Body(default=None),
    flea_available: bool = Body(default=True),
    player_level: int | None = Body(default=None),
    strength_level: int = Body(default=10),
    equip_ergo_modifier: float = Body(default=0.0),
    game_mode: str = Body(default="pvp"),
    db: Session = Depends(get_db),
):
    check_solve_rate_limit(get_client_ip(request))
    if not (STRENGTH_LEVEL_MIN <= strength_level <= STRENGTH_LEVEL_MAX):
        raise HTTPException(
            status_code=422, detail=f"strength_level must be between {STRENGTH_LEVEL_MIN} and {STRENGTH_LEVEL_MAX}"
        )
    if not (EQUIP_ERGO_MIN <= equip_ergo_modifier <= EQUIP_ERGO_MAX):
        raise HTTPException(
            status_code=422, detail=f"equip_ergo_modifier must be between {EQUIP_ERGO_MIN} and {EQUIP_ERGO_MAX}"
        )
    if trader_levels is not None:
        for level in trader_levels.values():
            if not (0 <= level <= 4):
                raise HTTPException(status_code=422, detail="trader_levels values must be between 0 and 4")
    if game_mode not in VALID_GAME_MODES:
        raise HTTPException(status_code=422, detail=f"game_mode must be one of {sorted(VALID_GAME_MODES)}")

    with solve_slot(get_client_ip(request)):
        result = run_gunsmith(
            task_name,
            trader_levels=trader_levels,
            flea_available=flea_available,
            player_level=player_level,
            strength_level=strength_level,
            equip_ergo_modifier=equip_ergo_modifier,
            game_mode=game_mode,
        )
    if result["status"] == "error":
        raise HTTPException(status_code=404, detail=result["reason"])
    return result
