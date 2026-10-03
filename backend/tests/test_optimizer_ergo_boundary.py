"""Exercise TED floor boundaries and progress after a repeated rejected selection."""

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from optimizer.milp import ConstraintBuilder, _solve_with_min_true_ergo
from stats import effective_ergo, true_ergo_delta


def test_equipment_floor_uses_weight_cut_at_float_equal_ergo():
    ids = ["heavy", "light"]
    idx = {i: j for j, i in enumerate(ids)}
    mods = {
        "heavy": SimpleNamespace(ergonomics_modifier=0, weight=2.698),
        "light": SimpleNamespace(ergonomics_modifier=15.5, weight=0.686),
    }
    cb = ConstraintBuilder(3)
    cb.eq({0: 1, 1: 1}, 1)

    def compute(selected):
        ergo = 40.5 + sum(mods[i].ergonomics_modifier for i in selected)
        weight = 2.8 + sum(mods[i].weight for i in selected)
        return {"true_ergo_delta": round(true_ergo_delta(weight, effective_ergo(ergo, 0.05)), 2)}

    result = _solve_with_min_true_ergo(
        np.array([-2.0, -1.0, 0]),
        cb,
        2,
        ids,
        idx,
        SimpleNamespace(id="weapon"),
        mods,
        {i: [(i + "_slot", "weapon")] for i in ids},
        {i: {"price_rub": 100} for i in ids},
        40.5,
        2.8,
        0.05,
        51,
        42.525000000000006,
        solve_stats=SimpleNamespace(compute=compute, item_weights={i: mods[i].weight for i in ids}),
    )

    assert result["status"] == "optimal"
    assert result["selected_items"] == ["light"]
    assert compute(result["selected_items"])["true_ergo_delta"] >= 42.525
    assert result["metrics"]["solve_count"] == 2


def test_repeated_rejected_selection_gets_an_exact_no_good_row():
    cb = ConstraintBuilder(2)
    invalid = {"status": "optimal", "selected_items": ["mod"], "metrics": {}}
    valid = {"status": "optimal", "selected_items": [], "metrics": {}}
    with (
        patch("optimizer.milp._solve_once", side_effect=[invalid, invalid, valid]),
        patch(
            "optimizer.milp._compute_stats",
            side_effect=[{"true_ergo_delta": -1}, {"true_ergo_delta": -1}, {"true_ergo_delta": 10}],
        ),
        patch("optimizer.milp._add_overswing_cut_at"),
    ):
        result = _solve_with_min_true_ergo(
            np.zeros(2),
            cb,
            1,
            ["mod"],
            {"mod": 0},
            SimpleNamespace(id="weapon"),
            {"mod": SimpleNamespace(ergonomics_modifier=0)},
            {},
            {},
            50,
            3,
            0,
            10,
            5,
        )
    assert result["status"] == "optimal"
    assert result["metrics"]["solve_count"] == 3
    assert cb.rows == [({0: -1}, 0, np.inf)]
