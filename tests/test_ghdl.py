"""Tests for the gh_download utility functions."""

import subprocess
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import requests
from typer.testing import CliRunner

from gh_download import (
    _download_and_save_file,
    _fetch_content_metadata,
    _handle_download_errors,
    download,
)
from gh_download.cli import app
from gh_download.gh import (
    _check_gh_auth_status,
    _check_gh_cli_availability,
    _check_gh_executable,
    _github_token_from_gh_cli,
    _handle_gh_authentication_status,
    _perform_gh_login_and_verify,
    _retrieve_gh_auth_token,
    _run_gh_auth_login,
    setup_download_headers,
)


# Suppress console output during tests for cleaner test runs
@pytest.fixture(autouse=True)
def no_console_output(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("rich.console.Console.print", lambda *_, **__: None)
    monkeypatch.setattr(
        "rich.prompt.Confirm.ask",
        lambda *_, **__: True,
    )


@pytest.fixture
def mock_subprocess_run() -> Generator[mock.MagicMock]:
    with mock.patch("subprocess.run") as mock_run:
        yield mock_run


def test_check_gh_executable_not_found(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("gh_download.gh.shutil.which", lambda _: None)
    assert _check_gh_executable() is None


def test_check_gh_executable_found(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("gh_download.gh.shutil.which", lambda _: "/usr/bin/gh")
    assert _check_gh_executable() == "/usr/bin/gh"


def test_check_gh_auth_status_success(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.return_value = mock.Mock(
        stdout="Logged in to github.com account user",
        returncode=0,
    )
    assert _check_gh_auth_status("gh")


def test_check_gh_auth_status_subprocess_error(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.side_effect = subprocess.SubprocessError
    assert not _check_gh_auth_status("gh")


def test_check_gh_auth_status_os_error(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.side_effect = OSError
    assert not _check_gh_auth_status("gh")


def test_check_gh_auth_status_unexpected_stderr(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.return_value = mock.Mock(
        stdout="",
        stderr="Something unexpected",
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
    mock_subprocess_run.side_effect = subprocess.SubprocessError
    assert not _perform_gh_login_and_verify("gh")


def test_perform_gh_login_and_verify_auth_check_fails(
    mock_subprocess_run: mock.MagicMock,
):
    # First call for auth login (successful), then auth status calls
    mock_subprocess_run.side_effect = [
        mock.Mock(returncode=0),  # auth login succeeds
        mock.Mock(stdout="", stderr="", returncode=1),  # auth status fails
    ]
    assert not _perform_gh_login_and_verify("gh")


def test_perform_gh_login_and_verify_success(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.side_effect = [
        mock.Mock(returncode=0),  # auth login succeeds
        mock.Mock(
            stdout="Logged in to github.com account user",
            stderr="",
            returncode=0,
        ),  # auth status succeeds
    ]
    assert _perform_gh_login_and_verify("gh")


def test_perform_gh_login_and_verify_login_fails(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.side_effect = [
        mock.Mock(returncode=1),  # auth login fails
        mock.Mock(stdout="", stderr="", returncode=1),  # auth status check
    ]
    assert not _perform_gh_login_and_verify("gh")


def test_check_gh_cli_availability_gh_not_found_at_which(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("gh_download.gh.shutil.which", lambda _: None)
    assert _check_gh_cli_availability() is None


def test_check_gh_cli_availability_version_file_not_found(
    mock_subprocess_run: mock.MagicMock,
    monkeypatch: pytest.MonkeyPatch,
):
    # Mock shutil.which to return a specific path
    monkeypatch.setattr("gh_download.gh.shutil.which", lambda _: "gh")
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
    monkeypatch: pytest.MonkeyPatch,
):
    # Mock shutil.which to return a specific path
    monkeypatch.setattr("gh_download.gh.shutil.which", lambda _: "gh")
    mock_subprocess_run.side_effect = subprocess.CalledProcessError(1, "cmd")
    assert _check_gh_cli_availability() == "gh"


def test_handle_gh_authentication_status_authenticated(
    mock_subprocess_run: mock.MagicMock,
):
    mock_subprocess_run.return_value = mock.Mock(
        stdout="Logged in to github.com account user",
        stderr="",
        returncode=0,
    )
    assert _handle_gh_authentication_status("gh")


def test_handle_gh_authentication_status_user_declines_login(
    mock_subprocess_run: mock.MagicMock,
    monkeypatch: pytest.MonkeyPatch,
):
    mock_subprocess_run.return_value = mock.Mock(
        stdout="",
        stderr="Error: not logged in",
        returncode=1,
    )
    monkeypatch.setattr("rich.prompt.Confirm.ask", lambda *_, **__: False)
    assert not _handle_gh_authentication_status("gh")


def test_handle_gh_authentication_status_run_gh_auth_login_fails(
    mock_subprocess_run: mock.MagicMock,
    monkeypatch: pytest.MonkeyPatch,
):
    mock_subprocess_run.side_effect = [
        mock.Mock(
            stdout="",
            stderr="Error: not logged in",
            returncode=1,
        ),  # gh auth status (initial check)
        FileNotFoundError,  # gh auth login fails
    ]
    monkeypatch.setattr("gh_download.gh._run_gh_auth_login", lambda: False)
    assert not _handle_gh_authentication_status("gh")


def test_handle_gh_authentication_status_still_not_authed_after_login(
    mock_subprocess_run: mock.MagicMock,
    monkeypatch: pytest.MonkeyPatch,
):
    mock_subprocess_run.side_effect = [
        mock.Mock(
            stdout="",
            stderr="Error: not logged in",
            returncode=1,
        ),  # gh auth status (initial check)
        mock.Mock(
            stdout="",
            stderr="Error: not logged in",
            returncode=1,
        ),  # gh auth status (after login attempt)
    ]
    monkeypatch.setattr("gh_download.gh._run_gh_auth_login", lambda: True)
    assert not _handle_gh_authentication_status("gh")


@pytest.fixture
def mock_requests_get() -> Generator[mock.MagicMock]:
    with mock.patch("requests.Session.get") as mock_get:
        yield mock_get


@pytest.fixture
def mock_get_token_from_cli() -> Generator[mock.MagicMock]:
    with mock.patch("gh_download.gh._github_token_from_gh_cli") as mock_get_token:
        yield mock_get_token


def test_get_github_token_gh_not_installed(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.side_effect = FileNotFoundError
    assert _github_token_from_gh_cli() is None


def test_get_github_token_gh_auth_status_fail(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.side_effect = [
        mock.Mock(returncode=0, stdout="", stderr=""),  # gh --version
        mock.Mock(
            stdout="",
            stderr="Error: not logged in",
            returncode=1,
        ),  # gh auth status
    ]
    assert _github_token_from_gh_cli() is None


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
    assert _github_token_from_gh_cli() == "MOCK_TOKEN_VALUE"


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
        ),  # gh auth login (in _run_gh_auth_login)
        mock.Mock(
            stdout="Logged in to github.com account user",
            returncode=0,
        ),  # gh auth status (in _run_gh_auth_login)
        mock.Mock(
            stdout="Logged in to github.com account user",
            returncode=0,
        ),  # gh auth status (2nd check)
        mock.Mock(stdout="MOCK_TOKEN_VALUE\n", returncode=0),  # gh auth token
    ]
    assert _github_token_from_gh_cli() == "MOCK_TOKEN_VALUE"


def test_retrieve_gh_auth_token_called_process_error(
    mock_subprocess_run: mock.MagicMock,
):
    mock_subprocess_run.side_effect = subprocess.CalledProcessError(
        1,
        "cmd",
        stderr="Token error",
    )
    assert _retrieve_gh_auth_token("gh") is None


def test_get_github_token_from_gh_cli_exception_handling(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("gh_download.gh._check_gh_cli_availability", lambda: "gh")
    monkeypatch.setattr("gh_download.gh._handle_gh_authentication_status", lambda _: True)
    monkeypatch.setattr("gh_download.gh._retrieve_gh_auth_token", lambda _: None)
    assert _github_token_from_gh_cli() is None


def test_get_github_token_from_gh_cli_called_process_error_handling(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr("gh_download.gh._check_gh_cli_availability", lambda: "gh")
    monkeypatch.setattr("gh_download.gh._handle_gh_authentication_status", lambda _: True)
    monkeypatch.setattr(
        "gh_download.gh._retrieve_gh_auth_token",
        mock.Mock(
            side_effect=subprocess.CalledProcessError(1, "cmd", stderr="Token error"),
        ),
    )
    assert _github_token_from_gh_cli() is None


def test_get_github_token_from_gh_cli_general_exception_handling(
    monkeypatch: pytest.MonkeyPatch,
):
    def raise_exception() -> None:
        msg = "Something went wrong"
        raise RuntimeError(msg)

    monkeypatch.setattr("gh_download.gh._check_gh_cli_availability", lambda: "gh")
    monkeypatch.setattr(
        "gh_download.gh._handle_gh_authentication_status",
        lambda _: raise_exception(),
    )
    assert _github_token_from_gh_cli() is None


def test_perform_download_and_save_success(
    mock_requests_get: mock.MagicMock,
    tmp_path: Path,
):
    """Test that _download_and_save_file works correctly."""
    mock_response = mock.Mock()
    mock_response.raise_for_status = mock.Mock()
    mock_response.iter_content.return_value = [b"file content"]
    mock_requests_get.return_value = mock_response

    output_file = tmp_path / "test.txt"
    headers = {"Authorization": "token test"}

    result = _download_and_save_file(
        "https://example.com/file.txt",
        headers,
        output_file,
        "test.txt",
        quiet=False,
    )

    assert result is True
    assert output_file.read_bytes() == b"file content"


def test_perform_download_and_save_http_error(
    mock_requests_get: mock.MagicMock,
    tmp_path: Path,
):
    """Test that _download_and_save_file handles HTTP errors correctly."""
    mock_response = mock.Mock()
    mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
        response=mock.Mock(status_code=404, json=lambda: {"message": "Not Found"}),
    )
    mock_requests_get.return_value = mock_response

    output_file = tmp_path / "test.txt"
    headers = {"Authorization": "token test"}

    result = _download_and_save_file(
        "https://example.com/file.txt",
        headers,
        output_file,
        "test.txt",
        quiet=False,
    )

    assert result is False
    assert not output_file.exists()


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
                        side_effect=requests.exceptions.JSONDecodeError("err", "doc", 0),
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
                    json=lambda: {"message": "Unauthorized", "documentation_url": "url"},
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
    assert not download("owner", "repo", "file.txt", "main", output_file)
    mock_get_token_from_cli.assert_called_once()


def test_download_file_success(
    mock_get_token_from_cli: mock.MagicMock,
    mock_requests_get: mock.MagicMock,
    tmp_path: Path,
):
    mock_get_token_from_cli.return_value = "MOCK_TOKEN"

    # First call to get metadata
    metadata_response = mock.Mock()
    metadata_response.raise_for_status = mock.Mock()
    metadata_response.json.return_value = {
        "type": "file",
        "name": "file.txt",
        "download_url": "https://raw.githubusercontent.com/owner/repo/main/file.txt",
    }

    # Second call to download the actual file
    download_response = mock.Mock()
    download_response.raise_for_status = mock.Mock()
    download_response.iter_content.return_value = [b"file content"]

    mock_requests_get.side_effect = [metadata_response, download_response]

    output_file = tmp_path / "downloaded.txt"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    assert download("owner", "repo", "file.txt", "main", output_file)

    # Should be called twice - once for metadata, once for download
    assert mock_requests_get.call_count == 2

    # First call gets metadata
    metadata_call = mock_requests_get.call_args_list[0]
    assert metadata_call[1]["headers"]["Accept"] == "application/vnd.github.v3+json"

    # Second call downloads the file
    download_call = mock_requests_get.call_args_list[1]
    assert download_call[1]["headers"]["Accept"] == "application/octet-stream"

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
    assert not download(
        "owner",
        "repo",
        "file.txt",
        "main",
        output_file,
    )
    mock_get_token_from_cli.assert_called_once()
    assert not output_file.exists()


def test_download_file_success_with_default_filename(
    mock_get_token_from_cli: mock.MagicMock,
    mock_requests_get: mock.MagicMock,
    tmp_path: Path,
):
    """Test that download uses the repository filename when no explicit output filename is given."""
    mock_get_token_from_cli.return_value = "MOCK_TOKEN"

    # First call to get metadata
    metadata_response = mock.Mock()
    metadata_response.raise_for_status = mock.Mock()
    metadata_response.json.return_value = {
        "type": "file",
        "name": "README.md",
        "download_url": "https://raw.githubusercontent.com/owner/repo/main/README.md",
    }

    # Second call to download the actual file
    download_response = mock.Mock()
    download_response.raise_for_status = mock.Mock()
    download_response.iter_content.return_value = [b"# Project README"]

    mock_requests_get.side_effect = [metadata_response, download_response]

    # Use a directory as output path, so it should use the repo filename
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    assert download("owner", "repo", "README.md", "main", output_dir)

    # The file should be saved as README.md in the output directory
    expected_file = output_dir / "README.md"
    assert expected_file.exists()
    assert expected_file.read_bytes() == b"# Project README"


def test_download_file_missing_download_url(
    mock_get_token_from_cli: mock.MagicMock,
    mock_requests_get: mock.MagicMock,
    tmp_path: Path,
):
    """Test that download handles the case where download_url is missing from the API response."""
    mock_get_token_from_cli.return_value = "MOCK_TOKEN"

    # Metadata response without download_url
    metadata_response = mock.Mock()
    metadata_response.raise_for_status = mock.Mock()
    metadata_response.json.return_value = {
        "type": "file",
        "name": "file.txt",
        # Missing download_url
    }

    mock_requests_get.return_value = metadata_response

    output_file = tmp_path / "downloaded.txt"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    assert not download("owner", "repo", "file.txt", "main", output_file)

    mock_get_token_from_cli.assert_called_once()
    assert not output_file.exists()


def test_download_file_directory_success(
    mock_get_token_from_cli: mock.MagicMock,
    mock_requests_get: mock.MagicMock,
    tmp_path: Path,
):
    """Test that downloading a directory works correctly."""
    mock_get_token_from_cli.return_value = "MOCK_TOKEN"

    # Mock the metadata API call that returns directory contents
    metadata_response = mock.Mock()
    metadata_response.raise_for_status = mock.Mock()
    metadata_response.json.return_value = [
        {
            "type": "file",
            "name": "file1.txt",
            "path": "folder/file1.txt",
            "download_url": "https://raw.githubusercontent.com/owner/repo/main/folder/file1.txt",
        },
    ]

    # Mock the file metadata response
    file_metadata_response = mock.Mock()
    file_metadata_response.raise_for_status = mock.Mock()
    file_metadata_response.json.return_value = {
        "type": "file",
        "name": "file1.txt",
        "download_url": "https://raw.githubusercontent.com/owner/repo/main/folder/file1.txt",
    }

    # Mock the file download calls
    file_response = mock.Mock()
    file_response.raise_for_status = mock.Mock()
    file_response.iter_content.return_value = [b"file content"]

    # Set up the mock to return different responses for different calls
    mock_requests_get.side_effect = [
        metadata_response,  # First call: get folder metadata
        file_metadata_response,  # Second call: get file1 metadata
        file_response,  # Third call: download file1
    ]

    # Call the function
    result = download(
        repo_owner="owner",
        repo_name="repo",
        file_path="folder",
        branch="main",
        output_path=tmp_path / "downloaded_folder",
    )

    assert result is True
    # Check that files were created
    assert (tmp_path / "downloaded_folder" / "folder").exists()


def test_setup_download_headers_success(
    mock_get_token_from_cli: mock.MagicMock,
):
    """Test that setup_download_headers returns correct headers when token is available."""
    mock_get_token_from_cli.return_value = "test_token"

    headers = setup_download_headers()

    assert headers is not None
    assert headers["Authorization"] == "token test_token"
    assert headers["Accept"] == "application/vnd.github.v3+json"


def test_setup_download_headers_no_token(
    mock_get_token_from_cli: mock.MagicMock,
):
    """Test that setup_download_headers returns None when no token is available."""
    mock_get_token_from_cli.return_value = None

    headers = setup_download_headers()

    assert headers is None


def test_fetch_content_metadata_success(
    mock_requests_get: mock.MagicMock,
):
    """Test that _fetch_content_metadata successfully fetches and returns metadata."""
    mock_response = mock.Mock()
    mock_response.raise_for_status = mock.Mock()
    mock_response.json.return_value = {"type": "file", "name": "test.txt"}
    mock_requests_get.return_value = mock_response

    headers = {
        "Authorization": "token test",
        "Accept": "application/vnd.github.v3+json",
    }

    result = _fetch_content_metadata(
        repo_owner="owner",
        repo_name="repo",
        normalized_path="test.txt",
        branch="main",
        headers=headers,
        display_name="test.txt",
    )

    assert result == {"type": "file", "name": "test.txt"}


def test_fetch_content_metadata_error(
    mock_requests_get: mock.MagicMock,
):
    """Test that _fetch_content_metadata returns None on error."""
    # Create a proper HTTPError with a mock response
    mock_response = mock.Mock()
    mock_response.status_code = 404
    mock_response.json.return_value = {"message": "Not Found"}

    http_error = requests.exceptions.HTTPError("404 Not Found")
    http_error.response = mock_response

    mock_requests_get.side_effect = http_error

    headers = {
        "Authorization": "token test",
        "Accept": "application/vnd.github.v3+json",
    }

    result = _fetch_content_metadata(
        repo_owner="owner",
        repo_name="repo",
        normalized_path="nonexistent.txt",
        branch="main",
        headers=headers,
        display_name="nonexistent.txt",
    )

    assert result is None


def test_download_file_unexpected_content_type(
    mock_get_token_from_cli: mock.MagicMock,
    mock_requests_get: mock.MagicMock,
    tmp_path: Path,
):
    """Test that download handles unexpected content types gracefully."""
    mock_get_token_from_cli.return_value = "MOCK_TOKEN"

    # Return unexpected content (neither dict with type=file nor list)
    metadata_response = mock.Mock()
    metadata_response.raise_for_status = mock.Mock()
    metadata_response.json.return_value = {"unexpected": "content"}

    mock_requests_get.return_value = metadata_response

    output_file = tmp_path / "downloaded.txt"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    result = download("owner", "repo", "file.txt", "main", output_file)

    assert result is False


def test_cli_run_command():
    """Test that the CLI runs without error."""
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])

    # Just check that the CLI can be invoked successfully
    assert result.exit_code == 0


def test_cli_help_short_option():
    """Test that -h works as well as --help."""
    runner = CliRunner()

    # Test -h
    result_short = runner.invoke(app, ["-h"])
    assert result_short.exit_code == 0

    # Test --help
    result_long = runner.invoke(app, ["--help"])
    assert result_long.exit_code == 0

    # Both should produce similar output (both should be successful)
    assert result_short.exit_code == result_long.exit_code


def test_cli_get_command_help():
    """Test that the main command supports both -h and --help."""
    runner = CliRunner()

    # Test -h
    result_short = runner.invoke(app, ["-h"])
    assert result_short.exit_code == 0

    # Test --help
    result_long = runner.invoke(app, ["--help"])
    assert result_long.exit_code == 0


def test_run_gh_auth_login_success(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.side_effect = [
        mock.Mock(returncode=0),  # gh auth login
        mock.Mock(
            stdout="Logged in to github.com account user",
            stderr="",
            returncode=0,
        ),  # gh auth status
    ]
    assert _run_gh_auth_login() is True


def test_run_gh_auth_login_fails_login_command(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.side_effect = [
        mock.Mock(returncode=1),  # gh auth login
        mock.Mock(stdout="", stderr="", returncode=1),  # gh auth status
    ]
    assert _run_gh_auth_login() is False


def test_run_gh_auth_login_fails_status_check(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.side_effect = [
        mock.Mock(returncode=0),  # gh auth login
        mock.Mock(stdout="", stderr="", returncode=1),  # gh auth status
    ]
    assert _run_gh_auth_login() is False


def test_run_gh_auth_login_gh_not_found(mock_subprocess_run: mock.MagicMock):
    mock_subprocess_run.side_effect = FileNotFoundError
    assert _run_gh_auth_login() is False


def test_download_directory_no_double_nesting(
    mock_get_token_from_cli: mock.MagicMock,
    mock_requests_get: mock.MagicMock,
    tmp_path: Path,
):
    """Test that downloading a directory doesn't create double nesting when output path matches folder name."""
    mock_get_token_from_cli.return_value = "MOCK_TOKEN"

    # Mock the metadata API call that returns directory contents for "tests/map"
    metadata_response = mock.Mock()
    metadata_response.raise_for_status = mock.Mock()
    metadata_response.json.return_value = [
        {
            "type": "file",
            "name": "test_file.py",
            "path": "tests/map/test_file.py",
            "download_url": "https://raw.githubusercontent.com/owner/repo/main/tests/map/test_file.py",
        },
    ]

    # Mock the file metadata response
    file_metadata_response = mock.Mock()
    file_metadata_response.raise_for_status = mock.Mock()
    file_metadata_response.json.return_value = {
        "type": "file",
        "name": "test_file.py",
        "download_url": "https://raw.githubusercontent.com/owner/repo/main/tests/map/test_file.py",
    }

    # Mock the file download calls
    file_response = mock.Mock()
    file_response.raise_for_status = mock.Mock()
    file_response.iter_content.return_value = [b"test content"]

    # Set up the mock to return different responses for different calls
    mock_requests_get.side_effect = [
        metadata_response,  # First call: get folder metadata
        file_metadata_response,  # Second call: get test_file.py metadata
        file_response,  # Third call: download test_file.py
    ]

    # Simulate CLI behavior: output_path is already named "map" (from Path("tests/map").name)
    output_path = tmp_path / "map"

    # Call the function
    result = download(
        repo_owner="owner",
        repo_name="repo",
        file_path="tests/map",
        branch="main",
        output_path=output_path,
    )

    assert result is True

    # Verify that files are directly in "map" directory, NOT in "map/map"
    expected_file = tmp_path / "map" / "test_file.py"
    assert expected_file.exists()
    assert expected_file.read_bytes() == b"test content"

    # Verify there's no double nesting
    double_nested_path = tmp_path / "map" / "map"
    assert not double_nested_path.exists()


def test_download_directory_correct_nesting_in_existing_dir(
    mock_get_token_from_cli: mock.MagicMock,
    mock_requests_get: mock.MagicMock,
    tmp_path: Path,
):
    """Test that downloading a directory correctly nests inside an existing directory."""
    mock_get_token_from_cli.return_value = "MOCK_TOKEN"

    # Mock the metadata API call that returns directory contents for "tests/map"
    metadata_response = mock.Mock()
    metadata_response.raise_for_status = mock.Mock()
    metadata_response.json.return_value = [
        {
            "type": "file",
            "name": "test_file.py",
            "path": "tests/map/test_file.py",
            "download_url": "https://raw.githubusercontent.com/owner/repo/main/tests/map/test_file.py",
        },
    ]

    # Mock the file metadata response
    file_metadata_response = mock.Mock()
    file_metadata_response.raise_for_status = mock.Mock()
    file_metadata_response.json.return_value = {
        "type": "file",
        "name": "test_file.py",
        "download_url": "https://raw.githubusercontent.com/owner/repo/main/tests/map/test_file.py",
    }

    # Mock the file download calls
    file_response = mock.Mock()
    file_response.raise_for_status = mock.Mock()
    file_response.iter_content.return_value = [b"test content"]

    # Set up the mock to return different responses for different calls
    mock_requests_get.side_effect = [
        metadata_response,  # First call: get folder metadata
        file_metadata_response,  # Second call: get test_file.py metadata
        file_response,  # Third call: download test_file.py
    ]

    # Create an existing directory and download into it
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir()

    # Call the function - should create "downloads/map/" not "downloads/"
    result = download(
        repo_owner="owner",
        repo_name="repo",
        file_path="tests/map",
        branch="main",
        output_path=downloads_dir,
    )

    assert result is True

    # Verify that folder is nested inside the existing directory
    expected_file = downloads_dir / "map" / "test_file.py"
    assert expected_file.exists()
    assert expected_file.read_bytes() == b"test content"


def test_cli_success_message_correct_path(
    mock_get_token_from_cli: mock.MagicMock,
    mock_requests_get: mock.MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test that CLI success message shows correct path without double nesting."""
    # Change to tmp_path for this test
    monkeypatch.chdir(tmp_path)
    mock_get_token_from_cli.return_value = "MOCK_TOKEN"

    # Mock the metadata API call that returns directory contents for "tests/map"
    metadata_response = mock.Mock()
    metadata_response.raise_for_status = mock.Mock()
    metadata_response.json.return_value = [
        {
            "type": "file",
            "name": "test_file.py",
            "path": "tests/map/test_file.py",
            "download_url": "https://raw.githubusercontent.com/owner/repo/main/tests/map/test_file.py",
        },
    ]

    # Mock the file metadata response
    file_metadata_response = mock.Mock()
    file_metadata_response.raise_for_status = mock.Mock()
    file_metadata_response.json.return_value = {
        "type": "file",
        "name": "test_file.py",
        "download_url": "https://raw.githubusercontent.com/owner/repo/main/tests/map/test_file.py",
    }

    # Mock the file download calls
    file_response = mock.Mock()
    file_response.raise_for_status = mock.Mock()
    file_response.iter_content.return_value = [b"test content"]

    # Set up the mock to return different responses for different calls
    mock_requests_get.side_effect = [
        metadata_response,  # First call: get folder metadata
        file_metadata_response,  # Second call: get test_file.py metadata
        file_response,  # Third call: download test_file.py
    ]

    runner = CliRunner()
    result = runner.invoke(app, ["owner", "repo", "tests/map"])

    # Should succeed
    assert result.exit_code == 0

    # Verify that files are in the correct location (most important check)
    expected_file = tmp_path / "map" / "test_file.py"
    assert expected_file.exists()
    assert expected_file.read_bytes() == b"test content"

    # Verify there's no double nesting (the key test for this bug fix)
    double_nested_path = tmp_path / "map" / "map"
    assert not double_nested_path.exists()
