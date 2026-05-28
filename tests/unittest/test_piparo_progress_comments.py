from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pr_agent.algo.utils import PRReviewHeader
from pr_agent.tools.pr_code_suggestions import (
    PIPARO_SUGGESTIONS_PROGRESS_MARKER,
    PIPARO_SUGGESTIONS_STATUS_CONTEXT,
    PIPARO_SUGGESTIONS_STATUS_PENDING,
    PRCodeSuggestions,
)
from pr_agent.tools.pr_reviewer import PIPARO_REVIEW_PROGRESS_MARKER, PRReviewer


class FakeProvider:
    def __init__(self, comments=None):
        self.comments = comments or []
        self.edited = []
        self.removed = []
        self.published = []

    def get_issue_comments(self):
        return self.comments

    def publish_comment(self, body, is_temporary=False):
        comment = SimpleNamespace(body=body, is_temporary=is_temporary)
        self.comments.append(comment)
        self.published.append(comment)
        return comment

    def edit_comment(self, comment, body):
        comment.body = body
        self.edited.append(comment)

    def remove_comment(self, comment):
        self.removed.append(comment)
        if comment in self.comments:
            self.comments.remove(comment)

    def publish_persistent_comment(self, body, initial_header, update_header=True, final_update_message=True):
        for comment in self.comments:
            if comment.body.startswith(initial_header):
                self.edit_comment(comment, body)
                return comment
        return self.publish_comment(body)


class FakeStatusProvider(FakeProvider):
    def __init__(self, comments=None):
        super().__init__(comments)
        self.statuses = []
        self.completed_statuses = []

    def get_pr_url(self):
        return "https://github.com/acme/repo/pull/1"

    def publish_progress_status(self, context, description, target_url=None):
        status = {"context": context, "description": description, "target_url": target_url}
        self.statuses.append(status)
        return status

    def complete_progress_status(self, progress_status, description, success=True, target_url=None):
        status = {
            "progress_status": progress_status,
            "description": description,
            "success": success,
            "target_url": target_url,
        }
        self.completed_statuses.append(status)
        return status


class Settings(dict):
    def __getattr__(self, item):
        return self[item]


def _make_code_suggestions(provider):
    obj = PRCodeSuggestions.__new__(PRCodeSuggestions)
    obj.git_provider = provider
    return obj


def _make_reviewer(provider):
    obj = PRReviewer.__new__(PRReviewer)
    obj.git_provider = provider
    return obj


def test_suggestions_progress_uses_marker_without_overwriting_final_comment():
    final_comment = SimpleNamespace(body="## PR Code Suggestions ✨\n\nExisting suggestions")
    progress_comment = SimpleNamespace(body=f"{PIPARO_SUGGESTIONS_PROGRESS_MARKER}\nold progress")
    provider = FakeProvider([final_comment, progress_comment])
    tool = _make_code_suggestions(provider)

    result = tool._publish_or_update_progress_comment("new progress")

    assert result is progress_comment
    assert final_comment.body == "## PR Code Suggestions ✨\n\nExisting suggestions"
    assert progress_comment.body == f"{PIPARO_SUGGESTIONS_PROGRESS_MARKER}\nnew progress"


def test_suggestions_progress_does_not_delete_final_comment_on_error():
    final_comment = SimpleNamespace(body="## PR Code Suggestions ✨\n\nExisting suggestions")
    progress_comment = SimpleNamespace(body=f"{PIPARO_SUGGESTIONS_PROGRESS_MARKER}\nold progress")
    provider = FakeProvider([final_comment, progress_comment])
    tool = _make_code_suggestions(provider)
    tool.progress_response = progress_comment

    tool._remove_progress_comment()

    assert final_comment in provider.comments
    assert progress_comment in provider.removed


def test_suggestions_progress_uses_status_when_available():
    provider = FakeStatusProvider()
    tool = _make_code_suggestions(provider)

    result = tool._publish_progress_status()

    assert result == provider.statuses[0]
    assert provider.statuses == [
        {
            "context": PIPARO_SUGGESTIONS_STATUS_CONTEXT,
            "description": PIPARO_SUGGESTIONS_STATUS_PENDING,
            "target_url": "https://github.com/acme/repo/pull/1",
        }
    ]
    assert provider.published == []


def test_suggestions_progress_status_can_be_completed():
    provider = FakeStatusProvider()
    tool = _make_code_suggestions(provider)
    tool.progress_status = provider.publish_progress_status(
        PIPARO_SUGGESTIONS_STATUS_CONTEXT,
        PIPARO_SUGGESTIONS_STATUS_PENDING,
    )

    result = tool._complete_progress_status(
        "Code suggestions ready",
        target_url="https://github.com/acme/repo/pull/1#comment",
    )

    assert result == provider.completed_statuses[0]
    assert provider.completed_statuses[0]["progress_status"] == tool.progress_status
    assert provider.completed_statuses[0]["description"] == "Code suggestions ready"
    assert provider.completed_statuses[0]["success"] is True
    assert provider.completed_statuses[0]["target_url"] == "https://github.com/acme/repo/pull/1#comment"


def test_reviewer_progress_uses_marker_without_overwriting_final_comment():
    review_header = f"{PRReviewHeader.REGULAR.value} 🔍"
    final_comment = SimpleNamespace(body=f"{review_header}\n\nExisting review")
    progress_comment = SimpleNamespace(body=f"{PIPARO_REVIEW_PROGRESS_MARKER}\nold progress")
    provider = FakeProvider([final_comment, progress_comment])
    tool = _make_reviewer(provider)

    result = tool._publish_or_update_progress_comment("new progress")

    assert result is progress_comment
    assert final_comment.body == f"{review_header}\n\nExisting review"
    assert progress_comment.body == f"{PIPARO_REVIEW_PROGRESS_MARKER}\nnew progress"


@patch("pr_agent.tools.pr_reviewer.get_settings")
def test_reviewer_final_output_updates_persistent_review_and_removes_progress(mock_get_settings):
    review_header = f"{PRReviewHeader.REGULAR.value} 🔍"
    final_comment = SimpleNamespace(body=f"{review_header}\n\nExisting review")
    progress_comment = SimpleNamespace(body=f"{PIPARO_REVIEW_PROGRESS_MARKER}\nold progress")
    provider = FakeProvider([final_comment, progress_comment])
    tool = _make_reviewer(provider)
    mock_get_settings.return_value = SimpleNamespace(pr_reviewer=Settings(final_update_message=False))

    result = tool._publish_persistent_review("new review", review_header, progress_comment)

    assert result is final_comment
    assert final_comment.body == "new review"
    assert progress_comment in provider.removed
    assert progress_comment not in provider.comments
