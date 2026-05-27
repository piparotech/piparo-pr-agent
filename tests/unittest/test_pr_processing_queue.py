import asyncio
import copy
import threading
import time
from collections import defaultdict

import pytest
from starlette_context import request_cycle_context

from pr_agent.config_loader import get_settings, global_settings
from pr_agent.servers.pr_processing_queue import RedisPRProcessingQueue, get_github_pr_url


class FakeRedis:
    def __init__(self):
        self.strings = {}
        self.lists = defaultdict(list)
        self.zsets = defaultdict(dict)
        self.hashes = defaultdict(dict)

    async def ping(self):
        return True

    async def set(self, key, value, nx=False, px=None, ex=None):
        if nx and key in self.strings:
            return False
        self.strings[key] = value
        return True

    async def get(self, key):
        return self.strings.get(key)

    async def delete(self, *keys):
        deleted = 0
        for key in keys:
            deleted += int(key in self.strings or key in self.lists or key in self.zsets or key in self.hashes)
            self.strings.pop(key, None)
            self.lists.pop(key, None)
            self.zsets.pop(key, None)
            self.hashes.pop(key, None)
        return deleted

    async def rpush(self, key, value):
        self.lists[key].append(value)
        return len(self.lists[key])

    async def lindex(self, key, index):
        values = self.lists[key]
        try:
            return values[index]
        except IndexError:
            return None

    async def lpop(self, key):
        if not self.lists[key]:
            return None
        return self.lists[key].pop(0)

    async def llen(self, key):
        return len(self.lists[key])

    async def zadd(self, key, mapping, nx=False):
        for member, score in mapping.items():
            if nx and member in self.zsets[key]:
                continue
            self.zsets[key][member] = float(score)
        return len(mapping)

    async def zrem(self, key, *members):
        removed = 0
        for member in members:
            if member in self.zsets[key]:
                del self.zsets[key][member]
                removed += 1
        return removed

    async def zcard(self, key):
        return len(self.zsets[key])

    async def zrange(self, key, start, end):
        values = [member for member, _ in sorted(self.zsets[key].items(), key=lambda item: item[1])]
        if end == -1:
            return values[start:]
        return values[start:end + 1]

    async def zrangebyscore(self, key, min_score, max_score):
        min_value = float("-inf") if min_score == "-inf" else float(min_score)
        max_value = float("inf") if max_score == "+inf" else float(max_score)
        return [
            member
            for member, score in sorted(self.zsets[key].items(), key=lambda item: item[1])
            if min_value <= score <= max_value
        ]

    async def zscore(self, key, member):
        return self.zsets[key].get(member)

    async def hset(self, key, field, value):
        self.hashes[key][field] = value
        return 1

    async def hget(self, key, field):
        return self.hashes[key].get(field)

    async def hdel(self, key, *fields):
        removed = 0
        for field in fields:
            if field in self.hashes[key]:
                del self.hashes[key][field]
                removed += 1
        return removed

    async def eval(self, script, numkeys, key, token):
        if self.strings.get(key) == token:
            del self.strings[key]
            return 1
        return 0


@pytest.fixture
def queue_settings():
    settings = get_settings()
    original_queue = copy.deepcopy(settings.get("QUEUE", None))
    settings.set("QUEUE.ENABLED", True)
    settings.set("QUEUE.BACKEND", "redis")
    settings.set("QUEUE.REDIS_URL", "redis://example:6379/0")
    settings.set("QUEUE.REDIS_KEY_PREFIX", "pr-agent:test")
    settings.set("QUEUE.MAX_CONCURRENT_PRS", 2)
    settings.set("QUEUE.LEASE_SECONDS", 60)
    try:
        yield
    finally:
        if original_queue is None:
            settings.unset("QUEUE", force=True)
        else:
            settings.set("QUEUE", original_queue)


async def _enqueue(queue, pr_url):
    await queue.enqueue_github_webhook(
        {"pull_request": {"url": pr_url, "state": "open", "draft": False}},
        "pull_request",
        pr_url,
        1,
    )


@pytest.mark.asyncio
async def test_redis_queue_limits_active_prs_globally(queue_settings):
    queue = RedisPRProcessingQueue()
    queue.redis = FakeRedis()

    await _enqueue(queue, "https://api.github.com/repos/org/repo/pulls/1")
    await _enqueue(queue, "https://api.github.com/repos/org/repo/pulls/2")
    await _enqueue(queue, "https://api.github.com/repos/org/repo/pulls/3")

    first = await queue.claim_next_job()
    second = await queue.claim_next_job()
    third = await queue.claim_next_job()

    assert {first.job.pr_url, second.job.pr_url} == {
        "https://api.github.com/repos/org/repo/pulls/1",
        "https://api.github.com/repos/org/repo/pulls/2",
    }
    assert third is None


@pytest.mark.asyncio
async def test_redis_queue_allows_only_one_active_job_per_pr(queue_settings):
    queue = RedisPRProcessingQueue()
    queue.redis = FakeRedis()
    pr_url = "https://api.github.com/repos/org/repo/pulls/1"

    await _enqueue(queue, pr_url)
    first = await queue.claim_next_job()
    await _enqueue(queue, pr_url)

    assert await queue.claim_next_job() is None

    await queue.finish_job(first)
    second = await queue.claim_next_job()

    assert second.job.pr_url == pr_url
    assert second.job.id != first.job.id


