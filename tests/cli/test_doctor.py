"""Conformance tests for `molt doctor` -- the command, its streams and its exit code.

Net-new. changesets has no ``doctor`` command, so there is no upstream behaviour to conform to and
no row in ``roadmap/research/test-suite/``. Primary source: the accepted change
``openspec/changes/add-doctor-command/`` (``specs/doctor-command/spec.md``,
``specs/cli-shell/spec.md``, ``design.md`` D3 and D5). Website docs: ``website/docs/cli/doctor.md``.

The unit-level suite for the checks themselves is ``tests/doctor/``; this module drives the command
seam, which owns three things nothing else does: the stream split, the exit code, and the promise
that no secret leaves the process in either stream.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from tests.cli.fake_cli import RecordingConsole

if TYPE_CHECKING:
    from pathlib import Path

pytest.importorskip("molt.commands.doctor", reason="`molt doctor` lands with add-doctor-command")

from molt.commands import doctor as command

pytestmark = pytest.mark.functional


def run(root: Path, *, console: Any = None, **options: Any) -> Any:
    """Drive the command's ``run`` seam, returning ``(report, exit_code)``.

    The command raises ``ExitError`` for a failing report *after* producing it, so a row that cares
    about the report cannot simply let the exception escape.
    """
    from molt.errors import ExitError

    try:
        return command.run(cwd=root, console=console, **options), 0
    except ExitError as exit_request:
        return None, exit_request.code


@pytest.fixture
def healthy(tmp_project: Any) -> Any:
    """A workspace with nothing wrong with it: a package, a configuration, a changeset directory."""
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.set_config(base_branch="main")
    (tmp_project.root / ".changeset").mkdir()
    return tmp_project


# ======================================================================================
# Exit codes (spec: "The exit code reflects failures only, never warnings")
# ======================================================================================


def test_a_healthy_workspace_exits_zero(healthy: Any) -> None:
    console = RecordingConsole()

    report, code = run(healthy.root, console=console)

    assert code == 0
    assert report is not None
    assert report.failed is False


def test_warnings_alone_exit_zero(tmp_project: Any) -> None:
    """A versionless package warns; on its own it must not fail the run."""
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("app", version=None)
    tmp_project.set_config(base_branch="main")
    (tmp_project.root / ".changeset").mkdir()

    report, code = run(tmp_project.root, console=RecordingConsole())

    assert code == 0
    assert report is not None
    assert report.counts[report.rows[0].status.WARN] > 0


def test_an_uninitialized_directory_still_gets_a_report_and_exits_non_zero(
    tmp_project: Any,
) -> None:
    """spec: "An uninitialized directory still gets a report".

    ``doctor`` is the one verb that does not refuse a missing ``.changeset/``: being unsure whether
    the project is set up is the reason somebody runs it.
    """
    tmp_project.add_package("pkg-a", "1.0.0")
    console = RecordingConsole()

    _, code = run(tmp_project.root, console=console)
    text = "\n".join(call.message for call in console.calls)

    assert code == 1
    assert "molt init" in text
    assert "Environment" in text, "the whole report is produced, not just the failure"


# ======================================================================================
# The stream contract (spec: "The report is available as a machine-readable document")
# ======================================================================================


def test_the_document_is_the_only_thing_on_stdout(
    healthy: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """spec: "The document is the only thing on stdout"."""
    console = RecordingConsole()

    run(healthy.root, console=console, output="json")
    captured = capsys.readouterr()

    document = json.loads(captured.out)
    assert document["status"] == "ok"
    assert captured.out.count("{") >= 1
    assert "Environment" not in captured.out


def test_the_human_report_never_reaches_stdout(
    healthy: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without ``--output`` the report goes through the console seam, which writes to stderr."""
    console = RecordingConsole()

    run(healthy.root, console=console)
    captured = capsys.readouterr()

    assert captured.out == ""
    assert any("Environment" in call.message for call in console.calls)


