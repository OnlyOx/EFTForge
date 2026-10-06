"""Guard exact model inputs and independent solve state during request-local reuse."""

import copy
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

import numpy as np
import pytest
from scipy.sparse import csc_array
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

os.environ.setdefault("IP_HASH_SECRET", "optimizer-model-input-test-secret")
os.environ.setdefault("ADMIN_API_KEY", "optimizer-model-input-test-admin")

from database import Base  # noqa: E402
from models_items import Item  # noqa: E402
from optimizer import milp  # noqa: E402
from optimizer.matching_placement import MatchingPlacementModel  # noqa: E402
from optimizer.milp import ConstraintBuilder, ModelInputCache, _SolveStats, _build_constraints  # noqa: E402
from optimizer.solver import OptimizeParams, prepare_optimize_weapon  # noqa: E402
from tests.test_optimizer_prepared import setup_tradeoffs  # noqa: E402
from tests.test_reachability_integration import setup_graph  # noqa: E402


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _setup_stat_graph(db):
    setup_graph(
        db,
        {
            ("magazine", "gun"): ["heavy", "light"],
            ("barrel", "gun"): ["long", "short"],
            ("muzzle", "long"): ["suppressor", "brake"],
            ("optic", "gun"): ["scope"],
            ("launcher", "gun"): ["ubgl"],
        },
        required=["magazine", "barrel"],
        fields={
            "gun": {"weight": 2.0, "base_ergonomics": 60, "sighting_range": 100},
            "heavy": {"weight": 0.7, "magazine_capacity": 60, "ergonomics_modifier": -12},
            "light": {"weight": 0.2, "magazine_capacity": 30, "ergonomics_modifier": -4},
            "long": {"center_of_impact": 0.4, "accuracy_modifier": 0, "ergonomics_modifier": 2},
            "short": {"center_of_impact": 0.9, "accuracy_modifier": 0, "ergonomics_modifier": 5},
            "scope": {"category_ids": "optic", "sighting_range": 800, "accuracy_modifier": 1.5},
            "suppressor": {
                "category_ids": milp.SUPPRESSOR_CATEGORY_ID,
                "recoil_modifier": -0.2,
                "accuracy_modifier": 4.5,
            },
            "brake": {"recoil_modifier": -0.15, "accuracy_modifier": 2.5},
            "ubgl": {"caliber": "grenade", "weight": 0.9},
            "ammo": {"is_ammo": True, "weight": 0.012, "ammo_accuracy_modifier": 15},
            "grenade": {"is_ammo": True, "weight": 0.23, "caliber": "grenade"},
        },
    )


def _model(prepared, params, ammo=None, grenade=None, *, cache=None, cuts=None, candidate_ids=None):
    weapon, compat, mods, (loaded_ids, prices) = prepared.candidates
    return _build_constraints(
        weapon,
        mods,
        compat,
        loaded_ids if candidate_ids is None else candidate_ids,
        prices,
        params,
        _SolveStats(weapon, mods, params, ammo, grenade),
        model_cache=cache,
        placement_cut_cache=cuts,
    )


def _row_order(builder):
    return [(tuple(coefficients.items()), lower, upper) for coefficients, lower, upper in builder.rows]


def _assert_models_equal(fresh, cached):
    assert fresh[:2] == cached[:2]
    assert fresh[3:] == cached[3:]
    left, right = fresh[2], cached[2]
    assert left.n == right.n
    assert _row_order(left) == _row_order(right)
    assert left.placement.signature == right.placement.signature
    assert list(left.placement.slots.items()) == list(right.placement.slots.items())
    assert left.placement.groups == right.placement.groups
    assert list(fresh[3].items()) == list(cached[3].items())
    expected = left.build()
    actual = right.build()
    for name in ("data", "indices", "indptr"):
        np.testing.assert_array_equal(getattr(actual.A, name), getattr(expected.A, name))
    np.testing.assert_array_equal(actual.lb, expected.lb)
    np.testing.assert_array_equal(actual.ub, expected.ub)

    # Compare against the original coordinate-triplet conversion too.
    row_indices, columns, values = [], [], []
    for row_index, (coefficients, _lower, _upper) in enumerate(right.rows):
        for column, value in coefficients.items():
            if value:
                row_indices.append(row_index)
                columns.append(column)
                values.append(value)
    original = csc_array((values, (row_indices, columns)), shape=(len(right.rows), right.n), dtype=float)
    for name in ("data", "indices", "indptr"):
        np.testing.assert_array_equal(getattr(actual.A, name), getattr(original, name))


