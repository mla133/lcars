"""Textual widget hosting a real interactive Windows console program.

Uses ConPTY (via ``pywinpty``) so that programs which need a real terminal
(PowerShell, the GitHub Copilot CLI, vim, etc.) run and render correctly,
including colors, cursor movement, and interactive line editing.

Terminal output is parsed with ``pyte``, a terminal-emulator state machine,
and the resulting screen buffer is rendered as Rich text each frame.
"""

from __future__ import annotations

import threading
from queue import Empty, Queue
from typing import Optional

from rich.style import Style
from rich.text import Text
from textual import events
from textual.message import Message
from textual.widget import Widget

try:
    import pyte
except ImportError:  # pragma: no cover - surfaced at runtime via placeholder UI
    pyte = None

try:
    from winpty import PtyProcess
except ImportError:  # pragma: no cover - surfaced at runtime via placeholder UI
    PtyProcess = None


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


class Terminal(Widget, can_focus=True):
    """A live, interactive terminal pane backed by a Windows ConPTY process."""

    DEFAULT_CSS = """
    Terminal {
        width: 1fr;
        height: 1fr;
        background: #000000;
        color: #e6e6e6;
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
        name: Optional[str] = None,
        id: Optional[str] = None,  # noqa: A002
        classes: Optional[str] = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.command = command
        self._proc: Optional["PtyProcess"] = None
        self._screen = pyte.Screen(80, 24) if pyte else None
        self._stream = pyte.Stream(self._screen) if pyte else None
        self._reader_thread: Optional[threading.Thread] = None
        self._out_queue: "Queue[bytes]" = Queue()
        self._stopped = threading.Event()
        self._missing_deps = pyte is None or PtyProcess is None

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
        self._proc = PtyProcess.spawn(self.command, dimensions=(rows, cols))
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

    def restart(self) -> None:
        self.stop()
        if self._screen is not None:
            self._screen.reset()
        self.start()

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
            self.refresh()

    # -- input -------------------------------------------------------------
    async def on_key(self, event: events.Key) -> None:
        if self._proc is None:
            return
        event.stop()
        data = _KEY_MAP.get(event.key)
        if data is None:
            data = event.character or ""
        if data:
            try:
                self._proc.write(data)
            except Exception:
                pass

    def on_resize(self, event: events.Resize) -> None:
        if self._missing_deps:
            return
        cols = max(event.size.width, 2)
        rows = max(event.size.height, 2)
        if self._screen is not None:
            self._screen.resize(rows, cols)
        if self._proc is not None:
            try:
                self._proc.setwinsize(rows, cols)
            except Exception:
                pass

    # -- rendering -----------------------------------------------------
    def render(self):
        if self._missing_deps:
            return Text(
                "Terminal backend unavailable.\n"
                "Install 'pywinpty' and 'pyte' in this environment,\n"
                "then restart the app.",
                style="bold red",
            )

        lines = []
        for y in range(self._screen.lines):
            row = self._screen.buffer[y]
            text = Text()
            for x in range(self._screen.columns):
                char = row[x]
                style = Style(
                    color=_color(char.fg),
                    bgcolor=_color(char.bg),
                    bold=char.bold,
                    underline=char.underscore,
                    reverse=char.reverse,
                    italic=char.italics,
                )
                text.append(char.data or " ", style=style)
            lines.append(text)
        return Text("\n").join(lines)


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
