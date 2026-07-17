"""LCARS-style one-screen TUI hosting PowerShell, GitHub Copilot CLI, and any
other console program side by side, Star-Trek-console fashion.
"""

from __future__ import annotations

import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Static

# Compact single-line weather forecast (auto-located by wttr.in via IP)
# shown in the upper-left elbow block. Each theme gets its own wttr.in
# custom %-format string (https://wttr.in/:help) so the readout content,
# not just its color, matches the active console's personality: TNG shows
# the friendly icon+temp glimpse a Starfleet console would; DS9's is a
# station-ops temp+humidity reading; Klingon's is a terse ALL-CAPS
# text-only condition+temp (no cutesy emoji on a warship); Romulan's is a
# cold-empire temp+moon-phase reading. Units are pinned per theme regardless of
# the requester's locale: TNG and Klingon read out in Fahrenheit ("&u",
# USCS units) while DS9 and Romulan stay in Celsius ("&m", metric) so
# switching themes doesn't randomly change the temperature scale. A short
# per-request timeout means a dead/absent network just leaves the block
# blank instead of hanging the UI.
WEATHER_FORMATS: dict[str, str] = {
    "tng": "%c+%t",       # e.g. "\u2601\ufe0f +57\u00b0F"
    "ds9": "%t+%h",       # e.g. "+14\u00b0C 62%"
    "klingon": "%C+%t",   # e.g. "CLOUDY +57\u00b0F" (uppercased below)
    "romulan": "%t+%m",   # e.g. "+14\u00b0C \U0001f314"
}

# Per-theme wttr.in unit flag: Fahrenheit ("&u") for TNG/Klingon, Celsius
# ("&m") for everyone else.
WEATHER_UNITS: dict[str, str] = {
    "tng": "u",
    "ds9": "m",
    "klingon": "u",
    "romulan": "m",
}
WEATHER_REFRESH_SECS = 900

# Alternate full-app color palettes, swapped at runtime via Ctrl+T /
# LcarsApp.get_css_variables(). Every `$lcars-*` variable used throughout
# lcars.tcss is redefined here per theme, so toggling recolors the whole UI
# (sidebar, panes, status bars, elbows) in one shot -- no CSS class juggling
# needed. "tng" is the classic cool orange/lilac/blue console look; "ds9" is
# a warmer red/gold/amber station-console palette (Cardassian-influenced,
# no blue/lilac accents); "klingon" is a hot red/orange/gold warship-console
# palette (Qapla'!) with no blue/lilac/green accents at all -- those slots
# are remapped onto the red/orange/gold family so nothing stays "cool";
# "romulan" is a cold green/yellow Star Empire console palette -- no red/
# orange/blue accents, those slots are remapped onto the green/yellow family.
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
    "klingon": {
        "lcars-black": "#000000",
        "lcars-orange": "#ff6600",
        "lcars-peach": "#ffb347",
        "lcars-red": "#990000",
        "lcars-lilac": "#cc0000",
        "lcars-blue": "#ff9c00",
        "lcars-yellow": "#ffd700",
        "lcars-green": "#e6a817",
    },
    "romulan": {
        "lcars-black": "#000000",
        "lcars-orange": "#8fbc4d",
        "lcars-peach": "#c5e17a",
        "lcars-red": "#4a7a2a",
        "lcars-lilac": "#7fbf3f",
        "lcars-blue": "#6b8e23",
        "lcars-yellow": "#d4d900",
        "lcars-green": "#33691e",
    },
}
THEME_ORDER = ("tng", "ds9", "klingon", "romulan")

from .widgets.pane import TerminalPane
from .widgets.terminal import Terminal

CSS_PATH = Path(__file__).parent / "lcars.tcss"
PROMPT_SCRIPT = Path(__file__).parent / "assets" / "lcars_prompt.ps1"