@pytest.mark.parametrize("assume_full_mag", [False, True])
def test_cached_topology_preserves_dynamic_rows_order_and_sparse_matrix(db, assume_full_mag):
    _setup_stat_graph(db)
    params = OptimizeParams(selected_ammo_id="ammo", selected_ubgl_ammo_id="grenade", assume_full_mag=assume_full_mag)
    prepared = prepare_optimize_weapon(db, "gun", params)
    ammo = db.get(Item, "ammo") if assume_full_mag else None
    grenade = db.get(Item, "grenade") if assume_full_mag else None
    variants = [
        params,
        replace(params, min_ergonomics=59.25),
        replace(params, max_ergonomics=83.5, max_recoil_v=90, max_recoil_sum=180),
        replace(params, max_price=450, max_weight=3.2),
        replace(params, include_categories=[["optic", milp.SUPPRESSOR_CATEGORY_ID]], require_suppressor=True),
        replace(params, min_mag_capacity=60, min_sighting_range=700),
        replace(params, max_moa=20),
        replace(
            params,
            min_ergonomics=45,
            max_price=650,
            max_weight=4.5,
            include_items=["scope"],
            include_categories=[[milp.SUPPRESSOR_CATEGORY_ID], ["optic"]],
            require_suppressor=True,
            min_mag_capacity=60,
            min_sighting_range=700,
            max_moa=15,
        ),
        params,
    ]
    for variant in variants:
        fresh = _model(prepared, variant, ammo, grenade)
        cached = _model(prepared, variant, ammo, grenade, cache=prepared.model_cache)
        _assert_models_equal(fresh, cached)


def test_shallow_copies_initialize_one_shared_topology_without_parallel_native_solves(db):
    setup_tradeoffs(db, nested=True)
    prepared = prepare_optimize_weapon(db, "gun", OptimizeParams())
    copies = [copy.copy(prepared), copy.copy(prepared)]
    barrier = threading.Barrier(2)
    weapon, compat, mods, (candidate_ids, _prices) = prepared.candidates

    def initialize(context):
        barrier.wait(timeout=5)
        return context.model_cache.get(weapon, mods, compat, candidate_ids)

    with patch("optimizer.milp.MatchingPlacementModel", wraps=MatchingPlacementModel) as constructor:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(initialize, copies))
    assert constructor.call_count == 1
    assert results[0] is results[1]
    assert prepared.model_cache.get(weapon, mods, compat, candidate_ids) is results[0]
    assert all(context.model_cache is prepared.model_cache for context in copies)
    assert results[0][2].shared_cuts is None


def test_cloned_topology_keeps_learned_cuts_and_builder_state_independent(db):
    setup_graph(
        db,
        {
            ("one", "gun"): ["a", "b", "c", "d"],
            ("two", "gun"): ["a", "b", "d"],
            ("three", "gun"): ["d", "e"],
        },
    )
    prepared = prepare_optimize_weapon(db, "gun", OptimizeParams())
    weapon, compat, mods, (candidate_ids, _prices) = prepared.candidates
    _idx, _slots, template = prepared.model_cache.get(weapon, mods, compat, candidate_ids)
    caches = [{}, {}]
    placements = [template.for_solve(cache) for cache in caches]
    builders = [ConstraintBuilder(len(candidate_ids) + 1) for _ in placements]
    for placement, builder in zip(placements, builders):
        placement.add_constraints(builder)
    untouched_rows = _row_order(builders[1])
    untouched_keys = set(builders[1].matching_cut_keys)

    assert placements[0].add_matching_cut(builders[0], ["a", "b", "c"])
    assert placements[0].shared_cuts
    assert placements[1].shared_cuts == {}
    assert template.shared_cuts is None
    assert _row_order(builders[1]) == untouched_rows
    assert builders[1].matching_cut_keys == untouched_keys
    assert placements[0].expressions is not placements[1].expressions
    assert placements[0].expressions is not template.expressions

    replay = template.for_solve(caches[0])
    replay_builder = ConstraintBuilder(len(candidate_ids) + 1)
    replay.add_constraints(replay_builder)
    assert replay.shared_cut_count == 1
    assert placements[1].shared_cut_count == template.shared_cut_count == 0


def test_model_cache_rebuilds_for_candidate_column_reordering(db):
    setup_tradeoffs(db, nested=True)
    params = OptimizeParams()
    prepared = prepare_optimize_weapon(db, "gun", params)
    cache = ModelInputCache()
    original = _model(prepared, params, cache=cache)
    reordered_ids = list(reversed(original[0]))
    assert reordered_ids != original[0]
    reordered = _model(prepared, params, cache=cache, candidate_ids=reordered_ids)
    fresh = _model(prepared, params, candidate_ids=reordered_ids)
    _assert_models_equal(fresh, reordered)
    assert reordered[1] is not original[1]
    assert reordered[2].placement.signature != original[2].placement.signature
    assert list(reordered[1]) == reordered_ids
