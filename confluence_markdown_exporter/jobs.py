"""Persistent background-job queue for long-running CME commands."""

import json
import os
import subprocess
import sys
import time
import urllib.parse
import uuid
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Annotated
from typing import Any

import typer
from filelock import FileLock
from filelock import Timeout
from rich.table import Table

from confluence_markdown_exporter.utils.app_data_store import APP_CONFIG_PATH
from confluence_markdown_exporter.utils.export import save_file
from confluence_markdown_exporter.utils.rich_console import console

_STATE_VERSION = 1
_TERMINAL_STATUSES = {"succeeded", "failed", "interrupted"}
_SAFE_ENV_PREFIXES = ("CME_EXPORT__", "CME_CONNECTION_CONFIG__")
_BACKGROUND_COMMANDS = {
    "list-spaces",
    "orgs",
    "pages",
    "pages-with-descendants",
    "retry-failures",
    "spaces",
}


def get_job_dir() -> Path:
    """Return the per-user job-state directory."""
    override = os.environ.get("CME_JOB_DIR")
    return Path(override).expanduser() if override else APP_CONFIG_PATH.parent / "jobs"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(job_dir: Path) -> Path:
    return job_dir / "jobs.json"


def _state_lock(job_dir: Path) -> FileLock:
    job_dir.mkdir(parents=True, exist_ok=True)
    return FileLock(job_dir / "jobs.lock", timeout=10)


