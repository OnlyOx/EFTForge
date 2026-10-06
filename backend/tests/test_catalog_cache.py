"""Tests for the catalog cache headers (catalog_cache.py), driven as raw ASGI so they
need no synced tarkov.db and run on CI."""

import asyncio
import os

from catalog_cache import CatalogCacheMiddleware, code_stamp, make_data_version


async def _inner_app(scope, receive, send):
    status = 404 if scope["path"].endswith("/missing/slots") else 200
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": b"[]"})


def _get(app, path, query=b"", method="GET"):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": method, "path": path, "query_string": query, "headers": []}
    asyncio.run(app(scope, receive, send))
    start = sent[0]
    return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}


def _versions(*values):
    """A data_version stub returning each value in turn (before, after)."""
    it = iter(values)
    return lambda: next(it)


def test_matching_version_is_cacheable():
    app = CatalogCacheMiddleware(_inner_app, lambda: "v1")
    status, headers = _get(app, "/guns", b"lang=en&dv=v1")
    assert status == 200
    assert headers["cache-control"] == "public, max-age=86400"
    assert headers["x-data-version"] == "v1"


def test_stale_version_is_never_stored():
    app = CatalogCacheMiddleware(_inner_app, lambda: "v2")
    _, headers = _get(app, "/slots/abc/allowed-items", b"lang=zh&dv=v1")
    assert headers["cache-control"] == "no-store"
    # tells the client the current version so its next requests become cacheable
    assert headers["x-data-version"] == "v2"


def test_unversioned_requests_are_never_stored():
    app = CatalogCacheMiddleware(_inner_app, lambda: "v1")
    _, headers = _get(app, "/guns/abc/init", b"lang=en")
    assert headers["cache-control"] == "no-store"


def test_sync_starting_mid_request_is_not_cached():
    app = CatalogCacheMiddleware(_inner_app, _versions("v1", None))
    _, headers = _get(app, "/traders", b"dv=v1")
    assert headers["cache-control"] == "no-store"
    assert "x-data-version" not in headers


def test_data_changing_mid_request_is_not_cached():
    app = CatalogCacheMiddleware(_inner_app, _versions("v1", "v2"))
    _, headers = _get(app, "/items/ids", b"dv=v1")
    assert headers["cache-control"] == "no-store"


def test_errors_are_not_cached():
    app = CatalogCacheMiddleware(_inner_app, lambda: "v1")
    status, headers = _get(app, "/items/missing/slots", b"dv=v1")
    assert status == 404
    assert headers["cache-control"] == "no-store"


def test_other_routes_pass_through_untouched():
    calls = []

    def version():
        calls.append(1)
        return "v1"

    app = CatalogCacheMiddleware(_inner_app, version)
    for path, method in [
        ("/guns/abc/image", "GET"),
        ("/builds/public", "GET"),
        ("/sync-status", "GET"),
        ("/build/explore", "POST"),
        ("/guns", "POST"),
    ]:
        _, headers = _get(app, path, b"dv=v1", method)
        assert "cache-control" not in headers and "x-data-version" not in headers
    assert not calls


def test_version_changes_with_db_epoch_and_sync(tmp_path):
    db = tmp_path / "tarkov.db"
    db.write_bytes(b"one")
    state = {"epoch": "e1", "syncing": False}
    version = make_data_version(str(db), lambda: state["epoch"], lambda: state["syncing"], "stamp")

    first = version()
    assert first and version() == first

    state["epoch"] = "e2"
    second = version()
    assert second != first

    st = os.stat(db)
    db.write_bytes(b"two!")
    os.utime(db, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert version() != second

    (tmp_path / "tarkov.db-wal").write_bytes(b"wal")
    third = version()
    assert third != second

    state["syncing"] = True
    assert version() is None


def test_missing_db_is_never_cacheable(tmp_path):
    version = make_data_version(str(tmp_path / "nope.db"), lambda: "", lambda: False, "stamp")
    assert version() is None


def test_code_stamp_tracks_source_but_ignores_venv(tmp_path):
    (tmp_path / "main.py").write_text("x")
    os.utime(tmp_path / "main.py", ns=(1, 1_000))
    (tmp_path / "venv").mkdir()
    (tmp_path / "venv" / "lib.py").write_text("x")
    os.utime(tmp_path / "venv" / "lib.py", ns=(1, 9_000))
    assert code_stamp(str(tmp_path)) == "1000"
