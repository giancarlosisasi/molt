"""The checks, driven over real temporary workspaces.

Net-new; nothing here is a port. Primary source: the accepted change
``openspec/changes/add-doctor-command/`` (``specs/doctor-command/spec.md``, ``design.md`` D3/D4/D6).

Every row here drives :func:`molt.doctor.build_context` plus :func:`molt.doctor.run_checks` over a
``tmp_project`` fixture, because the value of these checks is precisely what they say about a real
directory -- a unit test over a hand-built context would pin the formatting and none of the
diagnosis.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

doctor = pytest.importorskip("molt.doctor", reason="molt.doctor lands with add-doctor-command")

CheckStatus = doctor.CheckStatus
build_context = doctor.build_context
run_checks = doctor.run_checks

pytestmark = pytest.mark.functional


def report_for(root: Path, *, online: bool = False) -> Any:
    """The full report for ``root``, exactly as the command would produce it."""
    return run_checks(build_context(root, online=online))


def rows_of(report: Any, check: str) -> list[Any]:
    """Every row one check produced."""
    return [row for row in report.rows if row.check == check]


def status_of(report: Any, check: str, subject: str) -> Any:
    """The status of one row, by check and subject; fails loudly when the row is absent."""
    for row in report.rows:
        if row.check == check and row.subject == subject:
            return row.status
    raise AssertionError(f"no {check} row for {subject!r} in {[r.subject for r in report.rows]}")


# ======================================================================================
# Configuration -- every problem reported, and none of them suppressing the rest
# ======================================================================================


def test_three_unknown_keys_at_three_depths_are_three_separate_rows(tmp_project: Any) -> None:
    """spec: "Several bad keys are all reported".

    Three depths on purpose -- top level, inside ``changelog``, inside ``snapshot`` -- because the
    parser walks nested tables separately and a pre-pass that only scanned the top level would pass
    a one-key version of this row.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.set_config(
        access="public",
        changelog={"generator": "molt.changelog.default", "prettier": True},
        snapshot={"use_calculated_version": False, "useCalculatedVersionn": True},
    )

    report = report_for(tmp_project.root)
    failures = [row for row in rows_of(report, "config.parse") if row.status is CheckStatus.FAIL]

    assert len(failures) == 3, [row.subject for row in failures]
    named = " ".join(f"{row.subject} {row.message}" for row in failures)
    for key in ("access", "prettier", "useCalculatedVersionn"):
        assert key in named
    assert all(row.remedy for row in failures), "a failure always names what to do"


def test_an_unknown_key_names_its_replacement(tmp_project: Any) -> None:
    """spec: "An unknown key names its replacement"."""
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.set_config(changelog_template="entry.md.jinja")

    report = report_for(tmp_project.root)
    (failure,) = [row for row in rows_of(report, "config.parse") if row.status is CheckStatus.FAIL]

    assert "changelog_template" in f"{failure.subject} {failure.message}"
    assert "changelog" in failure.message


def test_a_broken_configuration_does_not_suppress_the_rest_of_the_report(
    tmp_project: Any,
) -> None:
    """spec: "A broken configuration does not suppress the rest of the report"."""
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.set_config(nonsense_key=True)

    report = report_for(tmp_project.root)
    groups = {row.group for row in report.rows}

    assert doctor.CheckGroup.CONFIG in groups
    for still_reported in (
        doctor.CheckGroup.ENVIRONMENT,
        doctor.CheckGroup.WORKSPACE,
        doctor.CheckGroup.CHANGESETS,
        doctor.CheckGroup.PUBLISH,
    ):
        assert still_reported in groups, f"{still_reported} vanished when the config failed"


def test_a_glob_matching_nothing_warns_rather_than_fails(tmp_project: Any) -> None:
    """spec: "A glob matching nothing warns rather than fails"."""
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.set_config(ignore=["not-a-package-*"])

    report = report_for(tmp_project.root)
    mentions = [row for row in report.rows if "not-a-package-*" in row.message]

    assert mentions, "an entry matching nothing must still be reported"
    assert all(row.status is not CheckStatus.FAIL for row in mentions)


def test_a_missing_configuration_names_molt_init(tmp_project: Any) -> None:
    """spec: "An uninitialized directory still gets a report"."""
    tmp_project.add_package("pkg-a", "1.0.0")

    report = report_for(tmp_project.root)
    (source,) = rows_of(report, "config.source")

    assert source.status is CheckStatus.FAIL
    assert "molt init" in source.remedy


# ======================================================================================
# Versions -- the four outcomes, and the two that must not collapse into one
# ======================================================================================


