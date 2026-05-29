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
TOTAL_USAGE_MAX_RUNS = 25
TOTAL_USAGE_CHECK_NAME = "PR-Agent token usage"
# GitHub caps check-run output text at 65535 chars; stay below it with headroom and shed
# per-call detail from the stored state before we hit the hard limit.
TOTAL_USAGE_TEXT_SOFT_LIMIT = 60000


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
    cost_usd: float | None = None
    duration_ms: float | None = None


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
    duration_ms: float | None = None,
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
        cost_usd=_compute_cost_usd(model, prompt_tokens, completion_tokens),
        duration_ms=float(duration_ms) if duration_ms is not None else None,
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

    if _supports_usage_check(git_provider):
        _publish_total_usage_check(git_provider, ai_handler, command, calls, run_id)
    else:
        _publish_total_usage_comment(git_provider, ai_handler, command, calls, run_id)


def _supports_usage_check(git_provider: Any) -> bool:
    return callable(getattr(git_provider, "publish_total_usage_check", None)) and callable(
        getattr(git_provider, "get_total_usage_check_text", None)
    )


def _accumulate_total_usage_data(
    existing_body: str, git_provider: Any, ai_handler: Any, command: str, calls: list, run_id: str
) -> dict:
    data = _normalize_total_usage_data(_load_total_usage_data(existing_body))
    run_ids = {run.get("run_id") for run in data.get("runs", [])}
    if run_id not in run_ids:
        run_entry = _build_run_entry(git_provider, ai_handler, command, calls)
        data.setdefault("runs", []).append(run_entry)
        _increment_total_usage_summary(data, run_entry)
    return _compact_total_usage_data(data, _get_total_usage_max_runs())


def _find_legacy_usage_comment(git_provider: Any) -> tuple[Any, str]:
    try:
        comments = list(git_provider.get_issue_comments())
    except Exception as e:
        get_logger().debug(f"Issue comments unavailable for AI usage lookup: {e}")
        return None, ""
    for comment in comments:
        body = _get_comment_body(comment)
        if body.startswith(TOTAL_USAGE_HEADER) or TOTAL_USAGE_DATA_START in body:
            return comment, body
    return None, ""


def _publish_total_usage_check(
    git_provider: Any, ai_handler: Any, command: str, calls: list, run_id: str
) -> None:
    try:
        existing_body = git_provider.get_total_usage_check_text(TOTAL_USAGE_CHECK_NAME) or ""
    except Exception as e:
        get_logger().debug(f"Failed to read existing AI usage check run: {e}")
        existing_body = ""

    # First run on a PR that still carries the legacy comment: migrate its history, then drop it.
    legacy_comment = None
    if TOTAL_USAGE_DATA_START not in existing_body:
        legacy_comment, legacy_body = _find_legacy_usage_comment(git_provider)
        if legacy_body:
            existing_body = legacy_body

    data = _accumulate_total_usage_data(existing_body, git_provider, ai_handler, command, calls, run_id)
    summary = _render_total_usage_summary(data)
    text = _render_total_usage_text(data)

    try:
        published = git_provider.publish_total_usage_check(
            TOTAL_USAGE_CHECK_NAME, TOTAL_USAGE_CHECK_NAME, summary, text
        )
    except Exception as e:
        get_logger().debug(f"Failed to publish AI usage check run: {e}")
        return

    if published and legacy_comment is not None:
        try:
            git_provider.remove_comment(legacy_comment)
        except Exception as e:
            get_logger().debug(f"Failed to remove legacy AI usage comment: {e}")


def _publish_total_usage_comment(
    git_provider: Any, ai_handler: Any, command: str, calls: list, run_id: str
) -> None:
    existing_comment, existing_body = _find_legacy_usage_comment(git_provider)
    data = _accumulate_total_usage_data(existing_body, git_provider, ai_handler, command, calls, run_id)
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


