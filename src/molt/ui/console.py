"""The console adapter -- every human-facing byte molt writes leaves through here.

Ports ``packages/logger/src/index.ts:1-35`` (research doc 03 §1.2): one function per level, each
rendering a visible label so the four levels stay distinguishable from the text alone. Upstream
writes straight to ``console.*``; molt writes through the :class:`Console` protocol so the backend
is swappable in one file (``terminal-ui`` spec, "Console output adapter").

Two contracts this module owns:

**Streams.** Every level -- ``info``, ``success``, ``warn``, ``error``, ``note``, spinners and
progress -- goes to **stderr**. stdout carries machine-readable payloads only: the ``--output json``
document of ``status``/``publish-plan`` and the bare ``molt --version`` string. Without that split,
``molt status --output json | jq`` -- the recipe ``website/docs/guide/status.md`` tells CI to
run -- dies on the startup banner.

**Encoding.** A Windows console on a legacy code page (cp1252) cannot encode the butterfly glyph or
typographic punctuation. Text is transcoded to the target stream's encoding with
``errors="replace"`` *before* it reaches ``rich``, so an unencodable glyph degrades to a visible
``?`` instead of raising ``UnicodeEncodeError``. Being Windows-correct from day one is a deliberate
differentiator (repo ``CLAUDE.md``); upstream only added Windows CI in 2026-07.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import IO, TYPE_CHECKING, Final, Protocol

from rich.console import Console as _RichBackend
from rich.text import Text

if TYPE_CHECKING:
    from collections.abc import Iterator
    from contextlib import AbstractContextManager

__all__ = ["Console", "RichConsole", "console"]


#: Level -> ``rich`` style. The label *text* is what makes the levels distinguishable; the style is
#: an enhancement on colour-capable terminals, never the only signal (a piped or ``NO_COLOR`` run
#: still has to tell an error from an info).
_LEVEL_STYLES: Final[dict[str, str]] = {
    "info": "bold cyan",
    "success": "bold green",
    "warn": "bold yellow",
    "error": "bold red",
}


class Console(Protocol):
    """The terminal-output surface molt's shell and commands are allowed to depend on.

    Structural, not nominal: test doubles (``tests/cli/fake_cli.py::RecordingConsole``) implement it
    by shape and never import this module. Keep the method set and their argument order stable --
    widening it is cheap, renaming is not.
    """

    def info(self, *messages: object) -> None:
        """Neutral progress information."""
        ...

    def success(self, *messages: object) -> None:
        """A step completed."""
        ...

    def warn(self, *messages: object) -> None:
        """Something surprising that is not fatal (e.g. a deprecated command spelling)."""
        ...

    def error(self, *messages: object) -> None:
        """A failure. Written to stderr, like every other level."""
        ...

    def note(self, title: str, body: str = "") -> None:
        """A titled block -- used for multi-line guidance such as the issue-report URL."""
        ...

    def spinner(self, message: str = "") -> AbstractContextManager[None]:
        """Indeterminate progress around a block of work."""
        ...

    def progress(self, message: str = "", total: int | None = None) -> AbstractContextManager[None]:
        """Determinate progress around a block of work."""
        ...


class RichConsole:
    """The ``rich``-backed :class:`Console`.

    ``file=None`` (the default) resolves to ``sys.stderr`` **at write time**, not at construction:
    the module-level :data:`console` is built when ``molt.cli`` is imported, long before a test's
    capture fixture swaps the stream, and a cached reference would write past it.
    """

    def __init__(self, file: IO[str] | None = None) -> None:
        self._backend = _RichBackend(
            file=file,
            stderr=file is None,
            # Messages are data, not templates: a package name or an error string containing
            # `[...]` must render literally, and a path must not be re-coloured as a "value".
            markup=False,
            highlight=False,
            soft_wrap=True,
        )

    # -- protocol surface ---------------------------------------------------------------
    def info(self, *messages: object) -> None:
        self._emit("info", messages)

    def success(self, *messages: object) -> None:
        self._emit("success", messages)

    def warn(self, *messages: object) -> None:
        self._emit("warn", messages)

    def error(self, *messages: object) -> None:
        self._emit("error", messages)

    def note(self, title: str, body: str = "") -> None:
        text = Text()
        text.append(self._encodable(title), style="bold")
        if body:
            text.append("\n")
            text.append(self._encodable(body))
        self._print(text)

    @contextmanager
    def spinner(self, message: str = "") -> Iterator[None]:
        if not self._backend.is_terminal:
            # A spinner on a pipe or a CI log is animation nobody sees; the message still matters.
            if message:
                self.info(message)
            yield
            return
        with self._backend.status(self._encodable(message)):
            yield

    @contextmanager
    def progress(self, message: str = "", total: int | None = None) -> Iterator[None]:
        # Determinate rendering lands with the first command that has a countable unit of work
        # (`publish` over N distributions). Until then this keeps the call sites honest.
        del total
        if message:
            self.info(message)
        yield

    # -- internals ----------------------------------------------------------------------
    def _emit(self, level: str, messages: tuple[object, ...]) -> None:
        text = Text()
        text.append(self._encodable(level), style=_LEVEL_STYLES[level])
        text.append(" ")
        # `util.format("", ...args)` (logger/src/index.ts:6-15): every argument, joined in order.
        text.append(self._encodable(" ".join(str(message) for message in messages)))
        self._print(text)

    def _print(self, text: Text) -> None:
        # soft_wrap: no hard line breaks inserted. A wrapped issue-report URL is an unusable
        # issue-report URL, and the terminal already soft-wraps long lines for the reader.
        self._backend.print(text, soft_wrap=True)

    def _encodable(self, text: str) -> str:
        """Return ``text`` rewritten so the target stream can encode every character.

        ``errors="replace"`` yields a visible ``?`` for each unencodable glyph. Dropping the glyph
        silently would not be a degradation, it would be a different message (``terminal-ui`` spec,
        "Emoji banner on a legacy code page").
        """
        encoding = getattr(self._backend.file, "encoding", None)
        if not encoding:
            # An in-memory stream (``io.StringIO``) has no encoding and no limits.
            return text
        try:
            text.encode(encoding)
        except (UnicodeEncodeError, LookupError):
            return text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        return text


#: The one adapter instance the shell and every command write through. Swap this attribute (not the
#: class) to redirect output -- that is what ``tests/cli/test_cli.py`` does to prove nothing reaches
#: the raw file descriptors behind the protocol's back.
console: Console = RichConsole()
