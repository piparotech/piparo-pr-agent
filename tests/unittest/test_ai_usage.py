from types import SimpleNamespace
from unittest.mock import patch

from pr_agent.algo.ai_usage import (
    AiCallUsage,
    append_ai_usage_footer,
    publish_ai_usage_total_comment,
    record_ai_call_usage,
)


class Config(dict):
    def __getattr__(self, item):
        return self[item]


class FakeProvider:
    def __init__(self):
        self.comments = []
        self.edited = []

    def is_supported(self, capability):
        return capability == "gfm_markdown"

    def get_issue_comments(self):
        return self.comments

    def publish_comment(self, body, is_temporary=False):
        comment = SimpleNamespace(body=body)
        self.comments.append(comment)
        return comment

    def edit_comment(self, comment, body):
        comment.body = body
        self.edited.append(body)

    def get_latest_commit_url(self):
        return "https://example.test/repo/commit/abcdef123456"


class FakeCheckProvider(FakeProvider):
    """GitHub-like provider that publishes the cumulative usage as a check run."""

    def __init__(self):
        super().__init__()
        self.check = None  # {"summary": ..., "text": ...}
        self.publish_calls = 0
        self.removed = []

    def get_total_usage_check_text(self, check_name):
        return self.check["text"] if self.check else ""

    def publish_total_usage_check(self, check_name, title, summary, text):
        self.publish_calls += 1
        self.check = {"title": title, "summary": summary, "text": text}
        return True

    def remove_comment(self, comment):
        self.removed.append(comment)
        if comment in self.comments:
            self.comments.remove(comment)


def _handler(run_id, tokens):
    return SimpleNamespace(
        ai_usage_run_id=run_id,
        ai_usage_calls=[AiCallUsage("openai", "gpt-5.5", "high", tokens, 0, tokens, "stop")],
    )


def test_publish_total_usage_uses_check_run_and_skips_comment():
    provider = FakeCheckProvider()

    publish_ai_usage_total_comment(provider, _handler("run-1", 15), "/review")
    publish_ai_usage_total_comment(provider, _handler("run-1", 15), "/review")

    assert provider.comments == []  # no standalone comment is posted
    assert provider.publish_calls == 2
    assert "Total tokens used by PR-Agent on this PR:** 15" in provider.check["summary"]
    assert provider.check["text"].count("run-1") == 1  # idempotent per run


def test_publish_total_usage_migrates_then_deletes_legacy_comment():
    provider = FakeCheckProvider()
    # Seed a legacy standalone comment carrying one prior run's hidden data.
    legacy = SimpleNamespace(
        body=(
            "## PR-Agent Usage 📊\n\n**Total tokens used by PR-Agent on this PR:** 10\n\n"
            '<!-- pr-agent-ai-usage-total:data\n'
            '{"runs":[{"run_id":"old","time_utc":"t","command":"/review","commit":"c",'
            '"models":[],"tokens":10,"estimated":false,"calls":[]}],'
            '"summary":{"run_count":1,"tokens":10}}\n-->'
        )
    )
    provider.check = None
    provider.comments = [legacy]

    publish_ai_usage_total_comment(provider, _handler("new", 15), "/review")

    assert legacy not in provider.comments  # legacy comment removed
    assert provider.removed == [legacy]
    assert "Total tokens used by PR-Agent on this PR:** 25" in provider.check["summary"]
    assert provider.check["text"].count("old") == 1 and provider.check["text"].count("new") == 1


def test_run_footer_includes_cost_and_latency():
    handler = SimpleNamespace(
        ai_usage_run_id="run-1",
        ai_usage_calls=[
            AiCallUsage("openai", "gpt-4o", "high", 1000, 500, 1500, "stop", False, 0.0325, 1234.0),
        ],
    )
    out = append_ai_usage_footer("Body", handler, "/review", FakeProvider())
    assert "$0.0325" in out
    assert "1.2s" in out


def test_total_usage_check_includes_cost_and_latency():
    provider = FakeCheckProvider()
    handler = SimpleNamespace(
        ai_usage_run_id="c1",
        ai_usage_calls=[
            AiCallUsage("openai", "gpt-4o", "high", 1000, 500, 1500, "stop", False, 0.0325, 2000.0),
        ],
    )
    publish_ai_usage_total_comment(provider, handler, "/review")
    assert "est. cost $0.0325" in provider.check["summary"]
    assert "| Cost |" in provider.check["text"]
    assert "$0.0325" in provider.check["text"]
    assert "2.0s" in provider.check["text"]


