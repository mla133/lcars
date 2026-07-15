# LCARS Terminal Interface

A one-screen, LCARS (Star Trek console) styled TUI that hosts several real,
interactive terminal programs side by side: PowerShell, the GitHub Copilot
CLI, and any other console command you like.

Built with [Textual](https://github.com/Textualize/textual). Each pane is a
genuine Windows console session created via ConPTY (`pywinpty`) and rendered
with a terminal-emulator state machine (`pyte`), so colors, cursor movement,
and fully interactive programs work as expected.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\pip.exe install --no-index --find-links <your-wheelhouse> -r requirements.txt
```

## Run

```powershell
.\.venv\Scripts\python.exe -m lcars_tui
```

## Usage

- Sidebar buttons (`PWSH`, `COPILOT`, `AUX`) jump focus to a station.
- `Ctrl+N` — open a dialog to launch a new pane running any command.
- `Ctrl+K` — kill the focused pane's process.
- `Ctrl+R` — restart the focused pane's process.
- `Ctrl+Q` — quit.
- Click into any pane and type normally — keystrokes are forwarded to the
  real console process running inside it.

## Customizing stations

Edit `DEFAULT_PANES` in `lcars_tui/app.py` to change the default set of
panes, their titles, accent colors, and the command each one launches (e.g.
swap `copilot` for another CLI tool, or add `wsl.exe`, `ssh`, etc.).
