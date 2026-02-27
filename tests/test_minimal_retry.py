"""Test minimal retry functionality."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest import mock

import requests

from gh_download import _download_and_save_file

if TYPE_CHECKING:
    from pathlib import Path


@mock.patch("gh_download.time.sleep")
def test_retry_on_network_error(mock_sleep: mock.Mock, tmp_path: Path):
    """Test that download retries on network errors."""
    download_url = "https://example.com/file.txt"
    headers = {"Authorization": "token test"}
    output_path = tmp_path / "test_file.txt"

    with mock.patch("requests.Session") as mock_session_class:
        mock_session = mock_session_class.return_value.__enter__.return_value

        # First attempt fails with ConnectionError, second succeeds
        mock_response_success = mock.Mock()
        mock_response_success.raise_for_status = mock.Mock()
        mock_response_success.iter_content.return_value = [b"test content"]

        mock_session.get.side_effect = [
            requests.exceptions.ConnectionError("Connection broken"),
            mock_response_success,
        ]

        # Should succeed after retry
        result = _download_and_save_file(
            download_url,
            headers,
            output_path,
            "test_file.txt",
            quiet=True,
        )

        assert result is True
        assert output_path.exists()
        assert output_path.read_bytes() == b"test content"
        assert mock_session.get.call_count == 2  # First failed, second succeeded
        mock_sleep.assert_called_once_with(1)  # 2**0 = 1s backoff


@mock.patch("gh_download.time.sleep")
def test_retry_on_incomplete_read(mock_sleep: mock.Mock, tmp_path: Path):
    """Test that download retries on ChunkedEncodingError (IncompleteRead)."""
    download_url = "https://example.com/file.txt"
    headers = {"Authorization": "token test"}
    output_path = tmp_path / "test_file.txt"

    with mock.patch("requests.Session") as mock_session_class:
        mock_session = mock_session_class.return_value.__enter__.return_value

        # First two attempts fail with ChunkedEncodingError, third succeeds
        mock_response_success = mock.Mock()
        mock_response_success.raise_for_status = mock.Mock()
        mock_response_success.iter_content.return_value = [b"test content"]

        mock_session.get.side_effect = [
            requests.exceptions.ChunkedEncodingError("IncompleteRead"),
            requests.exceptions.ChunkedEncodingError("IncompleteRead"),
            mock_response_success,
        ]

        result = _download_and_save_file(
            download_url,
            headers,
            output_path,
            "test_file.txt",
            quiet=True,
        )

        assert result is True
        assert output_path.exists()
        assert output_path.read_bytes() == b"test content"
        assert mock_session.get.call_count == 3  # All 3 attempts used
        assert mock_sleep.call_args_list == [mock.call(1), mock.call(2)]  # Exponential backoff


@mock.patch("gh_download.time.sleep")
def test_retry_on_404(mock_sleep: mock.Mock, tmp_path: Path):
    """Test that download retries on 404 (transient CDN propagation delay)."""
    download_url = "https://example.com/file.txt"
    headers = {"Authorization": "token test"}
    output_path = tmp_path / "test_file.txt"

    with mock.patch("requests.Session") as mock_session_class:
        mock_session = mock_session_class.return_value.__enter__.return_value

        # First attempt: transient 404, second attempt: success
        mock_response_404 = mock.Mock()
        mock_response_404.status_code = 404
        mock_response_404.json.return_value = {"message": "Not Found"}
        mock_response_404.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response_404,
        )

        mock_response_success = mock.Mock()
        mock_response_success.raise_for_status = mock.Mock()
        mock_response_success.iter_content.return_value = [b"test content"]

        mock_session.get.side_effect = [mock_response_404, mock_response_success]

        result = _download_and_save_file(
            download_url,
            headers,
            output_path,
            "test_file.txt",
            quiet=True,
        )

        assert result is True
        assert output_path.read_bytes() == b"test content"
        assert mock_session.get.call_count == 2  # 404 retried, then succeeded
        mock_sleep.assert_called_once_with(1)  # 2**0 = 1s backoff


def test_no_retry_on_401(tmp_path: Path):
    """Test that download does not retry on 401 Unauthorized."""
    download_url = "https://example.com/file.txt"
    headers = {"Authorization": "token test"}
    output_path = tmp_path / "test_file.txt"

    with mock.patch("requests.Session") as mock_session_class:
        mock_session = mock_session_class.return_value.__enter__.return_value

        mock_response_401 = mock.Mock()
        mock_response_401.status_code = 401
        mock_response_401.json.return_value = {"message": "Bad credentials"}
        mock_response_401.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response_401,
        )

        mock_session.get.return_value = mock_response_401

        result = _download_and_save_file(
            download_url,
            headers,
            output_path,
            "test_file.txt",
            quiet=True,
        )

        assert result is False
        assert not output_path.exists()
        assert mock_session.get.call_count == 1  # No retries for 401


@mock.patch("gh_download.time.sleep")
def test_retry_prints_warning_when_not_quiet(mock_sleep: mock.Mock, tmp_path: Path):
    """Test that retry prints a warning message when quiet=False."""
    download_url = "https://example.com/file.txt"
    headers = {"Authorization": "token test"}
    output_path = tmp_path / "test_file.txt"

    with (
        mock.patch("requests.Session") as mock_session_class,
        mock.patch("gh_download.console") as mock_console,
    ):
        mock_session = mock_session_class.return_value.__enter__.return_value

        mock_response_success = mock.Mock()
        mock_response_success.raise_for_status = mock.Mock()
        mock_response_success.iter_content.return_value = [b"test content"]

        mock_session.get.side_effect = [
            requests.exceptions.ConnectionError("Connection broken"),
            mock_response_success,
        ]

        result = _download_and_save_file(
            download_url,
            headers,
            output_path,
            "test_file.txt",
            quiet=False,
        )

        assert result is True
        # Check that retry warning was printed
        retry_calls = [
            c for c in mock_console.print.call_args_list if "Transient error" in str(c)
        ]
        assert len(retry_calls) == 1
        mock_sleep.assert_called_once_with(1)


@mock.patch("gh_download.time.sleep")
def test_retry_on_429_rate_limit(mock_sleep: mock.Mock, tmp_path: Path):
    """Test that download retries on 429 Too Many Requests."""
    download_url = "https://example.com/file.txt"
    headers = {"Authorization": "token test"}
    output_path = tmp_path / "test_file.txt"

    with mock.patch("requests.Session") as mock_session_class:
        mock_session = mock_session_class.return_value.__enter__.return_value

        mock_response_429 = mock.Mock()
        mock_response_429.status_code = 429
        mock_response_429.json.return_value = {"message": "rate limit exceeded"}
        mock_response_429.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response_429,
        )

        mock_response_success = mock.Mock()
        mock_response_success.raise_for_status = mock.Mock()
        mock_response_success.iter_content.return_value = [b"test content"]

        mock_session.get.side_effect = [mock_response_429, mock_response_success]

        result = _download_and_save_file(
            download_url,
            headers,
            output_path,
            "test_file.txt",
            quiet=True,
        )

        assert result is True
        assert output_path.read_bytes() == b"test content"
        assert mock_session.get.call_count == 2


@mock.patch("gh_download.time.sleep")
def test_fails_after_3_attempts(mock_sleep: mock.Mock, tmp_path: Path):
    """Test that download fails after 3 attempts."""
    download_url = "https://example.com/file.txt"
    headers = {"Authorization": "token test"}
    output_path = tmp_path / "test_file.txt"

    with mock.patch("requests.Session") as mock_session_class:
        mock_session = mock_session_class.return_value.__enter__.return_value

        # All attempts fail
        mock_session.get.side_effect = requests.exceptions.ConnectionError("Connection broken")

        result = _download_and_save_file(
            download_url,
            headers,
            output_path,
            "test_file.txt",
            quiet=True,
        )

        assert result is False
        assert not output_path.exists()
        assert mock_session.get.call_count == 3  # Exactly 3 attempts
        assert mock_sleep.call_args_list == [mock.call(1), mock.call(2)]  # Backoff on first 2
