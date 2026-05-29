from types import SimpleNamespace
from unittest.mock import MagicMock

from pr_agent.git_providers.github_provider import GithubProvider


def _make_provider():
    provider = GithubProvider.__new__(GithubProvider)
    provider.last_commit_id = MagicMock()
    provider.last_commit_id.create_status.return_value = SimpleNamespace(id=1)
    provider.get_pr_url = MagicMock(return_value="https://github.com/acme/repo/pull/1")
    return provider


def test_publish_progress_status_creates_pending_commit_status():
    provider = _make_provider()

    result = provider.publish_progress_status("piparo-pr-agent", "Review in progress")

    provider.last_commit_id.create_status.assert_called_once_with(
        state="pending",
        target_url="https://github.com/acme/repo/pull/1",
        description="Review in progress",
        context="piparo-pr-agent",
    )
    assert result["context"] == "piparo-pr-agent"
    assert result["target_url"] == "https://github.com/acme/repo/pull/1"


def test_complete_progress_status_creates_success_commit_status_for_same_context():
    provider = _make_provider()
    progress_status = {"context": "piparo-pr-agent", "target_url": "https://github.com/acme/repo/pull/1"}

    provider.complete_progress_status(
        progress_status,
        "Code suggestions ready",
        target_url="https://github.com/acme/repo/pull/1#issuecomment-1",
    )

    provider.last_commit_id.create_status.assert_called_once_with(
        state="success",
        target_url="https://github.com/acme/repo/pull/1#issuecomment-1",
        description="Code suggestions ready",
        context="piparo-pr-agent",
    )


def test_complete_progress_status_creates_failure_commit_status_on_error():
    provider = _make_provider()
    progress_status = {"context": "piparo-pr-agent", "target_url": "https://github.com/acme/repo/pull/1"}

    provider.complete_progress_status(progress_status, "Failed to generate code suggestions", success=False)

    provider.last_commit_id.create_status.assert_called_once_with(
        state="failure",
        target_url="https://github.com/acme/repo/pull/1",
        description="Failed to generate code suggestions",
        context="piparo-pr-agent",
    )


def test_commit_status_description_is_clipped_to_github_limit():
    provider = _make_provider()
    description = "x" * 200

    provider.publish_progress_status("piparo-pr-agent", description)

    assert len(provider.last_commit_id.create_status.call_args.kwargs["description"]) == 140
    assert provider.last_commit_id.create_status.call_args.kwargs["description"].endswith("...")
