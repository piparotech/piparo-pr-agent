import datetime
import json
import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from tiktoken import encoding_for_model, get_encoding

from pr_agent.config_loader import get_settings
from pr_agent.log import get_logger

RUN_USAGE_START = "<!-- pr-agent-ai-usage:start -->"
RUN_USAGE_END = "<!-- pr-agent-ai-usage:end -->"
TOTAL_USAGE_HEADER = "## PR-Agent Usage 📊"
TOTAL_USAGE_DATA_START = "<!-- pr-agent-ai-usage-total:data"
TOTAL_USAGE_DATA_END = "-->"


@dataclass
class AiCallUsage:
    provider: str
    model: str
    thinking_level: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    finish_reason: str
    is_estimated: bool = False


def record_ai_call_usage(
    ai_handler: Any,
    *,
    model: str,
    response: Any = None,
    system: str = "",
    user: str = "",
    output: str = "",
    finish_reason: str = "",
    thinking_level: str | None = None,
) -> None:
    """Store usage for one model call on the AI handler instance."""
    prompt_tokens, completion_tokens, total_tokens, is_estimated = _extract_usage_tokens(
        response, model, system, user, output
    )
    provider, model_name = split_provider_model(model)
    usage = AiCallUsage(
        provider=provider,
        model=model_name,
        thinking_level=thinking_level or "n/a",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        finish_reason=str(finish_reason or ""),
        is_estimated=is_estimated,
    )

    if not hasattr(ai_handler, "ai_usage_run_id"):
        ai_handler.ai_usage_run_id = str(uuid.uuid4())
    if not hasattr(ai_handler, "ai_usage_calls"):
        ai_handler.ai_usage_calls = []
    ai_handler.ai_usage_calls.append(usage)


def append_ai_usage_footer(body: str, ai_handler: Any, command: str, git_provider: Any = None) -> str:
    if not get_settings().config.get("publish_ai_usage", True):
        return body
    calls = get_ai_usage_calls(ai_handler)
    if not calls:
        return body

    body = _strip_existing_run_usage(body or "")
    supports_details = True
    try:
        supports_details = git_provider is None or git_provider.is_supported("gfm_markdown")
    except Exception:
        supports_details = True

    footer = _render_run_usage(command, calls, getattr(ai_handler, "ai_usage_run_id", ""), supports_details)
    return f"{body.rstrip()}\n\n{footer}" if body else footer


def publish_ai_usage_total_comment(git_provider: Any, ai_handler: Any, command: str) -> None:
    if not get_settings().config.get("publish_ai_usage_total_comment", True):
        return
    if not get_settings().config.get("publish_output", True):
        return

    calls = get_ai_usage_calls(ai_handler)
    run_id = getattr(ai_handler, "ai_usage_run_id", "")
    if not calls or not run_id:
        return

    try:
        comments = list(git_provider.get_issue_comments())
    except Exception as e:
        get_logger().debug(f"Skipping AI usage total comment; issue comments are unavailable: {e}")
        return

    existing_comment = None
    existing_body = ""
    for comment in comments:
        body = _get_comment_body(comment)
        if body.startswith(TOTAL_USAGE_HEADER) or TOTAL_USAGE_DATA_START in body:
            existing_comment = comment
            existing_body = body
            break

    data = _load_total_usage_data(existing_body)
    run_ids = {run.get("run_id") for run in data.get("runs", [])}
    if run_id not in run_ids:
        data.setdefault("runs", []).append(_build_run_entry(git_provider, ai_handler, command, calls))

    body = _render_total_usage(data)
    try:
        if existing_comment:
            git_provider.edit_comment(existing_comment, body)
        else:
            git_provider.publish_comment(body)
    except Exception as e:
        get_logger().debug(f"Failed to publish AI usage total comment: {e}")


def get_ai_usage_calls(ai_handler: Any) -> list[AiCallUsage]:
    return list(getattr(ai_handler, "ai_usage_calls", []) or [])


def split_provider_model(model: str) -> tuple[str, str]:
    model = str(model or "unknown")
    if "/" in model:
        provider, model_name = model.split("/", 1)
        return provider or "unknown", model_name or model
    lowered = model.lower()
    if lowered.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai", model
    if "claude" in lowered:
        return "anthropic", model
    if "gemini" in lowered:
        return "gemini", model
    return "unknown", model


def _extract_usage_tokens(response: Any, model: str, system: str, user: str, output: str) -> tuple[int, int, int, bool]:
    usage = _extract_usage_object(response)
    prompt_tokens = _get_usage_value(usage, "prompt_tokens", "input_tokens")
    completion_tokens = _get_usage_value(usage, "completion_tokens", "output_tokens")
    total_tokens = _get_usage_value(usage, "total_tokens")
    is_estimated = False

    if prompt_tokens is None:
        prompt_tokens = _estimate_tokens(f"{system}\n\n{user}", model)
        is_estimated = True
    if completion_tokens is None:
        completion_tokens = _estimate_tokens(output, model)
        is_estimated = True
    if total_tokens is None:
        total_tokens = prompt_tokens + completion_tokens
        is_estimated = True

    return int(prompt_tokens or 0), int(completion_tokens or 0), int(total_tokens or 0), is_estimated


def _extract_usage_object(response: Any) -> Any:
    if response is None:
        return None

    usage = _get_value(response, "usage")
    if usage is not None:
        return usage

    usage_metadata = _get_value(response, "usage_metadata")
    if usage_metadata is not None:
        return usage_metadata

    response_metadata = _get_value(response, "response_metadata") or {}
    token_usage = _get_value(response_metadata, "token_usage")
    if token_usage is not None:
        return token_usage

    return None