@pytest.fixture
def four_outcomes(tmp_project: Any) -> Any:
    """A workspace holding one package of each of the four version outcomes, plus a resolvable one.

    ``versionless`` and ``dynamic-unresolvable`` are the pair that matters: ``Package.version is
    None`` is true of both, and only ``Package.dynamic_version`` tells them apart.
    """
    tmp_project.add_package("static-pkg", "1.2.3")
    tmp_project.add_package(
        "file-pkg",
        version=None,
        dynamic_version=True,
        version_source={"kind": "file", "path": "__about__.py"},
    )
    tmp_project.write_file("packages/file-pkg/__about__.py", '__version__ = "2.0.0"\n')
    tmp_project.add_package("dynamic-pkg", version=None, dynamic_version=True)
    tmp_project.add_package("versionless-pkg", version=None)
    tmp_project.add_package(
        "tag-pkg",
        version=None,
        dynamic_version=True,
        build_requires=["hatchling", "hatch-vcs"],
        tool_tables={"hatch": {"version": {"source": "vcs"}}},
    )
    tmp_project.set_config(base_branch="main")
    return tmp_project


@pytest.mark.parametrize(
    ("package", "expected"),
    [
        ("static-pkg", CheckStatus.OK),
        ("file-pkg", CheckStatus.OK),
        ("versionless-pkg", CheckStatus.WARN),
        ("dynamic-pkg", CheckStatus.FAIL),
        ("tag-pkg", CheckStatus.FAIL),
    ],
)
def test_every_version_outcome_lands_at_its_own_status(
    four_outcomes: Any, package: str, expected: Any
) -> None:
    """``design.md`` D4's table, one row per outcome."""
    report = report_for(four_outcomes.root)

    assert status_of(report, "versions.resolvable", package) is expected


def test_versionless_warns_while_unresolvable_dynamic_fails(four_outcomes: Any) -> None:
    """The one mutation that fails quietly: collapsing the two turns a failure into a warning.

    Asserted as an inequality between two packages in **one** report, so an implementation that
    reported both at the same status fails no matter which status it picked.
    """
    report = report_for(four_outcomes.root)

    versionless = status_of(report, "versions.resolvable", "versionless-pkg")
    dynamic = status_of(report, "versions.resolvable", "dynamic-pkg")

    assert versionless is CheckStatus.WARN
    assert dynamic is CheckStatus.FAIL
    assert versionless is not dynamic


def test_a_resolvable_package_names_where_its_version_came_from(four_outcomes: Any) -> None:
    """spec: "A resolvable package names its source"."""
    report = report_for(four_outcomes.root)
    rows = {row.subject: row for row in rows_of(report, "versions.resolvable")}

    assert "1.2.3" in rows["static-pkg"].message
    assert "[project].version" in rows["static-pkg"].message
    assert "2.0.0" in rows["file-pkg"].message
    assert "__about__.py" in rows["file-pkg"].message


def test_a_git_tag_source_is_named_as_refused_not_as_missing(four_outcomes: Any) -> None:
    """spec: "A git-tag-derived version is named as refused, not as missing".

    Distinguishable from the merely-unlocatable row, which is the requirement: a user must be able
    to tell "molt will not do this" from "molt could not find it".
    """
    report = report_for(four_outcomes.root)
    rows = {row.subject: row for row in rows_of(report, "versions.resolvable")}

    refused = rows["tag-pkg"].message
    assert "git tag" in refused
    assert "known limit" in refused
    assert refused != rows["dynamic-pkg"].message
    assert "could not work out" in rows["dynamic-pkg"].message


def test_every_package_appears_including_the_skipped_ones(four_outcomes: Any) -> None:
    """spec: "The check covers packages that were skipped, not only released ones"."""
    report = report_for(four_outcomes.root)
    reported = {row.subject for row in rows_of(report, "versions.resolvable")}

    # `workspace-root` is there because uv makes the root a member of its own workspace; asserting
    # set equality rather than a subset is what makes this row notice a package going missing.
    assert reported == {
        "workspace-root",
        "static-pkg",
        "file-pkg",
        "dynamic-pkg",
        "versionless-pkg",
        "tag-pkg",
    }


# ======================================================================================
# Changesets
# ======================================================================================


def test_an_empty_queue_is_healthy(tmp_project: Any) -> None:
    """spec: "An empty queue is healthy" -- zero pending is ``ok``, never a warning."""
    tmp_project.add_package("pkg-a", "1.0.0")
    (tmp_project.root / ".changeset").mkdir()

    report = report_for(tmp_project.root)

    assert status_of(report, "changesets.directory", "directory") is CheckStatus.OK
    assert status_of(report, "changesets.pending", "pending") is CheckStatus.OK


