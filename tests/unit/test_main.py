"""Unit tests for main module."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from confluence_markdown_exporter.main import _finish_export
from confluence_markdown_exporter.main import _write_failure_report
from confluence_markdown_exporter.main import app
from confluence_markdown_exporter.main import version
from confluence_markdown_exporter.utils.rich_console import reset_stats


class TestVersionCommand:
    """Test cases for version command."""

    def test_version_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that version command outputs correct format."""
        version()

        captured = capsys.readouterr()
        assert "confluence-markdown-exporter" in captured.out
        # Should contain version information
        assert len(captured.out.strip()) > len("confluence-markdown-exporter")


class TestAppConfiguration:
    """Test cases for the Typer app configuration."""

    def test_app_is_typer_instance(self) -> None:
        """Test that app is a Typer instance."""
        assert isinstance(app, typer.Typer)

    def test_app_has_commands(self) -> None:
        """Test that app has expected top-level commands."""
        commands = [
            callback.callback.__name__.replace("_", "-")
            for callback in app.registered_commands
            if callback.callback is not None
        ]

        expected_commands = [
            "pages",
            "pages-with-descendants",
            "spaces",
            "list-spaces",
            "orgs",
            "version",
        ]
        for expected_command in expected_commands:
            assert expected_command in commands

    def test_app_has_config_group(self) -> None:
        """Test that the config sub-app is registered as a command group."""
        group_names = [group.name for group in app.registered_groups]
        assert "config" in group_names


class TestListSpacesCommand:
    """Space inventory is available in machine-readable formats."""

    @staticmethod
    def _organization() -> SimpleNamespace:
        return SimpleNamespace(
            spaces=[
                SimpleNamespace(
                    key="BTT",
                    name="Bedrock Tech Team",
                    type="global",
                    status="current",
                    homepage=123,
                    description="Engineering docs",
                ),
                SimpleNamespace(
                    key="~user",
                    name="Personal Space",
                    type="personal",
                    status="archived",
                    homepage=None,
                    description="",
                ),
            ]
        )

    def test_json_inventory_can_be_written_to_file(self, tmp_path: Path) -> None:
        output = tmp_path / "spaces.json"
        with (
            patch("confluence_markdown_exporter.main._init_logging"),
            patch(
                "confluence_markdown_exporter.confluence.Organization.inventory_from_url",
                return_value=self._organization(),
            ) as inventory,
        ):
            result = CliRunner().invoke(
                app,
                [
                    "list-spaces",
                    "https://company.atlassian.net?token=secret",
                    "--format",
                    "json",
                    "--output",
                    str(output),
                ],
            )

        assert result.exit_code == 0, result.output
        inventory.assert_called_once_with("https://company.atlassian.net")
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["schema_version"] == 1
        assert payload["space_count"] == 2
        assert payload["spaces"][0]["key"] == "BTT"
        assert payload["spaces"][1]["status"] == "archived"
        assert "token=secret" not in output.read_text(encoding="utf-8")

    def test_csv_inventory_prints_to_stdout(self) -> None:
        with (
            patch("confluence_markdown_exporter.main._init_logging"),
            patch(
                "confluence_markdown_exporter.confluence.Organization.inventory_from_url",
                return_value=self._organization(),
            ),
        ):
            result = CliRunner().invoke(
                app,
                ["list-spaces", "https://company.atlassian.net", "--format", "csv"],
            )

        assert result.exit_code == 0, result.output
        assert "base_url,key,name,type,status,homepage_id,description,space_url" in result.output
        assert "BTT,Bedrock Tech Team,global,current,123" in result.output


class TestOrganizationBackupScope:
    """The opt-in backup scope includes every inventoried space."""

    def test_all_spaces_uses_complete_inventory(self, tmp_path: Path) -> None:
        settings = SimpleNamespace(
            export=SimpleNamespace(
                output_path=tmp_path,
                log_level="ERROR",
                save_log_to_file=False,
            )
        )
        organization = MagicMock()

        with (
            patch("confluence_markdown_exporter.main.get_settings", return_value=settings),
            patch("confluence_markdown_exporter.main.LockfileManager"),
            patch("confluence_markdown_exporter.main._finish_export"),
            patch(
                "confluence_markdown_exporter.confluence.Organization.inventory_from_url",
                return_value=organization,
            ) as inventory,
            patch(
                "confluence_markdown_exporter.confluence.Organization.from_url"
            ) as current_only,
            patch("confluence_markdown_exporter.confluence.sync_removed_pages"),
        ):
            result = CliRunner().invoke(
                app,
                ["orgs", "https://company.atlassian.net", "--all-spaces"],
            )

        assert result.exit_code == 0, result.output
        inventory.assert_called_once_with("https://company.atlassian.net")
        current_only.assert_not_called()
        organization.export.assert_called_once_with()


