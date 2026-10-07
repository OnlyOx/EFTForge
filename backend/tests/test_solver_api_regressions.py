"""Endpoint-level regressions for solver caches and Combo truncation metadata.

These use the same synced development database as test_optimizer_solver.py and
are skipped in CI environments where tarkov.db is intentionally absent.
"""

import asyncio
import inspect
import json
import os

import pytest
from starlette.requests import Request

M4A1_ID = "5447a9cd4bdc2dbd208b4567"
M4A1_STOCK_SLOT_ID = "55d5a3074bdc2d61338b4574"
PPSH41_ID = "5ea03f7400685063ec28bfa8"
OPTIMIZER_TEST_IP = "203.0.113.40"

_HAS_DB = os.path.exists(os.path.join(os.path.dirname(__file__), "..", "tarkov.db"))

pytestmark = pytest.mark.skipif(
    not _HAS_DB,
    reason="requires a synced tarkov.db - run sync_tarkov_dev.py first",
)

if _HAS_DB:
    os.environ.setdefault("IP_HASH_SECRET", "solver-api-regression-test-secret")
    os.environ.setdefault("ADMIN_API_KEY", "solver-api-regression-admin-key")
    os.environ.setdefault("DISABLE_BG_MIGRATE", "1")

    import main  # noqa: F401 - creates the app databases
    from routers import combo, optimizer
    from services import solve_limits, solver_cache
    from database import SessionLocal
    from solver_cache_epoch import SolverCacheEpochTracker


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def isolated_solver_caches():
    solver_cache.clear_solver_caches()
    try:
        yield
    finally:
        solver_cache.clear_solver_caches()


def _endpoint_defaults(function, excluded):
    defaults = {}
    for name, parameter in inspect.signature(function).parameters.items():
        if name not in excluded:
            defaults[name] = parameter.default.default
    return defaults


def _run_optimizer(db, **overrides):
    kwargs = _endpoint_defaults(optimizer.build_optimize, {"request", "weapon_id", "db"})
    kwargs.update(overrides)
    solve_limits._solve_request_last.pop(OPTIMIZER_TEST_IP, None)
    request = Request({"type": "http", "headers": [], "client": (OPTIMIZER_TEST_IP, 12345)})
    return optimizer.build_optimize(request=request, weapon_id=PPSH41_ID, db=db, **kwargs)


async def _consume_combo(response):
    buffer = ""
    async for chunk in response.body_iterator:
        buffer += chunk.decode() if isinstance(chunk, bytes) else chunk
    for part in buffer.split("\n\n"):
        if not part.startswith("data: "):
            continue
        event = json.loads(part[6:])
        if event.get("type") == "result":
            return event["data"]
    raise AssertionError("combo-full stream ended without a result")


def _run_combo(db, *, strength_level=10):
    response = combo.combo_full(
        M4A1_ID,
        [],
        M4A1_STOCK_SLOT_ID,
        "en",
        strength_level,
        0.0,
        [],
        [],
        db,
    )
    if isinstance(response, dict):
        return response
    return asyncio.run(_consume_combo(response))


def test_optimizer_cache_key_and_hot_timing(db):
    cold = _run_optimizer(db)
    hot = _run_optimizer(db)
    different_strength = _run_optimizer(db, strength_level=11)

    assert cold["metrics"]["cache_hit"] is False
    assert hot["metrics"]["cache_hit"] is True
    assert hot["metrics"]["processing_ms"] < cold["metrics"]["processing_ms"]
    assert different_strength["metrics"]["cache_hit"] is False


def test_epoch_change_clears_both_result_caches(monkeypatch):
    generation = {"value": "before-sync"}
    tracker = SolverCacheEpochTracker(lambda: generation["value"])
    monkeypatch.setattr(solver_cache, "_SOLVER_CACHE_EPOCH_TRACKER", tracker)

    solver_cache.COMBO_FULL_CACHE[("combo",)] = {"result": "stale"}
    solver_cache.OPTIMIZE_CACHE[("optimizer",)] = {"result": "stale"}
    assert solver_cache.solver_cache_generation() == "before-sync"
    assert solver_cache.COMBO_FULL_CACHE and solver_cache.OPTIMIZE_CACHE

    generation["value"] = "after-sync"
    assert solver_cache.solver_cache_generation() == "after-sync"
    assert solver_cache.COMBO_FULL_CACHE == {}
    assert solver_cache.OPTIMIZE_CACHE == {}


def test_frontier_cap_truncation_survives_cache_and_cache_key_changes(db, monkeypatch):
    monkeypatch.setattr(combo, "_COMBO_FRONTIER_CAP", 1)

    cold = _run_combo(db)
    hot = _run_combo(db)
    different_strength = _run_combo(db, strength_level=11)

    for result in (cold, hot, different_strength):
        assert result["truncated"] is True
        assert "frontier_cap" in result["truncation_reasons"]
        assert result["metrics"]["frontier_cap_hits"] > 0
    assert cold["metrics"]["cache_hit"] is False
    assert hot["metrics"]["cache_hit"] is True
    assert different_strength["metrics"]["cache_hit"] is False


def test_nested_expansion_truncation_is_reported(db, monkeypatch):
    monkeypatch.setattr(combo, "_COMBO_NESTED_EXPANSION_LIMIT", 0)

    result = _run_combo(db)

    assert result["truncated"] is True
    assert "nested_expansion_limit" in result["truncation_reasons"]
    assert result["metrics"]["nested_expansion_skips"] > 0
