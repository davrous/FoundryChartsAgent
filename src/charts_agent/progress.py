from collections.abc import Awaitable, Callable
from contextvars import ContextVar

PROGRESS: ContextVar[Callable[[str], Awaitable[None]] | None] = ContextVar("chart_progress", default=None)


async def report_progress(text: str) -> None:
    reporter = PROGRESS.get()
    if reporter is not None:
        await reporter(text)
