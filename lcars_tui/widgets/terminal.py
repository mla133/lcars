"""Textual widget hosting a real interactive Windows console program.

Uses ConPTY (via ``pywinpty``) so that programs which need a real terminal
(PowerShell, the GitHub Copilot CLI, vim, etc.) run and render correctly,
including colors, cursor movement, and interactive line editing.

Terminal output is parsed with ``pyte``, a terminal-emulator state machine,
and the resulting screen buffer is rendered as Rich text each frame.
"""

from __future__ import annotations

import threading
import time
from queue import Empty, Queue
from typing import Optional

from rich.style import Style
from rich.text import Text
from textual import events
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Input, Static

try:
    import pyte
except ImportError:  # pragma: no cover - surfaced at runtime via placeholder UI
    pyte = None

try:
    from winpty import PtyProcess
except ImportError:  # pragma: no cover - surfaced at runtime via placeholder UI
    PtyProcess = None

# How many off-screen lines pyte keeps around for scrollback (see
# HistoryScreen usage in Terminal.__init__). Only the top queue is ever
# used here -- see the scrollback section below.
SCROLLBACK_LINES = 5000

# Number of lines a single mouse wheel "tick" scrolls.
WHEEL_SCROLL_LINES = 3


# Textual key name -> bytes to send to the pty for keys that don't produce a
# plain printable character.
_KEY_MAP = {
    "enter": "\r",
    "return": "\r",
    "tab": "\t",
    "shift+tab": "\x1b[Z",
    "backspace": "\x7f",
    "escape": "\x1b",
    "up": "\x1b[A",
    "down": "\x1b[B",
    "right": "\x1b[C",
    "left": "\x1b[D",
    "home": "\x1b[H",
    "end": "\x1b[F",
    "pageup": "\x1b[5~",
    "pagedown": "\x1b[6~",
    "delete": "\x1b[3~",
    "insert": "\x1b[2~",
    "f1": "\x1bOP",
    "f2": "\x1bOQ",
    "f3": "\x1bOR",
    "f4": "\x1bOS",
    "f5": "\x1b[15~",
    "f6": "\x1b[17~",
    "f7": "\x1b[18~",
    "f8": "\x1b[19~",
    "f9": "\x1b[20~",
    "f10": "\x1b[21~",
    "f11": "\x1b[23~",
    "f12": "\x1b[24~",
    "ctrl+a": "\x01",
    "ctrl+b": "\x02",
    "ctrl+c": "\x03",
    "ctrl+d": "\x04",
    "ctrl+e": "\x05",
    "ctrl+k": "\x0b",
    "ctrl+l": "\x0c",
    "ctrl+r": "\x12",
    "ctrl+u": "\x15",
    "ctrl+w": "\x17",
    "ctrl+z": "\x1a",
    "ctrl+underscore": "\x1f",
    "ctrl+space": "\x00",
}

# Keys reserved for switching between panes / quitting the app. These are
# *not* forwarded to the child process, so they bubble up to the App's
# BINDINGS instead. Kept to an uncommon, small set so we don't clobber
# shell/editor keybindings (e.g. Ctrl+R for PSReadLine reverse search).
RESERVED_APP_KEYS = frozenset(
    {
        "ctrl+1",
        "ctrl+2",
        "ctrl+3",
        "ctrl+4",
        "ctrl+q",
        "ctrl+n",
        "ctrl+k",
        "ctrl+r",
        "ctrl+g",
        "ctrl+t",
        "f1",
    }
)

# Keys handled directly by the Terminal itself (scrollback, search, copy)
# rather than being written to the child process. Also not sent to
# RESERVED_APP_KEYS since they never need to bubble up to the App.
_SCROLL_KEYS = {"shift+pageup", "shift+pagedown", "ctrl+home", "ctrl+end"}


