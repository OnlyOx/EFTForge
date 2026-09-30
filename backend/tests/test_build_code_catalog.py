"""Keep shared dictionaries deterministic and prevent accidental replacement."""

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_code_catalog.py"
SPEC = importlib.util.spec_from_file_location("build_code_catalog", SCRIPT)
catalog_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalog_module)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "items.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            "CREATE TABLE items (id TEXT PRIMARY KEY);"
            "CREATE TABLE slots (id TEXT PRIMARY KEY, parent_item_id TEXT);"
            "CREATE TABLE slot_allowed_items (slot_id TEXT, allowed_item_id TEXT);"
        )
        connection.executemany("INSERT INTO items VALUES (?)", [("gun",), ("attachment-b",), ("attachment-a",)])
        connection.executemany("INSERT INTO slots VALUES (?, ?)", [("slot-b", "gun"), ("slot-a", "gun")])
        connection.executemany(
            "INSERT INTO slot_allowed_items VALUES (?, ?)",
            [("slot-a", "attachment-b"), ("slot-a", "attachment-a"), ("slot-a", "attachment-b")],
        )
    return path


def test_catalog_uses_sorted_ids_and_retains_empty_slots(database):
    assert catalog_module.build_catalog(database) == {
        "items": ["attachment-a", "attachment-b", "gun"],
        "slots": {2: [["slot-a", [0, 1]], ["slot-b", []]]},
    }


def test_refuse_to_overwrite_dictionary_after_database_changes(database, tmp_path):
    destination = tmp_path / "catalog-v1.js"
    before_database = database.read_bytes()
    catalog_module.write_catalog(database, destination, 1)
    before_dictionary = destination.read_bytes()
    assert database.read_bytes() == before_database
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO items VALUES ('new-attachment')")
    with pytest.raises(FileExistsError):
        catalog_module.write_catalog(database, destination, 1)
    assert destination.read_bytes() == before_dictionary
    assert b"new-attachment" not in before_dictionary
    catalog_module.write_catalog(database, tmp_path / "catalog-v2.js", 2)
    assert b"new-attachment" in (tmp_path / "catalog-v2.js").read_bytes()


def test_generated_catalog_matches_data_and_rejects_invalid_version(database, tmp_path):
    destination = tmp_path / "catalog.js"
    catalog = catalog_module.write_catalog(database, destination, 1)
    text = destination.read_text(encoding="utf-8")
    encoded = text.split("EFTForge.buildCodeCatalogs[1] = ", 1)[1].removesuffix(";\n")
    assert json.loads(encoded) == json.loads(json.dumps(catalog))
    with pytest.raises(ValueError):
        catalog_module.write_catalog(database, tmp_path / "invalid.js", 256)
