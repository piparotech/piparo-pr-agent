import gc
import os
import tracemalloc
from typing import Any, Optional

from pr_agent.log import get_logger

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _to_bool(value: Optional[str]) -> bool:
    return str(value or "").lower() in _TRUE_VALUES


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        get_logger().warning("Invalid integer memory profiler setting", setting=name, value=os.getenv(name))
        return default


def enabled() -> bool:
    return _to_bool(os.getenv("PR_AGENT_MEMORY_PROFILE"))


def rss_bytes() -> Optional[int]:
    try:
        with open("/proc/self/status", encoding="utf-8") as status_file:
            for line in status_file:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        return None
    return None


def start() -> None:
    if not enabled():
        return
    frames = _env_int("PR_AGENT_MEMORY_PROFILE_FRAMES", 25, minimum=1)
    if not tracemalloc.is_tracing():
        tracemalloc.start(frames)
    get_logger().info(
        "PR-Agent memory profiling enabled",
        traceback_limit=tracemalloc.get_traceback_limit(),
        rss_bytes=rss_bytes(),
    )


def log_snapshot(label: str, **context: Any) -> None:
    if not enabled():
        return
    if not tracemalloc.is_tracing():
        start()
    if _to_bool(os.getenv("PR_AGENT_MEMORY_PROFILE_GC", "true")):
        gc.collect()

    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    snapshot = tracemalloc.take_snapshot()
    top_n = _env_int("PR_AGENT_MEMORY_PROFILE_TOP_N", 10, minimum=1)
    top_allocations = []
    for stat in snapshot.statistics("traceback")[:top_n]:
        frame = stat.traceback[0]
        top_allocations.append(
            {
                "file": frame.filename,
                "line": frame.lineno,
                "size_bytes": stat.size,
                "count": stat.count,
            }
        )

    get_logger().info(
        "PR-Agent memory profile snapshot",
        label=label,
        rss_bytes=rss_bytes(),
        traced_current_bytes=current_bytes,
        traced_peak_bytes=peak_bytes,
        top_allocations=top_allocations,
        **context,
    )
