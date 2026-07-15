# LCARS Terminal Interface

A one-screen, LCARS (Star Trek console) styled TUI that hosts several real,
interactive terminal programs as full-size tabs: PowerShell, the GitHub
Copilot CLI, and any other console command you like.

Built with [Textual](https://github.com/Textualize/textual). Each pane is a
genuine Windows console session created via ConPTY (`pywinpty`) and rendered
with a terminal-emulator state machine (`pyte`), so colors, cursor movement,
and fully interactive programs work as expected. Only one pane is shown at a
time (like browser tabs) so each station gets the full screen — the others
keep running in the background and are instant to switch back to.

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

- Sidebar buttons `COPILOT` / `PWSH` switch the visible tab; `AUX` opens a
  third, hidden-by-default terminal tab, or closes it again if it's already
  showing.
- `Ctrl+1` / `Ctrl+2` / `Ctrl+3` — switch tabs from the keyboard (Copilot /
  PowerShell / Aux), even while a terminal has focus. These keys are
  reserved and never forwarded to the shell running inside a pane.
- `Ctrl+N` — open a dialog to launch a new pane running any command (it
  becomes its own tab).
- `Ctrl+K` — kill the focused pane's process.
- `Ctrl+R` — restart the focused pane's process.
- `Ctrl+Q` — quit (also available via the `QUIT` sidebar button).
- Click into any pane and type normally — keystrokes are forwarded to the
  real console process running inside it.

## LCARS prompt

The PowerShell and Aux panes launch with `-NoProfile` and a small custom
prompt (`lcars_tui/assets/lcars_prompt.ps1`) instead of your normal profile
(Starship, Oh My Posh, etc.), so they stay compact and match the console
theme. Edit that script to change the prompt's look, or pass a different
`-Accent`/`-Label` when building the command in `lcars_tui/app.py`.

## Customizing stations

Edit `DEFAULT_PANES` (and `AUX_PANE`) in `lcars_tui/app.py` to change the
default set of panes, their titles, accent colors, and the command each one
launches (e.g. swap `copilot` for another CLI tool, or add `wsl.exe`, `ssh`,
etc.).
