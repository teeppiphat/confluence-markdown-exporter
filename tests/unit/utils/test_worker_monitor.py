"""Tests for the live worker activity registry."""

from confluence_markdown_exporter.utils.worker_monitor import WorkerMonitor


def test_activity_restores_parent_then_becomes_idle() -> None:
    monitor = WorkerMonitor()

    with monitor.activity("page", "exporting", "42 · Example"):
        [page_state] = monitor.snapshot()
        assert page_state.state == "exporting"
        assert page_state.work == "42 · Example"

        with monitor.activity("page", "attachment", "image.png"):
            [attachment_state] = monitor.snapshot()
            assert attachment_state.state == "attachment"
            assert attachment_state.work == "image.png"

        [restored_state] = monitor.snapshot()
        assert restored_state.state == "exporting"
        assert restored_state.work == "42 · Example"

    [idle_state] = monitor.snapshot()
    assert idle_state.state == "idle"
    assert idle_state.work == "Waiting for work"
