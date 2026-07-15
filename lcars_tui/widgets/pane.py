"""A single LCARS-styled terminal pane: a colored header bar plus a live
interactive terminal below it."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static

from .terminal import Terminal


class PaneHeader(Static):
    """The colored LCARS bar above a terminal pane."""

    DEFAULT_CSS = """
    PaneHeader {
        width: 1fr;
        height: 1;
        color: #000000;
        text-style: bold;
        padding: 0 1;
        content-align: left middle;
    }
    """


class ClosePaneButton(Button):
    """Small "X" button shown in the header of closable stations."""

    DEFAULT_CSS = """
    ClosePaneButton {
        width: 3;
        min-width: 3;
        height: 1;
        border: none;
        color: #000000;
        text-style: bold;
        padding: 0;
        content-align: center middle;
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

    TerminalPane .pane-header-row {
        height: 1;
        width: 1fr;
    }
    """

    def __init__(
        self,
        title: str,
        command: str | list[str],
        *,
        accent: str = "#ff9c00",
        accent_key: str | None = None,
        cwd: str | None = None,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
        closable: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.title_text = title
        self.command = command
        self.accent = accent
        # Semantic theme-palette slot (e.g. "lilac", "orange") this pane's
        # accent color was resolved from, if any -- lets LcarsApp look up
        # the equivalent color in a new theme and re-tint this pane via
        # set_accent_color() on Ctrl+T instead of it staying stuck on
        # whatever palette was active when the pane was created.
        self.accent_key = accent_key
        self.cwd = cwd
        self.closable = closable
        self._header: PaneHeader | None = None
        self._close_btn: ClosePaneButton | None = None

    def compose(self) -> ComposeResult:
        header = PaneHeader(self.title_text)
        header.styles.background = self.accent
        self._header = header
        if self.closable:
            with Horizontal(classes="pane-header-row"):
                yield header
                close_btn = ClosePaneButton("\u2715", id=f"close-{self.id}")
                close_btn.styles.background = self.accent
                self._close_btn = close_btn
                yield close_btn
        else:
            yield header
        yield Terminal(self.command, cwd=self.cwd, id=f"{self.id}-term")

    def on_mount(self) -> None:
        self.styles.border = ("round", self.accent)

    def set_accent_color(self, color: str) -> None:
        """Re-tint this pane's border, header, and close button (e.g. when
        the app-wide color theme changes) without recreating the pane."""
        self.accent = color
        self.styles.border = ("round", color)
        if self._header is not None:
            self._header.styles.background = color
        if self._close_btn is not None:
            self._close_btn.styles.background = color

    @property
    def terminal(self) -> Terminal:
        return self.query_one(Terminal)

    def set_displayed_title(self, text: str) -> None:
        """Update the header's rendered text without touching title_text."""
        self.query_one(PaneHeader).update(text)

