"""LCARS-style one-screen TUI hosting PowerShell, GitHub Copilot CLI, and any
other console program side by side, Star-Trek-console fashion.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from .widgets.pane import TerminalPane
from .widgets.terminal import Terminal

CSS_PATH = Path(__file__).parent / "lcars.tcss"

# Default stations. Edit / extend freely.
DEFAULT_PANES = [
    dict(id="pane-pwsh", title="POWERSHELL", command="powershell.exe -NoLogo", accent="#ff9c00"),
    dict(id="pane-copilot", title="GITHUB COPILOT", command="powershell.exe -NoLogo -Command copilot", accent="#9999ff"),
    dict(id="pane-shell", title="AUX TERMINAL", command="powershell.exe -NoLogo", accent="#99ccff"),
]


class NewPaneScreen(ModalScreen[str]):
    """Modal dialog asking for a shell command to launch in a new pane."""

    def compose(self) -> ComposeResult:
        with Vertical(id="new-pane-dialog"):
            yield Static("ENTER COMMAND TO LAUNCH")
            yield Input(placeholder="e.g. powershell.exe -NoLogo -Command copilot", id="cmd-input")
            with Horizontal():
                yield Button("LAUNCH", id="launch", variant="success")
                yield Button("CANCEL", id="cancel", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "launch":
            value = self.query_one("#cmd-input", Input).value.strip()
            self.dismiss(value or None)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)


class LcarsApp(App):
    """The main LCARS console application."""

    CSS_PATH = CSS_PATH
    TITLE = "LCARS TERMINAL INTERFACE"
    BINDINGS = [
        ("ctrl+n", "new_pane", "New pane"),
        ("ctrl+k", "kill_pane", "Kill focused pane"),
        ("ctrl+r", "restart_pane", "Restart focused pane"),
        ("ctrl+q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal(id="root"):
            with Vertical(id="sidebar"):
                yield Static("\u25c9 LCARS", id="elbow-top")
                yield Button("PWSH", id="focus-pane-pwsh", classes="btn-orange")
                yield Button("COPILOT", id="focus-pane-copilot", classes="btn-lilac")
                yield Button("AUX", id="focus-pane-shell", classes="btn-blue")
                yield Static(id="sidebar-spacer")
                yield Button("NEW", id="new-pane", classes="btn-yellow")
                yield Button("KILL", id="kill-pane", classes="btn-red")
                yield Button("QUIT", id="quit", classes="btn-orange")
                yield Static("\u25c9 2410", id="elbow-bottom")
            with Vertical(id="main"):
                yield Static(self.TITLE, id="topbar")
                with Container(id="panes"):
                    for spec in DEFAULT_PANES:
                        yield TerminalPane(
                            spec["title"], spec["command"], accent=spec["accent"], id=spec["id"]
                        )
                yield Static(id="bottombar")

    def on_mount(self) -> None:
        self.set_interval(1, self._tick)
        self._tick()
        first_pane = self.query(TerminalPane).first()
        if first_pane is not None:
            first_pane.terminal.focus()

    def _tick(self) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.query_one("#bottombar", Static).update(f"STARDATE {stamp}")

    # -- sidebar actions -------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "quit":
            self.action_quit()
        elif button_id == "new-pane":
            self.action_new_pane()
        elif button_id == "kill-pane":
            self.action_kill_pane()
        elif button_id.startswith("focus-"):
            pane_id = button_id.removeprefix("focus-")
            pane = self.query_one(f"#{pane_id}", TerminalPane)
            pane.terminal.focus()

    def action_new_pane(self) -> None:
        def handle_result(command: str | None) -> None:
            if not command:
                return
            panes = self.query_one("#panes", Container)
            index = len(panes.children) + 1
            pane_id = f"pane-extra-{index}"
            pane = TerminalPane(f"STATION {index}", command, accent="#cc6666", id=pane_id)
            panes.mount(pane)
            pane.terminal.focus()

        self.push_screen(NewPaneScreen(), handle_result)

    def action_kill_pane(self) -> None:
        terminal = self.focused if isinstance(self.focused, Terminal) else None
        if terminal is not None:
            terminal.stop()

    def action_restart_pane(self) -> None:
        terminal = self.focused if isinstance(self.focused, Terminal) else None
        if terminal is not None:
            terminal.restart()

    def on_terminal_process_exited(self, message: Terminal.ProcessExited) -> None:
        message.terminal.refresh()


def main() -> None:
    LcarsApp().run()


if __name__ == "__main__":
    main()
