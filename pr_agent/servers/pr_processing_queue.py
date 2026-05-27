import asyncio
import copy
import hashlib
import json
import os
import socket
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from starlette_context import request_cycle_context

from pr_agent.config_loader import get_settings, global_settings
from pr_agent.log import get_logger
from pr_agent.servers.async_utils import run_async_function_off_loop


class PRQueueError(Exception):
    """Base error for PR processing queue failures."""


class PRQueueUnavailable(PRQueueError):
    """Raised when the configured queue backend cannot accept jobs."""


@dataclass
class QueuedPRJob:
    id: str
    pr_url: str
    pr_hash: str
    event: Optional[str]
    body: Dict[str, Any]
    installation_id: Optional[int]
    created_at: float


@dataclass
class ClaimedPRJob:
    job: QueuedPRJob
    owner: str


_RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""


class RedisPRProcessingQueue:
    def __init__(self, runner: Optional[Callable[[QueuedPRJob], Any]] = None):
        self.redis = None
        self.runner = runner or run_github_webhook_job
        self._workers: list[asyncio.Task] = []
        self._stop_event = asyncio.Event()
        self._worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"

    @property
    def enabled(self) -> bool:
        settings = get_settings()
        backend = str(settings.get("QUEUE.BACKEND", "redis")).lower()
        return _to_bool(settings.get("QUEUE.ENABLED", False)) and backend == "redis"

    @property
    def prefix(self) -> str:
        return str(get_settings().get("QUEUE.REDIS_KEY_PREFIX", "pr-agent:queue")).rstrip(":")

    @property
    def ready_key(self) -> str:
        return f"{self.prefix}:ready"

    @property
    def active_key(self) -> str:
        return f"{self.prefix}:active"

    @property
    def active_owner_key(self) -> str:
        return f"{self.prefix}:active_owner"

    @property
    def lock_key(self) -> str:
        return f"{self.prefix}:lock"

    def jobs_key(self, pr_hash: str) -> str:
        return f"{self.prefix}:jobs:{pr_hash}"

    def job_key(self, job_id: str) -> str:
        return f"{self.prefix}:job:{job_id}"

    async def start(self):
        if not self.enabled or self._workers:
            return
        await self._connect()
        await self.redis.ping()
        configured_worker_count = int(get_settings().get("QUEUE.LOCAL_WORKER_COUNT", 0))
        worker_count = configured_worker_count if configured_worker_count > 0 else self.max_concurrent_prs
        self._stop_event.clear()
        for index in range(worker_count):
            self._workers.append(asyncio.create_task(self._worker_loop(index)))
        get_logger().info(
            "Redis PR processing queue started",
            redis_key_prefix=self.prefix,
            max_concurrent_prs=self.max_concurrent_prs,
            local_worker_count=worker_count,
        )

    async def stop(self):
        self._stop_event.set()
        for task in self._workers:
            task.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        if self.redis:
            close = getattr(self.redis, "aclose", None) or getattr(self.redis, "close", None)
            if close:
                result = close()
                if asyncio.iscoroutine(result):
                    await result
            self.redis = None
        get_logger().info("Redis PR processing queue stopped")

    @property
    def max_concurrent_prs(self) -> int:
        return max(1, int(get_settings().get("QUEUE.MAX_CONCURRENT_PRS", 2)))

    @property
    def lease_seconds(self) -> int:
        return max(60, int(get_settings().get("QUEUE.LEASE_SECONDS", 1800)))

    @property
    def heartbeat_seconds(self) -> int:
        return max(10, int(get_settings().get("QUEUE.HEARTBEAT_SECONDS", 60)))

    @property
    def poll_seconds(self) -> float:
        return max(0.1, float(get_settings().get("QUEUE.POLL_SECONDS", 0.5)))

    @property
    def lock_ms(self) -> int:
        return max(1000, int(get_settings().get("QUEUE.LOCK_MS", 5000)))

    @property
    def job_ttl_seconds(self) -> int:
        return max(3600, int(get_settings().get("QUEUE.JOB_TTL_SECONDS", 604800)))

    async def enqueue_github_webhook(
        self, body: Dict[str, Any], event: Optional[str], pr_url: str, installation_id: Optional[int]
    ):
        if not self.enabled:
            raise PRQueueUnavailable("Redis PR processing queue is not enabled")
        await self._connect()

        now = time.time()
        job_id = uuid.uuid4().hex
        pr_hash = hash_pr_url(pr_url)
        payload = {
            "id": job_id,
            "pr_url": pr_url,
            "pr_hash": pr_hash,
            "event": event,
            "body": body,
            "installation_id": installation_id,
            "created_at": now,
        }
        await self.redis.set(self.job_key(job_id), json.dumps(payload, separators=(",", ":")), ex=self.job_ttl_seconds)
        await self.redis.rpush(self.jobs_key(pr_hash), job_id)
        await self.redis.zadd(self.ready_key, {pr_hash: now}, nx=True)
        get_logger().info("Queued PR processing job", pr_url=pr_url, event=event, job_id=job_id)

    async def _connect(self):
        if self.redis is not None:
            return
        try:
            from redis.asyncio import Redis
        except ImportError as e:
            raise PRQueueUnavailable("The 'redis' package is required for QUEUE.BACKEND=redis") from e

        redis_url = get_settings().get("QUEUE.REDIS_URL")
        if not redis_url:
            raise PRQueueUnavailable("QUEUE.REDIS_URL is required when QUEUE.ENABLED=true")
        self.redis = Redis.from_url(redis_url, decode_responses=True)

    async def _worker_loop(self, index: int):
        while not self._stop_event.is_set():
            try:
                claimed = await self.claim_next_job()
                if not claimed:
                    await asyncio.sleep(self.poll_seconds)
                    continue
                await self._run_claimed_job(claimed)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                get_logger().exception("PR queue worker failed", worker_index=index, error=str(e))
                await asyncio.sleep(self.poll_seconds)

    async def claim_next_job(self) -> Optional[ClaimedPRJob]:
        token = await self._acquire_lock()
        if not token:
            return None
        try:
            return await self._claim_next_job_locked()
        finally:
            await self._release_lock(token)

    async def _claim_next_job_locked(self) -> Optional[ClaimedPRJob]:
        now = time.time()
        await self._requeue_expired_active_prs(now)
        active_count = await self.redis.zcard(self.active_key)
        if active_count >= self.max_concurrent_prs:
            return None

        candidates = await self.redis.zrange(self.ready_key, 0, 99)
        for pr_hash in candidates:
            if await self.redis.zscore(self.active_key, pr_hash) is not None:
                continue

            jobs_key = self.jobs_key(pr_hash)
            job_id = await self.redis.lindex(jobs_key, 0)
            if not job_id:
                await self.redis.zrem(self.ready_key, pr_hash)
                continue

            payload = await self.redis.get(self.job_key(job_id))
            if not payload:
                await self.redis.lpop(jobs_key)
                if await self.redis.llen(jobs_key) == 0:
                    await self.redis.zrem(self.ready_key, pr_hash)
                continue

            owner = f"{self._worker_id}:{uuid.uuid4().hex}"
            await self.redis.zrem(self.ready_key, pr_hash)
            await self.redis.zadd(self.active_key, {pr_hash: now + self.lease_seconds})
            await self.redis.hset(self.active_owner_key, pr_hash, owner)
            job = _decode_job(payload)
            get_logger().info(
                "Claimed PR processing job",
                pr_url=job.pr_url,
                event=job.event,
                job_id=job.id,
                active_prs=await self.redis.zcard(self.active_key),
            )
            return ClaimedPRJob(job=job, owner=owner)
        return None

    async def _requeue_expired_active_prs(self, now: float):
        expired = await self.redis.zrangebyscore(self.active_key, "-inf", now)
        for pr_hash in expired:
            await self.redis.zrem(self.active_key, pr_hash)
            await self.redis.hdel(self.active_owner_key, pr_hash)
            if await self.redis.llen(self.jobs_key(pr_hash)) > 0:
                await self.redis.zadd(self.ready_key, {pr_hash: now})
                get_logger().warning("Re-queued expired active PR job", pr_hash=pr_hash)

    async def _run_claimed_job(self, claimed: ClaimedPRJob):
        heartbeat = asyncio.create_task(self._heartbeat(claimed))
        should_finish = False
        try:
            await run_async_function_off_loop(self.runner, claimed.job)
            should_finish = True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            should_finish = True
            get_logger().exception(
                "Queued PR processing job failed",
                pr_url=claimed.job.pr_url,
                event=claimed.job.event,
                job_id=claimed.job.id,
                error=str(e),
            )
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            if should_finish:
                await self.finish_job(claimed)

    @property
    def finish_lock_wait_seconds(self) -> float:
        return max(0.0, float(get_settings().get("QUEUE.FINISH_LOCK_WAIT_SECONDS", 5)))

    async def _heartbeat(self, claimed: ClaimedPRJob):
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            owner = await self.redis.hget(self.active_owner_key, claimed.job.pr_hash)
            if owner != claimed.owner:
                return
            await self.redis.zadd(self.active_key, {claimed.job.pr_hash: time.time() + self.lease_seconds})

    async def finish_job(self, claimed: ClaimedPRJob):
        token = await self._acquire_lock(self.finish_lock_wait_seconds)
        if not token:
            get_logger().warning("Could not acquire queue lock to finish PR job", job_id=claimed.job.id)
            return
        try:
            owner = await self.redis.hget(self.active_owner_key, claimed.job.pr_hash)
            if owner != claimed.owner:
                get_logger().warning("Skipping finish for PR job owned by another worker", job_id=claimed.job.id)
                return

            jobs_key = self.jobs_key(claimed.job.pr_hash)
            head_job_id = await self.redis.lindex(jobs_key, 0)
            if head_job_id == claimed.job.id:
                await self.redis.lpop(jobs_key)
            await self.redis.delete(self.job_key(claimed.job.id))
            await self.redis.zrem(self.active_key, claimed.job.pr_hash)
            await self.redis.hdel(self.active_owner_key, claimed.job.pr_hash)
            if await self.redis.llen(jobs_key) > 0:
                await self.redis.zadd(self.ready_key, {claimed.job.pr_hash: time.time()})
            else:
                await self.redis.delete(jobs_key)
            get_logger().info(
                "Finished PR processing job",
                pr_url=claimed.job.pr_url,
                event=claimed.job.event,
                job_id=claimed.job.id,
            )
        finally:
            await self._release_lock(token)

    async def _acquire_lock(self, wait_seconds: float = 0) -> Optional[str]:
        deadline = time.monotonic() + wait_seconds
        while True:
            token = uuid.uuid4().hex
            acquired = await self.redis.set(self.lock_key, token, nx=True, px=self.lock_ms)
            if acquired:
                return token
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(0.05)

    async def _release_lock(self, token: str):
        await self.redis.eval(_RELEASE_LOCK_SCRIPT, 1, self.lock_key, token)


