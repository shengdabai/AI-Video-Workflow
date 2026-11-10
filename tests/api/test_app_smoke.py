"""Smoke tests for the FastAPI app on the Starlette 1.x stack.

They go through Starlette's TestClient, so the app lifespan, the CORS
middleware, FileResponse and request validation all run the same way they do
under uvicorn.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Keep the orchestrator DB and served files inside tmp_path.
    monkeypatch.setenv("ORCHESTRATOR_DB_PATH", str(tmp_path / "orchestrator.db"))
    monkeypatch.chdir(tmp_path)
    from api.app import app

    with TestClient(app) as c:  # runs the lifespan startup and shutdown
        yield c


def test_health_and_docs(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"

    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "swagger-ui" in docs.text

    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    assert "/health" in spec.json()["paths"]


def test_cors_preflight(client):
    resp = client.options(
        "/api/orchestrator/tasks",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert resp.headers["access-control-allow-credentials"] == "true"


@pytest.mark.parametrize("origin", ["https://untrusted.example", "null", "http://localhost:3000.evil.example"])
def test_cors_rejects_untrusted_origins(client, origin):
    resp = client.options(
        "/api/orchestrator/tasks",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )
    assert resp.status_code == 400
    assert "access-control-allow-origin" not in resp.headers


def test_file_response_serves_output_file(client, tmp_path):
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "result.json").write_text('{"ok": true}', encoding="utf-8")

    resp = client.get("/api/files/result.json")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.headers["content-disposition"] == 'inline; filename="result.json"'
    assert resp.json() == {"ok": True}

    assert client.get("/api/files/missing.json").status_code == 404


def test_request_validation_error_shape(client):
    resp = client.post("/api/orchestrator/tasks", json={"title": "no script"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail and {"loc", "msg", "type"} <= set(detail[0])


def test_orchestrator_tasks_on_fresh_database(tmp_path, monkeypatch):
    """A fresh service has no database file; the lifespan must create the tables."""
    import sqlite3

    import api.routers.orchestrator as orchestrator_routes
    from api.app import app

    db_path = tmp_path / "fresh" / "orchestrator.db"
    db_path.parent.mkdir()
    assert not db_path.exists()
    # The router reads ORCHESTRATOR_DB_PATH once at import time, so point the
    # module attribute at the fresh path directly.
    monkeypatch.setattr(orchestrator_routes, "_DB_PATH", str(db_path))
    monkeypatch.chdir(tmp_path)

    with TestClient(app) as c:
        resp = c.get("/api/orchestrator/tasks")
        assert resp.status_code == 200
        assert resp.json() == []

    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"video_tasks", "shot_tasks", "assets", "packs", "publish_records"} <= tables

    # A second start-up on the same database is a no-op (CREATE TABLE IF NOT EXISTS)
    # and keeps existing rows.
    with TestClient(app) as c:
        created = c.post("/api/orchestrator/tasks", json={"title": "t", "script": "s"})
        assert created.status_code == 201
    with TestClient(app) as c:
        resp = c.get("/api/orchestrator/tasks")
        assert resp.status_code == 200
        assert [task["title"] for task in resp.json()] == ["t"]
