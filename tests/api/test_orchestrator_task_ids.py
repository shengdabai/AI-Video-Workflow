"""Orchestrator routes only accept a task_id that is one plain path segment.

Task ids are server-generated UUID strings, and publish builds
output/exports/<task_id>/final.mp4 from the path parameter. Every route with
a {task_id} parameter answers 404 "Task not found" for a value with
characters other than letters, digits, "-" and "_", before any database or
file work. Creating shots for a task that does not exist also answers 404.

`_send_as_is` builds the ASGI request by hand, so the path reaches the app
exactly as a server passes it on (percent-decoded once, dot segments kept).
"""

import asyncio
import json
from urllib.parse import quote

import pytest

TASK_NOT_FOUND = {"detail": "Task not found"}


class _FakeProcess:
    returncode = 0

    async def communicate(self):
        return b"", b""


@pytest.fixture
async def app_dir(tmp_path, monkeypatch):
    app_dir = tmp_path / "app"
    (app_dir / "output" / "clips").mkdir(parents=True)
    (app_dir / "output" / "clips" / "0.mp4").write_bytes(b"clip")
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
def ffmpeg_calls(monkeypatch):
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return calls


@pytest.fixture
def uploads(monkeypatch):
    import orchestrator.publish.youtube as youtube

    calls = []

    async def fake_upload(video_path, meta):
        calls.append(video_path)
        return "video123"

    monkeypatch.setattr(youtube, "upload_to_youtube", fake_upload)
    return calls


async def _send_as_is(method: str, path: str, body=None) -> tuple[int, object]:
    """Send one request to the app the way uvicorn would (path not normalized)."""
    from api.app import app

    payload = b"" if body is None else json.dumps(body).encode("utf-8")
    headers = [(b"host", b"testserver")]
    if body is not None:
        headers.append((b"content-type", b"application/json"))
        headers.append((b"content-length", str(len(payload)).encode("ascii")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": quote(path, safe="/").encode("ascii"),
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    sent = []
    request_sent = False
    response_done = asyncio.Event()

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await response_done.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            response_done.set()

    await app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(raw) if raw else None


async def _create_task_with_approved_clip() -> str:
    from orchestrator.assets.manager import AssetManager
    from orchestrator.models import Asset

    status, task = await _send_as_is("POST", "/api/orchestrator/tasks", {"title": "t", "script": "s"})
    assert status == 201
    status, shots = await _send_as_is(
        "POST",
        f"/api/orchestrator/tasks/{task['id']}/shots",
        {"shots": [{"shot_index": 0, "prompt": "p"}]},
    )
    assert status == 201
    status, _ = await _send_as_is("POST", f"/api/orchestrator/shots/{shots[0]['id']}/approve")
    assert status == 200
    status, _ = await _send_as_is("POST", f"/api/orchestrator/tasks/{task['id']}/review/approve")
    assert status == 200
    await AssetManager().register(
        Asset(
            task_id=task["id"],
            shot_id=shots[0]["id"],
            type="video_clip",
            file_path="output/clips/0.mp4",
        )
    )
    return task["id"]


# Path parameter values as the app receives them (already percent-decoded).
REJECTED_TASK_IDS = [
    "..",
    ".",
    "...",
    "..\\..",
    "..\\..\\outer",
    "a\\b",
    "a.b",
    "a\x00b",
    "\x00",
    "a b",
    "a:b",
    "a%2e%2e",  # double-encoded on the wire; a literal "%" here
    "x" * 65,
]

ROUTES = [
    ("GET", "/api/orchestrator/tasks/{}", None),
    ("POST", "/api/orchestrator/tasks/{}/shots", {"shots": [{"shot_index": 0, "prompt": "p"}]}),
    ("GET", "/api/orchestrator/tasks/{}/shots", None),
    ("POST", "/api/orchestrator/tasks/{}/review/approve", None),
    ("POST", "/api/orchestrator/tasks/{}/publish", {"title": "Title"}),
]


@pytest.mark.parametrize(("method", "template", "body"), ROUTES)
@pytest.mark.parametrize("task_id", REJECTED_TASK_IDS)
async def test_routes_reject_task_ids_that_are_not_one_plain_segment(
    app_dir, ffmpeg_calls, uploads, method, template, body, task_id
):
    status, data = await _send_as_is(method, template.format(task_id), body)
    assert status == 404
    assert data == TASK_NOT_FOUND
    assert ffmpeg_calls == []
    assert uploads == []
    assert not (app_dir / "output" / "exports").exists()
    assert not (app_dir / "output" / "final.mp4.txt").exists()


async def test_publish_with_dot_segment_task_id_writes_nothing(app_dir, ffmpeg_calls, uploads):
    task_id = await _create_task_with_approved_clip()
    for path in (
        "/api/orchestrator/tasks/../publish",
        "/api/orchestrator/tasks/..\\..\\outer/publish",
    ):
        status, data = await _send_as_is("POST", path, {"title": "Title"})
        assert status == 404
        assert data == TASK_NOT_FOUND
    assert ffmpeg_calls == []
    assert uploads == []
    assert list((app_dir / "output").iterdir()) == [app_dir / "output" / "clips"]

    # The real task id still publishes into output/exports/<task_id>/.
    status, data = await _send_as_is("POST", f"/api/orchestrator/tasks/{task_id}/publish", {"title": "Title"})
    assert status == 200
    assert data["video_id"] == "video123"
    assert uploads == [f"output/exports/{task_id}/final.mp4"]


async def test_create_shots_for_unknown_task_answers_404(app_dir):
    from orchestrator.queue.manager import QueueManager

    unknown = "00000000-0000-4000-8000-000000000000"
    status, data = await _send_as_is(
        "POST",
        f"/api/orchestrator/tasks/{unknown}/shots",
        {"shots": [{"shot_index": 0, "prompt": "p"}]},
    )
    assert status == 404
    assert data == TASK_NOT_FOUND
    assert await QueueManager().get_shots_by_video(unknown) == []


async def test_routes_accept_server_generated_task_ids(app_dir):
    status, task = await _send_as_is("POST", "/api/orchestrator/tasks", {"title": "t", "script": "s"})
    assert status == 201
    task_id = task["id"]

    status, data = await _send_as_is("GET", f"/api/orchestrator/tasks/{task_id}")
    assert (status, data["id"]) == (200, task_id)

    status, shots = await _send_as_is(
        "POST", f"/api/orchestrator/tasks/{task_id}/shots", {"shots": [{"shot_index": 0, "prompt": "p"}]}
    )
    assert status == 201
    assert shots[0]["video_task_id"] == task_id

    status, listed = await _send_as_is("GET", f"/api/orchestrator/tasks/{task_id}/shots")
    assert (status, [s["id"] for s in listed]) == (200, [shots[0]["id"]])

    status, data = await _send_as_is("POST", f"/api/orchestrator/tasks/{task_id}/review/approve")
    assert (status, data) == (200, {"ok": True})

    # A well-formed id that does not exist still answers 404 from the lookup.
    status, data = await _send_as_is("GET", "/api/orchestrator/tasks/no_such_task-1")
    assert (status, data) == (404, TASK_NOT_FOUND)
