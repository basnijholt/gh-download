"""A CLI tool to download files from GitHub, including private repositories via gh CLI."""

from __future__ import annotations

import base64
import json
import time
from importlib.metadata import version
from pathlib import Path

import requests
from rich.progress import Progress
from rich.rule import Rule
from rich.text import Text

from .gh import setup_download_headers
from .rich import console, create_error_panel

__version__ = version("gh_download")

# HTTP status codes that are likely transient and worth retrying.
# 404: GitHub CDN propagation delays can cause intermittent 404s for valid paths.
# 429: Rate limiting.
# 500, 502, 503, 504: Server-side issues that are often temporary.
_RETRYABLE_STATUS_CODES = frozenset({404, 429, 500, 502, 503, 504})
_MAX_RETRIES = 3


def _is_retryable_http_error(exc: requests.exceptions.HTTPError) -> bool:
    """Check if an HTTP error has a status code worth retrying."""
    return exc.response is not None and exc.response.status_code in _RETRYABLE_STATUS_CODES


def _strip_slashes(path_str: str) -> str:
    """Remove leading and trailing slashes from a path string."""
    return path_str.strip("/")


def _retry_delay(attempt: int, status_code: int | None = None) -> float:
    """Calculate retry delay with exponential backoff.

    For 429 (rate limit), use longer delays.
    """
    base = 2**attempt  # 1s, 2s, 4s
    if status_code == 429:  # noqa: PLR2004
        base *= 2  # Double the delay for rate limits
    return base