def _read_state_unlocked(job_dir: Path) -> dict[str, Any]:
    path = _state_path(job_dir)
    if not path.exists():
        return {"schema_version": _STATE_VERSION, "jobs": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        msg = f"Invalid background-job state: {path}"
        raise ValueError(msg) from error
    if not isinstance(value, dict) or not isinstance(value.get("jobs"), list):
        msg = f"Invalid background-job state: {path}"
        raise TypeError(msg)
    return value


def _write_state_unlocked(job_dir: Path, state: dict[str, Any]) -> None:
    path = _state_path(job_dir)
    save_file(path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)


def _captured_environment() -> dict[str, str]:
    """Capture non-secret CME overrides needed by a detached worker."""
    return {
        key: value
        for key, value in os.environ.items()
        if key == "CME_CONFIG_PATH" or key.startswith(_SAFE_ENV_PREFIXES)
    }


def _sanitized_command(command: list[str]) -> list[str]:
    """Remove URL credentials and unsafe query values before durable storage."""
    result: list[str] = []
    for value in command:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            result.append(value)
            continue
        port = f":{parsed.port}" if parsed.port else ""
        query = urllib.parse.urlencode(
            [(key, item) for key, item in urllib.parse.parse_qsl(parsed.query) if key == "pageId"]
        )
        result.append(
            urllib.parse.urlunparse(
                (parsed.scheme, f"{parsed.hostname}{port}", parsed.path, "", query, "")
            )
        )
    return result


def submit_job(command: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
    """Append a command to the persistent FIFO queue and ensure a worker exists."""
    if not command or command[0] not in _BACKGROUND_COMMANDS:
        msg = "Unsupported background command"
        raise ValueError(msg)
    command = _sanitized_command(command)
    job_dir = get_job_dir()
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "command": command,
        "cwd": str((cwd or Path.cwd()).resolve()),
        "environment": _captured_environment(),
        "status": "queued",
        "submitted_at": _utc_now(),
        "started_at": None,
        "finished_at": None,
        "pid": None,
        "exit_code": None,
        "attempt": 0,
        "log_path": str(job_dir / f"{job_id}.log"),
    }
    with _state_lock(job_dir):
        state = _read_state_unlocked(job_dir)
        state["jobs"].append(job)
        _write_state_unlocked(job_dir, state)
    start_worker(job_dir)
    return job


def list_jobs() -> list[dict[str, Any]]:
    """Return a consistent snapshot of all recorded jobs."""
    job_dir = get_job_dir()
    with _state_lock(job_dir):
        return list(_read_state_unlocked(job_dir)["jobs"])


def get_job(job_id: str) -> dict[str, Any]:
    """Return one job by full ID or unambiguous prefix."""
    matches = [job for job in list_jobs() if str(job.get("id", "")).startswith(job_id)]
    if len(matches) == 1:
        return matches[0]
    msg = f"Unknown job: {job_id}" if not matches else f"Ambiguous job ID prefix: {job_id}"
    raise ValueError(msg)


def start_worker(job_dir: Path | None = None) -> int:
    """Start a worker detached from the caller's terminal and return its PID."""
    directory = job_dir or get_job_dir()
    directory.mkdir(parents=True, exist_ok=True)
    worker_log = directory / "worker.log"
    env = os.environ.copy()
    env["CME_JOB_DIR"] = str(directory)
    worker_log.touch(mode=0o600, exist_ok=True)
    worker_log.chmod(0o600)
    with worker_log.open("ab") as stream:
        process = subprocess.Popen(
            [sys.executable, "-m", "confluence_markdown_exporter.jobs", "worker"],
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            cwd=Path.cwd(),
            env=env,
            start_new_session=True,
            close_fds=True,
        )
    return process.pid


def _update_job(job_dir: Path, job_id: str, **changes: object) -> dict[str, Any]:
    with _state_lock(job_dir):
        state = _read_state_unlocked(job_dir)
        for job in state["jobs"]:
            if job.get("id") == job_id:
                job.update(changes)
                _write_state_unlocked(job_dir, state)
                return job
    msg = f"Unknown job: {job_id}"
    raise ValueError(msg)


def _next_queued_job(job_dir: Path) -> dict[str, Any] | None:
    with _state_lock(job_dir):
        state = _read_state_unlocked(job_dir)
        for job in state["jobs"]:
            if job.get("status") == "queued":
                job.update(
                    status="starting",
                    started_at=_utc_now(),
                    finished_at=None,
                    pid=None,
                    exit_code=None,
                    attempt=int(job.get("attempt", 0)) + 1,
                )
                _write_state_unlocked(job_dir, state)
                return dict(job)
    return None


def _execute_job(job_dir: Path, job: dict[str, Any]) -> None:
    if not job["command"] or job["command"][0] not in _BACKGROUND_COMMANDS:
        msg = f"Unsupported background command in job {job['id']}"
        raise ValueError(msg)
    command = [sys.executable, "-m", "confluence_markdown_exporter.main", *job["command"]]
    env = os.environ.copy()
    env.update(job.get("environment") or {})
    env["CME_JOB_ID"] = str(job["id"])
    log_path = Path(job["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.touch(mode=0o600, exist_ok=True)
    log_path.chmod(0o600)
    with log_path.open("ab") as stream:
        process = subprocess.Popen(  # noqa: S603 - executable fixed; command allowlisted above
            command,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            cwd=job["cwd"],
            env=env,
            close_fds=True,
        )
        _update_job(job_dir, job["id"], status="running", pid=process.pid)
        exit_code = process.wait()
    _update_job(
        job_dir,
        job["id"],
        status="succeeded" if exit_code == 0 else "failed",
        finished_at=_utc_now(),
        exit_code=exit_code,
        pid=None,
    )


def run_worker(job_dir: Path | None = None) -> None:
    """Drain queued jobs serially; only one worker may own a queue."""
    directory = job_dir or get_job_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        # A newly spawned worker waits briefly for the current worker. This
        # closes the hand-off race where a job is queued just as the owner sees
        # an empty queue and releases the lock.
        with FileLock(directory / "worker.lock", timeout=5):
            while job := _next_queued_job(directory):
                try:
                    _execute_job(directory, job)
                except Exception:  # noqa: BLE001 - preserve queue progress after any job error
                    _update_job(
                        directory,
                        job["id"],
                        status="interrupted",
                        finished_at=_utc_now(),
                        pid=None,
                    )
    except Timeout:
        return


def _pid_is_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def resume_jobs() -> int:
    """Requeue jobs whose worker disappeared, then start a detached worker."""
    job_dir = get_job_dir()
    requeued = 0
    with _state_lock(job_dir):
        state = _read_state_unlocked(job_dir)
        for job in state["jobs"]:
            status = job.get("status")
            if status in {"starting", "interrupted"} or (
                status == "running" and not _pid_is_alive(job.get("pid"))
            ):
                job.update(status="queued", pid=None, finished_at=None, exit_code=None)
                requeued += 1
        _write_state_unlocked(job_dir, state)
    if any(job.get("status") == "queued" for job in state["jobs"]):
        start_worker(job_dir)
    return requeued


def _display_command(job: dict[str, Any]) -> str:
    return "cme " + " ".join(str(value) for value in job.get("command", []))


def _render_jobs(jobs: list[dict[str, Any]]) -> None:
    table = Table(title=f"CME background jobs ({len(jobs)})")
    table.add_column("ID", style="cyan", width=12, no_wrap=True)
    table.add_column("Status")
    table.add_column("Submitted")
    table.add_column("Updated")
    table.add_column("Attempt", justify="right")
    table.add_column("Exit", justify="right")
    table.add_column("Command", overflow="fold")
    for job in reversed(jobs):
        table.add_row(
            str(job.get("id", "")),
            str(job.get("status", "unknown")),
            str(job.get("submitted_at", ""))[:19].replace("T", " "),
            str(
                job.get("finished_at") or job.get("started_at") or job.get("submitted_at", "")
            )[:19].replace("T", " "),
            str(job.get("attempt", 0)),
            "" if job.get("exit_code") is None else str(job["exit_code"]),
            _display_command(job),
        )
    console.print(table)


jobs_app = typer.Typer(
    help="Inspect and resume persistent background export jobs.",
    no_args_is_help=False,
    invoke_without_command=True,
)


@jobs_app.callback()
def jobs_callback(ctx: typer.Context) -> None:
    """List jobs when no jobs subcommand is supplied."""
    if ctx.invoked_subcommand is None:
        _render_jobs(list_jobs())


@jobs_app.command("status")
def jobs_status(
    job_id: Annotated[
        str | None,
        typer.Argument(help="Full job ID or an unambiguous prefix."),
    ] = None,
) -> None:
    """Show all jobs, or detailed state for one job."""
    if job_id is None:
        _render_jobs(list_jobs())
        return
    job = get_job(job_id)
    for key in (
        "id",
        "status",
        "submitted_at",
        "started_at",
        "finished_at",
        "attempt",
        "pid",
        "exit_code",
        "cwd",
        "log_path",
    ):
        typer.echo(f"{key}: {job.get(key)}")
    typer.echo(f"command: {_display_command(job)}")


@jobs_app.command("logs")
def jobs_logs(
    job_id: Annotated[str, typer.Argument(help="Full job ID or an unambiguous prefix.")],
    follow: Annotated[  # noqa: FBT002 - Typer exposes this as a CLI option
        bool,
        typer.Option("--follow", "-f", help="Continue printing until the job finishes."),
    ] = False,
) -> None:
    """Print a job's captured output; following logs never owns the job process."""
    job = get_job(job_id)
    path = Path(job["log_path"])
    position = 0
    while True:
        if path.exists():
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                stream.seek(position)
                chunk = stream.read()
                position = stream.tell()
            if chunk:
                typer.echo(chunk, nl=False)
        job = get_job(str(job["id"]))
        if not follow or job.get("status") in _TERMINAL_STATUSES:
            return
        time.sleep(1)


@jobs_app.command("resume")
def jobs_resume() -> None:
    """Requeue interrupted/stale jobs and ensure the worker is running."""
    requeued = resume_jobs()
    console.print(f"Recovery check complete; requeued {requeued} interrupted/stale job(s).")


def _worker_main() -> None:
    if sys.argv[1:] == ["worker"]:
        run_worker()


if __name__ == "__main__":
    _worker_main()
