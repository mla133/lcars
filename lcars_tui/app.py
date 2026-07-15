"""LCARS-style one-screen TUI hosting PowerShell, GitHub Copilot CLI, and any
other console program side by side, Star-Trek-console fashion.
"""

from __future__ import annotations

import os
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

# Alternate full-app color palettes, swapped at runtime via Ctrl+T /
# LcarsApp.get_css_variables(). Every `$lcars-*` variable used throughout
# lcars.tcss is redefined here per theme, so toggling recolors the whole UI
# (sidebar, panes, status bars, elbows) in one shot -- no CSS class juggling
# needed. "tng" is the classic cool orange/lilac/blue console look; "ds9" is
# a warmer red/gold/amber station-console palette (Cardassian-influenced,
# no blue/lilac accents).
THEMES: dict[str, dict[str, str]] = {
    "tng": {
        "lcars-black": "#000000",
        "lcars-orange": "#ff9c00",
        "lcars-peach": "#ffcc99",
        "lcars-red": "#cc6666",
        "lcars-lilac": "#9999ff",
        "lcars-blue": "#99ccff",
        "lcars-yellow": "#ffcc00",
        "lcars-green": "#66cc66",
    },
    "ds9": {
        "lcars-black": "#000000",
        "lcars-orange": "#cc6633",
        "lcars-peach": "#ffcc66",
        "lcars-red": "#990000",
        "lcars-lilac": "#cc9933",
        "lcars-blue": "#cc8533",
        "lcars-yellow": "#ffcc00",
        "lcars-green": "#669966",
    },
}
THEME_ORDER = ("tng", "ds9")

from .widgets.pane import TerminalPane
from .widgets.terminal import Terminal

CSS_PATH = Path(__file__).parent / "lcars.tcss"
PROMPT_SCRIPT = Path(__file__).parent / "assets" / "lcars_prompt.ps1"

# Working directory panes' shells/CLIs are launched in. Defaults to whatever
# directory the process itself was started from (e.g. the exe's own folder
# when double-clicked from Explorer, which usually isn't where you want a
# PowerShell/Copilot session to open) -- set LCARS_START_DIR (e.g. in a
# desktop shortcut's "Target", or a wrapper script/profile) to override it,
# so the built exe can always open into the same project directory
# regardless of how or from where it's launched.
START_DIR = os.environ.get("LCARS_START_DIR") or None


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


class ChangeDirScreen(ModalScreen[str]):
    """Modal dialog asking for a directory to restart the focused pane in."""

    def __init__(self, current: str | None) -> None:
        super().__init__()
        self._current = current or ""

    def compose(self) -> ComposeResult:
        with Vertical(id="new-pane-dialog"):
            yield Static("CHANGE DIRECTORY FOR FOCUSED PANE")
            yield Input(
                value=self._current,
                placeholder=r"e.g. C:\Users\you\projects\thing",
                id="cwd-input",
            )
            with Horizontal():
                yield Button("GO", id="launch", variant="success")
                yield Button("CANCEL", id="cancel", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "launch":
            value = self.query_one("#cwd-input", Input).value.strip()
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
        ("ctrl+g", "change_dir", "Change dir of focused pane"),
        ("ctrl+t", "toggle_theme", "Toggle color theme"),
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, *args, **kwargs) -> None:
        self._theme_name = "tng"
        super().__init__(*args, **kwargs)
        self._active_tab: str | None = None

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables.update(THEMES[self._theme_name])
        return variables

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
                with Container(classes="btn-shell btn-lilac"):
                    yield Button("CD", id="change-dir")
                with Container(classes="btn-shell btn-yellow"):
                    yield Button("THEME", id="toggle-theme")
                with Container(classes="btn-shell btn-red"):
                    yield Button("QUIT", id="quit")
                yield Static("\u25c9", id="elbow-bottom")
            with Vertical(id="main"):
                yield Static(self.TITLE, id="topbar")
                with Container(id="panes"):
                    for spec in DEFAULT_PANES:
                        yield TerminalPane(
                            spec["title"],
                            spec["command"],
                            accent=spec["accent"],
                            cwd=START_DIR,
                            id=spec["id"],
                        )
                with Horizontal(id="bottombar"):
                    yield Static(id="cwd-bar")
                    yield Static(id="stardate-bar")

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
        self.query_one("#stardate-bar", Static).update(f"STARDATE {stamp}")
        self._update_cwd_bar()
        self._update_activity_indicator()

    def _update_cwd_bar(self) -> None:
        """Show the active pane's working directory in the bottom-left bar."""
        panes = self.query_one("#panes", Container)
        try:
            active = panes.query_one(f"#{self._active_tab}", TerminalPane)
        except NoMatches:
            self.query_one("#cwd-bar", Static).update("")
            return
        cwd = active.terminal.cwd or os.getcwd()
        self.query_one("#cwd-bar", Static).update(f"CWD: {cwd}")

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
        self._update_cwd_bar()

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
        elif button_id == "change-dir":
            self.action_change_dir()
        elif button_id == "toggle-theme":
            self.action_toggle_theme()
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
                AUX_PANE["title"],
                AUX_PANE["command"],
                accent=AUX_PANE["accent"],
                cwd=START_DIR,
                id=AUX_PANE["id"],
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
            pane = TerminalPane(
                f"STATION {index}", command, accent="#cc6666", cwd=START_DIR, id=pane_id
            )
            panes.mount(pane)
            self._activate(pane_id)

        self.push_screen(NewPaneScreen(), handle_result)

    def _focused_terminal(self) -> Terminal | None:
        """Return the Terminal that should be acted on by kill/restart/CD.

        Prefers the actually-focused widget if it's a Terminal, but falls
        back to the active tab's terminal -- pressing a sidebar Button (as
        opposed to a keybinding) moves focus to that Button, which would
        otherwise make these actions silently no-op.
        """
        if isinstance(self.focused, Terminal):
            return self.focused
        try:
            pane = self.query_one("#panes", Container).query_one(f"#{self._active_tab}", TerminalPane)
        except NoMatches:
            return None
        return pane.terminal

    def action_kill_pane(self) -> None:
        terminal = self._focused_terminal()
        if terminal is not None:
            terminal.stop()

    def action_restart_pane(self) -> None:
        terminal = self._focused_terminal()
        if terminal is not None:
            terminal.restart()

    def action_toggle_theme(self) -> None:
        index = THEME_ORDER.index(self._theme_name)
        self._theme_name = THEME_ORDER[(index + 1) % len(THEME_ORDER)]
        self.refresh_css(animate=False)

    def action_change_dir(self) -> None:
        terminal = self._focused_terminal()
        if terminal is None:
            return

        def handle_result(path: str | None) -> None:
            if not path:
                return
            expanded = os.path.expandvars(os.path.expanduser(path))
            if not os.path.isdir(expanded):
                self.bell()
                return
            terminal.restart(cwd=expanded)
            self._update_cwd_bar()

        self.push_screen(ChangeDirScreen(terminal.cwd), handle_result)

    def on_terminal_process_exited(self, message: Terminal.ProcessExited) -> None:
        message.terminal.refresh()


def main() -> None:
    LcarsApp().run()


if __name__ == "__main__":
    main()
