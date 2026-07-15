# LCARS Terminal Interface

A one-screen, LCARS (Star Trek console) styled TUI built with [Textual](https://github.com/Textualize/textual)
that hosts several real, interactive terminal programs (PowerShell, GitHub Copilot CLI, etc.) as
full-size tabs. This is a Windows-only project.

## Setup / Run

There is no lockfile-based install step; dependencies come from a local wheelhouse (no PyPI index):

```powershell
python -m venv .venv
.\.venv\Scripts\pip.exe install --no-index --find-links <your-wheelhouse> -r requirements.txt
.\.venv\Scripts\python.exe -m lcars_tui
```

There are no tests, linters, or CI configured in this repo. Validate changes by running the app
(`python -m lcars_tui`) and exercising the affected pane/keybinding manually.

## Architecture

- `lcars_tui/app.py` — `LcarsApp` (Textual `App`). Owns tab switching, global keybindings
  (`BINDINGS`), and the sidebar. Panes are defined declaratively in `DEFAULT_PANES` / `AUX_PANE`
  as dicts with `id`, `title`, `command`, `accent` — add or edit stations here rather than in
  `compose()`. Only one `TerminalPane` is `display`-visible at a time; the rest keep running in
  the background (`_activate` toggles `.display`), so switching tabs never restarts a process.
  `Ctrl+N` opens `NewPaneScreen` (a modal `Input` dialog) to mount an ad-hoc extra pane
  (`pane-extra-N`) at runtime; `Ctrl+K` / `Ctrl+R` stop/restart the currently *focused* pane's
  `Terminal` (found via `self.focused`, not the active tab id).
- `lcars_tui/widgets/pane.py` — `TerminalPane`: a colored `PaneHeader` bar + a `Terminal` widget.
  Thin composition layer; no process/terminal logic lives here.
- `lcars_tui/widgets/terminal.py` — `Terminal`: the actual ConPTY-backed terminal emulator widget.
  This is the core of the app:
  - A real Windows console process is spawned via `pywinpty` (`PtyProcess`).
  - A background reader thread pushes raw output bytes into a `Queue`; a 30Hz `set_interval`
    timer (`_drain_queue`, main thread) drains the queue and feeds it into a `pyte.Stream`/`pyte.Screen`
    (terminal emulator state machine), then calls `self.refresh()`.
  - `render()` converts the pyte screen buffer into Rich `Text`, tracking `dirty` rows plus the
    cursor row (pyte doesn't always mark bare cursor moves dirty) to redraw correctly.
  - Keyboard input: `on_key` maps Textual key names to raw bytes via `_KEY_MAP` and writes them to
    the pty. Keys in `RESERVED_APP_KEYS` (`ctrl+1`/`2`/`3`/`4`/`q`) are *not* forwarded to the
    child process — they're left to bubble up to `LcarsApp.BINDINGS` for tab switching/quitting.
    When adding new global app keybindings that should work while a terminal has focus, add them
    to `RESERVED_APP_KEYS` too, or they'll be swallowed by the shell instead (`ctrl+n`/`k`/`r` are
    included alongside `ctrl+1`/`2`/`3`/`4`/`q`).
  - `pyte`/`pywinpty` imports are wrapped in `try/except ImportError` so the widget still loads
    (showing a "Terminal backend unavailable" message) if those Windows-only deps are missing.
- `lcars_tui/assets/lcars_prompt.ps1` — custom compact PowerShell prompt used for PowerShell/Aux
  panes instead of the user's normal profile (panes launch with `-NoProfile -NoExit`). Takes
  `-Label`/`-Accent` params built in `app.py`'s `_pwsh()` helper.
- `lcars_tui/lcars.tcss` — Textual CSS for the LCARS look (colors, borders, sidebar layout).
- `lcars.spec` / `build.ps1` — PyInstaller spec and build script producing a portable, onedir
  `dist\lcars\lcars.exe` that runs without Python installed on the target machine. Any new
  non-`.py` asset added under `lcars_tui/` must be added to the `datas` list in `lcars.spec`, or
  the frozen build won't find it (PyInstaller only auto-bundles `.py` files).

## Conventions

- Commands for panes are passed as `list[str]` argv (not a single shell string) whenever the
  command path may contain spaces (see `_pwsh()`), since the pty layer would otherwise re-split/
  re-quote a plain string using shell-style parsing.
- New pane/process-affecting exceptions around pty I/O (`proc.write`, `proc.terminate`, etc.) are
  broadly caught and swallowed — the pty layer can raise various OS-level errors on a dead/dying
  process, and killing the whole app over it is undesirable.
