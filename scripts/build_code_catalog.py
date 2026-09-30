"""Freeze a numbered build-code dictionary from a local item database.

Never replace a published dictionary: keep its file and script tag forever.
Create a new version when new items need compact codes; older clients can
continue exporting those items with the self-contained v2 format.
"""

import argparse
import json
import sqlite3
from pathlib import Path


def build_catalog(database):
    with sqlite3.connect(f"{Path(database).resolve().as_uri()}?mode=ro", uri=True) as connection:
        items = [row[0] for row in connection.execute("SELECT id FROM items ORDER BY id")]
        indices = {item: index for index, item in enumerate(items)}
        allowed = {}
        for slot, item in connection.execute("SELECT slot_id, allowed_item_id FROM slot_allowed_items"):
            if item in indices:
                allowed.setdefault(slot, set()).add(indices[item])
        slots = {}
        for slot, parent in connection.execute("SELECT id, parent_item_id FROM slots ORDER BY id"):
            if parent in indices:
                slots.setdefault(indices[parent], []).append([slot, sorted(allowed.get(slot, set()))])
    return {"items": items, "slots": slots}


def write_catalog(database, destination, version):
    if not 1 <= version <= 255:
        raise ValueError("Catalog version must be between 1 and 255")
    catalog = build_catalog(database)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Refuse to change an existing dictionary, including an unreleased one.
    with destination.open("x", encoding="utf-8", newline="\n") as output:
        output.write("// Keep this dictionary immutable so shared codes retain their meaning.\n")
        output.write("EFTForge.buildCodeCatalogs = EFTForge.buildCodeCatalogs || {};\n")
        output.write(f"EFTForge.buildCodeCatalogs[{version}] = ")
        output.write(json.dumps(catalog, separators=(",", ":")))
        output.write(";\n")
    return catalog


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("backend/tarkov.db"))
    parser.add_argument("--version", type=int, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    destination = root / "frontend" / "data" / f"build-code-catalog-v{args.version}.js"
    catalog = write_catalog(args.database, destination, args.version)
    print(f"Created {destination}: {len(catalog['items'])} items. Keep this file immutable.")
    print("Add its script tag before build-manager.js, retaining every earlier catalog tag.")
