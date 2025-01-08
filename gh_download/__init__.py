"""A CLI tool to download files from GitHub, including private repositories via gh CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
from importlib.metadata import version
from typing import TYPE_CHECKING

import requests
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm  # For a nice yes/no prompt
from rich.rule import Rule
from rich.text import Text

if TYPE_CHECKING:
    from pathlib import Path

__version__ = version("gh_download")
console = Console()


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


def _build_api_url_and_headers(
    repo_owner: str,
    repo_name: str,
    file_path: str,
    branch: str,
) -> tuple[str, dict[str, str], str] | None:
    """Builds the API URL and headers for GitHub API request."""
    console.print(
        "🔐 Attempting to ensure GitHub authentication and get token...",
        style="cyan",
    )
    token = get_github_token_from_gh_cli()
    if not token:
        console.print(
            "❌ Could not obtain GitHub token. Download aborted.",
            style="bold red",
        )
        return None

    api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file_path}?ref={branch}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3.raw",
    }
    download_target = (
        f"[bold magenta]{repo_owner}/{repo_name}[/bold magenta]/"
        f"[cyan]{file_path}[/cyan] (branch: [yellow]{branch}[/yellow])"
    )
    return api_url, headers, download_target


def _perform_download_and_save(
    api_url: str,
    headers: dict[str, str],
    output_path: Path,  # Changed to Path
    download_target: str,
) -> bool:
    """Performs the file download and saves it to output_path."""
    console.print(f"⏳ Attempting to download {download_target}...")
    try:
        with requests.Session() as session:
            response = session.get(api_url, headers=headers, timeout=30)
            response.raise_for_status()

        with output_path.open("wb") as f:
            f.write(response.content)
        return True
    except requests.exceptions.RequestException as e:
        _handle_download_errors(e, download_target, output_path)
        return False
    except OSError as e:
        _handle_download_errors(e, download_target, output_path)
        return False


def _handle_download_errors(
    e: Exception,
    download_target: str,
    output_path: Path,  # Changed to Path
) -> None:
    """Handles various errors that can occur during download."""
    if isinstance(e, requests.exceptions.HTTPError):
        error_panel_title = "[bold red]HTTP Error[/bold red]"
        status_not_found = 404
        status_unauthorized = 401
        status_forbidden = 403
        error_text = Text.assemble(
            (f"Status Code: {e.response.status_code}\n", "bold red"),
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
                "File not found. Please check repository owner, name, path, and branch.",
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
                f"🚨 Request timed out while trying to download {download_target}.",
                title="[bold red]Timeout Error[/bold red]",
                border_style="red",
            ),
        )
    elif isinstance(e, requests.exceptions.ConnectionError):
        msg = (
            f"🔗 Connection error while trying to download {download_target}. "
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
    ):  # General request exception
        console.print(
            Panel(
                f"❌ An unexpected request error occurred: {e}",
                title="[bold red]Request Error[/bold red]",
                border_style="red",
            ),
        )
    elif isinstance(e, OSError):  # File I/O error
        console.print(
            Panel(
                f"💾 Error writing file to '{output_path}': {e}",
                title="[bold red]File I/O Error[/bold red]",
                border_style="red",
            ),
        )
    else:
        console.print(
            Panel(
                f"🤷 An unexpected error occurred during download: {e}",
                title="[bold red]Unexpected Download Error[/bold red]",
                border_style="red",
            ),
        )


def download_file(
    repo_owner: str,
    repo_name: str,
    file_path: str,
    branch: str,
    output_path: Path,
) -> bool:
    """Core logic for downloading a file, assuming output_path is resolved."""
    console.print(
        Rule(
            f"[bold blue]GitHub File Downloader: {repo_owner}/{repo_name}[/bold blue]",
            style="blue",
        ),
    )
    console.print(f"Attempting to download: [cyan]{file_path}[/cyan]")
    console.print(f"Branch/Ref: [yellow]{branch}[/yellow]")
    console.print(f"Saving to: [green]{output_path}[/green]")
    console.print("-" * 30)

    url_headers_target = _build_api_url_and_headers(
        repo_owner,
        repo_name,
        file_path,
        branch,
    )
    if not url_headers_target:
        return False
    api_url, headers, download_target = url_headers_target

    return _perform_download_and_save(
        api_url,
        headers,
        output_path,
        download_target,
    )


def main() -> None:  # pragma: no cover
    """Main entry point for the CLI."""
    from gh_download.cli import app

    app()