def test_render_total_usage_text_sheds_call_detail_when_too_large():
    from pr_agent.algo.ai_usage import (TOTAL_USAGE_DATA_START,
                                        _render_total_usage_text)

    big_calls = [{"provider": "openai", "model": "gpt-4o", "blob": "y" * 200} for _ in range(50)]
    runs = [
        {"run_id": f"r{i}", "time_utc": "t", "command": "/review", "commit": "c", "models": [],
         "tokens": 10, "cost_usd": 0.01, "duration_ms": 100, "estimated": False, "calls": big_calls}
        for i in range(40)
    ]
    data = {"runs": runs, "summary": {"run_count": 40, "tokens": 400, "cost_usd": 0.4}}

    text = _render_total_usage_text(data)

    assert len(text) <= 65535  # stays under GitHub's hard cap
    hidden = text.split(TOTAL_USAGE_DATA_START, 1)[1]
    assert '"calls"' not in hidden  # per-call detail shed, run totals preserved
    assert '"tokens":10' in hidden


def test_record_ai_call_usage_extracts_provider_model_thinking_level_and_tokens():
    handler = SimpleNamespace()
    response = {"usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}}

    record_ai_call_usage(
        handler,
        model="openai/gpt-5.5-2026-04-23",
        response=response,
        system="sys",
        user="user",
        output="answer",
        finish_reason="stop",
        thinking_level="high",
    )

    usage = handler.ai_usage_calls[0]
    assert usage.provider == "openai"
    assert usage.model == "gpt-5.5-2026-04-23"
    assert usage.thinking_level == "high"
    assert usage.prompt_tokens == 10
    assert usage.completion_tokens == 4
    assert usage.total_tokens == 14
    assert usage.is_estimated is False


def test_append_ai_usage_footer_replaces_existing_footer():
    handler = SimpleNamespace(
        ai_usage_run_id="run-1",
        ai_usage_calls=[
            AiCallUsage("openai", "gpt-5.5", "high", 10, 5, 15, "stop"),
        ],
    )
    body = "Review body\n\n<!-- pr-agent-ai-usage:start -->\nold\n<!-- pr-agent-ai-usage:end -->"

    result = append_ai_usage_footer(body, handler, "/review", FakeProvider())

    assert result.count("pr-agent-ai-usage:start") == 1
    assert "old" not in result
    assert "AI run: `/review`" in result
    assert "thinking high" in result
    assert "tokens 15" in result


def test_publish_ai_usage_total_comment_upserts_once_per_run():
    provider = FakeProvider()
    handler = SimpleNamespace(
        ai_usage_run_id="run-1",
        ai_usage_calls=[
            AiCallUsage("openai", "gpt-5.5", "high", 10, 5, 15, "stop"),
        ],
    )

    publish_ai_usage_total_comment(provider, handler, "/review")
    publish_ai_usage_total_comment(provider, handler, "/review")

    assert len(provider.comments) == 1
    assert "Total tokens used by PR-Agent on this PR:** 15" in provider.comments[0].body
    assert provider.comments[0].body.count("run-1") == 1
    assert provider.comments[0].body.count("`/review`") == 1


@patch("pr_agent.algo.ai_usage.get_settings")
def test_publish_ai_usage_total_comment_keeps_totals_when_trimming_old_runs(mock_get_settings):
    mock_get_settings.return_value = SimpleNamespace(config=Config(
        publish_ai_usage_total_comment=True,
        publish_output=True,
        ai_usage_total_max_runs=2,
    ))
    provider = FakeProvider()

    for index, tokens in enumerate([10, 15, 20], start=1):
        handler = SimpleNamespace(
            ai_usage_run_id=f"run-{index}",
            ai_usage_calls=[
                AiCallUsage("openai", "gpt-5.5", "high", tokens, 0, tokens, "stop"),
            ],
        )
        publish_ai_usage_total_comment(provider, handler, "/review")

    body = provider.comments[0].body
    assert len(provider.comments) == 1
    assert "Total tokens used by PR-Agent on this PR:** 45" in body
    assert "Showing the latest 2 of 3 runs." in body
    assert "run-1" not in body
    assert body.count("run-2") == 1
    assert body.count("run-3") == 1