async def run_github_webhook_job(job: QueuedPRJob):
    from pr_agent.servers.github_app import handle_request

    context_data = {
        "settings": copy.deepcopy(global_settings),
        "git_provider": {},
    }
    if job.installation_id is not None:
        context_data["installation_id"] = job.installation_id
    with request_cycle_context(context_data):
        await handle_request(job.body, job.event)


def get_github_pr_url(body: Dict[str, Any]) -> Optional[str]:
    pull_request = body.get("pull_request")
    if isinstance(pull_request, dict) and pull_request.get("url"):
        return pull_request["url"]

    issue = body.get("issue")
    if isinstance(issue, dict):
        issue_pr = issue.get("pull_request")
        if isinstance(issue_pr, dict) and issue_pr.get("url"):
            return issue_pr["url"]

    comment = body.get("comment")
    if isinstance(comment, dict) and comment.get("pull_request_url"):
        return comment["pull_request_url"]

    return None


def hash_pr_url(pr_url: str) -> str:
    return hashlib.sha256(pr_url.encode("utf-8")).hexdigest()


def _to_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _decode_job(payload: str) -> QueuedPRJob:
    data = json.loads(payload)
    return QueuedPRJob(
        id=data["id"],
        pr_url=data["pr_url"],
        pr_hash=data["pr_hash"],
        event=data.get("event"),
        body=data["body"],
        installation_id=data.get("installation_id"),
        created_at=float(data["created_at"]),
    )


_pr_queue = RedisPRProcessingQueue()


def get_pr_processing_queue() -> RedisPRProcessingQueue:
    return _pr_queue
