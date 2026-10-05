from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.http_security import LocalOnlyMiddleware, hostname
from app.security import assert_safe_case_id, assert_under, csv_safe, quote_ident, sanitize_filename


def test_reject_path_traversal_case_id():
    for bad in ("../etc/passwd", "not-a-uuid", "", "479280c8-9ae9-49fe-8d25-2c7d749678bf/../x"):
        with pytest.raises(ValueError):
            assert_safe_case_id(bad)


def test_accept_uuid_case_id():
    cid = "479280c8-9ae9-49fe-8d25-2c7d749678bf"
    assert assert_safe_case_id(cid) == cid


def test_sanitize_filename_strips_paths():
    assert sanitize_filename("../../evil.db") == "evil.db"
    assert sanitize_filename("ok History") == "ok History"
    assert sanitize_filename("a<b>|c?.csv") == "a_b_c_.csv"
    for bad in ("..", ".", "", "\x00"):
        with pytest.raises(ValueError):
            sanitize_filename(bad)


def test_assert_under_blocks_escape(tmp_path: Path):
    root = tmp_path / "uploads"
    root.mkdir()
    good = root / "a" / "b.db"
    good.parent.mkdir()
    good.write_text("x")
    assert assert_under(good, root) == good.resolve()
    with pytest.raises(ValueError):
        assert_under(tmp_path / "outside.db", root)
    with pytest.raises(ValueError):
        assert_under(root / ".." / "outside.db", root)


def test_quote_ident_neutralises_hostile_table_names():
    conn = sqlite3.connect(":memory:")
    hostile = 'x"); DROP TABLE t; --'
    conn.execute("CREATE TABLE t (a)")
    conn.execute(f"CREATE TABLE {quote_ident(hostile)} (a)")
    cols = conn.execute(f"PRAGMA table_info({quote_ident(hostile)})").fetchall()
    assert [c[1] for c in cols] == ["a"]
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='t'").fetchone()[0] == 1


@pytest.mark.parametrize("value", ['=HYPERLINK("http://x")', "+1+1", "-2+3", "@SUM(A1)", "\tcmd", "\rcmd"])
def test_csv_safe_prefixes_formula_starters(value):
    assert csv_safe(value) == "'" + value


def test_csv_safe_leaves_normal_values_alone():
    assert csv_safe("hello") == "hello"
    assert csv_safe(-5.5) == -5.5
    assert csv_safe(None) is None


def test_hostname_parsing():
    assert hostname("127.0.0.1:8000") == "127.0.0.1"
    assert hostname("LocalHost") == "localhost"
    assert hostname("[::1]:8000") == "[::1]"
    assert hostname("evil.example:80") == "evil.example"


def _guarded_app(hosts=("127.0.0.1", "localhost"), origins=("https://ui.example",), loopback=True) -> TestClient:
    async def ok(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/api/x", ok, methods=["GET", "POST", "DELETE", "OPTIONS"]), Route("/page", ok)])
    app.add_middleware(
        LocalOnlyMiddleware, allowed_hosts=hosts, allowed_origins=origins, allow_loopback_origins=loopback
    )
    return TestClient(app, base_url="http://127.0.0.1:8000")


def test_foreign_host_header_is_rejected_against_dns_rebinding():
    client = _guarded_app()
    assert client.get("/api/x").status_code == 200
    assert client.get("/api/x", headers={"Host": "attacker.example"}).status_code == 400
    assert client.get("/api/x", headers={"Host": "attacker.example:8000"}).status_code == 400


def test_wildcard_host_disables_the_check():
    assert _guarded_app(hosts=("*",)).get("/api/x", headers={"Host": "anything.example"}).status_code == 200


def test_cross_origin_state_changes_are_blocked_but_reads_and_same_origin_work():
    client = _guarded_app()
    evil = {"Origin": "https://evil.example"}
    assert client.post("/api/x", headers=evil).status_code == 403
    assert client.delete("/api/x", headers=evil).status_code == 403
    assert client.post("/api/x", headers={"Origin": "null"}).status_code == 403
    assert client.post("/api/x", headers={"Origin": "http://127.0.0.1:8000"}).status_code == 200  # same origin
    assert client.post("/api/x", headers={"Origin": "https://ui.example"}).status_code == 200  # explicit allow-list
    assert client.post("/api/x").status_code == 200  # non-browser client, no Origin
    assert client.get("/api/x", headers=evil).status_code == 200  # cross-origin reads stay SOP-protected


@pytest.mark.parametrize("port", ["5173", "5174", "3000", "8080"])
def test_any_loopback_dev_server_port_may_write(port):
    client = _guarded_app()
    for host in ("127.0.0.1", "localhost", "[::1]"):
        assert client.post("/api/x", headers={"Origin": f"http://{host}:{port}"}).status_code == 200


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost.evil.com",
        "http://127.0.0.1.evil.com:5173",
        "http://evil.com/127.0.0.1",
        "http://user@evil.com#localhost",
        "ftp://localhost",
        "https://evil.com:5173",
    ],
)
def test_loopback_lookalike_origins_are_rejected(origin):
    assert _guarded_app().post("/api/x", headers={"Origin": origin}).status_code == 403


def test_loopback_origins_can_be_disabled():
    client = _guarded_app(loopback=False)
    assert client.post("/api/x", headers={"Origin": "http://localhost:5174"}).status_code == 403
    assert client.post("/api/x", headers={"Origin": "http://127.0.0.1:8000"}).status_code == 200  # still same-origin
    assert client.post("/api/x", headers={"Origin": "https://ui.example"}).status_code == 200


def test_security_headers_present_and_api_not_cacheable():
    client = _guarded_app()
    api, page = client.get("/api/x"), client.get("/page")
    for r in (api, page):
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["x-frame-options"] == "DENY"
    assert api.headers["cache-control"] == "no-store"
    assert "cache-control" not in page.headers


def test_parser_hint_requires_sniff(fixture_dir: Path):
    from app.parsers.registry import detect_parser

    with pytest.raises(ValueError, match="does not match source hint"):
        detect_parser(fixture_dir / "location.csv", preferred_source="browsing")