def _download_and_save_file(
    download_url: str,
    headers: dict[str, str],
    output_path: Path,
    display_name: str,
    *,
    quiet: bool = False,
) -> bool:
    """Download a file from download_url and save it to output_path with retry."""
    if not quiet:
        console.print(
            f"⏳ Downloading [cyan]{display_name}[/cyan] to [green]{output_path}[/green]...",
        )

    last_exception: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        try:
            # Ensure parent directory exists
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with requests.Session() as session:
                # Use stream=True for potentially large files
                response = session.get(download_url, headers=headers, timeout=60, stream=True)
                response.raise_for_status()

                with output_path.open("wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)

                if not quiet:
                    console.print(
                        f"✅ Saved [cyan]{display_name}[/cyan] to [green]{output_path}[/green]",
                    )
                return True

        except (  # noqa: PERF203
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as e:
            last_exception = e
            if attempt < _MAX_RETRIES - 1:
                delay = _retry_delay(attempt)
                if not quiet:
                    console.print(
                        f"⚠️ Network error downloading [cyan]{display_name}[/cyan] "
                        f"(attempt {attempt + 1}/{_MAX_RETRIES}). Retrying in {delay}s...",
                        style="yellow",
                    )
                time.sleep(delay)
                continue
            _handle_download_errors(e, display_name, output_path)
            return False

        except requests.exceptions.HTTPError as e:
            last_exception = e
            if _is_retryable_http_error(e) and attempt < _MAX_RETRIES - 1:
                status = e.response.status_code
                delay = _retry_delay(attempt, status)
                if not quiet:
                    console.print(
                        f"⚠️ HTTP {status} downloading [cyan]{display_name}[/cyan] "
                        f"(attempt {attempt + 1}/{_MAX_RETRIES}). Retrying in {delay}s...",
                        style="yellow",
                    )
                time.sleep(delay)
                continue
            _handle_download_errors(e, display_name, output_path)
            return False

        except requests.exceptions.RequestException as e:
            # Non-retryable request errors (e.g., 401, 403)
            _handle_download_errors(e, display_name, output_path)
            return False
        except OSError as e:  # For file I/O errors - don't retry
            _handle_download_errors(e, display_name, output_path)
            return False

    # All retries exhausted
    if last_exception is not None:
        _handle_download_errors(last_exception, display_name, output_path)
    return False


def _handle_download_errors(
    e: Exception,
    target_name: str,
    output_path: Path,
) -> None:
    """Handle various errors that can occur during download."""
    if isinstance(e, requests.exceptions.HTTPError):
        error_text = Text.assemble(
            (f"Failed to download {target_name}.\n", "bold red"),
            (f"Status Code: {e.response.status_code}\n", "red"),
        )
        try:
            error_details = e.response.json()
            error_text.append(
                f"GitHub API Message: {error_details.get('message', 'No message')}\n",
                style="red",
            )
            if "documentation_url" in error_details:
                error_text.append(
                    f"Documentation: {error_details['documentation_url']}\n",
                    style="blue link {error_details['documentation_url']}",
                )
        except json.JSONDecodeError:
            error_text.append(
                f"Raw Response: {e.response.text[:200]}...\n",
                style="red",
            )

        status_not_found = 404
        status_unauthorized = 401
        status_forbidden = 403
        if e.response.status_code == status_not_found:
            error_text.append(
                "Path not found. Please check repository owner, name, path, and branch.",
                style="yellow",
            )
        elif e.response.status_code in (status_unauthorized, status_forbidden):
            error_text.append(
                "Authentication/Authorization failed. Ensure your 'gh' CLI token has "
                "'repo' scope.\n",
                style="yellow",
            )
            error_text.append(
                "You might need to re-run 'gh auth login' or 'gh auth refresh -s repo'.",
                style="yellow",
            )
        console.print(create_error_panel("HTTP Error", error_text))
    elif isinstance(e, requests.exceptions.Timeout):
        console.print(
            create_error_panel("Timeout Error", f"🚨 Request timed out for {target_name}."),
        )
    elif isinstance(e, requests.exceptions.ConnectionError):
        console.print(
            create_error_panel(
                "Connection Error",
                f"🔗 Connection error for {target_name}. Check your network.",
            ),
        )
    elif isinstance(e, requests.exceptions.RequestException):
        console.print(
            create_error_panel(
                "Request Error",
                f"❌ An unexpected request error occurred for {target_name}: {e}",
            ),
        )
    elif isinstance(e, OSError):
        console.print(
            create_error_panel(
                "File I/O Error",
                f"💾 Error writing file to '{output_path}' for {target_name}: {e}",
            ),
        )
    else:
        console.print(
            create_error_panel(
                "Unexpected Error",
                f"🤷 An unexpected error occurred with {target_name}: {e}",
            ),
        )


def _fetch_content_metadata(
    repo_owner: str,
    repo_name: str,
    normalized_path: str,
    branch: str,
    headers: dict[str, str],
    display_name: str,
    *,
    quiet: bool = False,
) -> dict | list | None:
    """Fetch metadata for the given path from GitHub API with retry."""
    metadata_api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{normalized_path}?ref={branch}"

    last_exception: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        try:
            if not quiet:
                console.print(f"🔎 Fetching metadata for {display_name}...")
            with requests.Session() as session:
                response = session.get(metadata_api_url, headers=headers, timeout=30)
                response.raise_for_status()
                return response.json()

        except (
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as e:
            last_exception = e
            if attempt < _MAX_RETRIES - 1:
                delay = _retry_delay(attempt)
                if not quiet:
                    console.print(
                        f"⚠️ Network error fetching metadata for {display_name} "
                        f"(attempt {attempt + 1}/{_MAX_RETRIES}). Retrying in {delay}s...",
                        style="yellow",
                    )
                time.sleep(delay)
                continue

        except requests.exceptions.HTTPError as e:
            last_exception = e
            if _is_retryable_http_error(e) and attempt < _MAX_RETRIES - 1:
                status = e.response.status_code
                delay = _retry_delay(attempt, status)
                if not quiet:
                    console.print(
                        f"⚠️ HTTP {status} fetching metadata for {display_name} "
                        f"(attempt {attempt + 1}/{_MAX_RETRIES}). Retrying in {delay}s...",
                        style="yellow",
                    )
                time.sleep(delay)
                continue

        except requests.exceptions.RequestException as e:
            # Non-retryable request errors: capture and surface details.
            last_exception = e

        # If we get here without continuing, break out of the retry loop
        break

    if last_exception is not None:
        _handle_download_errors(last_exception, f"metadata for {display_name}", Path())
    return None


def _is_lfs_download_url(download_url: str) -> bool:
    """Check if a download URL is for a Git LFS file.

    GitHub LFS (Large File Storage) files are served from different URLs than regular files.
    When using GitHub App authentication, these URLs already contain authentication tokens
    and sending an additional Authorization header causes a "Bad credentials" error.

    This is a known issue with GitHub Apps and LFS:
    https://github.com/actions/checkout/issues/415

    Args:
        download_url: The download URL from GitHub's content API

    Returns:
        True if this is an LFS download URL, False otherwise

    """
    # LFS files are served from media.githubusercontent.com
    # or contain /storage/lfs/ in the path (for GitHub Enterprise)
    return "media.githubusercontent.com" in download_url or "/storage/lfs/" in download_url


def _prepare_download_headers(
    download_url: str,
    headers: dict[str, str],
    *,
    quiet: bool = False,
) -> dict[str, str]:
    """Prepare headers for downloading a file from GitHub.

    For regular files, we include the Authorization header from the GitHub API.
    For LFS files, we must NOT include the Authorization header because:
    1. The LFS download URL already contains a RemoteAuth token
    2. GitHub Apps send duplicate Authorization headers which causes authentication errors

    This implements the same workaround as the GitHub Actions checkout action.

    Args:
        download_url: The download URL from GitHub's content API
        headers: The headers used for API requests (includes Authorization)
        quiet: Whether to suppress output messages

    Returns:
        Headers appropriate for downloading the file

    """
    if _is_lfs_download_url(download_url):
        # For LFS files, don't include Authorization header to avoid duplicate auth
        # The LFS server provides its own RemoteAuth token embedded in the URL
        if not quiet:
            console.print(
                "🔐 Downloading LFS file without GitHub token (LFS auth handled separately)",
            )
        return {
            "Accept": "application/octet-stream",
        }
    # For regular files, include the Authorization header
    return {
        "Authorization": headers["Authorization"],
        "Accept": "application/octet-stream",
    }


def _resolve_output_file_path(output_path: Path, file_name: str) -> Path:
    """Resolve where a downloaded file should be written locally."""
    looks_like_directory = (
        output_path.is_dir()
        or (
            not output_path.exists()
            and output_path.name != file_name
            and not output_path.suffix
        )
    )
    if looks_like_directory:
        return output_path / file_name
    return output_path


def _should_try_blob_fallback(
    *,
    download_succeeded: bool,
    download_url: str,
    repo_owner: str,
    repo_name: str,
    file_sha: str | None,
) -> bool:
    """Return True when blob API fallback is safe and applicable."""
    if download_succeeded or not repo_owner or not repo_name or not file_sha:
        return False
    # For LFS files, Contents API `sha` points to an LFS pointer blob, not the real object.
    return not _is_lfs_download_url(download_url)


def _download_via_blob_api(
    repo_owner: str,
    repo_name: str,
    file_sha: str,
    headers: dict[str, str],
    output_path: Path,
    display_name: str,
    *,
    quiet: bool = False,
) -> bool:
    """Download a file using the Git Blobs API as a fallback.

    The Blobs API (``/repos/{owner}/{repo}/git/blobs/{sha}``) is more reliable
    than ``download_url`` from the Contents API because it accesses Git objects
    directly rather than going through the CDN.

    Args:
        repo_owner: The owner of the repository.
        repo_name: The name of the repository.
        file_sha: The SHA of the file blob.
        headers: Pre-authenticated headers for API calls.
        output_path: The path to save the downloaded file.
        display_name: The display name of the file.
        quiet: Whether to suppress output messages.

    Returns:
        True if the download was successful, False otherwise.

    """
    blob_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/git/blobs/{file_sha}"

    for attempt in range(_MAX_RETRIES):
        try:
            if not quiet:
                console.print(
                    f"🔄 Trying blob API for [cyan]{display_name}[/cyan]...",
                )
            with requests.Session() as session:
                response = session.get(blob_url, headers=headers, timeout=60)
                response.raise_for_status()

            blob_data = response.json()
            content = blob_data.get("content", "")
            encoding = blob_data.get("encoding", "")

            if encoding != "base64":
                if not quiet:
                    console.print(
                        f"⚠️ Blob API returned unsupported encoding '{encoding}' for {display_name}",
                        style="yellow",
                    )
                return False

            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(base64.b64decode(content))

            if not quiet:
                console.print(
                    f"✅ Saved [cyan]{display_name}[/cyan] via blob API to [green]{output_path}[/green]",
                )
            return True

        except (  # noqa: PERF203
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as e:
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_retry_delay(attempt))
                continue
            _handle_download_errors(e, display_name, output_path)
            return False

        except requests.exceptions.HTTPError as e:
            if _is_retryable_http_error(e) and attempt < _MAX_RETRIES - 1:
                time.sleep(_retry_delay(attempt, e.response.status_code))
                continue
            _handle_download_errors(e, display_name, output_path)
            return False

        except requests.exceptions.RequestException as e:
            _handle_download_errors(e, display_name, output_path)
            return False
        except (OSError, Exception) as e:  # noqa: BLE001
            _handle_download_errors(e, display_name, output_path)
            return False

    return False


def _download_single_file(
    content_info: dict,
    normalized_path: str,
    output_path: Path,
    headers: dict[str, str],
    *,
    repo_owner: str = "",
    repo_name: str = "",
    quiet: bool = False,
) -> bool:
    """Download a single file based on content metadata."""
    file_name = str(content_info.get("name") or Path(normalized_path).name)
    download_url = content_info.get("download_url")

    if not isinstance(download_url, str) or not download_url:
        console.print(f"❌ Could not get download_url for file: {file_name}", style="red")
        return False

    final_file_output_path = _resolve_output_file_path(output_path, file_name)

    # Ensure parent directory for the file exists
    try:
        final_file_output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        console.print(
            f"❌ Error creating directory for {final_file_output_path.parent}: {e}",
            style="red",
        )
        return False

    # Prepare headers based on whether this is an LFS file
    raw_download_headers = _prepare_download_headers(download_url, headers, quiet=quiet)

    success = _download_and_save_file(
        download_url,
        raw_download_headers,
        final_file_output_path,
        file_name,
        quiet=quiet,
    )

    file_sha = content_info.get("sha")
    normalized_sha = file_sha if isinstance(file_sha, str) else None

    if normalized_sha and _should_try_blob_fallback(
        download_succeeded=success,
        download_url=download_url,
        repo_owner=repo_owner,
        repo_name=repo_name,
        file_sha=normalized_sha,
    ):
        if not quiet:
            console.print(
                f"🔄 download_url failed for [cyan]{file_name}[/cyan], trying blob API fallback...",
                style="yellow",
            )
        return _download_via_blob_api(
            repo_owner,
            repo_name,
            normalized_sha,
            headers,
            final_file_output_path,
            file_name,
            quiet=quiet,
        )

    if not success and _is_lfs_download_url(download_url) and not quiet:
        console.print(
            f"⚠️ download_url failed for [cyan]{file_name}[/cyan]; "
            "skipping blob API fallback for LFS content.",
            style="yellow",
        )

    return success


def _process_directory_item(
    item: dict,
    repo_owner: str,
    repo_name: str,
    branch: str,
    target_dir_base: Path,
    display_name: str,
    headers: dict[str, str],
) -> bool:
    """Process a single item in a directory download.

    Args:
        item: Dictionary containing item metadata from GitHub API.
        repo_owner: The owner of the repository.
        repo_name: The name of the repository.
        branch: The branch, tag, or commit SHA to download from.
        target_dir_base: The base directory where items should be downloaded.
        display_name: The display name of the parent directory.
        headers: Pre-authenticated headers for API calls.

    Returns:
        True if the item was successfully processed, False otherwise.

    """
    item_name = item.get("name")
    item_type = item.get("type")
    item_path_in_repo = item.get("path")  # Full path in repo

    if not item_name or not item_type or not item_path_in_repo:
        console.print(f"⚠️ Skipping item with missing info: {item}", style="yellow")
        return False

    # Recursively call download for each item with quiet mode and shared headers
    success = download(
        repo_owner=repo_owner,
        repo_name=repo_name,
        file_path=item_path_in_repo,  # Use the full path from the API response
        branch=branch,
        output_path=target_dir_base,  # Children are downloaded *into* this directory
        quiet=True,  # Suppress verbose output for cleaner progress display
        headers=headers,  # Pass shared headers to avoid re-authentication
        show_progress=False,  # Disable progress for nested directories
    )

    if not success:
        console.print(
            f"❌ Failed to download {item_type} [yellow]{item_name}[/yellow] from {display_name}",
            style="red",
        )

    return success


def _download_directory(
    content_info: list,
    repo_owner: str,
    repo_name: str,
    normalized_path: str,
    branch: str,
    output_path: Path,
    display_name: str,
    *,
    headers: dict[str, str] | None = None,
    show_progress: bool = True,
) -> bool:
    """Download a directory and all its contents recursively."""
    # Create the base directory for the folder's contents
    target_dir_base = output_path

    # Check if we should create a subdirectory or use output_path directly
    folder_name = Path(normalized_path).name

    if (
        normalized_path  # Not downloading repo root
        and not output_path.suffix  # output_path is not a file
        and (output_path.is_dir() or not output_path.exists())  # output_path is/will be a directory
        and output_path.name
        != folder_name  # Avoid double nesting when CLI already set the right name
    ):
        # Output path is an existing directory or doesn't match the folder name,
        # so create the folder inside it
        target_dir_base = output_path / folder_name

    try:
        console.print(f"📁 Creating local directory: [green]{target_dir_base}[/green]")
        target_dir_base.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        console.print(f"❌ Error creating base directory '{target_dir_base}': {e}", style="red")
        return False

    # Get headers once for the whole directory download if not provided
    if headers is None:
        headers = setup_download_headers()
        if not headers:
            return False

    all_success = True
    total_items = len(content_info)

    console.print(f"📦 Found {total_items} items in directory {display_name}.")

    if show_progress:
        # Use Rich Progress for visual feedback (only at top level)
        with Progress(console=console) as progress:
            task = progress.add_task(
                "[cyan]Downloading directory items...",
                total=total_items,
            )

            for item in content_info:
                item_name = item.get("name")
                item_type = item.get("type")

                # Update progress with current item
                if item_name and item_type:
                    progress.update(
                        task,
                        description=f"[cyan]Downloading[/cyan] [blue]{item_type}[/blue]: [yellow]{item_name}[/yellow]",
                    )

                # Process the item
                success = _process_directory_item(
                    item=item,
                    repo_owner=repo_owner,
                    repo_name=repo_name,
                    branch=branch,
                    target_dir_base=target_dir_base,
                    display_name=display_name,
                    headers=headers,
                )

                if not success:
                    all_success = False

                # Advance progress
                progress.advance(task)
    else:
        # No progress bar for nested directory downloads
        for item in content_info:
            item_name = item.get("name")
            item_type = item.get("type")

            if item_name and item_type:
                console.print(
                    f"📄 Processing [blue]{item_type}[/blue]: [yellow]{item_name}[/yellow]",
                )

            # Process the item
            success = _process_directory_item(
                item=item,
                repo_owner=repo_owner,
                repo_name=repo_name,
                branch=branch,
                target_dir_base=target_dir_base,
                display_name=display_name,
                headers=headers,
            )

            if not success:
                all_success = False

    return all_success


def download(
    repo_owner: str,
    repo_name: str,
    file_path: str,  # This can be a file or a folder path
    branch: str,
    output_path: str | Path,  # Base output path provided by user or default
    *,
    quiet: bool = False,  # Suppress verbose output when True
    headers: dict[str, str] | None = None,  # Pre-authenticated headers
    show_progress: bool = True,  # Show progress bar for directory downloads
) -> bool:
    """Core logic for downloading a file or folder.

    Args:
        repo_owner: The owner of the repository.
        repo_name: The name of the repository.
        file_path: The path to the file or folder within the repository.
        branch: The branch, tag, or commit SHA to download from.
        output_path: The path to save the downloaded file or folder.
        quiet: Suppress verbose output when True.
        headers: Pre-authenticated headers.
        show_progress: Show progress bar for directory downloads.

    Returns:
        True if the download was successful, False otherwise.

    """
    if not quiet:
        console.print(
            Rule(
                f"[bold blue]GitHub Downloader: {repo_owner}/{repo_name}[/bold blue]",
                style="blue",
            ),
        )

    if isinstance(output_path, str):
        output_path = Path(output_path)

    # Clean the input file_path from leading/trailing slashes for API calls
    normalized_path = _strip_slashes(file_path)
    display_name = f"[cyan]{normalized_path}[/cyan]"
    if not normalized_path:  # Handle case where root of repo is requested
        display_name = "[cyan](repository root)[/cyan]"

    if not quiet:
        console.print(f"Attempting to download: {display_name}")
        console.print(f"Branch/Ref: [yellow]{branch}[/yellow]")
        console.print(f"Base output: [green]{output_path}[/green]")
        console.print("-" * 30)

    # Set up authentication headers once (or reuse provided headers).
    if headers is None:
        headers = setup_download_headers()
        if not headers:
            return False
    common_headers = headers

    # Fetch metadata to determine if it's a file or directory
    content_info = _fetch_content_metadata(
        repo_owner,
        repo_name,
        normalized_path,
        branch,
        common_headers,
        display_name,
        quiet=quiet,
    )
    if content_info is None:
        return False

    # Process based on whether it's a file or directory
    if isinstance(content_info, dict) and content_info.get("type") == "file":
        # It's a single file
        return _download_single_file(
            content_info,
            normalized_path,
            output_path,
            common_headers,
            repo_owner=repo_owner,
            repo_name=repo_name,
            quiet=quiet,
        )

    if isinstance(content_info, list):  # It's a directory
        return _download_directory(
            content_info,
            repo_owner,
            repo_name,
            normalized_path,
            branch,
            output_path,
            display_name,
            headers=common_headers,  # Pass headers to avoid re-authentication
            show_progress=show_progress,  # Pass progress setting
        )

    console.print(f"❌ Unexpected content type received from API for {display_name}.", style="red")
    console.print(f"Response: {content_info}", style="dim")
    return False


def main() -> None:  # pragma: no cover
    """Main entry point for the CLI."""
    from gh_download.cli import app

    app()