def test_a_missing_changeset_directory_names_molt_init(tmp_project: Any) -> None:
    tmp_project.add_package("pkg-a", "1.0.0")

    report = report_for(tmp_project.root)
    (directory,) = rows_of(report, "changesets.directory")

    assert directory.status is CheckStatus.FAIL
    assert "molt init" in directory.remedy


def test_a_malformed_changeset_is_named_by_path_and_the_rest_are_still_counted(
    tmp_project: Any,
) -> None:
    """spec: "A malformed changeset is named".

    The second half is what separates this check from ``read_changesets``, which fails the whole
    read on the first bad file: the two good changesets are still counted.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.write_changeset("good-one", {"pkg-a": "patch"}, "fine")
    tmp_project.write_changeset("good-two", {"pkg-a": "minor"}, "also fine")
    (tmp_project.root / ".changeset" / "broken.md").write_bytes(b"no frontmatter here\n")

    report = report_for(tmp_project.root)
    rows = rows_of(report, "changesets.pending")
    failures = [row for row in rows if row.status is CheckStatus.FAIL]

    assert len(failures) == 1
    assert "broken.md" in failures[0].subject
    assert "2 changesets waiting" in status_row(rows, "pending").message


def test_a_changeset_naming_an_unknown_package_fails(tmp_project: Any) -> None:
    """spec: "A changeset naming an unknown package fails"."""
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.write_changeset("stray", {"pkg-nope": "patch"}, "typo in the name")

    report = report_for(tmp_project.root)
    failures = [
        row for row in rows_of(report, "changesets.pending") if row.status is CheckStatus.FAIL
    ]

    assert len(failures) == 1
    assert "stray.md" in failures[0].subject
    assert "pkg-nope" in failures[0].message


def test_a_changeset_name_matches_its_package_across_pep_503_spellings(tmp_project: Any) -> None:
    """``Foo_Bar`` names the member declaring ``foo-bar``; a missed normalization fails this."""
    tmp_project.add_package("foo-bar", "1.0.0")
    tmp_project.write_changeset("spelled-differently", {"Foo_Bar": "patch"}, "same package")

    report = report_for(tmp_project.root)
    failures = [
        row for row in rows_of(report, "changesets.pending") if row.status is CheckStatus.FAIL
    ]

    assert failures == []


def status_row(rows: list[Any], subject: str) -> Any:
    """The one row in ``rows`` with ``subject``."""
    for row in rows:
        if row.subject == subject:
            return row
    raise AssertionError(f"no row for {subject!r}")


# ======================================================================================
# Groups and filters
# ======================================================================================


def test_a_fixed_group_names_the_packages_it_ties_together(tmp_project: Any) -> None:
    """The information nowhere else in molt's output: what a group resolves to *here*."""
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0")
    tmp_project.add_package("other", "1.0.0")
    tmp_project.set_config(fixed=[["pkg-*"]])

    report = report_for(tmp_project.root)
    (row,) = [r for r in rows_of(report, "groups.filters") if r.subject.startswith("fixed")]

    assert row.status is CheckStatus.OK
    assert "pkg-a" in row.message
    assert "pkg-b" in row.message
    assert "other" not in row.message


def test_an_ignore_entry_names_the_packages_it_removes_from_every_release(
    tmp_project: Any,
) -> None:
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("internal-tool", "1.0.0")
    tmp_project.set_config(ignore=["internal-tool"])

    report = report_for(tmp_project.root)
    (row,) = [r for r in rows_of(report, "groups.filters") if r.subject.startswith("ignore")]

    assert row.status is CheckStatus.OK
    assert "internal-tool" in row.message


def test_no_filters_configured_is_one_ok_row(tmp_project: Any) -> None:
    """Silence would be indistinguishable from "the check did not run"."""
    tmp_project.add_package("pkg-a", "1.0.0")

    report = report_for(tmp_project.root)
    (row,) = rows_of(report, "groups.filters")

    assert row.status is CheckStatus.OK


# ======================================================================================
# Workspace
# ======================================================================================


def test_a_directory_that_is_not_a_project_fails_and_its_dependants_are_not_applicable(
    tmp_path: Path,
) -> None:
    """spec: "A directory that is not a workspace at all"."""
    empty = tmp_path / "nothing-here"
    empty.mkdir()

    report = report_for(empty)
    (root,) = rows_of(report, "workspace.root")
    (packages,) = rows_of(report, "workspace.packages")
    (versions,) = rows_of(report, "versions.resolvable")

    assert root.status is CheckStatus.FAIL
    assert "pyproject.toml" in root.message
    assert packages.status is CheckStatus.WARN, "not applicable, not a second failure"
    assert versions.status is CheckStatus.WARN
    assert report.counts[CheckStatus.FAIL] >= 1


