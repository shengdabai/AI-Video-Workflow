import json
import os
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from google.oauth2.credentials import Credentials

from orchestrator.publish import youtube


@pytest.fixture
def token_path(tmp_path, monkeypatch):
    path = tmp_path / "tokens" / "youtube_token.json"
    monkeypatch.setattr(youtube, "TOKEN_PATH", str(path))
    monkeypatch.setattr(youtube, "Request", Mock(side_effect=AssertionError("No network in tests")))
    return path


def credentials():
    return Credentials(
        token="test-access-token", refresh_token="test-refresh-token",
        token_uri="https://oauth2.googleapis.com/token", client_id="test-client-id",
        client_secret="test-client-secret", scopes=youtube.SCOPES,
        expiry=datetime.now() + timedelta(hours=1),
    )


def test_loads_json_without_starting_oauth(token_path, monkeypatch):
    token_path.parent.mkdir()
    token_path.write_text(credentials().to_json(), encoding="utf-8")
    start_flow = Mock(side_effect=AssertionError("Cached credentials should be used"))
    monkeypatch.setattr(youtube.InstalledAppFlow, "from_client_secrets_file", start_flow)

    assert youtube._get_credentials().token == "test-access-token"
    start_flow.assert_not_called()


def test_first_authorization_saves_private_json(token_path, monkeypatch):
    creds = credentials()
    flow = Mock()
    flow.run_local_server.return_value = creds
    monkeypatch.setattr(youtube.InstalledAppFlow, "from_client_secrets_file", Mock(return_value=flow))

    assert youtube._get_credentials() is creds
    assert json.loads(token_path.read_text(encoding="utf-8"))["refresh_token"] == creds.refresh_token
    if os.name != "nt":
        assert token_path.stat().st_mode & 0o777 == 0o600
    assert list(token_path.parent.iterdir()) == [token_path]


def test_refresh_replaces_cache_with_private_json(token_path, monkeypatch):
    token_path.parent.mkdir()
    token_path.write_text("old-cache", encoding="utf-8")
    token_path.chmod(0o644)
    creds = credentials()
    creds.expiry = datetime(2000, 1, 1)
    refreshed = Mock()
    monkeypatch.setattr(creds, "refresh", refreshed)
    monkeypatch.setattr(youtube.Credentials, "from_authorized_user_file", Mock(return_value=creds))
    monkeypatch.setattr(youtube, "Request", Mock())

    assert youtube._get_credentials() is creds
    refreshed.assert_called_once()
    assert json.loads(token_path.read_text(encoding="utf-8"))["token"] == creds.token
    if os.name != "nt":
        assert token_path.stat().st_mode & 0o777 == 0o600


def test_cache_write_failure_keeps_previous_file(token_path, monkeypatch):
    token_path.parent.mkdir()
    token_path.write_text("previous-cache", encoding="utf-8")
    creds = credentials()
    creds.expiry = datetime(2000, 1, 1)
    monkeypatch.setattr(creds, "refresh", Mock())
    monkeypatch.setattr(youtube.Credentials, "from_authorized_user_file", Mock(return_value=creds))
    monkeypatch.setattr(youtube, "Request", Mock())
    monkeypatch.setattr(creds, "to_json", Mock(side_effect=OSError("disk failure")))

    with pytest.raises(OSError, match="disk failure"):
        youtube._get_credentials()
    assert token_path.read_text(encoding="utf-8") == "previous-cache"
    assert list(token_path.parent.iterdir()) == [token_path]
