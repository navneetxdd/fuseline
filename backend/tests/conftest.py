from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

# The data directory must be redirected before any `app` module is imported.
_DATA_DIR = tempfile.mkdtemp(prefix="fuseline-test-")
os.environ["FUSELINE_DATA_DIR"] = _DATA_DIR

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "evidence"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FIXTURE_FILES = (
    ("app_usage.db", "app_usage"),
    ("History", "browsing"),
    ("location.csv", "location"),
    ("plaso_sample.l2t.csv", "plaso"),
)


def _force_remove(func, path, _exc):
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    func(path)


def pytest_sessionfinish(session, exitstatus):
    if sys.version_info >= (3, 12):
        shutil.rmtree(_DATA_DIR, onexc=_force_remove, ignore_errors=False)
    else:
        shutil.rmtree(_DATA_DIR, onerror=_force_remove)


@pytest.fixture(scope="session")
def fixture_dir() -> Path:
    """Real evidence files used only by the test suite."""
    if not (FIXTURES / "app_usage.db").exists():
        import seed_test_fixtures

        seed_test_fixtures.main()
    missing = [name for name, _ in FIXTURE_FILES if not (FIXTURES / name).exists()]
    if missing:
        raise FileNotFoundError(f"Missing test fixtures under {FIXTURES}: {missing}")
    return FIXTURES


@pytest.fixture(scope="session")
def client() -> TestClient:
    from app.main import app

    return TestClient(app, base_url="http://127.0.0.1")


@pytest.fixture
def make_case(client: TestClient):
    def _make(timezone: str = "UTC", examiner: str = "Tester", name: str = "case") -> str:
        r = client.post("/api/cases", json={"name": name, "examiner": examiner, "timezone": timezone})
        assert r.status_code == 201, r.text
        return r.json()["id"]

    return _make


@pytest.fixture
def case_id(make_case) -> str:
    return make_case()


@pytest.fixture
def upload(client: TestClient):
    def _upload(case: str, name: str, data: bytes | Path, hint: str = "auto"):
        content = data.read_bytes() if isinstance(data, Path) else data
        return client.post(f"/api/cases/{case}/acquire", files={"file": (name, content)}, data={"source_hint": hint})

    return _upload


@pytest.fixture
def ingest_fixtures(client: TestClient, fixture_dir: Path, upload):
    """Ingest the packed fixture set through the normal acquire API."""

    def _ingest(case: str) -> dict:
        artifacts = []
        events_added = 0
        sessions_rebuilt = 0
        for filename, hint in FIXTURE_FILES:
            r = upload(case, filename, fixture_dir / filename, hint=hint)
            assert r.status_code == 200, r.text
            body = r.json()
            artifacts.append(body["artifact"])
            events_added += body["events_added"]
            sessions_rebuilt = body["sessions_rebuilt"]
        return {
            "artifacts": artifacts,
            "events_added": events_added,
            "sessions_rebuilt": sessions_rebuilt,
        }

    return _ingest