def _env_file_path() -> Path:
    """Location of the optional .env file used to remember settings (e.g.
    LCARS_START_DIR) across launches -- next to lcars.exe in a frozen
    (PyInstaller) build, or the repo root when running from source."""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent.parent
    return base / ".env"


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file, ignoring blank lines and lines
    starting with '#'. Missing/unreadable files just yield an empty dict."""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def _write_env_var(path: Path, key: str, value: str) -> None:
    """Set ``key=value`` in the .env file at ``path``, preserving any other
    lines already there (comments, other vars) and updating in place if
    ``key`` is already present rather than appending a duplicate."""
    lines: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} "):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


# Working directory panes' shells/CLIs are launched in. Defaults to whatever
# directory the process itself was started from (e.g. the exe's own folder
# when double-clicked from Explorer, which usually isn't where you want a
# PowerShell/Copilot session to open). Resolution order: the LCARS_START_DIR
# environment variable (e.g. set in a desktop shortcut's "Target", or a
# wrapper script/profile), then LCARS_START_DIR in a .env file next to the
# app (see _env_file_path()), then -- if neither is set -- a startup dialog
# prompts for it (see LcarsApp._prompt_start_dir) and offers to save the
# answer to .env for future launches.
START_DIR = os.environ.get("LCARS_START_DIR") or _read_env_file(_env_file_path()).get("LCARS_START_DIR") or None


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


# Default stations. Edit / extend freely. "accent" names a slot in THEMES
# ("lcars-<accent>") rather than a literal color, so each pane's border/
# header is resolved from -- and re-tinted to follow -- whichever palette
# is currently active instead of staying stuck on TNG colors after Ctrl+T.
DEFAULT_PANES = [
    dict(id="pane-copilot", title="GITHUB COPILOT", command="powershell.exe -NoLogo -Command copilot", accent="lilac"),
    dict(id="pane-pwsh", title="POWERSHELL", command=_pwsh("PWSH", "Gray"), accent="orange"),
]
DEFAULT_TAB = DEFAULT_PANES[0]["id"]

# The auxiliary station: hidden by default, toggled on/off via the AUX button.
AUX_PANE = dict(id="pane-shell", title="AUX TERMINAL", command=_pwsh("AUX", "DarkGray"), accent="blue")

# Every toggleable sidebar button, grouped "top" (above the spacer) or
# "bottom" (below it) so compose() can rebuild the same layout while letting
# SidebarConfigScreen show/hide any of them individually. "label" is a
# static button caption; entries whose caption changes at runtime (THEME)
# use "config_label" for what the config dialog shows instead. The CFG
# button itself (which opens that dialog) is deliberately left out of this
# list -- it's always visible, otherwise a user could hide their only way
# back in.
SIDEBAR_BUTTONS = [
    dict(id="tab-pane-copilot", group="top", label="COPILOT", accent="lilac"),
    dict(id="tab-pane-pwsh", group="top", label="PWSH", accent="orange"),
    dict(id="toggle-aux", group="top", label="AUX", accent="blue"),
    dict(id="new-pane", group="bottom", label="RUN", accent="green"),
    dict(id="kill-pane", group="bottom", label="KILL", accent="orange"),
    dict(id="change-dir", group="bottom", label="CWD", accent="lilac"),
    dict(id="toggle-theme", group="bottom", label=None, config_label="THEME", accent="yellow"),
    dict(id="show-help", group="bottom-2", label="HELP", accent="blue"),
    dict(id="quit", group="bottom-2", label="QUIT", accent="red"),
]

# These sidebar buttons stay hidden on launch; the user opts into them via
# the sidebar config dialog (Ctrl+B / CFG button) once they actually want an
# AUX terminal or ad-hoc stations.
STARTUP_HIDDEN_BUTTONS = {"toggle-aux", "new-pane", "kill-pane"}


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

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


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

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class StartupDirScreen(ModalScreen[tuple[str, bool] | None]):
    """Modal shown on launch when LCARS_START_DIR isn't set anywhere (not in
    the environment or .env), asking which directory panes should start in
    and offering to remember the answer in .env for future launches."""

    def compose(self) -> ComposeResult:
        with Vertical(id="new-pane-dialog"):
            yield Static("SET STARTING WORKING DIRECTORY")
            yield Input(
                value=os.getcwd(),
                placeholder=r"e.g. C:\Users\you\projects\thing",
                id="cwd-input",
            )
            yield Checkbox("Remember for future launches (save to .env)", value=True, id="save-cwd")
            with Horizontal():
                yield Button("START", id="launch", variant="success")
                yield Button("SKIP", id="cancel", variant="error")

    def _result(self) -> tuple[str, bool]:
        value = self.query_one("#cwd-input", Input).value.strip() or os.getcwd()
        save = self.query_one("#save-cwd", Checkbox).value
        return value, save

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "launch":
            self.dismiss(self._result())
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(self._result())

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    """Modal listing every keybinding, global and per-pane."""

    HELP_TEXT = """\
