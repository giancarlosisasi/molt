"""The two renderings of one report, so they cannot disagree.

Both read the same :class:`~molt.doctor.report.DoctorReport`, including the exit code, which is
derived from it rather than computed a second time. A report that says "1 failure" and exits 0 is
the failure mode this arrangement removes.

The human form is deliberately ASCII: it is written to a terminal that may be on a legacy Windows
code page, and a status marker that degrades to ``?`` would take the one signal that distinguishes
a warning from a failure with it. Document keys are snake_case, matching molt's plan documents.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from molt.doctor.report import CheckGroup, CheckStatus

if TYPE_CHECKING:
    from molt.doctor.report import DoctorReport, Row

__all__ = ["render_report", "report_payload", "summary_line"]

#: Section headings, in report order.
_GROUP_TITLES: Final[dict[CheckGroup, str]] = {
    CheckGroup.ENVIRONMENT: "Environment",
    CheckGroup.WORKSPACE: "Workspace",
    CheckGroup.CONFIG: "Configuration",
    CheckGroup.VERSIONS: "Versions",
    CheckGroup.CHANGESETS: "Changesets",
    CheckGroup.GROUPS: "Groups and filters",
    CheckGroup.PUBLISH: "Publishing",
}

#: Fixed-width status markers, so the subject column lines up and the three statuses stay
#: distinguishable from the text alone -- on a pipe, under NO_COLOR, and in a pasted issue.
_MARKS: Final[dict[CheckStatus, str]] = {
    CheckStatus.OK: "ok  ",
    CheckStatus.WARN: "warn",
    CheckStatus.FAIL: "FAIL",
}

_REMEDY_INDENT = " " * 7


def render_report(report: DoctorReport) -> str:
    """The grouped human report, as one block of text.

    One string rather than a call per row: this is the artifact a user copies into an issue, and a
    report interleaved with whatever else the process writes is one somebody has to reassemble.
    """
    lines: list[str] = []
    for group in CheckGroup:
        rows = [row for row in report.rows if row.group is group]
        if not rows:
            continue
        if lines:
            lines.append("")
        lines.append(_GROUP_TITLES[group])
        for row in rows:
            lines.extend(_row_lines(row))
    return "\n".join(lines)


def summary_line(report: DoctorReport) -> str:
    """One sentence counting the rows, for the level-appropriate closing message."""
    counts = report.counts
    return (
        f"{counts[CheckStatus.OK]} ok, "
        f"{counts[CheckStatus.WARN]} warning{_s(counts[CheckStatus.WARN])}, "
        f"{counts[CheckStatus.FAIL]} failure{_s(counts[CheckStatus.FAIL])}."
    )


def report_payload(report: DoctorReport) -> dict[str, Any]:
    """The report as a machine-readable document.

    Carries the summary counts as well as the rows so a consumer never has to re-derive whether the
    run passed by counting statuses itself -- and ``status`` is the same value the exit code is.
    """
    return {
        "status": CheckStatus.FAIL.value if report.failed else CheckStatus.OK.value,
        "exit_code": report.exit_code,
        "summary": {status.value: count for status, count in report.counts.items()},
        "rows": [
            {
                "group": row.group.value,
                "check": row.check,
                "status": row.status.value,
                "subject": row.subject,
                "message": row.message,
                "remedy": row.remedy,
            }
            for row in report.rows
        ],
    }


def _row_lines(row: Row) -> list[str]:
    """A row's marker, subject and message, plus its remedy on a second line when it has one."""
    lines = [f"  {_MARKS[row.status]} {row.subject}: {row.message}"]
    if row.remedy:
        lines.append(f"{_REMEDY_INDENT}-> {row.remedy}")
    return lines


def _s(count: int) -> str:
    return "" if count == 1 else "s"
