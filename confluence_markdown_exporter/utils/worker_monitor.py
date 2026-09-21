"""Live terminal dashboard for Confluence export worker threads."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

from rich.console import Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

from confluence_markdown_exporter.utils.rich_console import console


@dataclass
class WorkerState:
    """Current user-facing state of one executor thread."""

    name: str
    pool: str
    state: str
    work: str
    started_at: float


class WorkerMonitor:
    """Coordinate a nested, thread-safe Rich Live worker dashboard."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._workers: dict[int, WorkerState] = {}
        self._depth = 0
        self._live: Live | None = None
        self._page_workers = 0
        self._space_workers = 0

    @contextmanager
    def dashboard(self, *, page_workers: int = 0, space_workers: int = 0) -> Iterator[None]:
        """Show one dashboard across nested discovery and page export operations."""
        with self._lock:
            self._depth += 1
            self._page_workers = max(self._page_workers, page_workers)
            self._space_workers = max(self._space_workers, space_workers)
            if self._depth == 1 and console.is_terminal:
                self._live = Live(
                    self._render(),
                    console=console,
                    refresh_per_second=8,
                    transient=False,
                )
                self._live.start(refresh=True)
        try:
            yield
        finally:
            with self._lock:
                self._depth -= 1
                self._refresh()
                if self._depth == 0:
                    if self._live is not None:
                        self._live.stop()
                    self._live = None
                    self._workers.clear()
                    self._page_workers = 0
                    self._space_workers = 0

    @contextmanager
    def activity(self, pool: str, state: str, work: str) -> Iterator[None]:
        """Publish an activity for the current thread, restoring its parent activity."""
        ident = threading.get_ident()
        with self._lock:
            previous = self._workers.get(ident)
            self._workers[ident] = WorkerState(
                name=threading.current_thread().name,
                pool=pool,
                state=state,
                work=work,
                started_at=time.monotonic(),
            )
            self._refresh()
        try:
            yield
        finally:
            with self._lock:
                if previous is not None:
                    self._workers[ident] = previous
                else:
                    current = self._workers[ident]
                    self._workers[ident] = WorkerState(
                        name=current.name,
                        pool=current.pool,
                        state="idle",
                        work="Waiting for work",
                        started_at=time.monotonic(),
                    )
                self._refresh()

    def snapshot(self) -> list[WorkerState]:
        """Return a stable copy for tests and diagnostics."""
        with self._lock:
            return list(self._workers.values())

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._render(), refresh=True)

    def _render(self) -> Group:
        active = sum(worker.state != "idle" for worker in self._workers.values())
        header = Text()
        header.append("CME workers  ", style="bold cyan")
        header.append(f"page={self._page_workers}  space={self._space_workers}  ")
        header.append(f"active={active}/{len(self._workers)}", style="green" if active else "dim")

        table = Table(expand=True, show_edge=False, pad_edge=False)
        table.add_column("Worker", style="cyan", no_wrap=True)
        table.add_column("Pool", style="magenta", no_wrap=True)
        table.add_column("State", no_wrap=True)
        table.add_column("Current work", overflow="ellipsis")
        table.add_column("Elapsed", justify="right", no_wrap=True)
        now = time.monotonic()
        workers = sorted(self._workers.values(), key=lambda worker: worker.name)
        for worker in workers:
            elapsed = max(0, int(now - worker.started_at))
            state_style = "green" if worker.state == "idle" else "yellow"
            table.add_row(
                Text(worker.name),
                Text(worker.pool),
                Text(worker.state, style=state_style),
                Text(worker.work),
                f"{elapsed}s",
            )
        if not workers:
            table.add_row("—", "—", Text("starting", style="yellow"), "Preparing workers", "0s")
        return Group(header, table)


worker_monitor = WorkerMonitor()
