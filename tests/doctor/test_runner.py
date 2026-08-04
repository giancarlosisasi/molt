"""The runner's two invariants: nothing can shorten the report, and nothing can cascade a failure.

Net-new; nothing here is a port. Primary source: the accepted change
``openspec/changes/add-doctor-command/`` (``design.md`` D1/D2, ``specs/doctor-command/spec.md``
requirement "``doctor`` is read-only and never raises").
"""

from __future__ import annotations

from pathlib import Path

import pytest

doctor = pytest.importorskip("molt.doctor", reason="molt.doctor lands with add-doctor-command")

CheckBase = doctor.CheckBase
CheckGroup = doctor.CheckGroup
CheckStatus = doctor.CheckStatus
DoctorContext = doctor.DoctorContext
run_checks = doctor.run_checks

pytestmark = pytest.mark.unit


def context() -> object:
    """A context every double below ignores. The runner never reads it either."""
    from molt.config import ConfigResult

    return DoctorContext(
        cwd=Path("."),
        root=Path("."),
        config=ConfigResult(None, [], []),
        config_path=None,
        workspace=None,
        workspace_error=None,
        online=False,
    )


class Fine(CheckBase):
    id = "test.fine"
    group = CheckGroup.ENVIRONMENT

    def run(self, ctx: object) -> list[object]:
        del ctx
        return [self.ok("fine", "all good")]


class Raises(CheckBase):
    id = "test.raises"
    group = CheckGroup.WORKSPACE

    def run(self, ctx: object) -> list[object]:
        del ctx
        raise RuntimeError("the check exploded")


class Fails(CheckBase):
    id = "test.fails"
    group = CheckGroup.WORKSPACE

    def run(self, ctx: object) -> list[object]:
        del ctx
        return [self.fail("broken", "this is wrong", "fix it")]


class Dependant(CheckBase):
    id = "test.dependant"
    group = CheckGroup.VERSIONS
    requires = ("test.fails",)

    def run(self, ctx: object) -> list[object]:
        del ctx
        raise AssertionError("a dependant of a failed check must never run")


class Grandchild(CheckBase):
    id = "test.grandchild"
    group = CheckGroup.VERSIONS
    requires = ("test.dependant",)

    def run(self, ctx: object) -> list[object]:
        del ctx
        raise AssertionError("a dependant of a skipped check must never run either")


class Cancels(CheckBase):
    id = "test.cancels"
    group = CheckGroup.PUBLISH

    def run(self, ctx: object) -> list[object]:
        del ctx
        raise KeyboardInterrupt


def test_a_raising_check_does_not_stop_the_report() -> None:
    """spec: "A raising check does not stop the report"."""
    report = run_checks(context(), [Fine(), Raises(), Fine()])

    assert [row.status for row in report.rows] == [
        CheckStatus.OK,
        CheckStatus.FAIL,
        CheckStatus.OK,
    ]


def test_the_row_count_is_independent_of_how_many_checks_raised() -> None:
    """``design.md`` D2's correctness invariant, stated as an equality.

    A raising check contributes **exactly one** row, so a report can never come up silently short.
    Comparing the two runs is what makes that a measurement rather than a claim: the only
    difference between them is which check is in the middle.
    """
    healthy = run_checks(context(), [Fine(), Fine(), Fine()])
    broken = run_checks(context(), [Fine(), Raises(), Fine()])

    assert len(broken.rows) == len(healthy.rows)


def test_a_raising_check_is_named_by_its_own_id() -> None:
    """The subject is the check, so the report says which one broke."""
    report = run_checks(context(), [Raises()])

    (row,) = report.rows
    assert row.subject == "test.raises"
    assert row.check == "test.raises"
    assert "the check exploded" in row.message
    assert row.remedy, "even a molt bug tells the user what to do"


def test_a_dependant_of_a_failed_check_is_not_applicable_rather_than_failed() -> None:
    """spec: "A directory that is not a workspace at all".

    The distinction is the whole point: one broken manifest must read as one failure, not as five.
    """
    report = run_checks(context(), [Fails(), Dependant()])

    failure, skipped = report.rows
    assert failure.status is CheckStatus.FAIL
    assert skipped.status is CheckStatus.WARN
    assert "test.fails" in skipped.message
    assert report.counts[CheckStatus.FAIL] == 1


def test_the_block_propagates_to_a_dependant_of_a_dependant() -> None:
    """A check nobody could run cannot vouch for its own dependants either."""
    report = run_checks(context(), [Fails(), Dependant(), Grandchild()])

    assert [row.check for row in report.rows] == [
        "test.fails",
        "test.dependant",
        "test.grandchild",
    ]
    assert report.counts[CheckStatus.FAIL] == 1


def test_a_warning_does_not_block_a_dependant() -> None:
    """Only a *failure* makes a dependant not applicable; a warning is a fact, not a blocker."""

    class Warns(CheckBase):
        id = "test.fails"  # the id `Dependant` requires
        group = CheckGroup.WORKSPACE

        def run(self, ctx: object) -> list[object]:
            del ctx
            return [self.warn("odd", "surprising but fine", "maybe look at it")]

    class Ran(CheckBase):
        id = "test.dependant"
        group = CheckGroup.VERSIONS
        requires = ("test.fails",)

        def run(self, ctx: object) -> list[object]:
            del ctx
            return [self.ok("ran", "the dependant still ran")]

    report = run_checks(context(), [Warns(), Ran()])

    assert [row.status for row in report.rows] == [CheckStatus.WARN, CheckStatus.OK]


def test_ctrl_c_is_not_swallowed() -> None:
    """``design.md`` D2 -- ``KeyboardInterrupt`` must reach the shell, which exits 0 "Canceled".

    Turning it into a failed row would make Ctrl-C exit 1 and print a report nobody asked for.
    """
    with pytest.raises(KeyboardInterrupt):
        run_checks(context(), [Fine(), Cancels(), Fine()])


def test_an_exiting_check_is_not_swallowed_either() -> None:
    """``SystemExit`` is a ``BaseException`` too, and the shell's exit-code contract owns it."""

    class Exits(CheckBase):
        id = "test.exits"
        group = CheckGroup.PUBLISH

        def run(self, ctx: object) -> list[object]:
            del ctx
            raise SystemExit(3)

    with pytest.raises(SystemExit):
        run_checks(context(), [Exits()])


def test_the_shipped_registry_declares_every_id_it_requires() -> None:
    """A ``requires`` naming a check that does not exist -- or one declared later -- does nothing.

    Silently: the runner looks the blocker up in "what has failed so far", and a name that never
    gets there is indistinguishable from a name that never failed.
    """
    from molt.doctor import CHECKS

    seen: set[str] = set()
    for check in CHECKS:
        missing = [required for required in check.requires if required not in seen]
        assert missing == [], f"{check.id} requires {missing}, which no earlier check declares"
        seen.add(check.id)


def test_the_shipped_registry_has_unique_ids() -> None:
    """Two checks sharing an id make ``requires`` ambiguous and the document unreadable."""
    from molt.doctor import CHECKS

    ids = [check.id for check in CHECKS]
    assert len(ids) == len(set(ids))
