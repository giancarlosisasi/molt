"""Every problem with the configuration document, in one pass.

:func:`molt.config.load_config` was built to never raise and to return ``(config, warnings,
errors)`` so that a caller can report every problem at once instead of one per run. Until now no
caller did: every command stops at the first thing that blocks *it*. This check is the reporting
caller that shape was designed for -- it renders both channels, all of them, and lets the rest of
the report continue against whatever could still be determined.

molt's configuration is strict: an unknown key at any depth is a hard error, and the message the
config layer produces already names the key and, where molt knows it, the replacement. So the
message is passed through verbatim; rewording it here would drop the replacement name a migrating
user is looking for.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from molt.doctor.protocol import CheckBase
from molt.doctor.report import CheckGroup

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from molt.config import ConfigIssue
    from molt.doctor.protocol import DoctorContext
    from molt.doctor.report import Row

__all__ = ["ParseCheck", "SourceCheck"]

_MANIFEST_NAME = "pyproject.toml"

#: What a document-level issue is filed under -- one that names no option, such as "configuration
#: is defined in two places". Upstream interpolates the literal string ``null`` there, which molt
#: does not reproduce.
_DOCUMENT = "[tool.molt]"


class SourceCheck(CheckBase):
    """Whether this project has a molt configuration at all, and where it lives."""

    id: ClassVar[str] = "config.source"
    group: ClassVar[CheckGroup] = CheckGroup.CONFIG

    def run(self, ctx: DoctorContext) -> Iterator[Row]:
        if ctx.config_path is not None:
            yield self.ok("source", str(ctx.config_path))
            return
        yield self.fail(
            "source",
            f"This project has no molt configuration: neither [tool.molt] in "
            f"{ctx.root / _MANIFEST_NAME} nor .molt/config.json exists.",
            "Run `molt init` to create one.",
        )


class ParseCheck(CheckBase):
    """Every configuration error as a failure and every warning as a warning, together.

    Deliberately declares no ``requires``. A configuration that fails to parse must not suppress
    the rest of the report -- the context carries ``config=None`` and the remaining checks report
    what they still can, which is most of it.
    """

    id: ClassVar[str] = "config.parse"
    group: ClassVar[CheckGroup] = CheckGroup.CONFIG

    def run(self, ctx: DoctorContext) -> Iterator[Row]:
        config, warnings, errors = ctx.config
        where = ctx.config_path

        for issue in errors:
            yield self.fail(_subject(issue), issue.msg, _remedy(where))
        for issue in warnings:
            yield self.warn(_subject(issue), issue.msg, _remedy(where))

        if config is not None and not errors and not warnings:
            settled = "defaults" if where is None else "valid"
            yield self.ok("options", f"the configuration is {settled}")


def _subject(issue: ConfigIssue) -> str:
    """The option an issue is about, or the document when it is about no single option."""
    return issue.dotted or _DOCUMENT


def _remedy(where: Path | None) -> str:
    """One line naming the file to open. Nothing more -- the message already carries the fix."""
    return f"Edit {where}." if where is not None else "Run `molt init` to write a configuration."
