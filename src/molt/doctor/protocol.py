"""The check seam: what a check is, and what it is handed.

A check is an object in a registry rather than a branch in a long function (design D1). Two things
follow from that, and both are the reason for the shape:

- a check yields **zero or more** rows, because the interesting checks are per-package -- the
  version check reports one row for each member, not one row for "versions";
- a check declares what it :attr:`~Check.requires`, so "the workspace could not be discovered"
  makes its dependants *not applicable* instead of separately failed, which is the runner's job to
  apply rather than an ``if`` ladder inside each check.

Checks contain **no** defensive ``try/except``: :mod:`molt.doctor.runner` is the only place that
catches, so a raising check produces exactly one row naming itself and every other check still
runs. A second error shape inside a check would hide which check actually broke.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

from molt.doctor.report import CheckGroup, CheckStatus, Row

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from molt.config import ConfigResult
    from molt.ecosystem import Workspace

__all__ = ["Check", "CheckBase", "DoctorContext"]


@dataclass(frozen=True, slots=True)
class DoctorContext:
    """Everything the checks share, resolved once by the runner.

    Resolved up front rather than per check so twelve checks do not discover the workspace twelve
    times, and so the failures that resolution itself can produce are turned into data in one place
    -- which is what lets the workspace check *report* a broken manifest instead of dying on it.

    ``workspace`` is ``None`` exactly when ``workspace_error`` is set, mirroring
    :class:`~molt.config.ConfigResult`'s ``config is None`` iff ``errors``.
    """

    cwd: Path
    root: Path
    config: ConfigResult
    config_path: Path | None
    workspace: Workspace | None
    workspace_error: str | None
    online: bool


@runtime_checkable
class Check(Protocol):
    """One diagnosis molt can perform."""

    # All three are ``ClassVar``: a check's identity, section and dependencies are properties of the
    # kind of check, not of one instance, and the registry holds one instance of each. Declaring
    # them as instance attributes here would make every implementation carry a mutable copy.

    #: Stable and machine-readable (``config.parse``, ``versions.resolvable``). It is the subject of
    #: the row a crashed check produces and the name a dependant declares, so renaming one is a
    #: change to the machine-readable document.
    id: ClassVar[str]

    #: Which section of the report this check's rows belong to.
    group: ClassVar[CheckGroup]

    #: Check ids that must not have failed for this check to mean anything.
    requires: ClassVar[tuple[str, ...]]

    def run(self, ctx: DoctorContext) -> Iterable[Row]:
        """Examine ``ctx`` and yield what was found."""
        ...


class CheckBase:
    """Shared plumbing so a check body reads as findings rather than as row construction.

    The three helpers stamp :attr:`group` and :attr:`id` onto every row, which is what makes a row
    traceable back to the check that produced it without each call site repeating both.
    """

    id: ClassVar[str]
    group: ClassVar[CheckGroup]
    requires: ClassVar[tuple[str, ...]] = ()

    def ok(self, subject: str, message: str) -> Row:
        """Nothing to do. An ``ok`` row carries no remedy because there is nothing to remedy."""
        return self._row(CheckStatus.OK, subject, message, "")

    def warn(self, subject: str, message: str, remedy: str) -> Row:
        """A fact that may be intentional. Never affects the exit code."""
        return self._row(CheckStatus.WARN, subject, message, remedy)

    def fail(self, subject: str, message: str, remedy: str) -> Row:
        """Something that will stop or corrupt a release. Exits the command 1."""
        return self._row(CheckStatus.FAIL, subject, message, remedy)

    def _row(self, status: CheckStatus, subject: str, message: str, remedy: str) -> Row:
        return Row(
            group=self.group,
            check=self.id,
            status=status,
            subject=subject,
            message=message,
            remedy=remedy,
        )
