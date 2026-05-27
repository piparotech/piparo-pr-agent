import asyncio
from typing import Any, Awaitable, Callable


AsyncCallable = Callable[..., Awaitable[Any]]


def run_async_function_in_thread(async_fn: AsyncCallable, *args: Any, **kwargs: Any) -> Any:
    """Run an async function from a sync threadpool task."""
    return asyncio.run(async_fn(*args, **kwargs))


async def run_async_function_off_loop(async_fn: AsyncCallable, *args: Any, **kwargs: Any) -> Any:
    """Run an async function away from the current event loop."""
    return await asyncio.to_thread(run_async_function_in_thread, async_fn, *args, **kwargs)
