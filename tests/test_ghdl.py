"""Tests for the gh_download utility functions."""

import subprocess
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any, Never
from unittest import mock

import pytest
import requests
from typer.testing import CliRunner

from gh_download import (
    _build_api_url_and_headers,
    _check_gh_auth_status,
    _check_gh_cli_availability,
    _check_gh_executable_and_notify,
    _handle_download_errors,
    _handle_gh_authentication_status,
    _perform_download_and_save,
    _perform_gh_login_and_verify,
    _retrieve_gh_auth_token,
    download_file,
    get_github_token_from_gh_cli,
    run_gh_auth_login,
)
from gh_download.cli import app


# Suppress console output during tests for cleaner test runs
@pytest.fixture(autouse=True)
def no_console_output(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("rich.console.Console.print", lambda *_, **__: None)
    monkeypatch.setattr(
        "rich.prompt.Confirm.ask",
        lambda *_, **__: True,
    )


@pytest.fixture(autouse=True)
def mock_shutil_which(monkeypatch: pytest.MonkeyPatch):
    def mock_which(cmd: str) -> str | None:
        if cmd == "gh":
            return "gh"
        return None

    monkeypatch.setattr("gh_download.shutil.which", mock_which)


def test_check_gh_executable_and_notify_not_found(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("gh_download.shutil.which", lambda _: None)
    assert _check_gh_executable_and_notify() is None


@pytest.fixture
def mock_subprocess_run() -> Generator[mock.MagicMock]:
    with mock.patch("subprocess.run") as mock_run:
        yield mock_run


def test_check_gh_auth_status_subprocess_error(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.side_effect = subprocess.SubprocessError("Test SubprocessError")
    assert not _check_gh_auth_status("gh")


def test_check_gh_auth_status_os_error(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.side_effect = OSError("Test OSError")
    assert not _check_gh_auth_status("gh")


def test_check_gh_auth_status_unexpected_stderr(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.return_value = mock.Mock(
        stdout="Some output",
        stderr="Unexpected error message",
        returncode=1,
    )
    assert not _check_gh_auth_status("gh")


def test_perform_gh_login_and_verify_file_not_found(
    mock_subprocess_run: mock.MagicMock,
):
    mock_subprocess_run.side_effect = FileNotFoundError
    assert not _perform_gh_login_and_verify("gh")
    mock_subprocess_run.assert_called_once_with(
        ["gh", "auth", "login", "--hostname", "github.com", "--web"],
        check=False,
    )


def test_perform_gh_login_and_verify_subprocess_error(
    mock_subprocess_run: mock.MagicMock,
):
    mock_subprocess_run.side_effect = subprocess.SubprocessError("Test Error")
    assert not _perform_gh_login_and_verify("gh")


def test_perform_gh_login_and_verify_os_error(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.side_effect = OSError("Test Error")
    assert not _perform_gh_login_and_verify("gh")


def test_perform_gh_login_and_verify_login_command_fails(
    mock_subprocess_run: mock.MagicMock,
):
    mock_subprocess_run.side_effect = [
        mock.Mock(returncode=1, stdout="", stderr=""),
        mock.Mock(stdout="", stderr="Error: not logged in", returncode=1),
    ]
    assert not _perform_gh_login_and_verify("gh")


def test_perform_gh_login_and_verify_status_check_fails_after_login(
    mock_subprocess_run: mock.MagicMock,
):
    mock_subprocess_run.side_effect = [
        mock.Mock(returncode=0, stdout="", stderr=""),
        mock.Mock(stdout="", stderr="Error: not logged in", returncode=1),
    ]
    assert not _perform_gh_login_and_verify("gh")


def test_check_gh_cli_availability_gh_not_found_at_which(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("gh_download.shutil.which", lambda _: None)
    assert _check_gh_cli_availability() is None


def test_check_gh_cli_availability_version_file_not_found(
    mock_subprocess_run: mock.MagicMock,
):
    mock_subprocess_run.side_effect = FileNotFoundError
    assert _check_gh_cli_availability() is None
    mock_subprocess_run.assert_called_once_with(
        ["gh", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )


def test_check_gh_cli_availability_version_called_process_error(
    mock_subprocess_run: mock.MagicMock,
):
    mock_subprocess_run.side_effect = subprocess.CalledProcessError(1, "cmd")
    assert _check_gh_cli_availability() == "gh"


def test_handle_gh_authentication_status_user_declines_login(
    mock_subprocess_run: mock.MagicMock,
    monkeypatch: pytest.MonkeyPatch,
):
    mock_subprocess_run.return_value = mock.Mock(
        stdout="",
        stderr="not logged in",
        returncode=1,
    )
    monkeypatch.setattr("rich.prompt.Confirm.ask", lambda *_, **__: False)
    assert not _handle_gh_authentication_status("gh")


def test_handle_gh_authentication_status_run_gh_auth_login_fails(
    mock_subprocess_run: mock.MagicMock,
    monkeypatch: pytest.MonkeyPatch,
):
    mock_subprocess_run.return_value = mock.Mock(
        stdout="",
        stderr="not logged in",
        returncode=1,
    )
    monkeypatch.setattr("gh_download.run_gh_auth_login", lambda: False)
    assert not _handle_gh_authentication_status("gh")


def test_handle_gh_authentication_status_still_not_authed_after_login(
    mock_subprocess_run: mock.MagicMock,
    monkeypatch: pytest.MonkeyPatch,
):
    mock_subprocess_run.side_effect = [
        mock.Mock(stdout="", stderr="not logged in", returncode=1),
        mock.Mock(stdout="", stderr="not logged in", returncode=1),
    ]
    monkeypatch.setattr("gh_download.run_gh_auth_login", lambda: True)
    assert not _handle_gh_authentication_status("gh")


@pytest.fixture
def mock_requests_get() -> Generator[mock.MagicMock]:
    with mock.patch("requests.Session.get") as mock_get:
        yield mock_get


@pytest.fixture
def mock_get_token_from_cli() -> Generator[mock.MagicMock]:
    with mock.patch("gh_download.get_github_token_from_gh_cli") as mock_get_token:
        yield mock_get_token


def test_get_github_token_gh_not_installed(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.side_effect = FileNotFoundError
    assert get_github_token_from_gh_cli() is None


def test_get_github_token_gh_auth_status_fail(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.side_effect = [
        mock.Mock(returncode=0, stdout="", stderr=""),  # gh --version
        mock.Mock(
            stdout="",
            stderr="Error: not logged in",
            returncode=1,
        ),  # gh auth status
    ]
    with mock.patch("rich.prompt.Confirm.ask", return_value=False):
        assert get_github_token_from_gh_cli() is None


def test_get_github_token_gh_auth_status_success_then_token_success(
    mock_subprocess_run: mock.MagicMock,
):
    mock_subprocess_run.side_effect = [
        mock.Mock(returncode=0, stdout="", stderr=""),  # gh --version
        mock.Mock(
            stdout="Logged in to github.com account user",
            returncode=0,
        ),  # gh auth status
        mock.Mock(stdout="MOCK_TOKEN_VALUE\n", returncode=0),  # gh auth token
    ]
    assert get_github_token_from_gh_cli() == "MOCK_TOKEN_VALUE"


def test_get_github_token_gh_auth_status_fail_then_login_attempt_and_token_success(
    mock_subprocess_run: mock.MagicMock,
):
    mock_subprocess_run.side_effect = [
        mock.Mock(returncode=0, stdout="", stderr=""),  # gh --version
        mock.Mock(
            stdout="",
            stderr="Error: not logged in",
            returncode=1,
        ),  # gh auth status (1st check)
        mock.Mock(
            returncode=0,
            stdout="",
            stderr="",
        ),  # gh auth login (in run_gh_auth_login)
        mock.Mock(
            stdout="Logged in to github.com account user",
            returncode=0,
        ),  # gh auth status (in run_gh_auth_login)
        mock.Mock(
            stdout="Logged in to github.com account user",
            returncode=0,
        ),  # gh auth status (2nd check)
        mock.Mock(stdout="MOCK_TOKEN_VALUE\n", returncode=0),  # gh auth token
    ]
    assert get_github_token_from_gh_cli() == "MOCK_TOKEN_VALUE"


def test_retrieve_gh_auth_token_called_process_error(
    mock_subprocess_run: mock.MagicMock,
):
    mock_subprocess_run.side_effect = subprocess.CalledProcessError(
        1,
        "cmd",
        stderr="Token error",
    )
    assert _retrieve_gh_auth_token("gh") is None


def test_get_github_token_from_gh_cli_retrieve_token_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("gh_download._check_gh_cli_availability", lambda: "gh")
    monkeypatch.setattr("gh_download._handle_gh_authentication_status", lambda _: True)
    monkeypatch.setattr("gh_download._retrieve_gh_auth_token", lambda _: None)
    assert get_github_token_from_gh_cli() is None


def test_get_github_token_from_gh_cli_called_process_error_handling(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("gh_download._check_gh_cli_availability", lambda: "gh")
    monkeypatch.setattr("gh_download._handle_gh_authentication_status", lambda _: True)
    monkeypatch.setattr(
        "gh_download._retrieve_gh_auth_token",
        mock.Mock(
            side_effect=subprocess.CalledProcessError(1, "cmd", stderr="Token error"),
        ),
    )
    assert get_github_token_from_gh_cli() is None


def test_get_github_token_from_gh_cli_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("gh_download._check_gh_cli_availability", lambda: "gh")
    monkeypatch.setattr(
        "gh_download._handle_gh_authentication_status",
        mock.Mock(side_effect=Exception("Unexpected internal error")),
    )
    assert get_github_token_from_gh_cli() is None


def test_build_api_url_and_headers_no_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("gh_download.get_github_token_from_gh_cli", lambda: None)
    assert _build_api_url_and_headers("owner", "repo", "path", "main") is None


def test_perform_download_and_save_os_error(
    mock_requests_get: mock.MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    mock_response = mock.Mock()
    mock_response.raise_for_status = mock.Mock()
    mock_response.content = b"file content"
    mock_requests_get.return_value = mock_response
    output_file = tmp_path / "test.txt"
    mock_open_func = mock.mock_open()
    monkeypatch.setattr("pathlib.Path.open", mock_open_func)
    mock_open_func.side_effect = OSError("Test OS Error writing file")
    assert not _perform_download_and_save(
        "api_url",
        {"header": "value"},
        output_file,
        "target",
    )


@pytest.mark.parametrize(
    ("exception_type", "setup_mock_response", "expected_in_output_text"),
    [
        (
            requests.exceptions.HTTPError,
            lambda r_mock: setattr(
                r_mock,
                "response",
                mock.Mock(
                    status_code=500,
                    json=mock.Mock(
                        side_effect=requests.exceptions.JSONDecodeError(
                            "err",
                            "doc",
                            0,
                        ),
                    ),
                    text="Raw text",
                ),
            ),
            "Raw Response",
        ),
        (
            requests.exceptions.HTTPError,
            lambda r_mock: setattr(
                r_mock,
                "response",
                mock.Mock(
                    status_code=401,
                    json=lambda: {
                        "message": "Unauthorized",
                        "documentation_url": "url",
                    },
                ),
            ),
            "Authentication/Authorization failed",
        ),
        (
            requests.exceptions.HTTPError,
            lambda r_mock: setattr(
                r_mock,
                "response",
                mock.Mock(
                    status_code=403,
                    json=lambda: {"message": "Forbidden", "documentation_url": "url"},
                ),
            ),
            "Authentication/Authorization failed",
        ),
        (requests.exceptions.Timeout, None, "Request timed out"),
        (requests.exceptions.ConnectionError, None, "Connection error"),
        (
            requests.exceptions.RequestException,
            None,
            "An unexpected request error occurred",
        ),
        (OSError, None, "Error writing file"),
        (Exception, None, "An unexpected error occurred during download"),
    ],
)
def test_handle_download_errors(
    exception_type: type[Exception],
    setup_mock_response: Callable[[Any], None] | None,
    expected_in_output_text: str,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
):
    mock_console_print = mock.MagicMock()
    monkeypatch.setattr("gh_download.console.print", mock_console_print)
    mock_exception = exception_type("Test error")

    # Handle specific case where we need to add a response attribute
    if setup_mock_response:
        if exception_type is requests.exceptions.HTTPError:
            # Create a properly typed HTTPError with response attribute
            mock_response = mock.Mock()
            mock_exception = requests.exceptions.HTTPError("Test error")
            mock_exception.response = mock_response  # type: ignore[attr-defined]
            setup_mock_response(mock_exception)
        elif hasattr(mock_exception, "response"):
            setup_mock_response(mock_exception)

    _handle_download_errors(mock_exception, "download_target", Path("output/path.txt"))
    mock_console_print.assert_called()


def test_download_file_no_token(
    mock_get_token_from_cli: mock.MagicMock,
    tmp_path: Path,
):
    mock_get_token_from_cli.return_value = None
    output_file = tmp_path / "file.txt"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    assert not download_file(
        "owner",
        "repo",
        "file.txt",
        "main",
        output_file,
    )
    mock_get_token_from_cli.assert_called_once()


def test_download_file_success(
    mock_get_token_from_cli: mock.MagicMock,
    mock_requests_get: mock.MagicMock,
    tmp_path: Path,
):
    mock_get_token_from_cli.return_value = "MOCK_TOKEN"
    mock_response = mock.Mock()
    mock_response.raise_for_status = mock.Mock()
    mock_response.content = b"file content"
    mock_requests_get.return_value = mock_response
    output_file = tmp_path / "downloaded.txt"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    assert download_file("owner", "repo", "file.txt", "main", output_file)
    mock_get_token_from_cli.assert_called_once()
    mock_requests_get.assert_called_once_with(
        "https://api.github.com/repos/owner/repo/contents/file.txt?ref=main",
        headers={
            "Authorization": "token MOCK_TOKEN",
            "Accept": "application/vnd.github.v3.raw",
        },
        timeout=30,
    )
    assert output_file.read_bytes() == b"file content"


def test_download_file_http_error(
    mock_get_token_from_cli: mock.MagicMock,
    mock_requests_get: mock.MagicMock,
    tmp_path: Path,
):
    mock_get_token_from_cli.return_value = "MOCK_TOKEN"
    mock_response = mock.Mock()
    mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
        response=mock.Mock(status_code=404, json=lambda: {"message": "Not Found"}),
    )
    mock_requests_get.return_value = mock_response
    output_file = tmp_path / "downloaded.txt"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    assert not download_file(
        "owner",
        "repo",
        "file.txt",
        "main",
        output_file,
    )
    mock_get_token_from_cli.assert_called_once()
    assert not output_file.exists()


def test_run_gh_auth_login_success(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.side_effect = [
        mock.Mock(returncode=0, stdout="", stderr=""),
        mock.Mock(
            stdout="Logged in to github.com account user",
            returncode=0,
            stderr="",
        ),
    ]
    assert run_gh_auth_login() is True


def test_run_gh_auth_login_fails_login_command(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.side_effect = [
        mock.Mock(returncode=1, stdout="", stderr=""),
        mock.Mock(stdout="", stderr="Error: not logged in", returncode=1),
    ]
    assert run_gh_auth_login() is False


def test_run_gh_auth_login_fails_status_check(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.side_effect = [
        mock.Mock(returncode=0, stdout="", stderr=""),
        mock.Mock(stdout="", stderr="Error: not logged in", returncode=1),
    ]
    assert run_gh_auth_login() is False


def test_run_gh_auth_login_gh_not_found(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.side_effect = FileNotFoundError
    assert run_gh_auth_login() is False


# Tests for gh_download.cli module
runner = CliRunner()


@pytest.fixture
def mock_download_core_logic() -> Generator[mock.MagicMock]:
    with mock.patch("gh_download.cli.download_file") as mock_download:
        yield mock_download


@pytest.fixture(autouse=True)
def mock_path_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)


def test_cli_download_success_default_output(
    mock_download_core_logic: mock.MagicMock,
    tmp_path: Path,
):
    mock_download_core_logic.return_value = True
    result = runner.invoke(app, ["owner", "repo", "file.txt"])
    assert result.exit_code == 0, f"CLI failed with: {result.stdout + result.stderr}"
    mock_download_core_logic.assert_called_once_with(
        repo_owner="owner",
        repo_name="repo",
        file_path="file.txt",
        branch="main",
        output_path=tmp_path / "file.txt",
    )


def test_cli_download_success_custom_output(
    mock_download_core_logic: mock.MagicMock,
    tmp_path: Path,
):
    mock_download_core_logic.return_value = True
    output_file = tmp_path / "custom.txt"
    result = runner.invoke(app, ["owner", "repo", "file.txt", "-o", str(output_file)])
    assert result.exit_code == 0, f"CLI failed with: {result.stdout + result.stderr}"
    mock_download_core_logic.assert_called_once_with(
        repo_owner="owner",
        repo_name="repo",
        file_path="file.txt",
        branch="main",
        output_path=output_file.resolve(),
    )


def test_cli_download_download_fails(
    mock_download_core_logic: mock.MagicMock,
    tmp_path: Path,
):
    mock_download_core_logic.return_value = False
    result = runner.invoke(
        app,
        ["owner", "repo", "file.txt", "-o", str(tmp_path / "f.txt")],
    )
    assert result.exit_code == 1, (
        f"CLI expected to fail: {result.stdout + result.stderr}"
    )
    mock_download_core_logic.assert_called_once()


def test_cli_download_cannot_create_output_dir(
    mock_download_core_logic: mock.MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    def mock_mkdir(*args, **kwargs) -> Never:  # noqa: ARG001
        msg = "Test error creating directory"
        raise OSError(msg)

    monkeypatch.setattr(Path, "mkdir", mock_mkdir)
    output_file = tmp_path / "non_existent_subdir" / "file.txt"
    result = runner.invoke(app, ["owner", "repo", "file.txt", "-o", str(output_file)])
    assert result.exit_code == 1, (
        f"CLI expected to fail: {result.stdout + result.stderr}"
    )
    mock_download_core_logic.assert_not_called()


def test_cli_download_default_output_filename_from_path(
    mock_download_core_logic: mock.MagicMock,
    tmp_path: Path,
):
    mock_download_core_logic.return_value = True
    result = runner.invoke(app, ["owner", "repo", "path/to/some_file.zip"])
    assert result.exit_code == 0, f"CLI failed with: {result.stdout + result.stderr}"
    mock_download_core_logic.assert_called_once_with(
        repo_owner="owner",
        repo_name="repo",
        file_path="path/to/some_file.zip",
        branch="main",
        output_path=tmp_path / "some_file.zip",
    )