class TestFailureReport:
    """Failure reports are sanitized and drive the CLI exit status."""

    @staticmethod
    def _settings(tmp_path: Path) -> SimpleNamespace:
        return SimpleNamespace(
            export=SimpleNamespace(
                output_path=tmp_path,
                failure_report_name="confluence-failures.json",
                integrity_manifest_name="confluence-manifest.json",
                lockfile_name="confluence-lock.json",
            )
        )

    def test_write_failure_report_contains_only_sanitized_metadata(self, tmp_path: Path) -> None:
        stats = reset_stats(total=2)
        stats.inc_failed()
        stats.record_failure(
            category="page",
            identifier="123",
            title="Example page",
            error_type="RuntimeError",
        )

        with patch(
            "confluence_markdown_exporter.main.get_settings",
            return_value=self._settings(tmp_path),
        ):
            report_path = _write_failure_report()

        assert report_path == tmp_path / "confluence-failures.json"
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        assert payload["summary"]["pages_failed"] == 1
        assert payload["failures"] == [
            {
                "category": "page",
                "identifier": "123",
                "title": "Example page",
                "error_type": "RuntimeError",
                "status_code": None,
                "retry_url": None,
            }
        ]
        assert "message" not in payload["failures"][0]

    def test_successful_run_removes_stale_failure_report(self, tmp_path: Path) -> None:
        reset_stats(total=1)
        report_path = tmp_path / "confluence-failures.json"
        report_path.write_text("stale", encoding="utf-8")

        with patch(
            "confluence_markdown_exporter.main.get_settings",
            return_value=self._settings(tmp_path),
        ):
            assert _write_failure_report() is None

        assert not report_path.exists()

    def test_finish_export_raises_exit_one_when_report_exists(self, tmp_path: Path) -> None:
        report_path = tmp_path / "confluence-failures.json"
        with (
            patch(
                "confluence_markdown_exporter.main._write_failure_report",
                return_value=report_path,
            ),
            patch("confluence_markdown_exporter.main._print_summary"),
            patch("confluence_markdown_exporter.main.write_integrity_manifest"),
            patch("confluence_markdown_exporter.main.console"),
            pytest.raises(typer.Exit) as exc_info,
        ):
            _finish_export()

        assert exc_info.value.exit_code == 1

    def test_pages_command_returns_exit_one_after_partial_failure(self, tmp_path: Path) -> None:
        settings = self._settings(tmp_path)
        settings.export.log_level = "ERROR"
        settings.export.save_log_to_file = False
        page = MagicMock(id=123, title="Broken page", base_url="https://example.test")
        page.export.side_effect = RuntimeError("sensitive raw detail")

        with (
            patch("confluence_markdown_exporter.main.get_settings", return_value=settings),
            patch("confluence_markdown_exporter.main.LockfileManager") as mock_lockfile,
            patch(
                "confluence_markdown_exporter.confluence.Page.from_url",
                return_value=page,
            ),
            patch("confluence_markdown_exporter.confluence.sync_removed_pages"),
        ):
            mock_lockfile.should_export.return_value = True
            result = CliRunner().invoke(app, ["pages", "https://example.test/page"])

        assert result.exit_code == 1
        report = (tmp_path / "confluence-failures.json").read_text(encoding="utf-8")
        assert "Broken page" in report
        assert "RuntimeError" in report
        assert "sensitive raw detail" not in report

    def test_spaces_command_continues_after_one_space_fails(self, tmp_path: Path) -> None:
        settings = self._settings(tmp_path)
        settings.export.log_level = "ERROR"
        settings.export.save_log_to_file = False
        good_space = MagicMock(base_url="https://example.test")
        good_space.name = "Good"
        good_space.pages = [MagicMock(id=1, title="Good page")]
        settings.connection_config = SimpleNamespace(space_workers=2)
        first_url = "https://example.test/wiki/spaces/BAD/overview?token=secret"
        second_url = "https://example.test/wiki/spaces/GOOD/overview"

        with (
            patch("confluence_markdown_exporter.main.get_settings", return_value=settings),
            patch("confluence_markdown_exporter.main.LockfileManager"),
            patch(
                "confluence_markdown_exporter.confluence.Space.from_url",
                side_effect=[RuntimeError("private discovery detail"), good_space],
            ),
            patch("confluence_markdown_exporter.confluence.export_pages") as mock_export_pages,
            patch("confluence_markdown_exporter.confluence.sync_removed_pages") as mock_cleanup,
        ):
            result = CliRunner().invoke(app, ["spaces", first_url, second_url])

        assert result.exit_code == 1
        mock_export_pages.assert_called_once_with(good_space.pages)
        mock_cleanup.assert_called_once_with("https://example.test")
        report = (tmp_path / "confluence-failures.json").read_text(encoding="utf-8")
        assert '"category": "space"' in report
        assert "token=secret" not in report
        assert "private discovery detail" not in report

    def test_retry_failures_replays_page_and_removes_report(self, tmp_path: Path) -> None:
        settings = self._settings(tmp_path)
        settings.export.log_level = "ERROR"
        settings.export.save_log_to_file = False
        report_path = tmp_path / "confluence-failures.json"
        report_path.write_text(
            json.dumps(
                {
                    "report_version": 2,
                    "failures": [
                        {
                            "category": "page",
                            "retry_url": "https://example.test/wiki/spaces/KEY/pages/123",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        page = MagicMock(id=123, title="Recovered", base_url="https://example.test")
        page.export.return_value = {}

        with (
            patch("confluence_markdown_exporter.main.get_settings", return_value=settings),
            patch("confluence_markdown_exporter.main.LockfileManager") as mock_lockfile,
            patch(
                "confluence_markdown_exporter.confluence.Page.from_url",
                return_value=page,
            ) as mock_from_url,
        ):
            result = CliRunner().invoke(app, ["retry-failures"])

        assert result.exit_code == 0
        mock_from_url.assert_called_once_with(
            "https://example.test/wiki/spaces/KEY/pages/123"
        )
        page.export.assert_called_once_with()
        mock_lockfile.record_page.assert_called_once_with(page, {})
        assert not report_path.exists()
        assert (tmp_path / "confluence-manifest.json").exists()
