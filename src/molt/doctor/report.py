"""What a diagnostic run produces: a status, a group, a row, and the report they add up to.

molt-native; changesets has no ``doctor`` and nothing here ports. The one design constraint worth
reading before editing this module is :class:`Row`'s field list -- see its docstring.

The exit code lives here rather than in the command so the human report, the machine-readable
document and the process status are three renderings of **one** value (design D5). Computing the
status twice is how a report that says "1 failure" exits 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["CheckGroup", "CheckStatus", "DoctorReport", "Row"]


class CheckStatus(StrEnum):
    """How a row lands. Only :attr:`FAIL` affects the exit code.

    ``warn`` describes a fact about the workspace that may be intentional; ``fail`` describes
    something that will stop or corrupt a release. There is deliberately no mode that promotes one
    to the other: a glob matching no package today is a fact, and failing a build when somebody adds
    a package tomorrow is what such a mode would produce.
    """

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


class CheckGroup(StrEnum):
    """The seven sections of a report, and a closed set.

    Closed on purpose: every future "molt should warn about X" would otherwise land here until the
    report is unreadable. Adding a check inside a group is ordinary work; adding a *group* is a
    change to the specification.
    """

    ENVIRONMENT = "environment"
    WORKSPACE = "workspace"
    CONFIG = "config"
    VERSIONS = "versions"
    CHANGESETS = "changesets"
    GROUPS = "groups"
    PUBLISH = "publish"


@dataclass(frozen=True, slots=True)
class Row:
    """One finding: what was examined, how it landed, and what to do about it.

    **Do not add a free-form payload field.** ``doctor``'s output is the artifact users paste into
    public issue trackers, and the credential check's promise -- present or absent, never the value
    -- rests on this class having nowhere for a value to travel. ``status`` and ``group`` are closed
    enumerations, ``check`` is an identifier this package writes, and the three remaining strings
    are sentences a check composes. A ``details`` or ``data`` field is the obvious next addition and
    is exactly what would break the guarantee, in a way no reviewer would catch again.

    ``remedy`` is empty only for an ``ok`` row. Anything a user has to act on names the action.
    """

    group: CheckGroup
    check: str
    status: CheckStatus
    subject: str
    message: str
    remedy: str = ""


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Every row one run produced, plus the two things a caller derives from them."""

    rows: tuple[Row, ...]

    @property
    def counts(self) -> dict[CheckStatus, int]:
        """How many rows landed at each status; every status is present, including at zero."""
        tally = dict.fromkeys(CheckStatus, 0)
        for row in self.rows:
            tally[row.status] += 1
        return tally

    @property
    def failed(self) -> bool:
        """Whether any check reported a failure. Warnings never count."""
        return any(row.status is CheckStatus.FAIL for row in self.rows)

    @property
    def exit_code(self) -> int:
        """0 when nothing failed, 1 when something did."""
        return 1 if self.failed else 0