@pytest.mark.asyncio
async def test_redis_queue_requeues_expired_active_pr(queue_settings):
    queue = RedisPRProcessingQueue()
    queue.redis = FakeRedis()
    pr_url = "https://api.github.com/repos/org/repo/pulls/1"

    await _enqueue(queue, pr_url)
    first = await queue.claim_next_job()
    await queue.redis.zadd(queue.active_key, {first.job.pr_hash: time.time() - 1})

    second = await queue.claim_next_job()

    assert second.job.id == first.job.id
    assert second.owner != first.owner


@pytest.mark.asyncio
async def test_redis_queue_runs_claimed_job_off_web_event_loop(queue_settings):
    async def blocking_runner(job):
        time.sleep(0.05)

    queue = RedisPRProcessingQueue(runner=blocking_runner)
    queue.redis = FakeRedis()
    await _enqueue(queue, "https://api.github.com/repos/org/repo/pulls/1")
    claimed = await queue.claim_next_job()
    ticks = 0
    running = True

    async def tick():
        nonlocal ticks
        while running:
            await asyncio.sleep(0.005)
            ticks += 1

    ticker = asyncio.create_task(tick())
    await asyncio.sleep(0)
    await queue._run_claimed_job(claimed)
    running = False
    await ticker

    assert ticks >= 3
    assert await queue.redis.llen(queue.jobs_key(claimed.job.pr_hash)) == 0


def test_get_github_pr_url_extracts_supported_payload_shapes():
    assert get_github_pr_url({"pull_request": {"url": "pr-url"}}) == "pr-url"
    assert get_github_pr_url({"issue": {"pull_request": {"url": "issue-pr-url"}}}) == "issue-pr-url"
    assert get_github_pr_url({"comment": {"pull_request_url": "comment-pr-url"}}) == "comment-pr-url"
    assert get_github_pr_url({}) is None


@pytest.mark.asyncio
async def test_github_auto_commands_can_run_in_parallel(monkeypatch):
    import pr_agent.servers.github_app as github_app

    active = 0
    max_active = 0
    active_lock = threading.Lock()
    all_started = threading.Barrier(3)

    class FakeAgent:
        def __init__(self, ai_handler=None):
            self.ai_handler = ai_handler

        async def handle_request(self, pr_url, command):
            nonlocal active, max_active
            with active_lock:
                active += 1
                max_active = max(max_active, active)
            try:
                all_started.wait(timeout=1)
                await asyncio.sleep(0.01)
            finally:
                with active_lock:
                    active -= 1

    monkeypatch.setattr(github_app, "PRAgent", FakeAgent)
    monkeypatch.setattr(github_app, "apply_repo_settings", lambda pr_url: None)
    monkeypatch.setattr(github_app, "should_process_pr_logic", lambda body: True)

    with request_cycle_context({"settings": copy.deepcopy(global_settings), "git_provider": {}}):
        get_settings().set("QUEUE.RUN_PR_COMMANDS_PARALLEL", True)
        get_settings().set("GITHUB_APP.PR_COMMANDS", ["/describe", "/review", "/improve"])

        await github_app._perform_auto_commands_github(
            "pr_commands",
            FakeAgent(ai_handler="fake-ai"),
            {"pull_request": {"url": "https://api.github.com/repos/org/repo/pulls/1"}},
            "https://api.github.com/repos/org/repo/pulls/1",
            {},
        )

    assert max_active == 3


@pytest.mark.asyncio
async def test_github_auto_commands_do_not_block_current_event_loop(monkeypatch):
    import pr_agent.servers.github_app as github_app

    class FakeAgent:
        def __init__(self, ai_handler=None):
            self.ai_handler = ai_handler

        async def handle_request(self, pr_url, command):
            time.sleep(0.05)

    monkeypatch.setattr(github_app, "PRAgent", FakeAgent)
    monkeypatch.setattr(github_app, "apply_repo_settings", lambda pr_url: None)
    monkeypatch.setattr(github_app, "should_process_pr_logic", lambda body: True)

    with request_cycle_context({"settings": copy.deepcopy(global_settings), "git_provider": {}}):
        get_settings().set("QUEUE.RUN_PR_COMMANDS_PARALLEL", True)
        get_settings().set("GITHUB_APP.PR_COMMANDS", ["/review"])
        ticks = 0
        running = True

        async def tick():
            nonlocal ticks
            while running:
                await asyncio.sleep(0.005)
                ticks += 1

        ticker = asyncio.create_task(tick())
        await asyncio.sleep(0)
        await github_app._perform_auto_commands_github(
            "pr_commands",
            FakeAgent(ai_handler="fake-ai"),
            {"pull_request": {"url": "https://api.github.com/repos/org/repo/pulls/1"}},
            "https://api.github.com/repos/org/repo/pulls/1",
            {},
        )
        running = False
        await ticker

    assert ticks >= 3
