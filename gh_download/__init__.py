"""A CLI tool to download files from GitHub, including private repositories via gh CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
from importlib.metadata import version
from pathlib import Path

import requests
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress
from rich.prompt import Confirm
from rich.rule import Rule
from rich.text import Text

__version__ = version("gh_download")
console = Console()


def _strip_slashes(path_str: str) -> str:
    """Remove leading and trailing slashes from a path string."""
    return path_str.strip("/")


def _create_error_panel(title: str, message: Text | str, style: str = "red") -> Panel:
    """Create a standardized error panel."""
    return Panel(
        message,
        title=f"[bold {style}]{title}[/bold {style}]",
        border_style=style,
        expand=False,
    )


def _create_gh_not_found_message() -> Text:
    """Create the standard GitHub CLI not found message."""
    return Text.assemble(
        ("GitHub CLI ('gh') not found.\n", "bold red"),
        ("Please install it from ", "red"),
        (
            "https://cli.github.com/",
            "link https://cli.github.com/ blue underline",
        ),
        (" and ensure it's in your PATH.", "red"),
    )


def _check_gh_executable() -> str | None:
    """Check for 'gh' executable and return path or None if not found."""
    return shutil.which("gh")


def _notify_gh_not_found() -> None:
    """Display notification that GitHub CLI was not found."""
    console.print(
        _create_error_panel("Dependency Missing", _create_gh_not_found_message()),
    )


def _check_gh_auth_status(gh_executable: str) -> bool:
    """Check the GitHub CLI authentication status."""
    try:
        status_process = subprocess.run(
            [gh_executable, "auth", "status"],
            capture_output=True,
            text=True,
            check=False,  # We check status manually, don't raise on non-zero
        )
    except (subprocess.SubprocessError, OSError) as e:
        console.print(
            f"🚨 Error checking 'gh auth status': {e}",
            style="bold red",
        )
        return False
    else:
        if "Logged in to github.com account" in status_process.stdout:
            return True
        if (
            status_process.stderr
            and "not logged in" not in status_process.stderr.lower()
        ):
            console.print(
                Text(
                    f"Unexpected stderr from 'gh auth status': {status_process.stderr.strip()}",
                    style="italic dim",
                ),
            )
        return False


def _perform_gh_login_and_verify(gh_executable: str) -> bool:
    """Perform 'gh auth login' and verify the status."""
    console.print(
        Panel(
            Text.assemble(
                ("Attempting to initiate GitHub CLI login.\n", "bold yellow"),
                (
                    "Please follow the prompts from 'gh auth login' "
                    "in your terminal.\n",
                    "yellow",
                ),
                ("You may need to open a web browser and enter a code.", "yellow"),
            ),
            title="[bold blue]Initiating 'gh auth login'[/bold blue]",
            border_style="blue",
        ),
    )
    try:
        login_command = [
            gh_executable,
            "auth",
            "login",
            "--hostname",
            "github.com",
            "--web",
        ]
        console.print(f"Executing: `{' '.join(login_command)}`", style="dim")
        process = subprocess.run(
            login_command,
            check=False,  # We check status manually
        )
        if process.returncode != 0:
            msg = (
                f"⚠️ 'gh auth login' process exited with code {process.returncode}. "
                "Login may have failed or been cancelled."
            )
            console.print(msg, style="yellow")
        else:
            console.print("✅ 'gh auth login' process completed.", style="green")

        console.print(
            "Verifying authentication status after login attempt...",
            style="cyan",
        )
    except FileNotFoundError:
        _notify_gh_not_found()
        return False
    except (subprocess.SubprocessError, OSError) as e:  # For login command
        console.print(
            f"🚨 An unexpected error occurred while trying to run 'gh auth login': {e}",
            style="bold red",
        )
        return False
    else:  # Login command didn't raise an exception
        if _check_gh_auth_status(gh_executable):
            console.print(
                "👍 Successfully logged in to GitHub CLI!",
                style="bold green",
            )
            return True
        console.print(
            "❌ Still not logged in after 'gh auth login' attempt.",
            style="bold red",
        )
        return False


def run_gh_auth_login() -> bool:
    """Attempt to run 'gh auth login' interactively for the user."""
    gh_executable = _check_gh_executable()
    if not gh_executable:  # pragma: no cover
        _notify_gh_not_found()
        return False
    return _perform_gh_login_and_verify(gh_executable)


def _check_gh_cli_availability() -> str | None:
    """Check for 'gh' CLI and its version, notifying if issues are found."""
    gh_executable = _check_gh_executable()
    if not gh_executable:
        _notify_gh_not_found()
        return None

    try:
        subprocess.run(
            [gh_executable, "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        _notify_gh_not_found()
        return None
    except subprocess.CalledProcessError:
        console.print(
            "⚠️ Could not verify 'gh' CLI version. Proceeding with caution.",
            style="yellow",
        )
    return gh_executable


def _handle_gh_authentication_status(gh_executable: str) -> bool:
    """Check 'gh auth status' and handle login if necessary."""
    if _check_gh_auth_status(gh_executable):
        return True

    login_instructions = Text.assemble(
        ("You are not logged into the GitHub CLI.\n", "bold yellow"),
        (
            "This script needs access to GitHub to download files from "
            "private repositories.",
            "yellow",
        ),
    )
    console.print(
        Panel(
            login_instructions,
            title="[bold yellow]Authentication Required[/bold yellow]",
            border_style="yellow",
            expand=False,
        ),
    )

    if Confirm.ask(
        "Would you like to try running 'gh auth login' now to authenticate?",
        default=True,
        console=console,
    ):
        if not run_gh_auth_login():
            msg = (
                "Login attempt was not successful. Please try 'gh auth login' "
                "manually in your terminal."
            )
            console.print(msg, style="yellow")
            return False
        if not _check_gh_auth_status(gh_executable):
            msg = "❌ Still not authenticated after login attempt. Cannot proceed."
            console.print(msg, style="bold red")
            return False
        console.print("✅ Authentication successful!", style="bold green")
        return True
    msg = (
        "Okay, please log in manually using 'gh auth login' and then re-run the script."
    )
    console.print(msg, style="yellow")
    return False


def _retrieve_gh_auth_token(gh_executable: str) -> str | None:
    """Retrieve the GitHub auth token using 'gh auth token'."""
    try:
        token_process = subprocess.run(
            [gh_executable, "auth", "token"],
            capture_output=True,
            text=True,
            check=True,
        )
        console.print(
            "🔑 Successfully retrieved GitHub token via 'gh' CLI.",
            style="green",
        )
        return token_process.stdout.strip()
    except subprocess.CalledProcessError as e:
        error_message = Text.assemble(
            ("Error getting token with 'gh auth token':\n", "bold red"),
            (
                f"Stderr: {e.stderr.strip() if e.stderr else 'No stderr output.'}",
                "red",
            ),
        )
        console.print(_create_error_panel("CLI Token Error", error_message))
        return None


def get_github_token_from_gh_cli() -> str | None:
    """Attempt to get an OAuth token using the 'gh' CLI."""
    gh_executable = _check_gh_cli_availability()
    if not gh_executable:
        return None

    try:
        if not _handle_gh_authentication_status(gh_executable):
            return None
        return _retrieve_gh_auth_token(gh_executable)
    except subprocess.CalledProcessError as e:
        error_message = Text.assemble(
            (
                f"Error interacting with 'gh' CLI during '{' '.join(e.cmd)}':\n",
                "bold red",
            ),
            (
                f"Stderr: {e.stderr.strip() if e.stderr else 'No stderr output.'}",
                "red",
            ),
        )
        console.print(_create_error_panel("CLI Error", error_message))
        return None
    except Exception as e:  # noqa: BLE001
        console.print(
            f"🚨 An unexpected error occurred in get_github_token_from_gh_cli: {e}",
            style="bold red",
        )
        return None


def _download_and_save_file(
    download_url: str,
    headers: dict[str, str],
    output_path: Path,
    display_name: str,
    *,
    quiet: bool = False,
) -> bool:
    """Download a file from download_url and save it to output_path."""
    if not quiet:
        console.print(
            f"⏳ Downloading [cyan]{display_name}[/cyan] to [green]{output_path}[/green]...",
        )

    try:
        # Ensure parent directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with requests.Session() as session:
            # Use stream=True for potentially large files
            response = session.get(
                download_url,
                headers=headers,
                timeout=60,
                stream=True,
            )
            response.raise_for_status()

            with output_path.open("wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            if not quiet:
                console.print(
                    f"✅ Saved [cyan]{display_name}[/cyan] to [green]{output_path}[/green]",
                )
            return True
    except requests.exceptions.RequestException as e:
        _handle_download_errors(e, display_name, output_path)
        return False
    except OSError as e:  # For file I/O errors
        _handle_download_errors(e, display_name, output_path)
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
                "You might need to re-run 'gh auth login' or "
                "'gh auth refresh -s repo'.",
                style="yellow",
            )
        console.print(_create_error_panel("HTTP Error", error_text))
    elif isinstance(e, requests.exceptions.Timeout):
        console.print(
            _create_error_panel(
                "Timeout Error",
                f"🚨 Request timed out for {target_name}.",
            ),
        )
    elif isinstance(e, requests.exceptions.ConnectionError):
        console.print(
            _create_error_panel(
                "Connection Error",
                f"🔗 Connection error for {target_name}. Check your network.",
            ),
        )
    elif isinstance(e, requests.exceptions.RequestException):
        console.print(
            _create_error_panel(
                "Request Error",
                f"❌ An unexpected request error occurred for {target_name}: {e}",
            ),
        )
    elif isinstance(e, OSError):
        console.print(
            _create_error_panel(
                "File I/O Error",
                f"💾 Error writing file to '{output_path}' for {target_name}: {e}",
            ),
        )
    else:
        console.print(
            _create_error_panel(
                "Unexpected Error",
                f"🤷 An unexpected error occurred with {target_name}: {e}",
            ),
        )


def _setup_download_headers() -> dict[str, str] | None:
    """Set up authentication headers for GitHub API calls."""
    token = get_github_token_from_gh_cli()
    if not token:
        console.print(
            "❌ Could not obtain GitHub token. Download aborted.",
            style="bold red",
        )
        return None

    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }


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
    """Fetch metadata for the given path from GitHub API."""
    metadata_api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{normalized_path}?ref={branch}"

    try:
        if not quiet:
            console.print(f"🔎 Fetching metadata for {display_name}...")
        with requests.Session() as session:
            response = session.get(metadata_api_url, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json()
    except requests.exceptions.RequestException as e:
        _handle_download_errors(e, f"metadata for {display_name}", Path())
        return None


def _download_single_file(
    content_info: dict,
    normalized_path: str,
    output_path: Path,
    headers: dict[str, str],
    *,
    quiet: bool = False,
) -> bool:
    """Download a single file based on content metadata."""
    file_name = content_info.get("name", Path(normalized_path).name)
    download_url = content_info.get("download_url")

    if not download_url:
        console.print(
            f"❌ Could not get download_url for file: {file_name}",
            style="red",
        )
        return False

    # Determine the final output path for the file
    final_file_output_path = output_path
    if output_path.is_dir() or (
        not output_path.exists()
        and output_path.name != file_name
        and not output_path.suffix
    ):
        # If output_path is an existing dir, or a non-existent path that looks like a dir
        final_file_output_path = output_path / file_name

    # Ensure parent directory for the file exists
    try:
        final_file_output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        console.print(
            f"❌ Error creating directory for {final_file_output_path.parent}: {e}",
            style="red",
        )
        return False

    # Update headers for raw file download
    raw_download_headers = {
        "Authorization": headers["Authorization"],
        "Accept": "application/octet-stream",
    }

    return _download_and_save_file(
        download_url,
        raw_download_headers,
        final_file_output_path,
        file_name,
        quiet=quiet,
    )


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
        and (
            output_path.is_dir() or not output_path.exists()
        )  # output_path is/will be a directory
        and output_path.name
        != folder_name  # Avoid double nesting when CLI already set the right name
    ):
        # Output path is an existing directory or doesn't match the folder name,
        # so create the folder inside it
        target_dir_base = output_path / folder_name

    try:
        console.print(
            f"📁 Creating local directory: [green]{target_dir_base}[/green]",
        )
        target_dir_base.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        console.print(
            f"❌ Error creating base directory '{target_dir_base}': {e}",
            style="red",
        )
        return False

    # Get headers once for the whole directory download if not provided
    if headers is None:
        headers = _setup_download_headers()
        if not headers:
            return False

    all_success = True
    total_items = len(content_info)

    console.print(
        f"📦 Found {total_items} items in directory {display_name}.",
    )

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
                item_path_in_repo = item.get("path")  # Full path in repo

                if not item_name or not item_type or not item_path_in_repo:
                    console.print(
                        f"⚠️ Skipping item with missing info: {item}",
                        style="yellow",
                    )
                    all_success = False
                    progress.advance(task)
                    continue

                # Update progress with current item
                progress.update(
                    task,
                    description=f"[cyan]Downloading[/cyan] [blue]{item_type}[/blue]: [yellow]{item_name}[/yellow]",
                )

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
                    all_success = False
                    console.print(
                        f"❌ Failed to download {item_type} [yellow]{item_name}[/yellow] from {display_name}",
                        style="red",
                    )

                # Advance progress
                progress.advance(task)
    else:
        # No progress bar for nested directory downloads
        for item in content_info:
            item_name = item.get("name")
            item_type = item.get("type")
            item_path_in_repo = item.get("path")  # Full path in repo

            if not item_name or not item_type or not item_path_in_repo:
                console.print(
                    f"⚠️ Skipping item with missing info: {item}",
                    style="yellow",
                )
                all_success = False
                continue

            console.print(
                f"📄 Processing [blue]{item_type}[/blue]: [yellow]{item_name}[/yellow]",
            )

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
                all_success = False
                console.print(
                    f"❌ Failed to download {item_type} [yellow]{item_name}[/yellow] from {display_name}",
                    style="red",
                )

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
    """Core logic for downloading a file or folder."""
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

    # Set up authentication headers (only if not provided)
    if headers is None:
        headers = _setup_download_headers()
        if not headers:
            return False
    else:
        # Use provided headers (for directory downloads)
        common_headers = headers

    # Assign to common_headers for consistent naming
    if headers is not None:
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

    console.print(
        f"❌ Unexpected content type received from API for {display_name}.",
        style="red",
    )
    console.print(f"Response: {content_info}", style="dim")
    return False


def main() -> None:  # pragma: no cover
    """Main entry point for the CLI."""
    from gh_download.cli import app

    app()
