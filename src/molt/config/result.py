"""The non-throwing parse result: :class:`ConfigIssue` and :class:`ConfigResult`.

Ports changesets 3.0's ``{ config, warnings, errors }`` shape (``config/src/parse.ts:9-19``), which
replaced a ``ValidationError`` throw in ``@changesets/config@4.0.0-next.5``. Design D3 states why
the result object is the contract and not an exception: a configuration can be *partly* usable, and
a successful parse can still produce warnings. An exception cannot carry both outcomes at once, so
``molt status`` could never report every problem in one pass.

Two deliberate differences from upstream:

- Success returns empty **lists**, not ``undefined`` (``parse.ts:9-19``), so no caller branches on
  ``None`` before iterating.
- An issue keeps its ``loc`` **structured**. Upstream flattens location and message into one string
  (``parse.ts:21-23``), which is why its own tests can only assert on prose. molt's ``loc`` is
  pydantic's (``.errors()[*]["loc"]``, the 1:1 counterpart of valibot's ``getDotPath``), so tooling
  can point at an option without parsing English.

Standard library only: this module is on the import path of anything that wants to *type* against a
result, and must not drag pydantic in with it (design D2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from molt.config.models import Config

__all__ = ["ConfigIssue", "ConfigResult"]


class ConfigIssue(NamedTuple):
    """One problem with a configuration document -- an error or a warning.

    One type for both channels, deliberately: severity is which list an issue lands in, not a field
    on it, so a rule cannot claim to be an error while being collected as a warning.

    ``loc`` is the structured path to the offending option (``("snapshot",
    "use_calculated_version")``, ``("fixed", 0, 1)``). It is ``()`` for a document-level problem --
    upstream interpolates the literal string ``"null"`` there (research doc 02 section 3.1, the
    ``null:`` quirk), which molt does not reproduce.
    """

    loc: tuple[str | int, ...]
    msg: str

    @property
    def dotted(self) -> str:
        """``loc`` as a dotted path, the form the CLI prints. ``""`` for a document-level issue."""
        return ".".join(str(part) for part in self.loc)

    def __str__(self) -> str:
        return f"{self.dotted}: {self.msg}" if self.loc else self.msg


class ConfigResult(NamedTuple):
    """What every parse returns: ``(config, warnings, errors)``.

    A ``NamedTuple`` so both ``config, warnings, errors = parse_config(...)`` and
    ``result.errors`` read naturally -- the tuple form is what ports from upstream's destructuring,
    the attribute form is what stays readable at a call site three arguments deep.

    ``config`` is ``None`` **whenever** ``errors`` is non-empty, mirroring upstream's
    ``ParseResult`` union. Warnings never suppress the config.
    """

    config: Config | None
    warnings: list[ConfigIssue]
    errors: list[ConfigIssue]
