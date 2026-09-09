"""Tests for the persistent background-job queue."""

import json
import stat
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from confluence_markdown_exporter import jobs
from confluence_markdown_exporter import main as main_module
from confluence_markdown_exporter.main import app


@pytest.fixture
def job_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "jobs"
    monkeypatch.setenv("CME_JOB_DIR", str(directory))
    return directory


def test_submit_job_persists_safe_state_without_auth_secrets(
    job_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    starter = MagicMock(return_value=123)
    monkeypatch.setattr(jobs, "start_worker", starter)
    monkeypatch.setenv("CME_EXPORT__OUTPUT_PATH", "/srv/export")
    monkeypatch.setenv("CME_AUTH__CONFLUENCE__TOKEN", "must-not-be-persisted")

    job = jobs.submit_job(
        ["orgs", "https://user:password@example.test?token=secret", "--all-spaces"]
    )

    stored = json.loads((job_dir / "jobs.json").read_text(encoding="utf-8"))["jobs"][0]
    assert stored["id"] == job["id"]
    assert stored["status"] == "queued"
    assert stored["command"] == ["orgs", "https://example.test", "--all-spaces"]
    assert stored["environment"]["CME_EXPORT__OUTPUT_PATH"] == "/srv/export"
    assert "CME_CONFIG_PATH" in stored["environment"]
    assert "must-not-be-persisted" not in (job_dir / "jobs.json").read_text(encoding="utf-8")
    assert "password" not in (job_dir / "jobs.json").read_text(encoding="utf-8")
    assert "token=secret" not in (job_dir / "jobs.json").read_text(encoding="utf-8")
    assert stat.S_IMODE((job_dir / "jobs.json").stat().st_mode) == 0o600
    starter.assert_called_once_with(job_dir)


def test_worker_drains_jobs_in_fifo_order(
    job_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs, "start_worker", MagicMock())
    first = jobs.submit_job(["pages", "https://example.test/1"])
    second = jobs.submit_job(["spaces", "https://example.test/SPACE"])
    executed: list[str] = []

    def complete(directory: Path, job: dict) -> None:
        executed.append(job["id"])
        jobs._update_job(
            directory,
            job["id"],
            status="succeeded",
            finished_at="done",
            exit_code=0,
        )

    monkeypatch.setattr(jobs, "_execute_job", complete)

    jobs.run_worker(job_dir)

    assert executed == [first["id"], second["id"]]
    assert [item["status"] for item in jobs.list_jobs()] == ["succeeded", "succeeded"]


def test_worker_is_detached_from_terminal(job_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    popen = MagicMock(return_value=SimpleNamespace(pid=456))
    monkeypatch.setattr(jobs.subprocess, "Popen", popen)

    assert jobs.start_worker(job_dir) == 456

    _, kwargs = popen.call_args
    assert kwargs["stdin"] is jobs.subprocess.DEVNULL
    assert kwargs["stderr"] is jobs.subprocess.STDOUT
    assert kwargs["start_new_session"] is True
    assert kwargs["close_fds"] is True


def test_submit_rejects_commands_outside_background_allowlist(job_dir: Path) -> None:
    assert job_dir.name == "jobs"
    with pytest.raises(ValueError, match="Unsupported background command"):
        jobs.submit_job(["config", "set", "auth.token=secret"])


def test_resume_requeues_stale_and_interrupted_jobs(
    job_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs, "start_worker", MagicMock())
    stale = jobs.submit_job(["retry-failures"])
    interrupted = jobs.submit_job(["list-spaces", "https://example.test"])
    jobs._update_job(job_dir, stale["id"], status="running", pid=999_999_999)
    jobs._update_job(job_dir, interrupted["id"], status="interrupted", pid=None)
    starter = MagicMock(return_value=321)
    monkeypatch.setattr(jobs, "start_worker", starter)

    requeued = jobs.resume_jobs()

    assert requeued == 2
    assert [item["status"] for item in jobs.list_jobs()] == ["queued", "queued"]
    starter.assert_called_once_with(job_dir)


def test_org_background_queues_canonical_command(
    job_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    submitted = MagicMock(
        return_value={"id": "abc123", "status": "queued", "log_path": str(job_dir / "job.log")}
    )
    monkeypatch.setattr(jobs, "submit_job", submitted)
    output_lock = MagicMock()
    monkeypatch.setattr(main_module, "acquire_output_lock", output_lock)

    result = CliRunner().invoke(
        app,
        [
            "orgs",
            "https://example.test",
            "--all-spaces",
            "--background",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "abc123" in result.output
    submitted.assert_called_once_with(
        ["orgs", "https://example.test", "--all-spaces"]
    )
    output_lock.assert_not_called()


def test_detached_job_waits_for_existing_output_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_lock = MagicMock(return_value=nullcontext())
    settings = SimpleNamespace(export=SimpleNamespace(output_path=tmp_path))
    monkeypatch.setenv("CME_JOB_ID", "job123")
    monkeypatch.setattr(main_module, "acquire_output_lock", output_lock)
    monkeypatch.setattr(main_module, "get_settings", MagicMock(return_value=settings))

    def operation(*, background: bool = False) -> str:
        _ = background
        return "done"

    wrapped = main_module._with_output_lock(operation)

    assert wrapped() == "done"
    output_lock.assert_called_once_with(tmp_path, timeout=-1)


def test_jobs_status_lists_queued_work(job_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert job_dir.name == "jobs"
    monkeypatch.setattr(jobs, "start_worker", MagicMock())
    job = jobs.submit_job(["pages", "https://example.test/page"])

    result = CliRunner().invoke(app, ["jobs", "status"])

    assert result.exit_code == 0, result.output
    assert job["id"] in result.output
    assert "queued" in result.output
