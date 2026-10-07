"""Rebuild card trees from published pairs without the game database."""

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("IP_HASH_SECRET", "build-cards-test-secret")
os.environ.setdefault("ADMIN_API_KEY", "build-cards-test-admin")

from build_images import build_image_key  # noqa: E402
from services import build_cards  # noqa: E402

GUN, HANDGUARD, COVER, RAIL, RING, LASER, LIGHT = (f"{n:024x}" for n in range(1, 8))

# slot id -> (parent item, game slot name). Both rails share the Tactical slot id,
# the way two copies of one part do in the game data.
SLOTS = {
    "s_handguard": (GUN, "mod_handguard"),
    "s_mount_a": (HANDGUARD, "mod_mount_000"),
    "s_cover": (HANDGUARD, "mod_mount_001"),
    "s_mount_b": (COVER, "mod_mount"),
    "s_tactical": (RAIL, "mod_tactical"),
    "s_ring": (RING, "mod_tactical"),
}

# A handguard rail and a cover rail, each with its own Tactical part, in the
# breadth-first order collectSlotPairs publishes (build 2190's layout).
PAIRS = [
    ["s_handguard", HANDGUARD],
    ["s_mount_a", RAIL],
    ["s_cover", COVER],
    ["s_tactical", RING],
    ["s_mount_b", RAIL],
    ["s_ring", LIGHT],
    ["s_tactical", LASER],
]


@pytest.fixture(autouse=True)
def fake_slots(monkeypatch):
    slots = [
        SimpleNamespace(id=sid, parent_item_id=parent, slot_game_name=name, slot_name=name)
        for sid, (parent, name) in SLOTS.items()
    ]
    query = SimpleNamespace(filter=lambda *args: SimpleNamespace(all=lambda: slots))
    session = SimpleNamespace(query=lambda model: query)

    class FakeSession:
        def __enter__(self):
            return session

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(build_cards, "SessionLocal", FakeSession)


def _children(items, parent_id):
    return {i["slotId"]: i for i in items if i["parentId"] == parent_id}


def test_duplicate_parts_keep_their_own_children():
    items = build_cards.build_spt_items(GUN, PAIRS)
    build_image_key(GUN, items)  # raises on a duplicate instance or slot

    handguard = _children(items, items[0]["_id"])["mod_handguard"]
    rail_a = _children(items, handguard["_id"])["mod_mount_000"]
    cover = _children(items, handguard["_id"])["mod_mount_001"]
    rail_b = _children(items, cover["_id"])["mod_mount"]

    ring = _children(items, rail_a["_id"])["mod_tactical"]
    assert ring["_tpl"] == RING
    assert _children(items, ring["_id"])["mod_tactical"]["_tpl"] == LIGHT
    assert _children(items, rail_b["_id"])["mod_tactical"]["_tpl"] == LASER


def test_more_children_than_copies_is_rejected():
    with pytest.raises(ValueError, match="Unresolved build image parent"):
        build_cards.build_spt_items(GUN, PAIRS + [["s_tactical", LASER]])
