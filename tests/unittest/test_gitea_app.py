import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from pr_agent.config_loader import get_settings
from pr_agent.servers.gitea_app import app, handle_request


@pytest.fixture
def gitea_settings():
    settings = get_settings()
    original = {
        "webhook_secret": settings.get("GITEA.WEBHOOK_SECRET", None),
        "allowed_owners": settings.get("GITEA.ALLOWED_OWNERS", []),
        "bot_user": settings.get("GITEA.BOT_USER", ""),
    }
    settings.set("GITEA.WEBHOOK_SECRET", "test-secret")
    settings.set("GITEA.ALLOWED_OWNERS", ["piparotech"])
    settings.set("GITEA.BOT_USER", "piparo-agent")
    try:
        yield settings
    finally:
        for key, value in original.items():
            settings.set(f"GITEA.{key.upper()}", value)


def _signature(body: bytes, secret: str = "test-secret") -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _payload(owner: str = "piparotech", sender: str = "patrick") -> dict:
    return {
        "action": "opened",
        "repository": {"full_name": f"{owner}/service"},
        "sender": {"login": sender},
        "pull_request": {
            "url": f"https://git.example/api/v1/repos/{owner}/service/pulls/1",
            "title": "Test PR",
            "labels": [],
            "head": {"ref": "feature"},
            "base": {"ref": "main"},
        },
    }


def _post(client: TestClient, payload: dict, **headers):
    body = json.dumps(payload, separators=(",", ":")).encode()
    request_headers = {
        "content-type": "application/json",
        "x-forgejo-event": "pull_request",
        "x-gitea-signature": _signature(body),
        **headers,
    }
    return client.post("/api/v1/gitea_webhooks", content=body, headers=request_headers)


def test_health_endpoints():
    client = TestClient(app)
    assert client.get("/").json() == {"status": "ok"}
    assert client.get("/healthz").json() == {"status": "ok"}


def test_valid_forgejo_webhook_uses_forgejo_event_header(gitea_settings):
    client = TestClient(app)
    with patch("pr_agent.servers.gitea_app.run_async_function_in_thread") as run:
        response = _post(client, _payload())

    assert response.status_code == 200
    assert run.call_args.kwargs["event"] == "pull_request"


def test_missing_signature_is_rejected(gitea_settings):
    client = TestClient(app)
    body = json.dumps(_payload()).encode()
    response = client.post(
        "/api/v1/gitea_webhooks",
        content=body,
        headers={"content-type": "application/json", "x-forgejo-event": "pull_request"},
    )
    assert response.status_code == 403


def test_invalid_signature_is_rejected(gitea_settings):
    client = TestClient(app)
    response = _post(client, _payload(), **{"x-gitea-signature": "0" * 64})
    assert response.status_code == 403


def test_unconfigured_secret_fails_closed(gitea_settings):
    get_settings().set("GITEA.WEBHOOK_SECRET", "")
    client = TestClient(app)
    response = _post(client, _payload())
    assert response.status_code == 503


def test_unapproved_owner_is_rejected(gitea_settings):
    client = TestClient(app)
    response = _post(client, _payload(owner="outsider"))
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_bot_sender_is_ignored(gitea_settings):
    with patch("pr_agent.servers.gitea_app.PRAgent") as agent_cls:
        result = await handle_request(_payload(sender="piparo-agent"), "pull_request")
    assert result == {}
    agent_cls.assert_not_called()


@pytest.mark.asyncio
async def test_comment_command_accepts_leading_whitespace(gitea_settings):
    payload = _payload()
    payload["action"] = "created"
    payload["comment"] = {"body": "  /ask explain this"}
    agent = AsyncMock()

    with patch("pr_agent.servers.gitea_app.PRAgent", return_value=agent):
        await handle_request(payload, "issue_comment")

    agent.handle_request.assert_awaited_once_with(
        payload["pull_request"]["url"], "/ask explain this"
    )
