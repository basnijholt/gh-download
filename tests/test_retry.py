"""Tests for retry functionality in gh-download."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest import mock

import requests

from gh_download import _download_and_save_file, download

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


def test_download_with_retry_on_connection_error(tmp_path: Path):
    """Test that download retries on connection errors."""
    download_url = "https://example.com/file.txt"
    headers = {"Authorization": "token test"}
    output_path = tmp_path / "test_file.txt"

    with mock.patch("requests.Session") as mock_session_class:
        mock_session = mock_session_class.return_value.__enter__.return_value

        # First two attempts fail with ConnectionError, third succeeds
        mock_response_success = mock.Mock()
        mock_response_success.raise_for_status = mock.Mock()
        mock_response_success.iter_content.return_value = [b"test content"]

        mock_session.get.side_effect = [
            requests.exceptions.ConnectionError("Connection broken"),
            requests.exceptions.ConnectionError("Connection broken"),
            mock_response_success,
        ]

        # Should succeed after retries
        result = _download_and_save_file(
            download_url,
            headers,
            output_path,
            "test_file.txt",
            quiet=True,
            max_retries=2,
            retry_delay=0.1,  # Short delay for testing
        )

        assert result is True
        assert output_path.exists()
        assert output_path.read_bytes() == b"test content"
        assert mock_session.get.call_count == 3


def test_download_with_retry_on_chunked_encoding_error(tmp_path: Path):
    """Test that download retries on ChunkedEncodingError."""
    download_url = "https://example.com/file.txt"
    headers = {"Authorization": "token test"}
    output_path = tmp_path / "test_file.txt"

    with mock.patch("requests.Session") as mock_session_class:
        mock_session = mock_session_class.return_value.__enter__.return_value

        # First attempt fails with ChunkedEncodingError, second succeeds
        mock_response_success = mock.Mock()
        mock_response_success.raise_for_status = mock.Mock()
        mock_response_success.iter_content.return_value = [b"test content"]

        mock_session.get.side_effect = [
            requests.exceptions.ChunkedEncodingError("Connection broken: IncompleteRead"),
            mock_response_success,
        ]

        result = _download_and_save_file(
            download_url,
            headers,
            output_path,
            "test_file.txt",
            quiet=True,
            max_retries=2,
            retry_delay=0.1,
        )

        assert result is True
        assert output_path.exists()
        assert output_path.read_bytes() == b"test content"
        assert mock_session.get.call_count == 2


def test_download_with_retry_on_server_error(tmp_path: Path):
    """Test that download retries on 502/503/504 server errors."""
    download_url = "https://example.com/file.txt"
    headers = {"Authorization": "token test"}
    output_path = tmp_path / "test_file.txt"

    with mock.patch("requests.Session") as mock_session_class:
        mock_session = mock_session_class.return_value.__enter__.return_value

        # Create mock responses for server errors
        mock_response_502 = mock.Mock()
        mock_response_502.status_code = 502
        mock_response_502.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response_502,
        )

        mock_response_503 = mock.Mock()
        mock_response_503.status_code = 503
        mock_response_503.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response_503,
        )

        mock_response_success = mock.Mock()
        mock_response_success.raise_for_status = mock.Mock()
        mock_response_success.iter_content.return_value = [b"test content"]

        mock_session.get.side_effect = [
            mock_response_502,
            mock_response_503,
            mock_response_success,
        ]

        result = _download_and_save_file(
            download_url,
            headers,
            output_path,
            "test_file.txt",
            quiet=True,
            max_retries=2,
            retry_delay=0.1,
        )

        assert result is True
        assert output_path.exists()
        assert output_path.read_bytes() == b"test content"
        assert mock_session.get.call_count == 3


def test_download_with_retry_on_timeout(tmp_path: Path):
    """Test that download retries on timeout errors."""
    download_url = "https://example.com/file.txt"
    headers = {"Authorization": "token test"}
    output_path = tmp_path / "test_file.txt"

    with mock.patch("requests.Session") as mock_session_class:
        mock_session = mock_session_class.return_value.__enter__.return_value

        # First attempt times out, second succeeds
        mock_response_success = mock.Mock()
        mock_response_success.raise_for_status = mock.Mock()
        mock_response_success.iter_content.return_value = [b"test content"]

        mock_session.get.side_effect = [
            requests.exceptions.Timeout("Request timed out"),
            mock_response_success,
        ]

        result = _download_and_save_file(
            download_url,
            headers,
            output_path,
            "test_file.txt",
            quiet=True,
            max_retries=1,
            retry_delay=0.1,
        )

        assert result is True
        assert output_path.exists()
        assert output_path.read_bytes() == b"test content"
        assert mock_session.get.call_count == 2


def test_download_fails_after_max_retries(tmp_path: Path):
    """Test that download fails after exceeding max retries."""
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
            max_retries=2,
            retry_delay=0.1,
        )

        assert result is False
        assert not output_path.exists()
        assert mock_session.get.call_count == 3  # initial + 2 retries


def test_download_no_retry_on_404(tmp_path: Path):
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
            max_retries=2,
            retry_delay=0.1,
        )

        assert result is False
        assert not output_path.exists()
        assert mock_session.get.call_count == 1  # No retries for 404


def test_download_temp_file_cleanup_on_error(tmp_path: Path):
    """Test that temporary files are cleaned up on download errors."""
    download_url = "https://example.com/file.txt"
    headers = {"Authorization": "token test"}
    output_path = tmp_path / "test_file.txt"
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")

    with mock.patch("requests.Session") as mock_session_class:
        mock_session = mock_session_class.return_value.__enter__.return_value

        # Mock a response that starts downloading but then fails
        mock_response = mock.Mock()
        mock_response.raise_for_status = mock.Mock()

        # Simulate partial download that fails
        def iter_content_with_error(chunk_size: int | None = None) -> Generator[bytes, None, None]:  # noqa: ARG001
            yield b"partial content"
            msg = "Connection broken"
            raise requests.exceptions.ChunkedEncodingError(msg)

        mock_response.iter_content = iter_content_with_error
        mock_session.get.return_value = mock_response

        result = _download_and_save_file(
            download_url,
            headers,
            output_path,
            "test_file.txt",
            quiet=True,
            max_retries=0,  # No retries for this test
            retry_delay=0.1,
        )

        assert result is False
        assert not output_path.exists()
        assert not temp_path.exists()  # Temp file should be cleaned up


@mock.patch("gh_download.gh._github_token_from_gh_cli")
@mock.patch("requests.Session")
def test_download_with_retry_integration(
    mock_session_class: mock.MagicMock,
    mock_get_token: mock.MagicMock,
    tmp_path: Path,
):
    """Test the full download function with retry parameters."""
    mock_get_token.return_value = "test_token"

    mock_session = mock_session_class.return_value.__enter__.return_value

    # Mock metadata response
    metadata_response = mock.Mock()
    metadata_response.raise_for_status = mock.Mock()
    metadata_response.json.return_value = {
        "type": "file",
        "name": "test.txt",
        "download_url": "https://raw.githubusercontent.com/owner/repo/main/test.txt",
    }

    # Mock file download with retry
    mock_response_success = mock.Mock()
    mock_response_success.raise_for_status = mock.Mock()
    mock_response_success.iter_content.return_value = [b"test content"]

    mock_session.get.side_effect = [
        metadata_response,  # First call for metadata
        requests.exceptions.ConnectionError("Connection broken"),  # First download attempt fails
        mock_response_success,  # Second download attempt succeeds
    ]

    output_path = tmp_path / "downloaded.txt"
    result = download(
        "owner",
        "repo",
        "test.txt",
        "main",
        output_path,
        max_retries=1,
        retry_delay=0.1,
    )

    assert result is True
    assert output_path.exists()
    assert output_path.read_bytes() == b"test content"
    assert mock_session.get.call_count == 3
