"""Test minimal retry functionality."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING
from unittest import mock

import requests

from gh_download import (
    _download_and_save_file,
    _download_via_blob_api,
    _fetch_content_metadata,
    _is_retryable_http_error,
    _retry_delay,
)

if TYPE_CHECKING:
    from pathlib import Path


# --- Helper utility tests ---


def test_is_retryable_http_error():
    """Test that retryable status codes are correctly identified."""
    for code in (404, 429, 500, 502, 503, 504):
        resp = mock.Mock()
        resp.status_code = code
        exc = requests.exceptions.HTTPError(response=resp)
        assert _is_retryable_http_error(exc) is True, f"Expected {code} to be retryable"

    for code in (400, 401, 403, 405, 422):
        resp = mock.Mock()
        resp.status_code = code
        exc = requests.exceptions.HTTPError(response=resp)
        assert _is_retryable_http_error(exc) is False, f"Expected {code} to NOT be retryable"

    # No response attached
    exc_no_resp = requests.exceptions.HTTPError(response=None)
    assert _is_retryable_http_error(exc_no_resp) is False


def test_retry_delay():
    """Test exponential backoff calculation."""
    assert _retry_delay(0) == 1  # 2^0
    assert _retry_delay(1) == 2  # 2^1
    assert _retry_delay(2) == 4  # 2^2
    # 429 doubles the delay
    assert _retry_delay(0, status_code=429) == 2
    assert _retry_delay(1, status_code=429) == 4
    assert _retry_delay(2, status_code=429) == 8


# --- _download_and_save_file tests ---


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


def test_retry_on_404(tmp_path: Path):
    """Test that download retries on 404 errors (CDN propagation delay)."""
    download_url = "https://example.com/file.txt"
    headers = {"Authorization": "token test"}
    output_path = tmp_path / "test_file.txt"

    with mock.patch("gh_download.time.sleep"):  # skip actual sleep
        with mock.patch("requests.Session") as mock_session_class:
            mock_session = mock_session_class.return_value.__enter__.return_value

            # First attempt: 404, second attempt: success
            mock_response_404 = mock.Mock()
            mock_response_404.status_code = 404
            mock_response_404.json.return_value = {"message": "Not Found"}
            mock_response_404.raise_for_status.side_effect = requests.exceptions.HTTPError(
                response=mock_response_404,
            )

            mock_response_success = mock.Mock()
            mock_response_success.raise_for_status = mock.Mock()
            mock_response_success.iter_content.return_value = [b"test content"]

            mock_session.get.side_effect = [
                mock_response_404,
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
            assert mock_session.get.call_count == 2  # 404 retried, then succeeded


def test_retry_on_server_error(tmp_path: Path):
    """Test that download retries on 502/503 server errors."""
    download_url = "https://example.com/file.txt"
    headers = {"Authorization": "token test"}
    output_path = tmp_path / "test_file.txt"

    with mock.patch("gh_download.time.sleep"):
        with mock.patch("requests.Session") as mock_session_class:
            mock_session = mock_session_class.return_value.__enter__.return_value

            mock_response_502 = mock.Mock()
            mock_response_502.status_code = 502
            mock_response_502.json.return_value = {"message": "Bad Gateway"}
            mock_response_502.raise_for_status.side_effect = requests.exceptions.HTTPError(
                response=mock_response_502,
            )

            mock_response_success = mock.Mock()
            mock_response_success.raise_for_status = mock.Mock()
            mock_response_success.iter_content.return_value = [b"test content"]

            mock_session.get.side_effect = [
                mock_response_502,
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
            assert mock_session.get.call_count == 2


def test_no_retry_on_401(tmp_path: Path):
    """Test that download does NOT retry on 401 Unauthorized."""
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


def test_fails_after_3_http_retries(tmp_path: Path):
    """Test that download fails after 3 retryable HTTP errors."""
    download_url = "https://example.com/file.txt"
    headers = {"Authorization": "token test"}
    output_path = tmp_path / "test_file.txt"

    with mock.patch("gh_download.time.sleep"):
        with mock.patch("requests.Session") as mock_session_class:
            mock_session = mock_session_class.return_value.__enter__.return_value

            mock_response_503 = mock.Mock()
            mock_response_503.status_code = 503
            mock_response_503.json.return_value = {"message": "Service Unavailable"}
            mock_response_503.raise_for_status.side_effect = requests.exceptions.HTTPError(
                response=mock_response_503,
            )
            mock_response_503.text = "Service Unavailable"

            mock_session.get.return_value = mock_response_503

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


# --- _fetch_content_metadata tests ---


def test_metadata_retry_on_network_error():
    """Test that metadata fetch retries on network errors."""
    with mock.patch("gh_download.time.sleep"):
        with mock.patch("requests.Session") as mock_session_class:
            mock_session = mock_session_class.return_value.__enter__.return_value

            mock_response = mock.Mock()
            mock_response.raise_for_status = mock.Mock()
            mock_response.json.return_value = {"type": "file", "name": "test.txt"}

            mock_session.get.side_effect = [
                requests.exceptions.ConnectionError("Connection broken"),
                mock_response,
            ]

            result = _fetch_content_metadata(
                "owner", "repo", "path/test.txt", "main",
                {"Authorization": "token test"}, "test.txt",
                quiet=True,
            )

            assert result == {"type": "file", "name": "test.txt"}
            assert mock_session.get.call_count == 2


def test_metadata_retry_on_404():
    """Test that metadata fetch retries on 404."""
    with mock.patch("gh_download.time.sleep"):
        with mock.patch("requests.Session") as mock_session_class:
            mock_session = mock_session_class.return_value.__enter__.return_value

            mock_response_404 = mock.Mock()
            mock_response_404.status_code = 404
            mock_response_404.json.return_value = {"message": "Not Found"}
            mock_response_404.raise_for_status.side_effect = requests.exceptions.HTTPError(
                response=mock_response_404,
            )

            mock_response_ok = mock.Mock()
            mock_response_ok.raise_for_status = mock.Mock()
            mock_response_ok.json.return_value = [{"name": "a.txt", "type": "file"}]

            mock_session.get.side_effect = [
                mock_response_404,
                mock_response_ok,
            ]

            result = _fetch_content_metadata(
                "owner", "repo", "some/dir", "main",
                {"Authorization": "token test"}, "some/dir",
                quiet=True,
            )

            assert result == [{"name": "a.txt", "type": "file"}]
            assert mock_session.get.call_count == 2


def test_metadata_no_retry_on_403():
    """Test that metadata fetch does NOT retry on 403."""
    with mock.patch("requests.Session") as mock_session_class:
        mock_session = mock_session_class.return_value.__enter__.return_value

        mock_response_403 = mock.Mock()
        mock_response_403.status_code = 403
        mock_response_403.json.return_value = {"message": "Forbidden"}
        mock_response_403.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response_403,
        )

        mock_session.get.return_value = mock_response_403

        result = _fetch_content_metadata(
            "owner", "repo", "path/test.txt", "main",
            {"Authorization": "token test"}, "test.txt",
            quiet=True,
        )

        assert result is None
        assert mock_session.get.call_count == 1


def test_metadata_non_http_request_exception_is_reported():
    """Test that non-HTTP RequestException errors are surfaced via the error handler."""
    with mock.patch("requests.Session") as mock_session_class:
        mock_session = mock_session_class.return_value.__enter__.return_value

        request_error = requests.exceptions.InvalidHeader("Malformed header")
        mock_session.get.side_effect = request_error

        with mock.patch("gh_download._handle_download_errors") as mock_handle_errors:
            result = _fetch_content_metadata(
                "owner", "repo", "path/test.txt", "main",
                {"Authorization": "token test"}, "test.txt",
                quiet=True,
            )

            assert result is None
            mock_handle_errors.assert_called_once()
            assert mock_handle_errors.call_args.args[0] is request_error
            assert mock_handle_errors.call_args.args[1] == "metadata for test.txt"


# --- _download_via_blob_api tests ---


def test_blob_api_download(tmp_path: Path):
    """Test blob API download succeeds."""
    output_path = tmp_path / "test_file.txt"
    file_content = b"hello from blob"

    with mock.patch("requests.Session") as mock_session_class:
        mock_session = mock_session_class.return_value.__enter__.return_value

        mock_response = mock.Mock()
        mock_response.raise_for_status = mock.Mock()
        mock_response.json.return_value = {
            "content": base64.b64encode(file_content).decode(),
            "encoding": "base64",
        }
        mock_session.get.return_value = mock_response

        result = _download_via_blob_api(
            "owner", "repo", "abc123sha",
            {"Authorization": "token test"},
            output_path, "test_file.txt",
            quiet=True,
        )

        assert result is True
        assert output_path.exists()
        assert output_path.read_bytes() == file_content


def test_blob_api_retry_on_error(tmp_path: Path):
    """Test blob API retries on transient errors."""
    output_path = tmp_path / "test_file.txt"
    file_content = b"hello from blob"

    with mock.patch("gh_download.time.sleep"):
        with mock.patch("requests.Session") as mock_session_class:
            mock_session = mock_session_class.return_value.__enter__.return_value

            mock_response_ok = mock.Mock()
            mock_response_ok.raise_for_status = mock.Mock()
            mock_response_ok.json.return_value = {
                "content": base64.b64encode(file_content).decode(),
                "encoding": "base64",
            }

            mock_session.get.side_effect = [
                requests.exceptions.ConnectionError("fail"),
                mock_response_ok,
            ]

            result = _download_via_blob_api(
                "owner", "repo", "abc123sha",
                {"Authorization": "token test"},
                output_path, "test_file.txt",
                quiet=True,
            )

            assert result is True
            assert output_path.read_bytes() == file_content
            assert mock_session.get.call_count == 2


def test_blob_api_unsupported_encoding(tmp_path: Path):
    """Test blob API fails gracefully on unsupported encoding."""
    output_path = tmp_path / "test_file.txt"

    with mock.patch("requests.Session") as mock_session_class:
        mock_session = mock_session_class.return_value.__enter__.return_value

        mock_response = mock.Mock()
        mock_response.raise_for_status = mock.Mock()
        mock_response.json.return_value = {
            "content": "raw stuff",
            "encoding": "utf-8",
        }
        mock_session.get.return_value = mock_response

        result = _download_via_blob_api(
            "owner", "repo", "abc123sha",
            {"Authorization": "token test"},
            output_path, "test_file.txt",
            quiet=True,
        )

        assert result is False
        assert not output_path.exists()
