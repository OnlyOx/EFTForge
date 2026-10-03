"""Keep the FD917 and Baldr reference build runnable in CI without the full database."""

import json
from pathlib import Path

import pytest

from models_items import Item
from models_item_offers import ItemOffer
from models_slots import Slot
from models_slot_allowed import SlotAllowedItem
from optimizer.explore import explore_weapon
from optimizer.solver import OptimizeParams
from tests import test_reachability_integration

db = test_reachability_integration.db


@pytest.fixture
def glock(db):
    case = json.loads((Path(__file__).parent / "data/glock_fd917.json").read_text())
    for item in case["items"]:
        db.add(Item(**item))
        db.add(
            ItemOffer(
                item_id=item["id"],
                vendor_normalized="mechanic",
                trader_level=1,
                price=100,
                price_rub=100,
                currency="RUB",
            )
        )
    db.add_all(Slot(**slot) for slot in case["slots"])
    db.add_all(SlotAllowedItem(**edge) for edge in case["allowed"])
    db.commit()
    return db, case


def check_placement(build, case):
    pairs = build["slot_pairs"]
    assignments = dict(pairs)
    assert len(assignments) == len(pairs) == len(build["selected_items"])
    assert {iid for _, iid in pairs} == set(build["selected_items"])
    assert assignments[case["root_tactical_slot"]] == case["suppressor_id"]
    assert assignments[case["suppressor_tactical_slot"]] == case["light_id"]
    assert [iid for _, iid in pairs].index(case["suppressor_id"]) < [iid for _, iid in pairs].index(case["light_id"])


@pytest.mark.parametrize("use_true_ergo", [False, True])
@pytest.mark.parametrize("tradeoff", ["price", "recoil"])
def test_explore_preserves_exact_glock_reference_stats(glock, use_true_ergo, tradeoff):
    db, case = glock
    params = OptimizeParams(
        include_items=case["reference_parts"],
        assume_full_mag=False,
        min_mag_capacity=33,
        require_suppressor=True,
        use_true_ergo=use_true_ergo,
    )
    result = explore_weapon(db, case["weapon_id"], params, tradeoff, steps=10)
    assert result["status"] == "complete"
    assert result["points"]
    for point in result["points"]:
        build = point["build"]
        assert set(build["selected_items"]) == set(case["reference_parts"])
        check_placement(build, case)
        for name, expected in case["expected_stats"].items():
            assert build["final_stats"][name] == expected


@pytest.mark.parametrize("use_true_ergo", [False, True])
def test_explore_accepts_locked_fd917_and_baldr(glock, use_true_ergo):
    db, case = glock
    params = OptimizeParams(
        include_items=[case["suppressor_id"], case["light_id"]],
        assume_full_mag=False,
        min_mag_capacity=33,
        require_suppressor=True,
        use_true_ergo=use_true_ergo,
    )
    result = explore_weapon(db, case["weapon_id"], params, "recoil", steps=10)
    assert result["status"] == "complete"
    assert result["points"]
    for point in result["points"]:
        check_placement(point["build"], case)
        assert point["build"]["final_stats"]["recoil_vertical"] == 234


def test_locked_fd917_and_baldr_need_the_suppressor_rail(glock):
    db, case = glock
    db.query(SlotAllowedItem).filter_by(slot_id=case["suppressor_tactical_slot"]).delete()
    db.commit()
    params = OptimizeParams(include_items=[case["suppressor_id"], case["light_id"]], assume_full_mag=False)
    result = explore_weapon(db, case["weapon_id"], params, "recoil", steps=10)
    assert result["status"] == "infeasible"
    assert not result["points"]
