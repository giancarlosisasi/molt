"""The report model: counts, the exit code, and the field list the no-secrets promise rests on.

Net-new. changesets has no ``doctor``, so nothing here is a port and there is no upstream behaviour
to conform to. Primary source: the accepted change ``openspec/changes/add-doctor-command/``
(``specs/doctor-command/spec.md``, ``design.md`` D3 and D5).
"""

from __future__ import annotations

import dataclasses

import pytest

doctor = pytest.importorskip("molt.doctor", reason="molt.doctor lands with add-doctor-command")

CheckGroup = doctor.CheckGroup
CheckStatus = doctor.CheckStatus
DoctorReport = doctor.DoctorReport
Row = doctor.Row

pytestmark = pytest.mark.unit


def row(status: object, subject: str = "subject") -> object:
    """One row at ``status``; the group and check are irrelevant to every assertion here."""
    return Row(
        group=CheckGroup.ENVIRONMENT,
        check="test.check",
        status=status,
        subject=subject,
        message="message",
    )


def test_counts_cover_every_status_including_the_absent_ones() -> None:
    """A consumer reading ``summary["fail"]`` must not have to handle a missing key."""
    report = DoctorReport(rows=(row(CheckStatus.OK), row(CheckStatus.OK)))

    assert report.counts == {CheckStatus.OK: 2, CheckStatus.WARN: 0, CheckStatus.FAIL: 0}


def test_counts_match_the_rows() -> None:
    report = DoctorReport(
        rows=(
            row(CheckStatus.OK),
            row(CheckStatus.WARN),
            row(CheckStatus.WARN),
            row(CheckStatus.FAIL),
        )
    )

    assert report.counts == {CheckStatus.OK: 1, CheckStatus.WARN: 2, CheckStatus.FAIL: 1}
    assert sum(report.counts.values()) == len(report.rows)


def test_warnings_alone_exit_zero() -> None:
    """spec: "The exit code reflects failures only, never warnings"."""
    report = DoctorReport(rows=(row(CheckStatus.OK), row(CheckStatus.WARN)))

    assert report.failed is False
    assert report.exit_code == 0


def test_one_failure_exits_one() -> None:
    report = DoctorReport(rows=(row(CheckStatus.OK), row(CheckStatus.FAIL), row(CheckStatus.WARN)))

    assert report.failed is True
    assert report.exit_code == 1


def test_an_empty_report_exits_zero() -> None:
    """Not a real run, but the boundary the ``any(...)`` derivation has to get right."""
    assert DoctorReport(rows=()).exit_code == 0


def test_the_row_model_carries_no_free_form_payload_field() -> None:
    """``design.md`` D3 -- redaction is structural.

    The credential check's promise is that no secret can reach the report, and it rests on this
    class having nowhere for one to travel. This row is the enforcement: adding a ``details``,
    ``data`` or ``payload`` field fails it, which is the only moment anybody would notice.
    """
    fields = {field.name for field in dataclasses.fields(Row)}

    assert fields == {"group", "check", "status", "subject", "message", "remedy"}


def test_a_row_is_frozen() -> None:
    """Nothing may rewrite a finding after the check that made it returned."""
    subject = row(CheckStatus.OK)

    with pytest.raises(dataclasses.FrozenInstanceError):
        subject.message = "rewritten"  # type: ignore[misc]
