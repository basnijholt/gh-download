""""""

import json
import os
import subprocess

import requests
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm  # For a nice yes/no prompt
from rich.rule import Rule
from rich.text import Text

from ._version import __version__

# Initialize Rich Console
console = Console()


def run_gh_auth_login():
    """Attempts to run 'gh auth login' interactively for the user.
    Returns True if 'gh auth status' indicates a successful login afterwards, False otherwise.
    """
    console.print(
        Panel(
            Text.assemble(
                ("Attempting to initiate GitHub CLI login.\n", "bold yellow"),
                (
                    "Please follow the prompts from 'gh auth login' in your terminal.\n",
                    "yellow",
                ),
                ("You may need to open a web browser and enter a code.", "yellow"),
            ),
            title="[bold blue]Initiating 'gh auth login'[/bold blue]",
            border_style="blue",
        ),
    )
    try:
        # Run gh auth login.
        # We don't capture output here because it needs to be interactive.
        # It will use the script's stdin, stdout, and stderr.
        # We add '--hostname github.com' to be explicit, though often not strictly necessary.
        # '--git-protocol https' can also be useful to ensure consistency.
        # '--web' attempts to open the browser automatically.
        login_command = [
            "gh",
            "auth",
            "login",
            "--hostname",
            "github.com",
            "--web",
        ]  # You can add more flags if needed, e.g. --scopes repo,gist

        console.print(f"Executing: `{' '.join(login_command)}`", style="dim")
        process = subprocess.run(
            login_command,
            check=False,
        )  # check=False, we'll verify with status

        if process.returncode != 0:
            console.print(
                f"⚠️ 'gh auth login' process exited with code {process.returncode}. Login may have failed or been cancelled.",
                style="yellow",
            )
            # Even if it exited non-zero, we still check status, as user might have partially completed.
        else:
            console.print("✅ 'gh auth login' process completed.", style="green")

        # After the login attempt, explicitly check the status again
        console.print(
            "Verifying authentication status after login attempt...",
            style="cyan",
        )
        status_check_process = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            check=False,
        )
        if "Logged in to github.com account" in status_check_process.stderr:
            console.print(
                "👍 Successfully logged in to GitHub CLI!",
                style="bold green",
            )
            return True
        console.print(
            "❌ Still not logged in after 'gh auth login' attempt.",
            style="bold red",
        )
        if status_check_process.stderr:
            console.print(
                Text("Details from 'gh auth status':", style="italic dim"),
            )
            console.print(f"[dim]{status_check_process.stderr.strip()}[/dim]")
        return False

    except FileNotFoundError:
        # This should ideally be caught by the calling function first, but good to have
        console.print(
            Panel(
                Text.assemble(
                    ("GitHub CLI ('gh') not found.\n", "bold red"),
                    ("Please install it from ", "red"),
                    (
                        "https://cli.github.com/",
                        "link https://cli.github.com/ blue underline",
                    ),
                    (".", "red"),
                ),
                title="[bold red]Dependency Missing[/bold red]",
                border_style="red",
                expand=False,
            ),
        )
        return False
    except Exception as e:
        console.print(
            f"🚨 An unexpected error occurred while trying to run 'gh auth login': {e}",
            style="bold red",
        )
        return False