def test_statuses_and_counts_survive_the_round_trip(
    tmp_project: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """spec: "Statuses survive the round trip"."""
    tmp_project.add_package("pkg-a", "1.0.0")
    tmp_project.add_package("app", version=None)

    run(tmp_project.root, console=RecordingConsole(), output="json")
    document = json.loads(capsys.readouterr().out)

    statuses = [row["status"] for row in document["rows"]]
    assert set(statuses) >= {"ok", "warn", "fail"}
    for status in ("ok", "warn", "fail"):
        assert document["summary"][status] == statuses.count(status)


def test_the_exit_code_matches_the_document(
    tmp_project: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """spec: "The exit code matches the document"."""
    tmp_project.add_package("pkg-a", "1.0.0")

    _, code = run(tmp_project.root, console=RecordingConsole(), output="json")
    document = json.loads(capsys.readouterr().out)

    assert document["summary"]["fail"] > 0
    assert document["status"] == "fail"
    assert document["exit_code"] == code == 1


def test_the_document_keys_are_snake_case(healthy: Any, capsys: pytest.CaptureFixture[str]) -> None:
    """Matching molt's plan documents, so one consumer style reads every molt payload."""
    run(healthy.root, console=RecordingConsole(), output="json")
    document = json.loads(capsys.readouterr().out)

    keys = {*document, *document["rows"][0]}
    assert all(key == key.lower() and " " not in key for key in keys)
    assert "exit_code" in document


def test_an_output_value_other_than_json_is_refused(healthy: Any) -> None:
    """A filename is refused rather than written to -- writing files is what this command is not."""
    from molt.errors import MoltError

    with pytest.raises(MoltError, match="only accepted value"):
        command.run(cwd=healthy.root, console=RecordingConsole(), output="report.json")


# ======================================================================================
# Redaction (spec: "Credentials are reported as present or absent and never disclosed")
# ======================================================================================

#: A string that appears nowhere in molt. Any occurrence in either stream is a leak.
_SENTINEL = "pypi-AgEIcHlwaSDoNoTdIsClOsEtHiSvAlUe0123456789"


@pytest.mark.parametrize("output", [None, "json"])
def test_a_token_value_appears_in_neither_stream(
    healthy: Any,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    output: str | None,
) -> None:
    """spec: "A present token is reported without its value" and "The machine-readable report
    carries no secret either".

    This row is the enforcement mechanism for the whole no-secrets requirement, so it runs over
    **both** renderings: a check that formatted the value would otherwise be caught in one and not
    the other. Every stream is searched -- stdout, stderr, and everything the console recorded --
    because a leak is a leak wherever it lands.
    """
    monkeypatch.setenv("UV_PUBLISH_TOKEN", _SENTINEL)
    console = RecordingConsole()

    run(healthy.root, console=console, output=output)
    captured = capsys.readouterr()
    everything = "\n".join([captured.out, captured.err, *console.messages])

    assert _SENTINEL not in everything
    assert "UV_PUBLISH_TOKEN" in everything, "the source is named, so the row is still useful"


def test_not_even_a_fragment_of_the_token_is_disclosed(
    healthy: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Masking is the pattern that leaks: somebody later prints an unmasked copy while debugging,
    and a ``pypi-****`` mask still shows a prefix. The value is never read at all instead.

    Every 8-character window of the token is searched rather than the whole string, so a row that
    printed only the first or last few characters fails here too -- which a whole-string check
    would happily pass.
    """
    monkeypatch.setenv("UV_PUBLISH_TOKEN", _SENTINEL)
    console = RecordingConsole()

    run(healthy.root, console=console)
    text = "\n".join(call.message for call in console.calls)

    windows = [_SENTINEL[i : i + 8] for i in range(len(_SENTINEL) - 7)]
    assert [window for window in windows if window in text] == []


# ======================================================================================
# Read-only (spec: "`doctor` is read-only and never raises")
# ======================================================================================


def test_nothing_in_the_project_is_written(healthy: Any) -> None:
    """spec: "Nothing is written" -- the whole tree is byte-identical afterwards."""
    before = {path: path.read_bytes() for path in sorted(healthy.root.rglob("*")) if path.is_file()}

    run(healthy.root, console=RecordingConsole())

    after = {path: path.read_bytes() for path in sorted(healthy.root.rglob("*")) if path.is_file()}
    assert after == before


def test_a_raising_check_does_not_stop_the_command(
    healthy: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec: "A raising check does not stop the report", asserted through the command seam.

    Breaks a check in the **shipped** registry rather than injecting a synthetic one, so what this
    row exercises is the path a real crash would take -- registry, runner, renderer and exit code.
    """
    from molt.doctor.checks.environment import RuntimeCheck

    def explode(self: object, ctx: object) -> list[object]:
        del self, ctx
        raise RuntimeError("boom")

    monkeypatch.setattr(RuntimeCheck, "run", explode)
    console = RecordingConsole()

    _, code = run(healthy.root, console=console)
    text = "\n".join(call.message for call in console.calls)

    assert code == 1
    assert "environment.runtime" in text
    assert "boom" in text
    assert "Publishing" in text, "every later check still ran"
    assert "Traceback" not in text


# ======================================================================================
# The CLI surface (cli-shell delta: "`doctor` is registered as a read-only command")
# ======================================================================================


def test_doctor_offers_no_dry_run_flag() -> None:
    """spec: "No dry-run flag" -- every run is already a dry run, so there is nothing to guard.

    Read from the parser rather than the rendered help: ``rich`` wraps the help column, so a
    substring assertion over the rendered text can be defeated by a line break, and it fires on any
    prose that merely mentions the flag.
    """
    pytest.importorskip("typer", reason="typer lands with the cli-shell change")
    from tests.cli.fake_cli import require_cli_app
    from typer.main import get_command

    # `get_command` is typed as returning `Command`; the app is a group, whose `.commands` mapping
    # only `Group` declares. Narrowed for the checker, not worked around.
    group: Any = get_command(require_cli_app())
    subcommand = group.commands["doctor"]
    declared = {flag for param in subcommand.params for flag in param.opts}

    assert "--dry-run" not in declared
    assert {"--output", "--online", "--cwd"} <= declared