def _compute_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """Best-effort USD cost from litellm's price map. Returns None for models litellm
    doesn't know, so we never show a misleading number (and avoid litellm's noisy
    unknown-provider logging by checking the price map before pricing)."""
    try:
        import litellm
    except Exception:
        return None
    price_map = getattr(litellm, "model_cost", {}) or {}
    name = str(model or "")
    candidates = [name]
    if "/" in name:
        candidates.append(name.split("/", 1)[1])
    for candidate in candidates:
        if candidate and candidate in price_map:
            try:
                prompt_cost, completion_cost = litellm.cost_per_token(
                    model=candidate,
                    prompt_tokens=int(prompt_tokens or 0),
                    completion_tokens=int(completion_tokens or 0),
                )
                return round(float((prompt_cost or 0) + (completion_cost or 0)), 6)
            except Exception:
                continue
    return None


def _sum_cost(calls: list[AiCallUsage]) -> float | None:
    costs = [c.cost_usd for c in calls if c.cost_usd is not None]
    return round(sum(costs), 6) if costs else None


def _sum_duration_ms(calls: list[AiCallUsage]) -> float | None:
    durations = [c.duration_ms for c in calls if c.duration_ms is not None]
    return round(sum(durations), 1) if durations else None


def _fmt_cost(value: Any) -> str:
    try:
        cost = float(value)
    except (TypeError, ValueError):
        return ""
    if cost <= 0:
        return ""
    return f"${cost:,.4f}" if cost < 1 else f"${cost:,.2f}"


