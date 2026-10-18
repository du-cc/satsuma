"""Background job queue shared by conversions and tools."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Callable

from gi.repository import GLib, GObject

from . import backends as B
from . import formats as F
from . import tools as T


@dataclass
class Job:
    title: str            # e.g. "song.mp3" or "Compress · 3 files"
    work: Callable[[B.Runner], list[str]]
    runner: B.Runner = field(default_factory=B.Runner)
    state: str = "queued"  # queued, running, done, failed, cancelled
    outputs: list[str] = field(default_factory=list)
    error: str = ""

    def cancel(self):
        self.runner.cancel()


class JobQueue(GObject.Object):
    """Runs jobs on worker threads; signals arrive on the main loop."""

    __gsignals__ = {
        "job-added": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "job-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, parallel: int = 2):
        super().__init__()
        self.slots = threading.Semaphore(max(1, parallel))
        self.jobs: list[Job] = []

    def submit(self, job: Job) -> Job:
        self.jobs.append(job)
        self.emit("job-added", job)
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job: Job):
        with self.slots:
            if job.runner.cancel_event.is_set():
                job.state = "cancelled"
                GLib.idle_add(self.emit, "job-changed", job)
                return
            job.state = "running"
            GLib.idle_add(self.emit, "job-changed", job)
            try:
                job.outputs = job.work(job.runner)
                job.state = "done"
            except B.Cancelled:
                job.state = "cancelled"
            except Exception as exc:  # noqa: BLE001 - surface every failure to the UI
                job.state = "failed"
                job.error = str(exc) or exc.__class__.__name__
            GLib.idle_add(self.emit, "job-changed", job)

    def active(self) -> list[Job]:
        return [j for j in self.jobs if j.state in ("queued", "running")]

    # --- convenience constructors ---------------------------------------------------------------

    def convert(self, files: list[str], target: str, output_dir: str | None) -> list[Job]:
        """Queue one job per file; ``output_dir`` None means beside each original."""
        jobs = []
        for src in files:
            if F.detect_format(src) == target:
                continue
            out = F.conversion_output(src, target, output_dir)

            def work(runner, src=src, out=out):
                return [B.convert(runner, src, target, out)]
            jobs.append(self.submit(Job(os.path.basename(out), work)))
        return jobs

    def tool(self, tool: str, files: list[str], params: dict, output_dir: str | None) -> Job:
        name = F.TOOLS[tool][1]
        title = (f"{name} · {os.path.basename(files[0])}" if len(files) == 1
                 else f"{name} · {len(files)} files")

        def work(runner):
            return T.run_tool(runner, tool, files, params, output_dir)
        return self.submit(Job(title, work))