GLOBAL
  Ctrl+1 / Ctrl+2            Switch to Copilot / PowerShell tab
  Ctrl+3                     Toggle the AUX terminal
  Ctrl+N                     Open a new station (prompts for a command)
  Ctrl+K                     Kill the focused pane's process
  Ctrl+R                     Restart the focused pane's process
  Ctrl+G                     Change directory of the focused pane
  Ctrl+T                     Toggle color theme (TNG / DS9 / Klingon / Romulan)
  Ctrl+B                     Show/hide individual sidebar buttons
  F1                         Show this help
  Ctrl+Q                     Quit

STATIONS (AUX / extra "STATION N" panes)
  Click the \u2715 in a station's header, or toggle AUX off,
  to close it and stop its process.

INSIDE A TERMINAL PANE
  Ctrl+F                     Search scrollback (Enter again: previous match)
  Shift+PageUp / PageDown    Scroll scrollback by a page
  Ctrl+Home / Ctrl+End       Jump to top / bottom of scrollback
  Mouse wheel                Scroll scrollback
  Click + drag, then Ctrl+C  Select text, then copy it
  Ctrl+V (into this console) Paste into the focused pane
  Typing any key             Snaps back to the live view
"""

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Static("KEYBOARD SHORTCUTS", classes="help-title")
            yield Static(self.HELP_TEXT)
            yield Button("CLOSE", id="close", variant="success")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        self.dismiss(None)


class SidebarConfigScreen(ModalScreen[set[str] | None]):
    """Modal letting the user show/hide any sidebar button individually.

    Returns the new set of hidden button ids on save, or None on cancel.
    """

    def __init__(self, hidden: set[str]) -> None:
        super().__init__()
        self._hidden = hidden

    def compose(self) -> ComposeResult:
        with Vertical(id="sidebar-config-dialog"):
            yield Static("SIDEBAR BUTTONS", classes="config-title")
            # Checkboxes live in their own scrollable region so the SAVE/CANCEL
            # row below always stays reachable even on a short terminal where
            # the full list wouldn't otherwise fit within max-height.
            with VerticalScroll(id="sidebar-config-checkboxes"):
                for spec in SIDEBAR_BUTTONS:
                    label = spec.get("config_label") or spec["label"]
                    yield Checkbox(
                        label,
                        value=spec["id"] not in self._hidden,
                        id=f"cfg-{spec['id']}",
                    )
            with Horizontal():
                yield Button("SAVE", id="save", variant="success")
                yield Button("CANCEL", id="cancel", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            hidden = {
                spec["id"]
                for spec in SIDEBAR_BUTTONS
                if not self.query_one(f"#cfg-{spec['id']}", Checkbox).value
            }
            self.dismiss(hidden)
        else:
            self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        # Escape always cancels, regardless of which checkbox/button has
        # focus -- previously there was no keyboard way to close this dialog
        # if the mouse couldn't reach the SAVE/CANCEL row.
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


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
        ("ctrl+b", "show_sidebar_config", "Sidebar buttons"),
        ("f1", "show_help", "Help"),
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, *args, **kwargs) -> None:
        self._theme_name = "tng"
        super().__init__(*args, **kwargs)
        self._active_tab: str | None = None
        # Monotonically increasing so ids stay unique even after closing
        # some "STATION N" panes -- reusing len(children) here would risk
        # colliding with an existing pane's id.
        self._extra_pane_count = 0
        # ids of SIDEBAR_BUTTONS entries currently hidden by the user via
        # the sidebar config dialog (Ctrl+B / CFG button). AUX/NEW/KILL start
        # hidden on launch; the user opts them back in via that dialog.
        self._hidden_sidebar_buttons: set[str] = set(STARTUP_HIDDEN_BUTTONS)

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables.update(THEMES[self._theme_name])
        return variables

    def _theme_color(self, accent_key: str) -> str:
        """Resolve a semantic accent slot (e.g. "lilac") to its hex value
        in the currently active theme."""
        return THEMES[self._theme_name][f"lcars-{accent_key}"]

    def _refresh_pane_colors(self) -> None:
        """Re-tint every pane's border/header to the current theme's take
        on its accent slot -- called whenever the theme changes so panes
        don't stay stuck on whatever palette was active when created."""
        for pane in self.query_one("#panes", Container).query(TerminalPane):
            if pane.accent_key:
                pane.set_accent_color(self._theme_color(pane.accent_key))

    def _refresh_titles(self) -> None:
        """Re-render the topbar and every pane's header title (kept in sync
        after panes are added/removed or the theme changes)."""
        try:
            self.query_one("#topbar", Static).update(self.TITLE)
        except NoMatches:
            pass
        for pane in self.query_one("#panes", Container).query(TerminalPane):
            pane.set_displayed_title(pane.title_text)

    def _refresh_theme_button(self) -> None:
        """Show the active theme's name on the THEME button instead of a
        static label, so the button itself reflects current state."""
        try:
            self.query_one("#toggle-theme", Button).label = self._theme_name.upper()
        except NoMatches:
            pass

    def _apply_sidebar_visibility(self) -> None:
        """Hide/show each sidebar button's wrapping row per the user's
        choices in SidebarConfigScreen (see self._hidden_sidebar_buttons)."""
        for spec in SIDEBAR_BUTTONS:
            try:
                row = self.query_one(f"#row-{spec['id']}", Container)
            except NoMatches:
                continue
            row.display = spec["id"] not in self._hidden_sidebar_buttons

    def action_show_sidebar_config(self) -> None:
        def handle_result(hidden: set[str] | None) -> None:
            if hidden is None:
                return
            self._hidden_sidebar_buttons = hidden
            self._apply_sidebar_visibility()

        self.push_screen(SidebarConfigScreen(self._hidden_sidebar_buttons), handle_result)

    def compose(self) -> ComposeResult:
        with Horizontal(id="root"):
            with Vertical(id="sidebar"):
                yield Static(id="elbow-top")
                for spec in SIDEBAR_BUTTONS:
                    if spec["group"] != "top":
                        continue
                    label = spec["label"] or self._theme_name.upper()
                    with Container(
                        classes=f"btn-shell btn-{spec['accent']}",
                        id=f"row-{spec['id']}",
                    ) as row:
                        row.display = spec["id"] not in self._hidden_sidebar_buttons
                        yield Button(label, id=spec["id"])
                yield Static(id="sidebar-spacer")
                for spec in SIDEBAR_BUTTONS:
                    if spec["group"] != "bottom":
                        continue
                    label = spec["label"] or self._theme_name.upper()
                    with Container(
                        classes=f"btn-shell btn-{spec['accent']}",
                        id=f"row-{spec['id']}",
                    ) as row:
                        row.display = spec["id"] not in self._hidden_sidebar_buttons
                        yield Button(label, id=spec["id"])
                with Container(classes="btn-shell btn-green", id="row-show-sidebar-config"):
                    yield Button("CFG", id="show-sidebar-config")
                for spec in SIDEBAR_BUTTONS:
                    if spec["group"] != "bottom-2":
                        continue
                    label = spec["label"] or self._theme_name.upper()
                    with Container(
                        classes=f"btn-shell btn-{spec['accent']}",
                        id=f"row-{spec['id']}",
                    ) as row:
                        row.display = spec["id"] not in self._hidden_sidebar_buttons
                        yield Button(label, id=spec["id"])
                yield Static("\u25c9", id="elbow-bottom")
            with Vertical(id="main"):
                yield Static(self.TITLE, id="topbar")
                with Container(id="panes"):
                    for spec in DEFAULT_PANES:
                        yield TerminalPane(
                            spec["title"],
                            spec["command"],
                            accent=self._theme_color(spec["accent"]),
                            accent_key=spec["accent"],
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
        self._refresh_titles()
        self._apply_sidebar_visibility()
        self.set_interval(WEATHER_REFRESH_SECS, self._fetch_weather)
        self._fetch_weather()
        if not START_DIR:
            self._prompt_start_dir()

    def _prompt_start_dir(self) -> None:
        """Ask for a starting working directory when none was resolved from
        the environment or .env (see START_DIR above), then restart the
        already-mounted default panes' terminals in it."""

        def handle_result(result: tuple[str, bool] | None) -> None:
            if not result:
                return
            path, save = result
            expanded = os.path.expandvars(os.path.expanduser(path))
            if not os.path.isdir(expanded):
                self.bell()
                return
            self._apply_start_dir(expanded)
            if save:
                _write_env_var(_env_file_path(), "LCARS_START_DIR", expanded)

        self.push_screen(StartupDirScreen(), handle_result)

    def _apply_start_dir(self, path: str) -> None:
        """Set the resolved starting directory and restart every currently
        mounted pane's terminal in it (mirrors Ctrl+G's per-pane restart);
        panes created afterwards (AUX, Ctrl+N stations) pick it up too since
        they read the module-level START_DIR when launched."""
        global START_DIR
        START_DIR = path
        os.environ["LCARS_START_DIR"] = path
        for pane in self.query_one("#panes", Container).query(TerminalPane):
            pane.terminal.restart(cwd=path)
        self._update_cwd_bar()

    @work(thread=True, exclusive=True)
    def _fetch_weather(self) -> None:
        """Fetch a compact local forecast from wttr.in for the upper-left
        elbow block, using the current theme's custom format string (see
        WEATHER_FORMATS). Runs off the UI thread; any failure (no network,
        DNS, timeout, etc.) just blanks the block rather than raising."""
        fmt = WEATHER_FORMATS[self._theme_name]
        units = WEATHER_UNITS[self._theme_name]
        url = f"https://wttr.in/?format={fmt}&{units}"
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                text = resp.read().decode("utf-8", "replace").strip()
        except Exception:
            text = ""
        if self._theme_name == "klingon":
            text = text.upper()
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
        elif button_id == "show-help":
            self.action_show_help()
        elif button_id == "show-sidebar-config":
            self.action_show_sidebar_config()
        elif button_id == "toggle-aux":
            await self.action_toggle_aux()
        elif button_id.startswith("close-"):
            await self._close_pane(button_id.removeprefix("close-"))
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
                accent=self._theme_color(AUX_PANE["accent"]),
                accent_key=AUX_PANE["accent"],
                cwd=START_DIR,
                id=AUX_PANE["id"],
                closable=True,
            )
            await panes.mount(pane)
            pane.set_displayed_title(pane.title_text)
            self._activate(AUX_PANE["id"])
        else:
            if self._active_tab == AUX_PANE["id"]:
                await self._close_pane(AUX_PANE["id"])
            else:
                self._activate(AUX_PANE["id"])

    async def _close_pane(self, pane_id: str) -> None:
        """Stop and unmount a closable pane (AUX or an extra "STATION N").

        The two default stations (Copilot / PowerShell) are never closable
        -- this just bells rather than doing anything if asked to close one.
        """
        panes = self.query_one("#panes", Container)
        try:
            pane = panes.query_one(f"#{pane_id}", TerminalPane)
        except NoMatches:
            return
        if not pane.closable:
            self.bell()
            return
        pane.terminal.stop()
        await pane.remove()
        if self._active_tab == pane_id:
            self._activate(DEFAULT_TAB)

    def action_new_pane(self) -> None:
        async def handle_result(command: str | None) -> None:
            if not command:
                return
            self._extra_pane_count += 1
            index = self._extra_pane_count
            panes = self.query_one("#panes", Container)
            pane_id = f"pane-extra-{index}"
            pane = TerminalPane(
                f"STATION {index}", command, accent=self._theme_color("red"), accent_key="red",
                cwd=START_DIR, id=pane_id, closable=True
            )
            await panes.mount(pane)
            pane.set_displayed_title(pane.title_text)
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
        self._refresh_titles()
        self._refresh_pane_colors()
        self._refresh_theme_button()
        # Each theme has its own wttr.in format (see WEATHER_FORMATS), so
        # re-fetch immediately instead of waiting for the next scheduled
        # WEATHER_REFRESH_SECS tick to pick it up.
        self._fetch_weather()

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())

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