class TerminalSearchScreen(ModalScreen[str]):
    """Modal dialog asking for text to search for in a pane's scrollback."""

    def __init__(self, initial: str = "") -> None:
        super().__init__()
        self._initial = initial

    def compose(self):
        with Vertical(id="new-pane-dialog"):
            yield Static("SEARCH SCROLLBACK (Enter again: previous match)")
            yield Input(value=self._initial, placeholder="text to find", id="search-input")
            with Horizontal():
                yield Button("FIND", id="find", variant="success")
                yield Button("CANCEL", id="cancel", variant="error")

    def on_mount(self) -> None:
        self.query_one("#search-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "find":
            self.dismiss(self.query_one("#search-input", Input).value.strip() or None)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)


class Terminal(Widget, can_focus=True):
    """A live, interactive terminal pane backed by a Windows ConPTY process."""

    DEFAULT_CSS = """
    Terminal {
        width: 1fr;
        height: 1fr;
        background: #000000;
        color: #e6e6e6;
        /* Same thickness whether focused or not (just an invisible,
           background-matched color when unfocused) so the content box
           size - and thus the pty's row/col count - never changes when
           focus toggles. Textual doesn't emit a Resize event for a
           focus-only border change, so if the thickness differed the
           pty would keep the stale (larger) size and the bottom rows
           (e.g. a full-screen program's status line) would be clipped. */
        border: heavy #000000;
    }
    Terminal:focus {
        border: heavy #ff9c00;
    }
    """

    class ProcessExited(Message):
        """Posted when the underlying process ends."""

        def __init__(self, terminal: "Terminal") -> None:
            self.terminal = terminal
            super().__init__()

    def __init__(
        self,
        command: str | list[str],
        *,
        cwd: Optional[str] = None,
        name: Optional[str] = None,
        id: Optional[str] = None,  # noqa: A002
        classes: Optional[str] = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.command = command
        self.cwd = cwd
        self._proc: Optional["PtyProcess"] = None
        # HistoryScreen (rather than plain Screen) transparently keeps every
        # line that scrolls off the top of the visible area in
        # ``screen.history.top`` -- see the "scrollback" section below for
        # how that's read back out. ``ratio`` is irrelevant here since we
        # never call the built-in prev_page/next_page paging.
        self._screen = pyte.HistoryScreen(80, 24, history=SCROLLBACK_LINES, ratio=1.0) if pyte else None
        self._stream = pyte.Stream(self._screen) if pyte else None
        self._reader_thread: Optional[threading.Thread] = None
        self._out_queue: "Queue[bytes]" = Queue()
        self._stopped = threading.Event()
        self._missing_deps = pyte is None or PtyProcess is None
        self._line_cache: list[Text] = []
        self._prev_cursor_y = -1
        self._last_output_at = 0.0

        # -- scrollback state ------------------------------------------
        # Number of lines "back" from the live bottom currently shown; 0
        # means the live screen (normal mode). See _render_scrollback().
        self._scroll_offset = 0

        # -- search state ------------------------------------------------
        self._search_term: Optional[str] = None
        self._search_matches: list[int] = []
        self._search_match_idx: int = -1

    # -- lifecycle -----------------------------------------------------
    def on_mount(self) -> None:
        if self._missing_deps:
            return
        self.start()
        self.set_interval(1 / 30, self._drain_queue)

    def on_unmount(self) -> None:
        self.stop()

    def start(self) -> None:
        if self._proc is not None or self._missing_deps:
            return
        cols = max(self.size.width, 2)
        rows = max(self.size.height, 2)
        self._screen.resize(rows, cols)
        self._line_cache = [Text()] * rows
        self._screen.dirty.update(range(rows))
        self._proc = PtyProcess.spawn(
            self.command, dimensions=(rows, cols), cwd=self.cwd
        )
        self._stopped.clear()
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def stop(self) -> None:
        self._stopped.set()
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.terminate(force=True)
            except Exception:
                pass

    def restart(self, *, cwd: Optional[str] = None) -> None:
        """Stop the current child process and start a fresh one.

        If ``cwd`` is given, the pane's working directory is updated first,
        so the new process launches there (used by the in-app "change
        directory" dialog); otherwise it keeps whatever directory it had.
        """
        if cwd is not None:
            self.cwd = cwd
        self.stop()
        if self._screen is not None:
            self._screen.reset()
        self._reset_scroll_and_search()
        self.start()

    def _reset_scroll_and_search(self) -> None:
        self._scroll_offset = 0
        self._search_term = None
        self._search_matches = []
        self._search_match_idx = -1

    # -- pty plumbing ----------------------------------------------------
    def _read_loop(self) -> None:
        proc = self._proc
        while proc is not None and not self._stopped.is_set():
            try:
                data = proc.read(4096)
            except EOFError:
                break
            except Exception:
                break
            if not data:
                break
            self._out_queue.put(data)
        if not self._stopped.is_set():
            self.app.call_from_thread(self.post_message, self.ProcessExited(self))

    def _drain_queue(self) -> None:
        updated = False
        try:
            while True:
                data = self._out_queue.get_nowait()
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="replace")
                try:
                    self._stream.feed(data)
                except Exception:
                    # pyte doesn't understand every exotic escape sequence a
                    # given program may emit; skip the offending chunk rather
                    # than tearing down the whole pane. pyte resets its
                    # internal parser state after an error, so this is safe.
                    pass
                updated = True
        except Empty:
            pass
        if updated:
            self._last_output_at = time.monotonic()
            self.refresh()

    def is_active(self, window: float = 1.5) -> bool:
        """True if the child process has produced output within ``window``
        seconds. Used as a cheap "is this pane doing something" proxy so a
        background pane (e.g. Copilot) can be flagged as busy while another
        tab is in view -- most CLI programs sit silent while waiting on
        input and only stream output while actively working.
        """
        return self._proc is not None and (time.monotonic() - self._last_output_at) < window

    # -- input -------------------------------------------------------------
    async def on_key(self, event: events.Key) -> None:
        if event.key in RESERVED_APP_KEYS:
            # Let this bubble up to the App so global bindings (tab
            # switching, quit, etc.) work even while a terminal has focus.
            return
        if event.key == "ctrl+f":
            event.stop()
            self._open_search()
            return
        if event.key in _SCROLL_KEYS:
            event.stop()
            self._handle_scroll_key(event.key)
            return
        if self._proc is None:
            return
        event.stop()
        # Any real input to the child resumes following live output, like a
        # normal terminal snapping back to the bottom when you start typing.
        if self._scroll_offset:
            self._set_scroll(0)
        data = _KEY_MAP.get(event.key)
        if data is None:
            data = event.character or ""
        if data:
            try:
                self._proc.write(data)
            except Exception:
                pass

    def on_paste(self, event: events.Paste) -> None:
        """Write pasted text straight into the child process.

        Textual receives this from the *outer* terminal's bracketed-paste
        support when the user pastes (e.g. Ctrl+V) into the console window
        actually hosting this app -- no clipboard access of our own needed.
        """
        if self._proc is None:
            return
        event.stop()
        if self._scroll_offset:
            self._set_scroll(0)
        try:
            self._proc.write(event.text)
        except Exception:
            pass

    def _handle_scroll_key(self, key: str) -> None:
        page = max(1, self._screen.lines - 1)
        if key == "shift+pageup":
            self._set_scroll(self._scroll_offset + page)
        elif key == "shift+pagedown":
            self._set_scroll(self._scroll_offset - page)
        elif key == "ctrl+home":
            self._set_scroll(self._max_scroll())
        elif key == "ctrl+end":
            self._set_scroll(0)

    def on_resize(self, event: events.Resize) -> None:
        if self._missing_deps:
            return
        # Deliberately use self.size (the widget's actual content box, after
        # border/padding) rather than event.size: event.size can report the
        # pre-border/outer size, which is larger than what's really visible.
        # Sizing the pty/pyte screen to that inflated value left the bottom
        # rows (e.g. a full-screen program's status line) permanently
        # clipped since no further resize is emitted to correct it.
        cols = max(self.size.width, 2)
        rows = max(self.size.height, 2)
        if self._screen is not None:
            self._screen.resize(rows, cols)
            self._line_cache = [Text()] * rows
            self._screen.dirty.update(range(rows))
        if self._proc is not None:
            try:
                self._proc.setwinsize(rows, cols)
            except Exception:
                pass
        # Row offsets/selection coordinates from before the resize no longer
        # line up with anything; simplest and safest is to drop back to the
        # live view rather than risk pointing at the wrong lines.
        self._reset_scroll_and_search()

    # -- scrollback ------------------------------------------------------
    # HistoryScreen (see __init__) keeps every line that scrolls off the top
    # of the live screen in ``screen.history.top``, oldest first, ending
    # with the line immediately above the live buffer's row 0. Reading that
    # queue plus the live buffer gives the full chronological transcript
    # without ever touching pyte's own (half-page-at-a-time) prev_page /
    # next_page paging, which we don't use.
    def _all_rows(self) -> list:
        screen = self._screen
        rows = list(screen.history.top)
        rows.extend(screen.buffer[y] for y in range(screen.lines))
        return rows

    def _max_scroll(self) -> int:
        return len(self._screen.history.top) if self._screen is not None else 0

    def _set_scroll(self, offset: int) -> None:
        max_off = self._max_scroll()
        new_offset = max(0, min(offset, max_off))
        if new_offset == 0 and self._scroll_offset != 0:
            # Coming back to the live view: the incremental dirty-line
            # cache below was frozen while scrolled, so force a full
            # repaint rather than trusting stale diffs.
            self._screen.dirty.update(range(self._screen.lines))
            self._prev_cursor_y = -1
        self._scroll_offset = new_offset
        self.refresh()

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()
        self._set_scroll(self._scroll_offset + WHEEL_SCROLL_LINES)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()
        self._set_scroll(self._scroll_offset - WHEEL_SCROLL_LINES)

    # -- search ------------------------------------------------------------
    def _open_search(self) -> None:
        if self.app is None:
            return

        def handle(term: Optional[str]) -> None:
            if term:
                self.search(term)

        self.app.push_screen(TerminalSearchScreen(self._search_term or ""), handle)

    def search(self, term: str) -> None:
        """Jump to a match of ``term`` in the scrollback (case-insensitive).

        Repeating the same search term steps to the next older match,
        wrapping back around to the newest once the oldest is passed.
        """
        if self._screen is None:
            return
        rows = self._all_rows()
        columns = self._screen.columns
        term_l = term.lower()
        matches = [i for i, row in enumerate(rows) if term_l in self._row_text(row, columns).lower()]
        if not matches:
            self._search_term = term
            self._search_matches = []
            self._search_match_idx = -1
            self.app.bell()
            return
        if term == self._search_term and matches == self._search_matches and self._search_match_idx >= 0:
            self._search_match_idx = (self._search_match_idx - 1) % len(matches)
        else:
            self._search_term = term
            self._search_matches = matches
            self._search_match_idx = len(matches) - 1
        self._jump_to_row(matches[self._search_match_idx], len(rows))
        self.refresh()

    def _jump_to_row(self, row_index: int, total_rows: int) -> None:
        content_rows = max(1, self._screen.lines - 1)
        start = max(0, row_index - 2)
        offset = total_rows - (start + content_rows)
        self._scroll_offset = max(0, min(offset, self._max_scroll()))

    @staticmethod
    def _row_text(row, columns: int) -> str:
        return "".join((row[x].data or " ") for x in range(columns))

    # -- rendering -----------------------------------------------------
    # Mouse text selection and Ctrl+C-to-copy are provided by Textual's own
    # built-in Screen selection support (Widget.ALLOW_SELECT defaults to
    # True, and Screen has a "ctrl+c" -> copy_text binding wired to
    # app.copy_to_clipboard via OSC52). It extracts selected text straight
    # out of whatever render() below returns, so no bespoke tracking is
    # needed here. When nothing is selected that binding raises
    # SkipAction(), so Ctrl+C still falls through to on_key below and
    # interrupts the child process exactly as before.
    def render(self):
        if self._missing_deps:
            return Text(
                "Terminal backend unavailable.\n"
                "Install 'pywinpty' and 'pyte' in this environment,\n"
                "then restart the app.",
                style="bold red",
            )
        if self._scroll_offset:
            return self._render_scrollback()
        return self._render_live()

    def _render_live(self):
        screen = self._screen
        if len(self._line_cache) != screen.lines:
            self._line_cache = [Text()] * screen.lines
            screen.dirty.update(range(screen.lines))

        cursor_y = screen.cursor.y if screen.cursor and not screen.cursor.hidden else -1
        dirty = screen.dirty
        # The cursor row's appearance (reverse video) changes on every move
        # even when the underlying text doesn't, and pyte doesn't always
        # mark a line dirty for a bare cursor move (e.g. arrow keys in an
        # editor). Always redraw the current cursor row plus whichever row
        # it was on last frame, so the highlight moves/clears correctly.
        rows_to_render = set(dirty)
        if cursor_y >= 0:
            rows_to_render.add(cursor_y)
        if self._prev_cursor_y >= 0:
            rows_to_render.add(self._prev_cursor_y)
        for y in rows_to_render:
            if y < 0 or y >= screen.lines:
                continue
            self._line_cache[y] = self._render_line(screen, y, cursor_y)
        dirty.clear()
        self._prev_cursor_y = cursor_y

        return Text("\n").join(self._line_cache)

    def _render_scrollback(self):
        screen = self._screen
        rows = self._all_rows()
        total = len(rows)
        columns = screen.columns
        content_rows = max(1, screen.lines - 1)
        start = max(0, total - content_rows - self._scroll_offset)
        end = min(total, start + content_rows)
        current_match = None
        if self._search_matches and self._search_match_idx >= 0:
            current_match = self._search_matches[self._search_match_idx]

        lines: list[Text] = []
        for i in range(start, end):
            text = self._render_row(rows[i], columns)
            if i == current_match:
                text.stylize("black on #ffcc00")
            lines.append(text)
        while len(lines) < content_rows:
            lines.append(Text())

        status = f" -- SCROLLBACK: lines {start + 1}-{end}/{total} -- "
        if self._search_term:
            match_info = (
                f"'{self._search_term}' ({self._search_match_idx + 1}/{len(self._search_matches)})"
                if self._search_matches
                else f"'{self._search_term}' (no match)"
            )
            status += f"search {match_info} -- "
        status += "Shift+PgUp/PgDn scroll, Ctrl+End back to live, Ctrl+F search"
        lines.append(Text(status.ljust(columns)[:columns], style="black on #ffcc00"))

        return Text("\n").join(lines)

    def _render_line(self, screen, y: int, cursor_y: int) -> Text:
        row = screen.buffer[y]
        cursor_x = screen.cursor.x if y == cursor_y else -1
        return self._render_row(row, screen.columns, cursor_x=cursor_x)

    def _render_row(self, row, columns: int, cursor_x: int = -1) -> Text:
        text = Text()
        run_chars: list[str] = []
        run_style: Optional[Style] = None
        for x in range(columns):
            char = row[x]
            reverse = char.reverse or x == cursor_x
            style = Style(
                color=_color(char.fg),
                bgcolor=_color(char.bg),
                bold=char.bold,
                underline=char.underscore,
                reverse=reverse,
                italic=char.italics,
            )
            if run_style is not None and style == run_style:
                run_chars.append(char.data or " ")
            else:
                if run_chars:
                    text.append("".join(run_chars), style=run_style)
                run_chars = [char.data or " "]
                run_style = style
        if run_chars:
            text.append("".join(run_chars), style=run_style)
        return text


_NAMED_COLORS = {
    "black": "black",
    "red": "red",
    "green": "green",
    "brown": "yellow",
    "yellow": "yellow",
    "blue": "blue",
    "magenta": "magenta",
    "cyan": "cyan",
    "white": "white",
    "brightblack": "bright_black",
    "brightred": "bright_red",
    "brightgreen": "bright_green",
    "brightbrown": "bright_yellow",
    "brightyellow": "bright_yellow",
    "brightblue": "bright_blue",
    "brightmagenta": "bright_magenta",
    "brightcyan": "bright_cyan",
    "brightwhite": "bright_white",
}


def _color(value: str) -> Optional[str]:
    if not value or value == "default":
        return None
    if len(value) == 6:
        try:
            int(value, 16)
            return f"#{value}"
        except ValueError:
            pass
    return _NAMED_COLORS.get(value, None)
