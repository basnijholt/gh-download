"""A CLI tool to download files from GitHub, including private repositories via gh CLI."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.panel import Panel
from rich.text import Text

from gh_download import download

from .rich import console

app = typer.Typer(
    add_completion=False,
    rich_markup_mode="rich",
    help="🚀 GitHub Downloader (gh-download): A tool to download files from GitHub repos using 'gh' CLI auth.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@app.command(help="Download a specific file from a GitHub repository.")
def get(
    repo_owner: str = typer.Argument(
        ...,
        help="The owner of the repository (e.g., 'octocat').",
    ),
    repo_name: str = typer.Argument(
        ...,
        help="The name of the repository (e.g., 'Spoon-Knife').",
    ),
    file_path: str = typer.Argument(
        ...,
        help="The path to the file or folder within the repository (e.g., 'README.md' or 'src/my_folder').",
    ),
    branch: str = typer.Option(
        "main",
        "--branch",
        "-b",
        help="The branch, tag, or commit SHA to download from.",
    ),
    output_path_str: str | None = typer.Option(  # Accept string for Typer
        None,
        "--output",
        "-o",
        help=(
            "Local path to save the downloaded file or folder. "
            "If downloading a file, this can be a new filename or a directory. "
            "If downloading a folder, this is the directory where the folder will be placed. "
            "Defaults to the original filename/foldername in the current directory."
        ),
        show_default=False,
    ),
) -> None:
    """Downloads a file or folder from a GitHub repository.

    Examples:
        gh-download octocat Spoon-Knife README.md -o my_readme.md
        gh-download octocat Spoon-Knife src/app -o downloaded_app_code

    """
    # Determine the base output path
    if output_path_str:
        output_path = Path(output_path_str).resolve()
    else:
        # Default: current working directory with the filename appended
        filename = Path(file_path).name or "downloaded_file_gh_download"
        output_path = (Path.cwd() / filename).resolve()

    # Ensure parent directory exists
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        console.print(
            Panel(
                f"❌ Error creating output directory '{output_path.parent}': {e}",
                title="[bold red]Directory Creation Failed[/bold red]",
                border_style="red",
            ),
        )
        raise typer.Exit(code=1) from e

    success = download(
        repo_owner=repo_owner,
        repo_name=repo_name,
        file_path=file_path,
        branch=branch,
        output_path=output_path,
    )

    if success:
        # Determine the final save location for display purposes
        final_destination_name = Path(file_path).name or "downloaded_content"
        final_save_location = output_path

        # Only add subdirectory to display path if:
        # 1. output_path is an existing directory, AND
        # 2. output_path name doesn't match the folder being downloaded
        # This matches the logic in _download_directory
        if (
            output_path.is_dir()
            and output_path.name != final_destination_name
            and not output_path_str  # Only for default CLI behavior, not explicit -o paths
        ):
            final_save_location = output_path / final_destination_name

        console.print(
            Panel(
                Text.assemble(
                    ("🎉 Path downloaded successfully!\n", "bold green"),
                    ("Content from '", "green"),
                    (file_path, "cyan"),
                    ("' saved to or within: ", "green"),
                    (str(final_save_location), "bold white"),
                ),
                title="[bold green]Download Complete[/bold green]",
                border_style="green",
                expand=False,
            ),
        )
        raise typer.Exit(code=0)
    console.print(
        Panel(
            "❌ Download process failed. Please check messages above for details.",
            title="[bold red]Download Failed[/bold red]",
            border_style="red",
            expand=False,
        ),
    )
    raise typer.Exit(code=1)


def main() -> None:
    """Main entry point for the CLI."""
    app()
