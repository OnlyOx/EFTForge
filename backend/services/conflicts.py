"""Attachment conflict checks shared by the build validation endpoints and the
combo recommender."""

from routers.shared import item_name


def check_conflicts(
    candidate,
    candidate_id: str,
    installed_set: set,
    installed_items_map: dict,
    slots_by_item: dict,
    slot_id: str,
    lang: str,
) -> dict:
    """Run all four conflict checks using pre-loaded data. No DB queries."""
    # Item ↔ item
    if candidate.conflicting_item_ids:
        conflict_ids = set(candidate.conflicting_item_ids.split(","))
        overlap = conflict_ids.intersection(installed_set)
        if overlap:
            conflicting = installed_items_map.get(list(overlap)[0])
            if conflicting:
                return {
                    "valid": False,
                    "reason_key": "conflict.incompatibleWith",
                    "reason_name": item_name(conflicting, lang),
                    "conflicting_item_id": conflicting.id,
                    "conflicting_slot_id": None,
                }

    # Slot ↔ slot
    if candidate.conflicting_slot_ids:
        conflict_slots = set(candidate.conflicting_slot_ids.split(","))
        for iid in installed_set:
            for s in slots_by_item.get(iid, []):
                if s.id in conflict_slots:
                    return {
                        "valid": False,
                        "reason_key": "conflict.slot",
                        "reason_name": s.slot_name,
                        "conflicting_item_id": None,
                        "conflicting_slot_id": s.id,
                    }

    # Reverse item ↔ item
    for inst_item in installed_items_map.values():
        if inst_item.conflicting_item_ids:
            if candidate_id in set(inst_item.conflicting_item_ids.split(",")):
                return {
                    "valid": False,
                    "reason_key": "conflict.incompatibleWith",
                    "reason_name": item_name(inst_item, lang),
                    "conflicting_item_id": inst_item.id,
                    "conflicting_slot_id": None,
                }

    # Reverse slot
    for inst_item in installed_items_map.values():
        if inst_item.conflicting_slot_ids:
            if slot_id in set(inst_item.conflicting_slot_ids.split(",")):
                return {
                    "valid": False,
                    "reason_key": "conflict.blockedBy",
                    "reason_name": item_name(inst_item, lang),
                    "conflicting_item_id": inst_item.id,
                    "conflicting_slot_id": None,
                }

    return {
        "valid": True,
        "reason_key": None,
        "reason_name": None,
        "conflicting_item_id": None,
        "conflicting_slot_id": None,
    }
