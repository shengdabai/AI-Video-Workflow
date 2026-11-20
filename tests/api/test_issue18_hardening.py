"""Regression tests for Issue #18 security & workflow hardening."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient


class _FakeProcess:
    returncode = 0

    async def communicate(self):
        return b"", b""


@pytest.fixture
async def setup_env(tmp_path, monkeypatch):
    app_dir = tmp_path / "app"
    (app_dir / "output" / "clips").mkdir(parents=True)
    (app_dir / "output" / "clips" / "0.mp4").write_bytes(b"clip")
    (app_dir / "templates" / "1080x1920").mkdir(parents=True)
    (app_dir / "templates" / "1080x1920" / "image_test.html").write_text(
        '<!DOCTYPE html><html><head><meta name="template:media-width" content="800">'
        '<meta name="template:media-height" content="600"></head><body>{{text}}</body></html>',
        encoding="utf-8",
    )
    monkeypatch.setenv("PIXELLE_VIDEO_ROOT", str(app_dir))
    monkeypatch.chdir(app_dir)

    db_path = str(tmp_path / "orchestrator.db")
    monkeypatch.setenv("ORCHESTRATOR_DB_PATH", db_path)
    import api.routers.orchestrator as orch_module
    import orchestrator.db as db_module

    monkeypatch.setattr(db_module, "_DB_PATH", db_path)
    monkeypatch.setattr(orch_module, "_DB_PATH", db_path)
    await db_module.init_db(db_path)
    return app_dir


@pytest.fixture
def fake_publish_io(monkeypatch):
    import orchestrator.publish.youtube as youtube

    ffmpeg_calls = []
    upload_calls = []

    async def fake_exec(*args, **kwargs):
        ffmpeg_calls.append(args)
        return _FakeProcess()

    async def fake_upload(video_path, meta):
        upload_calls.append(video_path)
        return f"yt_{len(upload_calls)}"

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(youtube, "upload_to_youtube", fake_upload)
    return ffmpeg_calls, upload_calls


async def _make_task_with_clip(client: AsyncClient, *, headers: dict | None = None, approve_video: bool = True) -> str:
    from orchestrator.assets.manager import AssetManager
    from orchestrator.models import Asset

    hdrs = headers or {}
    task = (await client.post("/api/orchestrator/tasks", json={"title": "t", "script": "s"}, headers=hdrs)).json()
    shots = (
        await client.post(
            f"/api/orchestrator/tasks/{task['id']}/shots",
            json={"shots": [{"shot_index": 0, "prompt": "p"}]},
            headers=hdrs,
        )
    ).json()
    assert (await client.post(f"/api/orchestrator/shots/{shots[0]['id']}/approve", headers=hdrs)).status_code == 200
    await AssetManager().register(
        Asset(
            task_id=task["id"],
            shot_id=shots[0]["id"],
            type="video_clip",
            file_path="output/clips/0.mp4",
        )
    )
    if approve_video:
        assert (
            await client.post(f"/api/orchestrator/tasks/{task['id']}/review/approve", headers=hdrs)
        ).status_code == 200
    return task["id"]


async def test_f1_write_endpoints_enforce_api_token_and_non_loopback(setup_env, monkeypatch):
    from api.app import app

    monkeypatch.setenv("PIXELLE_API_TOKEN", "secret-token-123")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/orchestrator/tasks", json={"title": "t", "script": "s"})).status_code == 401
        assert (
            await client.post(
                "/api/orchestrator/tasks",
                json={"title": "t", "script": "s"},
                headers={"Authorization": "Bearer wrong-token"},
            )
        ).status_code == 403
        ok = await client.post(
            "/api/orchestrator/tasks",
            json={"title": "t", "script": "s"},
            headers={"Authorization": "Bearer secret-token-123"},
        )
        assert ok.status_code == 201
        assert (await client.get("/api/orchestrator/tasks")).json() != []

    monkeypatch.delenv("PIXELLE_API_TOKEN", raising=False)
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.10", 12345)),
        base_url="http://test",
    ) as remote_client:
        resp = await remote_client.post("/api/orchestrator/tasks", json={"title": "t", "script": "s"})
        assert resp.status_code == 401


def test_f3_loopback_default_host_and_explicit_container_override():
    root = Path(__file__).resolve().parents[2]
    app_py = (root / "api" / "app.py").read_text(encoding="utf-8")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert 'parser.add_argument("--host", default="127.0.0.1"' in app_py
    assert 'CMD ["uv", "run", "python", "api/app.py", "--host", "0.0.0.0"]' in dockerfile


def test_f4_unexpected_errors_hide_internal_details(setup_env):
    from api.app import app
    from api.dependencies import get_pixelle_video

    secret_msg = "/private/internal/path/secret_token_leaked"

    class _BrokenCore:
        async def llm(self, **_kwargs):
            raise RuntimeError(secret_msg)

        async def generate_video(self, **_kwargs):
            raise RuntimeError(secret_msg)

    app.dependency_overrides[get_pixelle_video] = lambda: _BrokenCore()
    try:
        with TestClient(app) as client:
            r1 = client.post("/api/llm/chat", json={"prompt": "hi"})
            assert r1.status_code == 500
            assert secret_msg not in r1.text
            assert re.search(r"request_id=[0-9a-f]{12}", r1.json()["detail"])

            r2 = client.post(
                "/api/video/generate/sync",
                json={"text": "hello", "frame_template": "1080x1920/image_test.html"},
            )
            assert r2.status_code == 500
            assert secret_msg not in r2.text
            assert re.search(r"request_id=[0-9a-f]{12}", r2.json()["detail"])
    finally:
        app.dependency_overrides.pop(get_pixelle_video, None)


async def test_f7_publish_requires_video_level_approval(setup_env, fake_publish_io):
    from api.app import app

    ffmpeg_calls, upload_calls = fake_publish_io
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        task_id = await _make_task_with_clip(client, approve_video=False)
        resp = await client.post(f"/api/orchestrator/tasks/{task_id}/publish", json={"title": "T"})
        assert resp.status_code == 400
        assert "approved" in resp.json()["detail"].lower()
        assert ffmpeg_calls == []
        assert upload_calls == []


async def test_f5_publish_is_idempotent_and_blocks_in_progress(setup_env, fake_publish_io):
    from api.app import app
    from orchestrator.db import get_db

    ffmpeg_calls, upload_calls = fake_publish_io
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        task_id = await _make_task_with_clip(client, approve_video=True)
        first = await client.post(f"/api/orchestrator/tasks/{task_id}/publish", json={"title": "T"})
        assert first.status_code == 200
        assert first.json()["video_id"] == "yt_1"

        second = await client.post(f"/api/orchestrator/tasks/{task_id}/publish", json={"title": "T"})
        assert second.status_code == 200
        assert second.json()["video_id"] == "yt_1"
        assert second.json()["idempotent"] is True
        assert len(ffmpeg_calls) == 1
        assert len(upload_calls) == 1

        # Simulate an upload whose post-upload DB update did not complete: retry returns 409 and never re-uploads.
        task2 = await _make_task_with_clip(client, approve_video=True)
        async with get_db() as conn:
            await conn.execute(
                "INSERT INTO publish_records (id, video_task_id, platform, status) VALUES (?,?,?,?)",
                ("rec_inflight", task2, "youtube", "uploading"),
            )
            await conn.commit()
        conflict = await client.post(f"/api/orchestrator/tasks/{task2}/publish", json={"title": "T2"})
        assert conflict.status_code == 409
        assert len(upload_calls) == 1


def test_f8_devcontainer_scripts_derive_repo_root():
    root = Path(__file__).resolve().parents[2]
    for name in ("postCreate.sh", "postStart.sh"):
        content = (root / ".devcontainer" / name).read_text(encoding="utf-8")
        assert "/workspaces/Pixelle-Video" not in content
        assert "BASH_SOURCE[0]" in content