def test_a_manifest_that_cannot_be_parsed_is_reported_with_its_path(tmp_project: Any) -> None:
    """``read_manifest`` re-raises carrying the path, which is why that primitive exists."""
    tmp_project.add_package("pkg-a", "1.0.0")
    (tmp_project.root / "packages" / "pkg-a" / "pyproject.toml").write_bytes(
        b"[project\nname = broken\n"
    )

    report = report_for(tmp_project.root)
    (packages,) = rows_of(report, "workspace.packages")

    assert packages.status is CheckStatus.FAIL
    assert "pkg-a" in packages.message


def test_the_backend_and_the_package_count_are_reported(tmp_project: Any) -> None:
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("pkg-b", "1.0.0")

    report = report_for(tmp_project.root)
    (packages,) = rows_of(report, "workspace.packages")

    assert packages.status is CheckStatus.OK
    assert "3" in packages.message, "two members plus the workspace root uv makes a member"


# ======================================================================================
# Publishing readiness, and the network opt-in
# ======================================================================================


def test_a_present_token_is_reported_without_its_value(
    tmp_project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: "A present token is reported without its value".

    The sentinel is what makes this an assertion rather than a hope: it is a string that appears
    nowhere else, so a check that formatted the value anywhere in the row fails here.
    """
    sentinel = "pypi-AgEIcHlwaS5vcmcSENINELTHESENTINELVALUE"
    monkeypatch.setenv("UV_PUBLISH_TOKEN", sentinel)
    tmp_project.add_package("pkg-a", "1.0.0")

    report = report_for(tmp_project.root)
    (row,) = [r for r in rows_of(report, "publish.credentials") if r.subject == "token"]

    assert row.status is CheckStatus.OK
    assert "UV_PUBLISH_TOKEN" in row.message
    assert sentinel not in f"{row.subject}{row.message}{row.remedy}"


def test_an_absent_credential_warns_and_names_the_accepted_sources(
    tmp_project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: "An absent credential is a warning, not a failure"."""
    from molt.publish import ACTIONS_ID_TOKEN_TOKEN, ACTIONS_ID_TOKEN_URL, TOKEN_ENVIRONMENT

    for name in (*TOKEN_ENVIRONMENT, ACTIONS_ID_TOKEN_URL, ACTIONS_ID_TOKEN_TOKEN):
        monkeypatch.delenv(name, raising=False)
    tmp_project.add_package("pkg-a", "1.0.0")

    report = report_for(tmp_project.root)
    (row,) = [r for r in rows_of(report, "publish.credentials") if r.subject == "token"]

    assert row.status is CheckStatus.WARN
    for name in TOKEN_ENVIRONMENT:
        assert name in row.remedy


def test_offline_by_default_makes_no_request(
    tmp_project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: "Offline by default".

    Enforced by poisoning the client rather than by counting requests: a probe that ran would raise
    here, and a probe that was merely quiet would not be distinguishable from one that was skipped.
    """
    import httpx

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("`molt doctor` made a network request without --online")

    monkeypatch.setattr(httpx.Client, "request", refuse)
    monkeypatch.setattr(httpx.Client, "send", refuse)
    tmp_project.add_package("pkg-a", "1.0.0")

    report = report_for(tmp_project.root, online=False)
    (row,) = [r for r in rows_of(report, "publish.index") if r.subject == "index"]

    assert row.status is CheckStatus.OK
    assert "--online" in row.message


@pytest.mark.network
def test_an_unreachable_index_warns_and_does_not_change_the_exit_code(
    tmp_project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: "An unreachable index warns" -- a proxy is not a defect in the user's molt setup."""
    import httpx

    def unreachable(*args: object, **kwargs: object) -> None:
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx.Client, "send", unreachable)
    tmp_project.add_package("pkg-a", "1.0.0")
    (tmp_project.root / ".changeset").mkdir()
    tmp_project.set_config(base_branch="main")

    report = report_for(tmp_project.root, online=True)
    (row,) = [r for r in rows_of(report, "publish.index") if r.subject == "index"]

    assert row.status is CheckStatus.WARN
    assert row.status is not CheckStatus.FAIL


@pytest.mark.network
def test_a_non_responding_index_stops_at_its_timeout_and_the_report_is_still_produced(
    tmp_project: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: "The probe cannot hang the command".

    A real timeout cannot be waited out in a test, so the timeout is raised the way ``httpx`` raises
    it and the assertion is that the report survives it.
    """
    import httpx

    def times_out(*args: object, **kwargs: object) -> None:
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(httpx.Client, "send", times_out)
    tmp_project.add_package("pkg-a", "1.0.0")

    report = report_for(tmp_project.root, online=True)

    assert report.rows, "the report is produced even when the probe times out"
    (row,) = [r for r in rows_of(report, "publish.index") if r.subject == "index"]
    assert row.status is CheckStatus.WARN
