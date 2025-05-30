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
from rich.prompt import Confirm  # For a nice yes/no prompt
from rich.rule import Rule
from rich.text import Text

__version__ = version("gh_download")
console = Console()


def _strip_slashes(path_str: str) -> str:
    return path_str.strip("/")


def _check_gh_executable_and_notify() -> str | None:
    """Checks for 'gh' executable and notifies if not found."""
    gh_executable = shutil.which("gh")
    if not gh_executable:
        console.print(
            Panel(
                Text.assemble(
                    ("GitHub CLI ('gh') not found in PATH.\n", "bold red"),
                    ("Please install it from ", "red"),
                    (
                        "https://cli.github.com/",
                        "link https://cli.github.com/ blue underline",
                    ),
                    (" and ensure it's in your PATH.", "red"),
                ),
                title="[bold red]Dependency Missing[/bold red]",
                border_style="red",
                expand=False,
            ),
        )
        return None
    return gh_executable


def _check_gh_auth_status(gh_executable: str) -> bool:
    """Checks the GitHub CLI authentication status.

    Returns:
        True if authenticated, False otherwise.

    """
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
    """Performs 'gh auth login' and verifies the status."""
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
        console.print(
            Panel(
                Text.assemble(
                    ("GitHub CLI ('gh') not found during login attempt.\n", "bold red"),
                    ("Please ensure it's installed and in PATH.", "red"),
                ),
                title="[bold red]Dependency Missing[/bold red]",
                border_style="red",
                expand=False,
            ),
        )
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
    """Attempts to run 'gh auth login' interactively for the user."""
    gh_executable = _check_gh_executable_and_notify()
    if not gh_executable:  # pragma: no cover
        return False
    return _perform_gh_login_and_verify(gh_executable)


