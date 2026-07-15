"""A single LCARS-styled terminal pane: a colored header bar plus a live
interactive terminal below it."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from .terminal import Terminal


class PaneHeader(Static):
    """The colored LCARS bar above a terminal pane."""

    DEFAULT_CSS = """
    PaneHeader {
        height: 1;
        width: 1fr;
        color: #000000;
        text-style: bold;
        padding: 0 1;
        content-align: left middle;
    }
    """


class TerminalPane(Vertical):
    """Bar + terminal, one LCARS console station."""

    DEFAULT_CSS = """
    TerminalPane {
        width: 1fr;
        height: 1fr;
        border: round #666666;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        title: str,
        command: str | list[str],
        *,
        accent: str = "#ff9c00",
        cwd: str | None = None,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.title_text = title
        self.command = command
        self.accent = accent
        self.cwd = cwd

    def compose(self) -> ComposeResult:
        header = PaneHeader(self.title_text)
        header.styles.background = self.accent
        yield header
        yield Terminal(self.command, cwd=self.cwd, id=f"{self.id}-term")

    def on_mount(self) -> None:
        self.styles.border = ("round", self.accent)

    @property
    def terminal(self) -> Terminal:
        return self.query_one(Terminal)