def get_github_token_from_gh_cli():
    """Attempts to get an OAuth token using the 'gh' CLI, with Rich output.
    Will offer to run 'gh auth login' if not authenticated.
    """
    try:
        # 1. Check if gh is installed first
        try:
            subprocess.run(
                ["gh", "--version"],
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
            # gh is found but --version failed for some reason, less likely
            console.print(
                "⚠️ Could not verify 'gh' CLI version. Proceeding with caution.",
                style="yellow",
            )

        # 2. Check if gh is authenticated
        status_process = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            check=False,
        )

        if "Logged in to github.com account" not in status_process.stderr:
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
                if not run_gh_auth_login():
                    console.print(
                        "Login attempt was not successful. Please try 'gh auth login' manually in your terminal.",
                        style="yellow",
                    )
                    return None
                # Re-check status after login attempt (run_gh_auth_login already does this, but an explicit re-fetch might be safer)
                status_process = subprocess.run(
                    ["gh", "auth", "status"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if "Logged in to github.com account" not in status_process.stderr:
                    console.print(
                        "❌ Still not authenticated after login attempt. Cannot proceed.",
                        style="bold red",
                    )
                    return None
                console.print("✅ Authentication successful!", style="bold green")

            else:
                console.print(
                    "Okay, please log in manually using 'gh auth login' and then re-run the script.",
                    style="yellow",
                )
                return None

        # 3. If authenticated, get the token
        token_process = subprocess.run(
            ["gh", "auth", "token"],
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
            (
                f"Error interacting with 'gh' CLI during '{' '.join(e.cmd)}':\n",
                "bold red",
            ),
            (f"Stderr: {e.stderr.strip() if e.stderr else 'No stderr output.'}", "red"),
        )
        console.print(
            Panel(
                error_message,
                title="[bold red]CLI Error[/bold red]",
                border_style="red",
            ),
        )
        return None


# --- [download_private_file_with_gh_token function remains largely the same, just calls the new get_github_token_from_gh_cli] ---
def download_private_file_with_gh_token(
    repo_owner,
    repo_name,
    file_path,
    branch="main",
    output_path="downloaded_file",
):
    """Downloads a file from a private GitHub repository using a token from gh cli, with Rich output."""
    console.print(
        "🔐 Attempting to ensure GitHub authentication and get token...",
        style="cyan",
    )
    token = (
        get_github_token_from_gh_cli()
    )  # This now handles the interactive login prompt

    if not token:
        console.print(
            "❌ Could not obtain GitHub token. Download aborted.",
            style="bold red",
        )
        return False

    api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file_path}?ref={branch}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3.raw",
    }

    download_target = f"[bold magenta]{repo_owner}/{repo_name}[/bold magenta]/[cyan]{file_path}[/cyan] (branch: [yellow]{branch}[/yellow])"
    console.print(f"⏳ Attempting to download {download_target}...")

    try:
        with requests.Session() as session:
            response = session.get(api_url, headers=headers, timeout=30)
            response.raise_for_status()

        with open(output_path, "wb") as f:
            f.write(response.content)
        console.print(
            Panel(
                Text.assemble(
                    ("🎉 File downloaded successfully!\n", "bold green"),
                    ("Saved to: ", "green"),
                    (output_path, "bold white"),
                ),
                title="[bold green]Download Complete[/bold green]",
                border_style="green",
                expand=False,
            ),
        )
        return True

    except requests.exceptions.HTTPError as e:
        error_panel_title = "[bold red]HTTP Error[/bold red]"
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

        if e.response.status_code == 404:
            error_text.append(
                "File not found. Please check repository owner, name, path, and branch.",
                style="yellow",
            )
        elif e.response.status_code == 401 or e.response.status_code == 403:
            error_text.append(
                "Authentication/Authorization failed. Ensure your 'gh' CLI token has 'repo' scope.\n",
                style="yellow",
            )
            error_text.append(
                "You might need to re-run 'gh auth login' or 'gh auth refresh -s repo'.",
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

    except requests.exceptions.Timeout:
        console.print(
            Panel(
                f"🚨 Request timed out while trying to download {download_target}.",
                title="[bold red]Timeout Error[/bold red]",
                border_style="red",
            ),
        )
    except requests.exceptions.ConnectionError:
        console.print(
            Panel(
                f"🔗 Connection error while trying to download {download_target}. Check your network.",
                title="[bold red]Connection Error[/bold red]",
                border_style="red",
            ),
        )
    except requests.exceptions.RequestException as e:
        console.print(
            Panel(
                f"❌ An unexpected request error occurred: {e}",
                title="[bold red]Request Error[/bold red]",
                border_style="red",
            ),
        )
    except OSError as e:
        console.print(
            Panel(
                f"💾 Error writing file to '{output_path}': {e}",
                title="[bold red]File I/O Error[/bold red]",
                border_style="red",
            ),
        )
    return False


if __name__ == "__main__":
    console.print(
        Rule("[bold blue]GitHub Private File Downloader[/bold blue]", style="blue"),
    )

    # --- Configuration ---
    REPO_OWNER = "your-github-username"  # Replace
    REPO_NAME = "your-private-repo-name"  # Replace
    FILE_IN_REPO = "README.md"  # Replace
    BRANCH = "main"
    LOCAL_OUTPUT_PATH = os.path.join(
        os.getcwd(),
        os.path.basename(FILE_IN_REPO) or "downloaded_file_rich",
    )
    # --- End Configuration ---

    if REPO_OWNER == "your-github-username" or REPO_NAME == "your-private-repo-name":
        config_notice = Text.assemble(
            ("Please update the script configuration:\n", "bold yellow"),
            (" - REPO_OWNER\n", "yellow"),
            (" - REPO_NAME\n", "yellow"),
            (
                " - FILE_IN_REPO (optional, defaults to README.md if not changed)\n",
                "yellow",
            ),
        )
        console.print(
            Panel(
                config_notice,
                title="[bold yellow]Configuration Needed[/bold yellow]",
                border_style="yellow",
                expand=False,
            ),
        )
        console.print("Exiting due to placeholder configuration.", style="dim")
    else:
        success = download_private_file_with_gh_token(
            REPO_OWNER,
            REPO_NAME,
            FILE_IN_REPO,
            branch=BRANCH,
            output_path=LOCAL_OUTPUT_PATH,
        )
        if success:
            console.print(
                f"✅ Process complete. Check [bold underline]{LOCAL_OUTPUT_PATH}[/bold underline]",
                style="green",
            )
        else:
            console.print(
                "❌ Download process failed. Please check the messages above.",
                style="bold red",
            )

    console.print(Rule(style="blue"))
