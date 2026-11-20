"""POST /api/orchestrator/tasks/{id}/publish validates bgm_path before ffmpeg runs.

bgm_path must name an audio file inside bgm/, data/bgm/ or output/ (relative
to the working directory). Other values answer 400 with a fixed body. ffmpeg
is replaced by a stub that records its argument list, and the YouTube upload
by a stub that returns a fixed id.
"""

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

BGM_ERROR = {"detail": "bgm_path must name an audio file in bgm/, data/bgm/ or output/"}


class _FakeProcess:
    returncode = 0

    async def communicate(self):
        return b"", b""


@pytest.fixture
async def app_dir(tmp_path, monkeypatch):
    """Working directory with BGM roots, an approved shot with a clip, and stubs."""
    (tmp_path / "outer.mp3").write_bytes(b"outer audio")

    app_dir = tmp_path / "app"
    (app_dir / "bgm").mkdir(parents=True)
    (app_dir / "bgm" / "default.mp3").write_bytes(b"default audio")
    (app_dir / "bgm" / "shared.mp3").write_bytes(b"default shared audio")
    (app_dir / "data" / "bgm").mkdir(parents=True)
    (app_dir / "data" / "bgm" / "custom.mp3").write_bytes(b"custom audio")
    (app_dir / "data" / "bgm" / "shared.mp3").write_bytes(b"custom shared audio")
    (app_dir / "output" / "audio").mkdir(parents=True)
    (app_dir / "output" / "audio" / "voice.wav").write_bytes(b"voice audio")
    (app_dir / "output" / "notes.txt").write_text("not audio\n", encoding="utf-8")
    (app_dir / "output" / "clips").mkdir()
    (app_dir / "output" / "clips" / "0.mp4").write_bytes(b"clip")
    (app_dir / "config.yaml").write_text("key: value\n", encoding="utf-8")
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
    """Record every ffmpeg start; fail the test if anything goes through a shell."""
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakeProcess()

    async def no_shell(*args, **kwargs):
        raise AssertionError("ffmpeg must not be started through a shell")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", no_shell)
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


@pytest.fixture
async def client(app_dir, ffmpeg_calls, uploads):
    from api.app import app
    from orchestrator.assets.manager import AssetManager
    from orchestrator.models import Asset

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        task = (await c.post("/api/orchestrator/tasks", json={"title": "t", "script": "s"})).json()
        shots = (
            await c.post(
                f"/api/orchestrator/tasks/{task['id']}/shots",
                json={"shots": [{"shot_index": 0, "prompt": "p"}]},
            )
        ).json()
        assert (await c.post(f"/api/orchestrator/shots/{shots[0]['id']}/approve")).status_code == 200
        assert (await c.post(f"/api/orchestrator/tasks/{task['id']}/review/approve")).status_code == 200
        await AssetManager().register(
            Asset(
                task_id=task["id"],
                shot_id=shots[0]["id"],
                type="video_clip",
                file_path="output/clips/0.mp4",
            )
        )
        c.task_id = task["id"]
        yield c


async def _publish(client, bgm_path):
    body = {"title": "Title"}
    if bgm_path is not ...:
        body["bgm_path"] = bgm_path
    return await client.post(f"/api/orchestrator/tasks/{client.task_id}/publish", json=body)


def _bgm_argument(args: tuple) -> str:
    """The value after the second -i is the BGM input."""
    inputs = [i for i, arg in enumerate(args) if arg == "-i"]
    assert len(inputs) == 2, args
    return args[inputs[1] + 1]


REJECTED_BGM = [
    # ".." segments
    "../outer.mp3",
    "bgm/../../outer.mp3",
    "data/bgm/../../../outer.mp3",
    "output/../../outer.mp3",
    "bgm\\..\\..\\outer.mp3",
    # absolute paths
    "/etc/hosts",
    "C:\\Windows\\win.ini",
    "C:/Windows/win.ini",
    # NUL byte
    "bgm/default.mp3\x00",
    # ffmpeg options, protocols and URLs are not file names in the roots
    "-i",
    "-filter_complex",
    "http://example.com/a.mp3",
    "file:bgm/default.mp3",
    "concat:bgm/default.mp3|config.yaml",
    "pipe:0",
    # not an audio file inside a root
    "config.yaml",
    "output/notes.txt",
    "bgm",
    "output/audio",
    "bgm/missing.mp3",
    "missing.mp3",
]


@pytest.mark.parametrize("bgm_path", REJECTED_BGM)
async def test_publish_rejects_bgm_outside_roots(client, ffmpeg_calls, uploads, bgm_path):
    resp = await _publish(client, bgm_path)
    assert resp.status_code == 400
    assert resp.json() == BGM_ERROR
    assert ffmpeg_calls == []
    assert uploads == []


async def test_publish_rejects_absolute_bgm_paths(client, app_dir, ffmpeg_calls, uploads):
    # Absolute paths are rejected even when they point inside a root.
    for target in (app_dir.parent / "outer.mp3", app_dir / "bgm" / "default.mp3"):
        resp = await _publish(client, str(target))
        assert resp.status_code == 400
        assert resp.json() == BGM_ERROR
    assert ffmpeg_calls == []
    assert uploads == []


async def test_publish_rejects_bgm_symlink_leaving_root(client, app_dir, ffmpeg_calls):
    bgm = app_dir / "bgm"
    try:
        (bgm / "link.mp3").symlink_to(app_dir.parent / "outer.mp3")
        (bgm / "inside.mp3").symlink_to(bgm / "default.mp3")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this platform")

    resp = await _publish(client, "bgm/link.mp3")
    assert resp.status_code == 400
    assert resp.json() == BGM_ERROR
    assert ffmpeg_calls == []

    # A symlink that stays inside the root is accepted.
    resp = await _publish(client, "bgm/inside.mp3")
    assert resp.status_code == 200
    assert _bgm_argument(ffmpeg_calls[0][0]) == str((bgm / "default.mp3").resolve())


@pytest.mark.parametrize(
    ("bgm_path", "expected"),
    [
        ("bgm/default.mp3", "bgm/default.mp3"),
        ("./bgm/default.mp3", "bgm/default.mp3"),
        ("default.mp3", "bgm/default.mp3"),
        ("data/bgm/custom.mp3", "data/bgm/custom.mp3"),
        ("custom.mp3", "data/bgm/custom.mp3"),
        ("shared.mp3", "data/bgm/shared.mp3"),  # data/bgm/ before bgm/
        ("bgm/shared.mp3", "bgm/shared.mp3"),
        ("output/audio/voice.wav", "output/audio/voice.wav"),
    ],
)
async def test_publish_passes_resolved_bgm_to_ffmpeg(
    client, app_dir, ffmpeg_calls, uploads, bgm_path, expected
):
    resp = await _publish(client, bgm_path)
    assert resp.status_code == 200
    assert resp.json()["video_id"] == "video123"
    assert len(ffmpeg_calls) == 1
    args, kwargs = ffmpeg_calls[0]
    # One argument per list item, no shell involved.
    assert args[0] == "ffmpeg"
    assert all(isinstance(arg, str) for arg in args)
    assert "shell" not in kwargs
    assert _bgm_argument(args) == str((app_dir / expected).resolve())
    assert uploads == ["output/exports/" + client.task_id + "/final.mp4"]


@pytest.mark.parametrize("bgm_path", [..., None, ""])
async def test_publish_without_bgm(client, ffmpeg_calls, uploads, bgm_path):
    resp = await _publish(client, bgm_path)
    assert resp.status_code == 200
    args, _ = ffmpeg_calls[0]
    assert args.count("-i") == 1
    assert "-stream_loop" not in args
