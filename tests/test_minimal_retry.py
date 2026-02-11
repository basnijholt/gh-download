"""Test retry functionality via urllib3 Retry + HTTPAdapter."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest import mock

import requests
from requests.adapters import HTTPAdapter

from gh_download import (
    BACKOFF_FACTOR,
    MAX_RETRIES,
    RETRYABLE_STATUS_CODES,
    _create_session,
    _download_and_save_file,
    _fetch_content_metadata,
)

if TYPE_CHECKING:
    from pathlib import Path

    from urllib3.util.retry import Retry


def test_create_session_has_retry_adapter():
    """Test that _create_session mounts an HTTPAdapter with correct retry config."""
    headers = {"Authorization": "token test", "Accept": "application/vnd.github.v3+json"}
    session = _create_session(headers)

    adapter = session.get_adapter("https://api.github.com")
    assert isinstance(adapter, HTTPAdapter)

    retry: Retry = adapter.max_retries
    assert retry.total == MAX_RETRIES
    assert retry.backoff_factor == BACKOFF_FACTOR
    assert retry.status_forcelist == RETRYABLE_STATUS_CODES
    assert "GET" in retry.allowed_methods

    # Headers are set on the session
    assert session.headers["Authorization"] == "token test"
    session.close()


def test_create_session_retries_on_configured_status_codes():
    """Test that RETRYABLE_STATUS_CODES includes expected codes."""
    assert 404 in RETRYABLE_STATUS_CODES  # CDN propagation delays
    assert 429 in RETRYABLE_STATUS_CODES  # Rate limiting
    assert 500 in RETRYABLE_STATUS_CODES  # Server errors
    assert 502 in RETRYABLE_STATUS_CODES
    assert 503 in RETRYABLE_STATUS_CODES
    assert 504 in RETRYABLE_STATUS_CODES
    # Auth errors should NOT be retried
    assert 401 not in RETRYABLE_STATUS_CODES
    assert 403 not in RETRYABLE_STATUS_CODES


def test_download_and_save_file_success(tmp_path: Path):
    """Test successful download."""
    output_path = tmp_path / "test_file.txt"
    headers = {"Authorization": "token test"}

    mock_response = mock.Mock()
    mock_response.raise_for_status = mock.Mock()
    mock_response.iter_content.return_value = [b"test content"]

    with mock.patch("gh_download._create_session") as mock_cs:
        mock_session = mock.MagicMock()
        mock_session.get.return_value = mock_response
        mock_session.__enter__ = mock.Mock(return_value=mock_session)
        mock_session.__exit__ = mock.Mock(return_value=False)
        mock_cs.return_value = mock_session

        result = _download_and_save_file(
            "https://example.com/file.txt",
            headers,
            output_path,
            "test_file.txt",
            quiet=True,
        )

    assert result is True
    assert output_path.read_bytes() == b"test content"


def test_download_and_save_file_http_error(tmp_path: Path):
    """Test that non-retryable HTTP errors fail immediately."""
    output_path = tmp_path / "test_file.txt"
    headers = {"Authorization": "token test"}

    mock_response = mock.Mock()
    mock_response.status_code = 401
    mock_response.json.return_value = {"message": "Bad credentials"}
    mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
        response=mock_response,
    )

    with mock.patch("gh_download._create_session") as mock_cs:
        mock_session = mock.MagicMock()
        mock_session.get.return_value = mock_response
        mock_session.__enter__ = mock.Mock(return_value=mock_session)
        mock_session.__exit__ = mock.Mock(return_value=False)
        mock_cs.return_value = mock_session

        result = _download_and_save_file(
            "https://example.com/file.txt",
            headers,
            output_path,
            "test_file.txt",
            quiet=True,
        )

    assert result is False
    assert not output_path.exists()


def test_download_and_save_file_os_error(tmp_path: Path):
    """Test that OS errors (file I/O) fail without retry."""
    # Use an impossible path to trigger OSError on mkdir
    output_path = tmp_path / "test_file.txt"
    headers = {"Authorization": "token test"}

    with mock.patch("gh_download._create_session") as mock_cs:
        mock_session = mock.MagicMock()
        mock_session.__enter__ = mock.Mock(return_value=mock_session)
        mock_session.__exit__ = mock.Mock(return_value=False)
        mock_session.get.side_effect = OSError("disk full")
        mock_cs.return_value = mock_session

        result = _download_and_save_file(
            "https://example.com/file.txt",
            headers,
            output_path,
            "test_file.txt",
            quiet=True,
        )

    assert result is False


def test_fetch_content_metadata_success():
    """Test successful metadata fetch."""
    headers = {"Authorization": "token test", "Accept": "application/vnd.github.v3+json"}

    mock_response = mock.Mock()
    mock_response.raise_for_status = mock.Mock()
    mock_response.json.return_value = {"type": "file", "name": "test.txt"}

    with mock.patch("gh_download._create_session") as mock_cs:
        mock_session = mock.MagicMock()
        mock_session.get.return_value = mock_response
        mock_session.__enter__ = mock.Mock(return_value=mock_session)
        mock_session.__exit__ = mock.Mock(return_value=False)
        mock_cs.return_value = mock_session

        result = _fetch_content_metadata(
            "owner", "repo", "test.txt", "main", headers, "test.txt", quiet=True,
        )

    assert result == {"type": "file", "name": "test.txt"}


def test_fetch_content_metadata_http_error():
    """Test metadata fetch handles HTTP errors."""
    headers = {"Authorization": "token test", "Accept": "application/vnd.github.v3+json"}

    mock_response = mock.Mock()
    mock_response.status_code = 403
    mock_response.json.return_value = {"message": "Forbidden"}
    mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
        response=mock_response,
    )

    with mock.patch("gh_download._create_session") as mock_cs:
        mock_session = mock.MagicMock()
        mock_session.get.return_value = mock_response
        mock_session.__enter__ = mock.Mock(return_value=mock_session)
        mock_session.__exit__ = mock.Mock(return_value=False)
        mock_cs.return_value = mock_session

        result = _fetch_content_metadata(
            "owner", "repo", "test.txt", "main", headers, "test.txt", quiet=True,
        )

    assert result is None
