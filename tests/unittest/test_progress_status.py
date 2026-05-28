from types import SimpleNamespace
from unittest.mock import patch

from pr_agent.tools.progress_status import (
    PIPARO_PROGRESS_STATUS_CONTEXT,
    complete_progress_status,
    get_response_url,
    publish_progress_status,
)


class FakeProvider:
    def __init__(self):
        self.published = []
        self.completed = []

    def get_pr_url(self):
        return "https://github.com/acme/repo/pull/1"

    def get_comment_url(self, response):
        return response.html_url

    def publish_progress_status(self, context, description, target_url=None):
        status = {"context": context, "description": description, "target_url": target_url}
        self.published.append(status)
        return status

    def complete_progress_status(self, progress_status, description, success=True, target_url=None):
        status = {
            "progress_status": progress_status,
            "description": description,
            "success": success,
            "target_url": target_url,
        }
        self.completed.append(status)
        return status


def _settings(enabled=True):
    return SimpleNamespace(config=SimpleNamespace(publish_output_progress=enabled))


@patch("pr_agent.tools.progress_status.get_settings")
def test_publish_progress_status_uses_shared_context(mock_get_settings):
    mock_get_settings.return_value = _settings()
    provider = FakeProvider()

    status = publish_progress_status(provider)

    assert status == provider.published[0]
    assert status["context"] == PIPARO_PROGRESS_STATUS_CONTEXT
    assert status["description"] == "Review in progress"
    assert status["target_url"] == "https://github.com/acme/repo/pull/1"


@patch("pr_agent.tools.progress_status.get_settings")
def test_publish_progress_status_respects_progress_config(mock_get_settings):
    mock_get_settings.return_value = _settings(enabled=False)
    provider = FakeProvider()

    assert publish_progress_status(provider) is None
    assert provider.published == []


def test_complete_progress_status_updates_existing_status():
    provider = FakeProvider()
    progress_status = {"context": PIPARO_PROGRESS_STATUS_CONTEXT}

    status = complete_progress_status(
        provider,
        progress_status,
        "Review ready",
        target_url="https://github.com/acme/repo/pull/1#issuecomment-1",
    )

    assert status == provider.completed[0]
    assert status["progress_status"] == progress_status
    assert status["description"] == "Review ready"
    assert status["success"] is True
    assert status["target_url"] == "https://github.com/acme/repo/pull/1#issuecomment-1"


def test_get_response_url_uses_comment_url():
    provider = FakeProvider()
    response = SimpleNamespace(html_url="https://fallback", id=1)

    assert get_response_url(provider, response) == "https://fallback"