def _fmt_duration(ms: Any) -> str:
    try:
        millis = float(ms)
    except (TypeError, ValueError):
        return ""
    if millis <= 0:
        return ""
    return f"{millis / 1000:,.1f}s" if millis >= 1000 else f"{int(millis)}ms"


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
    cost = _fmt_cost(_sum_cost(calls))
    duration = _fmt_duration(_sum_duration_ms(calls))
    extra = "".join(f" · {part}" for part in (cost, duration) if part)
    if len(calls) == 1 or not supports_details:
        call = calls[0]
        body = (
            f"<sub>AI run: `{command}` · {call.provider}/{call.model} · "
            f"thinking {call.thinking_level} · tokens {total_tokens:,}{estimate_suffix}{extra}</sub>"
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
            f"<details>\n<summary>🤖 AI usage for this run: {total_tokens:,}{estimate_suffix} tokens"
            f"{extra}</summary>\n\n"
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


def _normalize_total_usage_data(data: dict) -> dict:
    runs = data.setdefault("runs", [])
    summary = data.get("summary")
    if not isinstance(summary, dict):
        summary = {}
        data["summary"] = summary
    tokens_from_runs = sum(int(run.get("tokens", 0) or 0) for run in runs)
    cost_from_runs = sum(float(run.get("cost_usd", 0) or 0) for run in runs)
    summary["run_count"] = max(int(summary.get("run_count", 0) or 0), len(runs))
    summary["tokens"] = max(int(summary.get("tokens", 0) or 0), tokens_from_runs)
    summary["cost_usd"] = round(max(float(summary.get("cost_usd", 0) or 0), cost_from_runs), 6)
    return data


def _increment_total_usage_summary(data: dict, run_entry: dict) -> None:
    summary = data.setdefault("summary", {})
    summary["run_count"] = int(summary.get("run_count", 0) or 0) + 1
    summary["tokens"] = int(summary.get("tokens", 0) or 0) + int(run_entry.get("tokens", 0) or 0)
    summary["cost_usd"] = round(
        float(summary.get("cost_usd", 0) or 0) + float(run_entry.get("cost_usd", 0) or 0), 6
    )


def _compact_total_usage_data(data: dict, max_runs: int) -> dict:
    runs = data.get("runs", [])
    data["runs"] = runs[-max_runs:] if max_runs > 0 else []
    return data


def _get_total_usage_max_runs() -> int:
    try:
        max_runs = int(get_settings().config.get("ai_usage_total_max_runs", TOTAL_USAGE_MAX_RUNS))
    except Exception:
        max_runs = TOTAL_USAGE_MAX_RUNS
    return max(0, max_runs)


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
        "cost_usd": _sum_cost(calls),
        "duration_ms": _sum_duration_ms(calls),
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


def _render_total_usage_summary(data: dict) -> str:
    runs = data.get("runs", [])
    summary = data.get("summary", {}) if isinstance(data.get("summary"), dict) else {}
    total_tokens = max(
        int(summary.get("tokens", 0) or 0),
        sum(int(run.get("tokens", 0) or 0) for run in runs),
    )
    total_runs = max(int(summary.get("run_count", 0) or 0), len(runs))
    total_cost = _fmt_cost(summary.get("cost_usd"))
    cost_note = f" · est. cost {total_cost}" if total_cost else ""
    retention_note = ""
    if total_runs > len(runs):
        retention_note = f"\n\nShowing the latest {len(runs)} of {total_runs} runs."
    return f"**Total tokens used by PR-Agent on this PR:** {total_tokens:,}{cost_note}{retention_note}"


def _render_total_usage_rows(data: dict) -> str:
    rows = [
        "| Time (UTC) | Command | Commit | Model(s) | Tokens | Cost | Time |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for run in data.get("runs", []):
        tokens = int(run.get("tokens", 0) or 0)
        estimated = " ~" if run.get("estimated") else ""
        models = ", ".join(f"`{model}`" for model in run.get("models", []))
        cost = _fmt_cost(run.get("cost_usd")) or "-"
        duration = _fmt_duration(run.get("duration_ms")) or "-"
        rows.append(
            f"| {run.get('time_utc', '')} | `{run.get('command', '')}` | "
            f"{run.get('commit', '') or '-'} | {models or '-'} | {tokens:,}{estimated} | {cost} | {duration} |"
        )
    return "\n".join(rows)


def _render_hidden_usage_data(data: dict) -> str:
    hidden_data = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return f"{TOTAL_USAGE_DATA_START}\n{hidden_data}\n{TOTAL_USAGE_DATA_END}"


def _strip_call_detail(data: dict) -> dict:
    """Drop per-call arrays from the stored state. Per-run totals (tokens/cost/duration)
    are kept, so cumulative reporting stays correct — only the per-call breakdown is shed."""
    slim = dict(data)
    slim["runs"] = [{k: v for k, v in run.items() if k != "calls"} for run in data.get("runs", [])]
    return slim


def _render_total_usage_text(data: dict) -> str:
    # Body for the GitHub check-run output: headline carried by the check title, so just the
    # table plus the hidden state we read back on the next run.
    summary = _render_total_usage_summary(data)
    rows = _render_total_usage_rows(data)
    text = f"{summary}\n\n{rows}\n\n{_render_hidden_usage_data(data)}"
    if len(text) <= TOTAL_USAGE_TEXT_SOFT_LIMIT:
        return text
    # Approaching GitHub's hard cap: shed per-call detail from the stored JSON so the next
    # run can still parse it, rather than letting the state get silently truncated mid-JSON.
    get_logger().warning(
        "AI usage check text exceeds soft limit; dropping per-call detail from stored state",
        text_length=len(text),
    )
    return f"{summary}\n\n{rows}\n\n{_render_hidden_usage_data(_strip_call_detail(data))}"


def _render_total_usage(data: dict) -> str:
    return (
        f"{TOTAL_USAGE_HEADER}\n\n"
        f"{_render_total_usage_summary(data)}\n\n"
        "<details>\n<summary>Runs</summary>\n\n"
        f"{_render_total_usage_rows(data)}\n\n"
        "</details>\n\n"
        f"{_render_hidden_usage_data(data)}"
    )


def _get_comment_body(comment: Any) -> str:
    if isinstance(comment, dict):
        return str(comment.get("body") or comment.get("comment") or "")
    return str(getattr(comment, "body", "") or "")
