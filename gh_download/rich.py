"""Rich utilities for gh-download."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel

if TYPE_CHECKING:
    from rich.text import Text

console = Console()


def create_error_panel(title: str, message: Text | str, style: str = "red") -> Panel:
    """Create a standardized error panel."""
    return Panel(
        message,
        title=f"[bold {style}]{title}[/bold {style}]",
        border_style=style,
        expand=False,
    )