def _check_gh_cli_availability() -> str | None:
    """Checks for 'gh' CLI and its version, notifying if issues are found."""
    gh_executable = _check_gh_executable_and_notify()
    if not gh_executable:
        return None

    try:
        subprocess.run(
            [gh_executable, "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        console.print(
            Panel(
                Text.assemble(
                    ("GitHub CLI ('gh') not found.\n", "bold red"),
                    ("Please install it from ", "red"),
                    (
                        "https://cli.github.com/",
                        "link https://cli.github.com/ blue underline",
                    ),
                    (" and then run this script again.", "red"),
                ),
                title="[bold red]Dependency Missing[/bold red]",
                border_style="red",
                expand=False,
            ),
        )
        return None
    except subprocess.CalledProcessError:
        console.print(
            "⚠️ Could not verify 'gh' CLI version. Proceeding with caution.",
            style="yellow",
        )
    return gh_executable


def _handle_gh_authentication_status(gh_executable: str) -> bool:
    """Checks 'gh auth status' and handles login if necessary."""
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
    """Retrieves the GitHub auth token using 'gh auth token'."""
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
        console.print(
            Panel(
                error_message,
                title="[bold red]CLI Token Error[/bold red]",
                border_style="red",
            ),
        )
        return None


def get_github_token_from_gh_cli() -> str | None:
    """Attempts to get an OAuth token using the 'gh' CLI."""
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
        console.print(
            Panel(
                error_message,
                title="[bold red]CLI Error[/bold red]",
                border_style="red",
            ),
        )
        return None
    except Exception as e:  # noqa: BLE001
        console.print(
            f"🚨 An unexpected error occurred in get_github_token_from_gh_cli: {e}",
            style="bold red",
        )
        return None


def _perform_download_and_save(
    download_url: str,  # Direct download URL for the file
    headers: dict[str, str],  # Auth headers
    output_path: Path,
    display_name: str,  # For logging
) -> bool:
    """Performs the file download from download_url and saves it to output_path."""
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
    download_target_display_name: str,  # For logging
    output_path: Path,
) -> None:
    """Handles various errors that can occur during download."""
    if isinstance(e, requests.exceptions.HTTPError):
        error_panel_title = "[bold red]HTTP Error[/bold red]"
        status_not_found = 404
        status_unauthorized = 401
        status_forbidden = 403
        error_text = Text.assemble(
            (f"Failed to download {download_target_display_name}.\n", "bold red"),
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
        console.print(
            Panel(
                error_text,
                title=error_panel_title,
                border_style="red",
                expand=False,
            ),
        )
    elif isinstance(e, requests.exceptions.Timeout):
        console.print(
            Panel(
                f"🚨 Request timed out for {download_target_display_name}.",
                title="[bold red]Timeout Error[/bold red]",
                border_style="red",
            ),
        )
    elif isinstance(e, requests.exceptions.ConnectionError):
        msg = (
            f"🔗 Connection error for {download_target_display_name}. "
            "Check your network."
        )
        console.print(
            Panel(
                msg,
                title="[bold red]Connection Error[/bold red]",
                border_style="red",
            ),
        )
    elif isinstance(
        e,
        requests.exceptions.RequestException,
    ):
        console.print(
            Panel(
                f"❌ An unexpected request error occurred for {download_target_display_name}: {e}",
                title="[bold red]Request Error[/bold red]",
                border_style="red",
            ),
        )
    elif isinstance(e, OSError):
        console.print(
            Panel(
                f"💾 Error writing file to '{output_path}' for {download_target_display_name}: {e}",
                title="[bold red]File I/O Error[/bold red]",
                border_style="red",
            ),
        )
    else:
        console.print(
            Panel(
                f"🤷 An unexpected error occurred with {download_target_display_name}: {e}",
                title="[bold red]Unexpected Error[/bold red]",
                border_style="red",
            ),
        )


def download_file(  # noqa: PLR0911, PLR0912, PLR0915
    repo_owner: str,
    repo_name: str,
    file_path: str,  # This can be a file or a folder path
    branch: str,
    output_path: Path,  # Base output path provided by user or default
) -> bool:
    """Core logic for downloading a file or folder."""
    console.print(
        Rule(
            f"[bold blue]GitHub Downloader: {repo_owner}/{repo_name}[/bold blue]",
            style="blue",
        ),
    )
    # Clean the input file_path from leading/trailing slashes for API calls
    clean_file_path = _strip_slashes(file_path)
    display_repo_path = f"[cyan]{clean_file_path}[/cyan]"
    if not clean_file_path:  # Handle case where root of repo is requested
        display_repo_path = "[cyan](repository root)[/cyan]"

    console.print(f"Attempting to download: {display_repo_path}")
    console.print(f"Branch/Ref: [yellow]{branch}[/yellow]")
    console.print(f"Base output: [green]{output_path}[/green]")
    console.print("-" * 30)

    # Get auth token for API requests
    token = get_github_token_from_gh_cli()
    if not token:
        console.print(
            "❌ Could not obtain GitHub token. Download aborted.",
            style="bold red",
        )
        return False

    # Headers for all API requests
    common_headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    # API URL to get metadata for the given path (file or directory)
    metadata_api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{clean_file_path}?ref={branch}"

    try:
        console.print(f"🔎 Fetching metadata for {display_repo_path}...")
        with requests.Session() as session:
            response = session.get(metadata_api_url, headers=common_headers, timeout=30)
            response.raise_for_status()
            content_info = response.json()

    except requests.exceptions.RequestException as e:
        _handle_download_errors(e, f"metadata for {display_repo_path}", output_path)
        return False

    # Process based on whether it's a file or directory
    # content_info can be a dict (for a file) or a list of dicts (for a directory)
    if isinstance(content_info, dict) and content_info.get("type") == "file":
        # It's a single file
        file_name = content_info.get("name", Path(clean_file_path).name)
        download_url = content_info.get("download_url")

        if not download_url:
            console.print(
                f"❌ Could not get download_url for file: {file_name}",
                style="red",
            )
            return False

        # Determine the final output path for the file
        # If output_path ends with the file_name or is not a directory, use it directly.
        # Otherwise, it's a directory, so append file_name.
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

        raw_download_headers = {
            "Authorization": f"token {token}",
            "Accept": "application/octet-stream",
        }
        return _perform_download_and_save(
            download_url,
            raw_download_headers,
            final_file_output_path,
            file_name,
        )

    if isinstance(content_info, list):  # It's a directory
        # Create the base directory for the folder's contents
        target_dir_base = output_path
        if (
            clean_file_path
            and not output_path.suffix
            and (output_path.is_dir() or not output_path.exists())
        ):  # Not downloading repo root
            # If output_path itself is a directory, create the folder inside it.
            # If output_path was given as /path/to/new_folder_name, use new_folder_name.
            target_dir_base = output_path / Path(clean_file_path).name
            # else: output_path is likely a specific name for the downloaded folder itself.

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

        all_success = True
        console.print(
            f"📦 Found {len(content_info)} items in directory {display_repo_path}.",
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
                continue

            # Recursively call download_file for each item
            console.print(
                Rule(
                    f"Processing [blue]{item_type}[/blue]: [cyan]{item_name}[/cyan]",
                    style="dim",
                ),
            )
            success = download_file(
                repo_owner=repo_owner,
                repo_name=repo_name,
                file_path=item_path_in_repo,  # Use the full path from the API response
                branch=branch,
                output_path=target_dir_base,  # Children are downloaded *into* this directory
            )
            if not success:
                all_success = False
                console.print(
                    f"❌ Failed to download {item_type} [yellow]{item_name}[/yellow] from {display_repo_path}",
                    style="red",
                )
        return all_success

    console.print(
        f"❌ Unexpected content type received from API for {display_repo_path}.",
        style="red",
    )
    console.print(f"Response: {content_info}", style="dim")
    return False


def main() -> None:  # pragma: no cover
    """Main entry point for the CLI."""
    from gh_download.cli import app

    app()
