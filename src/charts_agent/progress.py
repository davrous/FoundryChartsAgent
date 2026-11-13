import asyncio
import math
import os
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

PROGRESS: ContextVar[Callable[[str], Awaitable[None]] | None] = ContextVar("chart_progress", default=None)


def progress_delay_seconds() -> float:
    try:
        delay = float(os.getenv("TOOL_PROGRESS_DELAY_SECONDS", "0"))
    except ValueError as error:
        raise ValueError("TOOL_PROGRESS_DELAY_SECONDS must be a number between 0 and 10") from error
    if not math.isfinite(delay) or not 0 <= delay <= 10:
        raise ValueError("TOOL_PROGRESS_DELAY_SECONDS must be a number between 0 and 10")
    return delay


async def report_progress(text: str) -> None:
    reporter = PROGRESS.get()
    if reporter is not None:
        await reporter(text)
        delay = progress_delay_seconds()
        if delay:
            await asyncio.sleep(delay)
