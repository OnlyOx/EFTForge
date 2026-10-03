"""Guard lazy Hall cuts, legal placement, and a shared refinement deadline."""

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from optimizer.matching_placement import MatchingPlacementModel
from optimizer.milp import ConstraintBuilder, _solve_once


def _model(slots, ids, cut_cache=None):
    weapon = SimpleNamespace(id="W", conflicting_item_ids="", conflicting_slot_ids="")
    mods = {i: SimpleNamespace(id=i, conflicting_item_ids="", conflicting_slot_ids="") for i in ids}
    compat = SimpleNamespace(
        slot_owner={s: "W" for s in slots},
        slots_by_id={s: SimpleNamespace(required=req) for s, (req, allowed) in slots.items()},
        slot_items={s: allowed for s, (req, allowed) in slots.items()},
    )
    placement = MatchingPlacementModel(weapon, mods, compat, ids, {i: j for j, i in enumerate(ids)}, cut_cache)
    cb = ConstraintBuilder(len(ids) + 1)
    placement.add_constraints(cb)
    return placement, cb


def _solve(cb, ids, costs, deadline=None):
    return _solve_once(
        np.array([*costs, 0.0]),
        cb,
        len(ids),
        ids,
        "W",
        {},
        {i: {"price_rub": 1} for i in ids},
        deadline=deadline,
    )


def test_shared_destinations_add_upper_hall_cut_and_keep_legal_alternative():
    ids = ["a", "b", "c", "d", "e"]
    placement, cb = _model(
        {
            "one": (False, ["a", "b", "c", "d"]),
            "two": (False, ["a", "b", "d"]),
            "three": (False, ["d", "e"]),
        },
        ids,
    )
    cb.eq({2: 1}, 1)
    result = _solve(cb, ids, [-2, -1, 0, 2, 2])

    assert result["status"] == "optimal"
    assert set(result["selected_items"]) == {"a", "c"}
    assert placement.match(result["selected_items"]) is not None
    assert result["metrics"]["solve_count"] == 2
    assert result["metrics"]["placement_refinement_count"] == 1


def test_distinct_required_slots_add_lower_hall_cut():
    ids = ["a", "b", "c"]
    placement, cb = _model(
        {
            "one": (True, ["a", "b"]),
            "two": (True, ["a", "c"]),
            "three": (False, ["b", "c"]),
        },
        ids,
    )
    assert placement.add_matching_cut(cb, ["a"])
    result = _solve(cb, ids, [1, 2, 3])

    assert result["status"] == "optimal"
    assert set(result["selected_items"]) == {"a", "b"}
    assert placement.match(result["selected_items"]) is not None
    assert result["metrics"]["solve_count"] == 1
    assert result["metrics"]["placement_refinement_count"] == 0


def test_refinement_deadline_never_exports_unplaced_incumbent():
    ids = ["a", "b", "c"]
    placement, cb = _model(
        {
            "one": (True, ["a", "b"]),
            "two": (True, ["a", "c"]),
            "three": (False, ["b", "c"]),
        },
        ids,
    )
    fake = SimpleNamespace(status=0, x=np.array([1, 0, 0, 0]), success=True, message="Optimal")
    clock = iter([0.0, 0.0, 0.0, 0.0, 2.0, 2.0, 2.0])
    with (
        patch("optimizer.milp.milp", return_value=fake) as native,
        patch("optimizer.milp.time.perf_counter", side_effect=lambda: next(clock, 2.0)),
    ):
        result = _solve(cb, ids, [1, 2, 3], deadline=1.0)

    assert result["status"] == "timeout"
    assert result["selected_items"] == []
    assert result["metrics"]["solve_count"] == 1
    assert result["metrics"]["placement_refinement_count"] == 1
    assert native.call_count == 1


def _learned_model():
    ids = ["a", "b", "c", "d", "e"]
    slots = {
        "one": (False, ["a", "b", "c", "d"]),
        "two": (False, ["a", "b", "d"]),
        "three": (False, ["d", "e"]),
    }
    cache = {}
    placement, cb = _model(slots, ids, cache)
    cb.eq({2: 1}, 1)
    result = _solve(cb, ids, [-2, -1, 0, 2, 2])
    assert result["status"] == "optimal"
    assert result["metrics"]["placement_refinement_count"] == 1
    return slots, ids, cache


def test_learned_rows_skip_repeated_refinement_without_sharing_stat_bounds():
    slots, ids, cache = _learned_model()
    placement, cb = _model(slots, ids, cache)
    assert placement.shared_cut_count == 1

    # The first solve forced c; the next solve can remove it and change costs.
    result = _solve(cb, ids, [-2, -1, 5, 2, 2])
    assert result["status"] == "optimal"
    assert set(result["selected_items"]) == {"a", "b"}
    assert result["metrics"]["solve_count"] == 1
    assert result["metrics"]["placement_refinement_count"] == 0


def test_same_item_ids_with_new_ports_do_not_inherit_invalid_rows():
    slots, ids, cache = _learned_model()
    slots["three"] = (False, ["c", "d", "e"])
    placement, cb = _model(slots, ids, cache)
    assert placement.shared_cut_count == 0
    cb.eq({2: 1}, 1)
    result = _solve(cb, ids, [-2, -1, 0, 2, 2])
    assert result["status"] == "optimal"
    assert set(result["selected_items"]) == {"a", "b", "c"}


def test_reordered_columns_do_not_reinterpret_previous_cuts():
    slots, ids, cache = _learned_model()
    ids.reverse()
    placement, cb = _model(slots, ids, cache)
    assert placement.shared_cut_count == 0
    cb.eq({ids.index("c"): 1}, 1)
    result = _solve(cb, ids, [2, 2, 0, -1, -2])
    assert result["status"] == "optimal"
    assert set(result["selected_items"]) == {"a", "c"}


def test_fresh_request_does_not_import_another_requests_cuts():
    slots, ids, cache = _learned_model()
    placement, cb = _model(slots, ids, {})
    assert placement.shared_cut_count == 0
    cb.eq({2: 1}, 1)
    result = _solve(cb, ids, [-2, -1, 0, 2, 2])
    assert result["metrics"]["placement_refinement_count"] == 1
