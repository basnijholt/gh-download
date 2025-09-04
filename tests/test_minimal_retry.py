"""Test minimal retry functionality."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest import mock

import requests

from gh_download import _download_and_save_file

if TYPE_CHECKING:
    from pathlib import Path


def test_retry_on_network_error(tmp_path: Path):
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


def test_retry_on_incomplete_read(tmp_path: Path):
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


def test_no_retry_on_404(tmp_path: Path):
    """Test that download does not retry on 404 errors."""
    download_url = "https://example.com/file.txt"
    headers = {"Authorization": "token test"}
    output_path = tmp_path / "test_file.txt"

    with mock.patch("requests.Session") as mock_session_class:
        mock_session = mock_session_class.return_value.__enter__.return_value

        # Create mock response for 404 error
        mock_response_404 = mock.Mock()
        mock_response_404.status_code = 404
        mock_response_404.json.return_value = {"message": "Not Found"}
        mock_response_404.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response_404,
        )

        mock_session.get.return_value = mock_response_404

        result = _download_and_save_file(
            download_url,
            headers,
            output_path,
            "test_file.txt",
            quiet=True,
        )

        assert result is False
        assert not output_path.exists()
        assert mock_session.get.call_count == 1  # No retries for 404


def test_fails_after_3_attempts(tmp_path: Path):
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
