"""GitHub CLI utilities for gh-download."""

from __future__ import annotations

import shutil
import subprocess

from rich.panel import Panel
from rich.prompt import Confirm
from rich.text import Text

from .rich import console, create_error_panel


def setup_download_headers() -> dict[str, str] | None:
    """Set up authentication headers for GitHub API calls."""
    token = _github_token_from_gh_cli()
    if not token:
        console.print("❌ Could not obtain GitHub token. Download aborted.", style="bold red")
        return None

    return {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}


def _create_gh_not_found_message() -> Text:
    """Create the standard GitHub CLI not found message."""
    return Text.assemble(
        ("GitHub CLI ('gh') not found.\n", "bold red"),
        ("Please install it from ", "red"),
        ("https://cli.github.com/", "link https://cli.github.com/ blue underline"),
        (" and ensure it's in your PATH.", "red"),
    )


def _check_gh_executable() -> str | None:
    """Check for 'gh' executable and return path or None if not found."""
    return shutil.which("gh")


def _notify_gh_not_found() -> None:
    """Display notification that GitHub CLI was not found."""
    console.print(
        create_error_panel("Dependency Missing", _create_gh_not_found_message()),
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
        console.print(f"🚨 Error checking 'gh auth status': {e}", style="bold red")
        return False
    else:
        if "Logged in to github.com account" in status_process.stdout:
            return True
        if status_process.stderr and "not logged in" not in status_process.stderr.lower():
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
                ("Please follow the prompts from 'gh auth login' in your terminal.\n", "yellow"),
                ("You may need to open a web browser and enter a code.", "yellow"),
            ),
            title="[bold blue]Initiating 'gh auth login'[/bold blue]",
            border_style="blue",
        ),
    )
    try:
        login_command = [gh_executable, "auth", "login", "--hostname", "github.com", "--web"]
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
            console.print("👍 Successfully logged in to GitHub CLI!", style="bold green")
            return True
        console.print("❌ Still not logged in after 'gh auth login' attempt.", style="bold red")
        return False


def _run_gh_auth_login() -> bool:
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
        subprocess.run([gh_executable, "--version"], capture_output=True, text=True, check=True)
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
            "This script needs access to GitHub to download files from private repositories.",
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
        if not _run_gh_auth_login():
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
    msg = "Okay, please log in manually using 'gh auth login' and then re-run the script."
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
        console.print("🔑 Successfully retrieved GitHub token via 'gh' CLI.", style="green")
        return token_process.stdout.strip()
    except subprocess.CalledProcessError as e:
        error_message = Text.assemble(
            ("Error getting token with 'gh auth token':\n", "bold red"),
            (
                f"Stderr: {e.stderr.strip() if e.stderr else 'No stderr output.'}",
                "red",
            ),
        )
        console.print(create_error_panel("CLI Token Error", error_message))
        return None


def _github_token_from_gh_cli() -> str | None:
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
            (f"Error interacting with 'gh' CLI during '{' '.join(e.cmd)}':\n", "bold red"),
            (f"Stderr: {e.stderr.strip() if e.stderr else 'No stderr output.'}", "red"),
        )
        console.print(create_error_panel("CLI Error", error_message))
        return None
    except Exception as e:  # noqa: BLE001
        console.print(
            f"🚨 An unexpected error occurred in _github_token_from_gh_cli: {e}",
            style="bold red",
        )
        return None
