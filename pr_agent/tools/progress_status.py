from pr_agent.config_loader import get_settings
from pr_agent.log import get_logger

PIPARO_PROGRESS_STATUS_CONTEXT = "piparo-pr-agent"
PIPARO_PROGRESS_STATUS_PENDING = "Review in progress"
PIPARO_PROGRESS_STATUS_FAILURE = "Failed to process request"


def publish_progress_status(git_provider, description: str = PIPARO_PROGRESS_STATUS_PENDING):
    if not get_settings().config.publish_output_progress:
        return None

    publish_status = getattr(git_provider, "publish_progress_status", None)
    if not callable(publish_status):
        return None

    try:
        get_pr_url = getattr(git_provider, "get_pr_url", None)
        target_url = get_pr_url() if callable(get_pr_url) else ""
        return publish_status(PIPARO_PROGRESS_STATUS_CONTEXT, description, target_url=target_url)
    except Exception as e:
        get_logger().exception(f"Failed to publish progress status, error: {e}")
        return None


def complete_progress_status(git_provider, progress_status, description: str, success: bool = True,
                             target_url: str = None):
    if not progress_status:
        return None

    complete_status = getattr(git_provider, "complete_progress_status", None)
    if not callable(complete_status):
        return None

    try:
        return complete_status(progress_status, description, success=success, target_url=target_url)
    except Exception as e:
        get_logger().exception(f"Failed to complete progress status, error: {e}")
        return None


def get_response_url(git_provider, response) -> str:
    if not response:
        return ""
    try:
        return git_provider.get_comment_url(response)
    except Exception:
        return getattr(response, "html_url", "")