def _get_usage_value(usage: Any, *keys: str) -> int | None:
    for key in keys:
        value = _get_value(usage, key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _get_value(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    try:
        value = obj[key]
        if not _is_mock_value(value):
            return value
    except Exception:
        pass
    if hasattr(obj, key):
        value = getattr(obj, key)
        if not _is_mock_value(value):
            return value
    return None


def _is_mock_value(value: Any) -> bool:
    return value.__class__.__module__.startswith("unittest.mock")


def _estimate_tokens(text: str, model: str) -> int:
    try:
        encoder = encoding_for_model(model) if "gpt" in model else get_encoding("o200k_base")
    except Exception:
        encoder = get_encoding("o200k_base")
    return len(encoder.encode(text or "", disallowed_special=()))


def _strip_existing_run_usage(body: str) -> str:
    pattern = re.compile(
        rf"\n*{re.escape(RUN_USAGE_START)}.*?{re.escape(RUN_USAGE_END)}\n*",
        re.DOTALL,
    )
    return pattern.sub("\n", body).rstrip()


def _render_run_usage(command: str, calls: list[AiCallUsage], run_id: str, supports_details: bool) -> str:
    total_tokens = sum(call.total_tokens for call in calls)
    estimate_suffix = " ~" if any(call.is_estimated for call in calls) else ""
    if len(calls) == 1 or not supports_details:
        call = calls[0]
        body = (
            f"<sub>AI run: `{command}` · {call.provider}/{call.model} · "
            f"thinking {call.thinking_level} · tokens {total_tokens:,}{estimate_suffix}</sub>"
        )
    else:
        rows = [
            "| Step | Provider | Model | Thinking | Prompt | Completion | Total |",
            "|---:|---|---|---|---:|---:|---:|",
        ]
        for index, call in enumerate(calls, start=1):
            row_total = f"{call.total_tokens:,}{' ~' if call.is_estimated else ''}"
            rows.append(
                f"| {index} | {call.provider} | `{call.model}` | {call.thinking_level} | "
                f"{call.prompt_tokens:,} | {call.completion_tokens:,} | {row_total} |"
            )
        body = (
            f"<details>\n<summary>🤖 AI usage for this run: {total_tokens:,}{estimate_suffix} tokens</summary>\n\n"
            + "\n".join(rows)
            + "\n\n</details>"
        )

    return f"{RUN_USAGE_START}\n<!-- pr-agent-ai-usage-run-id:{run_id} -->\n{body}\n{RUN_USAGE_END}"


def _load_total_usage_data(body: str) -> dict:
    if TOTAL_USAGE_DATA_START not in body:
        return {"runs": []}
    start = body.find(TOTAL_USAGE_DATA_START) + len(TOTAL_USAGE_DATA_START)
    end = body.find(TOTAL_USAGE_DATA_END, start)
    if end == -1:
        return {"runs": []}
    raw_json = body[start:end].strip()
    try:
        data = json.loads(raw_json)
    except Exception:
        get_logger().debug("Failed to parse existing AI usage total data; starting fresh")
        return {"runs": []}
    if not isinstance(data, dict) or not isinstance(data.get("runs", []), list):
        return {"runs": []}
    return data


def _build_run_entry(git_provider: Any, ai_handler: Any, command: str, calls: list[AiCallUsage]) -> dict:
    commit = ""
    try:
        latest_commit_url = git_provider.get_latest_commit_url()
        commit = latest_commit_url.rstrip("/").split("/")[-1][:7] if latest_commit_url else ""
    except Exception:
        commit = ""

    return {
        "run_id": getattr(ai_handler, "ai_usage_run_id", ""),
        "time_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "command": command,
        "commit": commit,
        "models": _unique_models(calls),
        "tokens": sum(call.total_tokens for call in calls),
        "estimated": any(call.is_estimated for call in calls),
        "calls": [asdict(call) for call in calls],
    }


def _unique_models(calls: list[AiCallUsage]) -> list[str]:
    models = []
    seen = set()
    for call in calls:
        model = f"{call.provider}/{call.model} (thinking {call.thinking_level})"
        if model not in seen:
            models.append(model)
            seen.add(model)
    return models


def _render_total_usage(data: dict) -> str:
    runs = data.get("runs", [])
    total_tokens = sum(int(run.get("tokens", 0) or 0) for run in runs)
    rows = [
        "| Time (UTC) | Command | Commit | Model(s) | Tokens |",
        "|---|---|---|---|---:|",
    ]
    for run in runs:
        tokens = int(run.get("tokens", 0) or 0)
        estimated = " ~" if run.get("estimated") else ""
        models = ", ".join(f"`{model}`" for model in run.get("models", []))
        rows.append(
            f"| {run.get('time_utc', '')} | `{run.get('command', '')}` | "
            f"{run.get('commit', '') or '-'} | {models or '-'} | {tokens:,}{estimated} |"
        )

    hidden_data = json.dumps(data, sort_keys=True, separators=(",", ":"))
    rows_text = "\n".join(rows)
    return (
        f"{TOTAL_USAGE_HEADER}\n\n"
        f"**Total tokens used by PR-Agent on this PR:** {total_tokens:,}\n\n"
        "<details>\n<summary>Runs</summary>\n\n"
        f"{rows_text}\n\n"
        "</details>\n\n"
        f"{TOTAL_USAGE_DATA_START}\n{hidden_data}\n{TOTAL_USAGE_DATA_END}"
    )


def _get_comment_body(comment: Any) -> str:
    if isinstance(comment, dict):
        return str(comment.get("body") or comment.get("comment") or "")
    return str(getattr(comment, "body", "") or "")
