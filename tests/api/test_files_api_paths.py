"""GET /api/files/{file_path} only serves regular files inside its roots.

uvicorn percent-decodes the request path once and passes it on without
removing "." or ".." segments (for example a request sent with
`curl --path-as-is`). httpx, which TestClient uses, removes literal ".."
segments before sending, so `_get_as_is` builds the ASGI request by hand to
send the path exactly as given.
"""

import asyncio
from urllib.parse import quote, unquote

import pytest
from fastapi.testclient import TestClient

OUTER_MARKER = "outer-file-marker-7c1f"
APP_MARKER = "app-config-marker-52ad"


@pytest.fixture
def app_dir(tmp_path, monkeypatch):
    """Working directory with served roots, plus files that must not be served."""
    monkeypatch.setenv("ORCHESTRATOR_DB_PATH", str(tmp_path / "orchestrator.db"))
    (tmp_path / "config.yaml").write_text(f"key: {OUTER_MARKER}\n", encoding="utf-8")

    app_dir = tmp_path / "app"
    (app_dir / "output" / "sub").mkdir(parents=True)
    (app_dir / "output" / "sub" / "clip.json").write_text('{"clip": 1}', encoding="utf-8")
    (app_dir / "output" / "top.json").write_text('{"top": 1}', encoding="utf-8")
    (app_dir / "workflows" / "runninghub").mkdir(parents=True)
    (app_dir / "workflows" / "runninghub" / "flow.json").write_text(
        '{"flow": 1}', encoding="utf-8"
    )
    (app_dir / "data" / "bgm").mkdir(parents=True)
    (app_dir / "data" / "bgm" / "track.json").write_text('{"track": 1}', encoding="utf-8")
    # A config file in the working directory but outside every served root.
    (app_dir / "config.yaml").write_text(f"key: {APP_MARKER}\n", encoding="utf-8")

    monkeypatch.chdir(app_dir)
    return app_dir


async def _get_as_is(raw_path: str) -> tuple[int, bytes]:
    """Send GET raw_path to the app the way uvicorn would (decoded once, no normalisation)."""
    from api.app import app

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": unquote(raw_path),
        "raw_path": raw_path.encode("ascii"),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
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
            return {"type": "http.request", "body": b"", "more_body": False}
        # Later calls wait for the response, then report the client gone.
        await response_done.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            response_done.set()

    await app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body


def _deep(prefix: str, segment: str, target: str) -> str:
    return prefix + segment * 40 + target


REJECTED_PATHS = [
    # literal ".." segments
    "output/../config.yaml",
    "../config.yaml",
    "../../config.yaml",
    "workflows/../config.yaml",
    "data/bgm/../../config.yaml",
    "output/sub/../../../config.yaml",
    # percent-encoded dots
    "%2e%2e/config.yaml",
    "output/%2e%2e/config.yaml",
    "%2e%2e/%2e%2e/config.yaml",
    "%2E%2E/config.yaml",
    # percent-encoded separator
    "..%2fconfig.yaml",
    "output/..%2f..%2fconfig.yaml",
    # backslash separator
    "..%5cconfig.yaml",
    "output\\..\\config.yaml",
    # double-encoded: decoded once this is a literal "%2e%2e" name, not ".."
    "%252e%252e/config.yaml",
    # deep chains up to the filesystem root
    _deep("", "%2e%2e/", "etc/hosts"),
    _deep("output/", "%2e%2e/", "etc/hosts"),
    _deep("", "..%2f", "etc/hosts"),
    # absolute paths
    "%2Fetc%2Fhosts",
    "/etc/hosts",
    "C:%5CWindows%5Cwin.ini",
    # NUL byte
    "output/sub/clip.json%00.png",
    "%00",
    # directories are not files
    "output",
    "output/sub",
    "sub",
]


def _assert_not_served(status: int, body: bytes) -> None:
    assert status == 404
    text = body.decode("utf-8", errors="replace")
    assert OUTER_MARKER not in text
    assert APP_MARKER not in text
    assert "localhost" not in text  # /etc/hosts content


