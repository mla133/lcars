"""LCARS-style one-screen TUI hosting PowerShell, GitHub Copilot CLI, and any
other console program side by side, Star-Trek-console fashion.
"""

from __future__ import annotations

import urllib.request
from datetime import datetime
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

# Compact single-line local forecast (auto-located by wttr.in via IP), e.g.
# "\u2601\ufe0f +14\u00b0C". format=1 keeps it short enough for the sidebar's
# elbow block; a short timeout means a dead/absent network just leaves the
# block blank instead of hanging the UI.
WEATHER_URL = "https://wttr.in/?format=1"
WEATHER_REFRESH_SECS = 900

from .widgets.pane import TerminalPane
from .widgets.terminal import Terminal

CSS_PATH = Path(__file__).parent / "lcars.tcss"
PROMPT_SCRIPT = Path(__file__).parent / "assets" / "lcars_prompt.ps1"


def _pwsh(label: str, accent: str) -> list[str]:
    """Build an argv list that launches PowerShell with the LCARS prompt.

    Passed as a list (rather than a single string) so the script path is
    never re-split/re-quoted by the pty layer's shell-style parsing --
    important since paths may contain spaces.
    """
    return [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NoExit",
        "-File",
        str(PROMPT_SCRIPT),
        "-Label",
        label,
        "-Accent",
        accent,
    ]


# Default stations. Edit / extend freely.
DEFAULT_PANES = [
    dict(id="pane-copilot", title="GITHUB COPILOT", command="powershell.exe -NoLogo -Command copilot", accent="#9999ff"),
    dict(id="pane-pwsh", title="POWERSHELL", command=_pwsh("PWSH", "DarkYellow"), accent="#ff9c00"),
]
DEFAULT_TAB = DEFAULT_PANES[0]["id"]

# The auxiliary station: hidden by default, toggled on/off via the AUX button.
AUX_PANE = dict(id="pane-shell", title="AUX TERMINAL", command=_pwsh("AUX", "Cyan"), accent="#99ccff")


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
        ("ctrl+1", "show_tab('pane-copilot')", "Copilot"),
        ("ctrl+2", "show_tab('pane-pwsh')", "PowerShell"),
        ("ctrl+3", "toggle_aux", "Aux"),
        ("ctrl+n", "new_pane", "New pane"),
        ("ctrl+k", "kill_pane", "Kill focused pane"),
        ("ctrl+r", "restart_pane", "Restart focused pane"),
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._active_tab: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="root"):
            with Vertical(id="sidebar"):
                yield Static(id="elbow-top")
                with Container(classes="btn-shell btn-lilac"):
                    yield Button("COPILOT", id="tab-pane-copilot")
                with Container(classes="btn-shell btn-orange"):
                    yield Button("PWSH", id="tab-pane-pwsh")
                with Container(classes="btn-shell btn-blue"):
                    yield Button("AUX", id="toggle-aux")
                yield Static(id="sidebar-spacer")
                with Container(classes="btn-shell btn-green"):
                    yield Button("NEW", id="new-pane")
                with Container(classes="btn-shell btn-orange"):
                    yield Button("KILL", id="kill-pane")
                with Container(classes="btn-shell btn-red"):
                    yield Button("QUIT", id="quit")
                yield Static("\u25c9", id="elbow-bottom")
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
        self._activate(DEFAULT_TAB)
        self.set_interval(WEATHER_REFRESH_SECS, self._fetch_weather)
        self._fetch_weather()

    @work(thread=True, exclusive=True)
    def _fetch_weather(self) -> None:
        """Fetch a compact local forecast from wttr.in for the upper-left
        elbow block. Runs off the UI thread; any failure (no network, DNS,
        timeout, etc.) just blanks the block rather than raising."""
        try:
            with urllib.request.urlopen(WEATHER_URL, timeout=3) as resp:
                text = resp.read().decode("utf-8", "replace").strip()
        except Exception:
            text = ""
        self.call_from_thread(self._set_weather, text)

    def _set_weather(self, text: str) -> None:
        self.query_one("#elbow-top", Static).update(text)

    def _tick(self) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.query_one("#bottombar", Static).update(f"STARDATE {stamp}")
        self._update_activity_indicator()

    def _update_activity_indicator(self) -> None:
        busy = self._busy_background_panes()
        indicator = self.query_one("#elbow-bottom", Static)
        indicator.set_class(bool(busy), "busy")
        if busy:
            indicator.update(", ".join(busy))
            indicator.tooltip = f"WORKING: {', '.join(busy)}"
        else:
            indicator.update("\u25c9")
            indicator.tooltip = None

    def _busy_background_panes(self) -> list[str]:
        """Titles of panes (other than the one currently shown) whose
        process has produced output recently -- i.e. still working."""
        busy = []
        for pane in self.query_one("#panes", Container).query(TerminalPane):
            if pane.id == self._active_tab:
                continue
            if pane.terminal.is_active():
                busy.append(pane.title_text)
        return busy

    # -- tab switching -----------------------------------------------------
    def _activate(self, pane_id: str) -> None:
        """Show the pane with the given id and hide every other pane."""
        panes = self.query_one("#panes", Container)
        try:
            active = panes.query_one(f"#{pane_id}", TerminalPane)
        except NoMatches:
            return
        for pane in panes.query(TerminalPane):
            pane.display = pane.id == pane_id
        self._active_tab = pane_id
        active.terminal.focus()
        self._update_tab_buttons()

    def _update_tab_buttons(self) -> None:
        for button in self.query("#sidebar Button"):
            button_id = button.id or ""
            is_tab_button = button_id.startswith("tab-") or button_id == "toggle-aux"
            if not is_tab_button:
                continue
            target = button_id.removeprefix("tab-") if button_id.startswith("tab-") else AUX_PANE["id"]
            button.set_class(target == self._active_tab, "active")

    def action_show_tab(self, pane_id: str) -> None:
        self._activate(pane_id)

    # -- sidebar actions -------------------------------------------------
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "quit":
            self.exit()
        elif button_id == "new-pane":
            self.action_new_pane()
        elif button_id == "kill-pane":
            self.action_kill_pane()
        elif button_id == "toggle-aux":
            await self.action_toggle_aux()
        elif button_id.startswith("tab-"):
            self._activate(button_id.removeprefix("tab-"))

    async def action_toggle_aux(self) -> None:
        panes = self.query_one("#panes", Container)
        try:
            pane = panes.query_one(f"#{AUX_PANE['id']}", TerminalPane)
        except NoMatches:
            pane = TerminalPane(
                AUX_PANE["title"], AUX_PANE["command"], accent=AUX_PANE["accent"], id=AUX_PANE["id"]
            )
            await panes.mount(pane)
            self._activate(AUX_PANE["id"])
        else:
            if self._active_tab == AUX_PANE["id"]:
                pane.terminal.stop()
                await pane.remove()
                self._activate(DEFAULT_TAB)
            else:
                self._activate(AUX_PANE["id"])

    def action_new_pane(self) -> None:
        def handle_result(command: str | None) -> None:
            if not command:
                return
            panes = self.query_one("#panes", Container)
            index = len(panes.children) + 1
            pane_id = f"pane-extra-{index}"
            pane = TerminalPane(f"STATION {index}", command, accent="#cc6666", id=pane_id)
            panes.mount(pane)
            self._activate(pane_id)

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
