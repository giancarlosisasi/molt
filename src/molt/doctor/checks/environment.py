"""What molt is running as, and whether this terminal can render what molt prints.

The three version rows exist for one reason: ``doctor``'s output is what a user pastes into a bug
report, and every bug report that omits the molt version, the interpreter and the platform costs a
round trip.

The encoding row is the Windows concern this repo has handled since day one. A console on a legacy
code page (cp1252) cannot encode molt's banner glyph, so the console adapter transcodes with
``errors="replace"`` and the glyph degrades to ``?``. That is a working terminal, not a broken one
-- hence a warning -- but it is worth naming, because the alternative is a user filing "molt prints
question marks".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from molt.doctor.protocol import CheckBase
from molt.doctor.report import CheckGroup

if TYPE_CHECKING:
    from collections.abc import Iterator

    from molt.doctor.protocol import DoctorContext
    from molt.doctor.report import Row

__all__ = ["EncodingCheck", "RuntimeCheck"]


class RuntimeCheck(CheckBase):
    """molt's version, the interpreter's, and the platform. Always ``ok`` -- these are facts."""

    id: ClassVar[str] = "environment.runtime"
    group: ClassVar[CheckGroup] = CheckGroup.ENVIRONMENT

    def run(self, ctx: DoctorContext) -> Iterator[Row]:
        import platform

        from molt import __version__

        del ctx
        yield self.ok("molt", __version__)
        yield self.ok("python", f"{platform.python_version()} ({platform.python_implementation()})")
        yield self.ok("platform", platform.platform())


class EncodingCheck(CheckBase):
    """Whether this console can render molt's own output without substitution.

    The probe goes through :func:`molt.ui.console.can_encode`, which is the same function the
    console adapter itself uses to decide whether to transcode. Re-deriving the rule here would
    give ``doctor`` a second opinion about the terminal it is printing to.
    """

    id: ClassVar[str] = "environment.encoding"
    group: ClassVar[CheckGroup] = CheckGroup.ENVIRONMENT

    #: U+1F40D SNAKE, molt's mark and the one non-ASCII character molt prints. Written as an escape
    #: so this source file stays ASCII-only.
    _GLYPH: ClassVar[str] = "\U0001f40d"

    def run(self, ctx: DoctorContext) -> Iterator[Row]:
        from molt.ui.console import can_encode, console_encoding

        del ctx
        encoding = console_encoding()
        named = encoding or "unknown"
        if can_encode(self._GLYPH, encoding):
            yield self.ok("console encoding", f"{named} renders molt's output in full")
            return
        yield self.warn(
            "console encoding",
            f"{named} cannot encode molt's banner glyph, which degrades to `?`. "
            "Nothing else is affected.",
            "Set PYTHONUTF8=1, or use a UTF-8 console such as Windows Terminal.",
        )