@pytest.mark.parametrize("raw", REJECTED_PATHS)
async def test_request_paths_outside_served_roots_return_404(app_dir, raw):
    if raw.startswith("/"):
        request_path = "/api/files/" + raw
    else:
        request_path = "/api/files/" + raw.replace("\\", "%5C")
    status, body = await _get_as_is(request_path)
    _assert_not_served(status, body)


async def test_absolute_path_to_existing_file_returns_404(app_dir):
    target = quote(str(app_dir / "config.yaml"), safe="")
    status, body = await _get_as_is("/api/files/" + target)
    _assert_not_served(status, body)


@pytest.mark.parametrize(
    "raw",
    [
        "%2e%2e/config.yaml",
        "output/%2e%2e/config.yaml",
        "..%2fconfig.yaml",
        _deep("output/", "%2e%2e/", "etc/hosts"),
    ],
)
def test_encoded_paths_through_test_client_return_404(app_dir, raw):
    from api.app import app

    with TestClient(app) as client:
        resp = client.get("/api/files/" + raw)
    _assert_not_served(resp.status_code, resp.content)


def test_symlinks_leaving_served_root_return_404(app_dir):
    output = app_dir / "output"
    try:
        (output / "link.yaml").symlink_to(app_dir.parent / "config.yaml")
        (output / "linkdir").symlink_to(app_dir.parent, target_is_directory=True)
        (output / "inside.json").symlink_to(output / "sub" / "clip.json")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available on this platform")

    from api.app import app

    with TestClient(app) as client:
        for path in ("link.yaml", "output/link.yaml", "linkdir/config.yaml"):
            resp = client.get("/api/files/" + path)
            _assert_not_served(resp.status_code, resp.content)

        # A symlink that stays inside the root is still served.
        resp = client.get("/api/files/inside.json")
        assert resp.status_code == 200
        assert resp.json() == {"clip": 1}


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("output/sub/clip.json", {"clip": 1}),
        ("sub/clip.json", {"clip": 1}),
        ("top.json", {"top": 1}),
        ("output/./sub/clip.json", {"clip": 1}),
        ("workflows/runninghub/flow.json", {"flow": 1}),
        ("data/bgm/track.json", {"track": 1}),
    ],
)
def test_files_inside_served_roots_are_served(app_dir, path, expected):
    from api.app import app

    with TestClient(app) as client:
        resp = client.get("/api/files/" + path)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == expected


async def test_nested_file_served_as_is(app_dir):
    status, body = await _get_as_is("/api/files/output/sub/clip.json")
    assert status == 200
    assert body == b'{"clip": 1}'


def test_unknown_path_returns_404(app_dir):
    from api.app import app

    with TestClient(app) as client:
        resp = client.get("/api/files/output/sub/missing.json")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "File not found"}


@pytest.mark.parametrize("name", ['quote"name.json', "结果.json"])
def test_file_response_safely_encodes_download_name(app_dir, name):
    from api.app import app

    (app_dir / "output" / name).write_text('{"ok": true}', encoding="utf-8")
    with TestClient(app) as client:
        resp = client.get("/api/files/" + quote(name, safe=""))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert resp.headers["content-disposition"] == "inline; filename*=utf-8''" + quote(name)
    assert "\r" not in resp.headers["content-disposition"]
    assert "\n" not in resp.headers["content-disposition"]


async def test_file_response_encodes_control_characters_in_header(app_dir):
    from api.routers.files import get_file

    # ASGI routing rejects newlines in paths. Exercise the response builder
    # directly so a local filename can never introduce raw header newlines.
    name = "line\r\nbreak.json"
    (app_dir / "output" / name).write_text("{}", encoding="utf-8")
    response = await get_file(name)
    assert response.headers["content-disposition"] == "inline; filename*=utf-8''" + quote(name)

