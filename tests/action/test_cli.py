"""Conformance tests for ``molt-action`` -- the command-line entry point the composite runs.

Port source
-----------
``changesets/action`` **v1.9.0** (fetched and read 2026-07-31):

* ``src/index.ts:14-20`` -- the token comes from the environment and its absence fails the run
  immediately, before anything else happens.
* ``src/index.ts:167`` -- the pull-request number is reported on the version path alone.
* ``action.yml`` outputs -- the four meanings, renamed to snake_case (research doc 04 section 6.7).

What is **not** a port: the ``GITHUB_OUTPUT`` writer. Upstream calls ``@actions/core``'s
``setOutput``; molt writes the file itself, always in the ``name<<DELIMITER`` heredoc form, because
``name=value`` truncates on the first newline and does it silently (change design D4).

Doubles, and what no row may touch
-----------------------------------
:func:`molt.action.orchestrate.run_action` is replaced by :class:`Recorder` in every row that runs
the command, so **no row reaches a forge, a remote, a subprocess or the network** -- what is under
test here is the shell around the loop, not the loop, which ``tests/action/test_orchestrate.py``
already pins. Every row also runs against a scrubbed environment (:func:`clean_environment`), so an
ambient ``GITHUB_TOKEN`` or ``GITHUB_OUTPUT`` -- on a developer's machine or on the runner molt's
own repository uses -- cannot change an outcome.

The output rows read the file back with :func:`read_github_output`, a parser written to GitHub's
**documented** rules (both accepted forms) rather than to molt's writer, so a writer that fell back
to ``name=value`` still parses and only its multi-line values come back wrong. It is still molt
reading molt, which is recorded as a weak gate (``openspec/GAPS.md`` ``CO-13``) and covered by the
manual run behind ``CO-12``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("molt.action.cli", reason="molt.action.cli is this suite's target")

from molt.action.cli import OUTPUT_NAMES, main, output_payload, write_github_output
from molt.action.orchestrate import ActionFailed, ActionResult
from molt.action.run import CommitMode, PublishedPackage
from molt.errors import ExitError, MoltError

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = [pytest.mark.unit]

#: A token shaped like a real one and valid nowhere. Every row that runs the command needs one,
#: because the entry point refuses before it does anything else without it.
TOKEN = "ghp_conformance_token"

#: GitHub's two accepted output-file forms, as the runner documents them.
_HEREDOC = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)<<(.+)$")
_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)=(.*)$")


# ======================================================================================
# Harness
# ======================================================================================


class Recorder:
    """A stand-in for ``run_action`` that records its keywords and answers a programmed result.

    It accepts keywords only. A positional call would be a caller bypassing the keyword-only
    contract :func:`molt.action.run_action` declares, and recording it as an argument tuple would
    hide that rather than fail it.
    """

    def __init__(
        self, result: ActionResult | None = None, error: BaseException | None = None
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._result = ActionResult() if result is None else result
        self._error = error

    def __call__(self, **kwargs: Any) -> ActionResult:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._result

    @property
    def kwargs(self) -> dict[str, Any]:
        """The one call this row made. The count assertion is what catches a double run."""
        assert len(self.calls) == 1, f"expected exactly one run_action call, got {len(self.calls)}"
        return self.calls[0]


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Drop every ambient variable this entry point reads, then supply a token.

    ``GITHUB_OUTPUT`` is deliberately left unset, so a row that does not opt into a file of its own
    cannot append to a real runner's.
    """
    for name in ("GITHUB_OUTPUT", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    yield


def double(monkeypatch: pytest.MonkeyPatch, recorder: Recorder) -> Recorder:
    """Replace ``run_action`` on the module the entry point resolves it from."""
    monkeypatch.setattr("molt.action.orchestrate.run_action", recorder)
    return recorder


def read_github_output(path: Path) -> dict[str, str]:
    """Parse a ``GITHUB_OUTPUT`` file the way the runner documents it.

    Two accepted forms, and this reader honours **both**: ``name=value`` for a single-line value,
    and ``name<<DELIMITER`` followed by the value and a line holding the delimiter alone. A line
    matching neither is ignored, exactly as a stray continuation line would be on the runner --
    which is what makes a truncating writer show up as a wrong *value* rather than as a crash.
    """
    values: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        heredoc = _HEREDOC.match(line)
        if heredoc is not None:
            name, delimiter = heredoc.group(1), heredoc.group(2)
            body: list[str] = []
            while index < len(lines) and lines[index] != delimiter:
                body.append(lines[index])
                index += 1
            index += 1  # step over the closing delimiter
            values[name] = "\n".join(body)
            continue
        assignment = _ASSIGNMENT.match(line)
        if assignment is not None:
            values[assignment.group(1)] = assignment.group(2)
    return values


def published(*packages: tuple[str, str]) -> ActionResult:
    """A publish-phase result announcing ``packages``."""
    return ActionResult(
        published=True,
        published_packages=tuple(PublishedPackage(name=name, version=v) for name, v in packages),
    )


# ======================================================================================
# 1. Every option reaches the release loop
# ======================================================================================

#: ``(argv, keyword, expected value)`` -- one row per option the entry point declares. The values
#: are deliberately distinguishable from each other, so an option wired to the *wrong* keyword
#: fails rather than passing on a shared default.
OPTION_ROWS = [
    pytest.param(
        ["--publish", "molt publish --filter pkg-a"],
        "publish",
        ("molt", "publish", "--filter", "pkg-a"),
        id="publish",
    ),
    # The quoted argument is the point: it proves the split is `shlex`, not `str.split`, the same
    # rule `--publish` above inherits (`CO-16`).
    pytest.param(
        ["--version-command", 'molt version --snapshot "canary build"'],
        "version_script",
        ("molt", "version", "--snapshot", "canary build"),
        id="version-command",
    ),
    pytest.param(["--title", "Release train"], "title", "Release train", id="title"),
    pytest.param(
        ["--commit-message", "chore: release"], "commit_message", "chore: release", id="commit"
    ),
    pytest.param(["--create-releases", "false"], "create_releases", False, id="create-releases"),
    pytest.param(["--base-branch", "develop"], "base_branch", "develop", id="base-branch"),
    # The mode arrives as the string a composite substitutes and reaches the loop as the enum
    # member, never as that string: `run_version` compares identities, so a raw string would
    # silently select the git command line on a workflow that asked for the API.
    pytest.param(["--commit-mode", "api"], "commit_mode", CommitMode.API, id="commit-mode"),
    pytest.param(["--cwd", "."], "cwd", Path("."), id="cwd"),
]


@pytest.mark.parametrize(("argv", "keyword", "expected"), OPTION_ROWS)
@pytest.mark.usefixtures("clean_environment")
def test_every_option_reaches_the_release_loop(
    argv: list[str], keyword: str, expected: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each action input arrives at ``run_action`` as the keyword that means it.

    ``--publish`` is split into argv with POSIX rules rather than handed over as a string:
    ``run_action`` takes a command as a sequence precisely so a publish command with arguments is
    expressible, which is build step 22's documented fix of upstream's bare-string script
    (``run.ts:121``).
    """
    recorder = double(monkeypatch, Recorder())

    assert main(argv) == 0
    assert recorder.kwargs[keyword] == expected


# ======================================================================================
# 2. Boolean inputs arrive as the strings a composite substitutes
# ======================================================================================

#: A composite forwards ``${{ inputs.create-releases }}`` verbatim, so a boolean reaches this
#: process as a string -- and an input the workflow left out reaches it as an empty one.
BOOLEAN_ROWS = [
    pytest.param("true", True, id="true"),
    pytest.param("TRUE", True, id="upper-case"),
    pytest.param("false", False, id="false"),
    pytest.param("", True, id="empty-is-the-default"),
]


@pytest.mark.parametrize(("written", "expected"), BOOLEAN_ROWS)
@pytest.mark.usefixtures("clean_environment")
def test_a_boolean_input_is_read_case_insensitively(
    written: str, expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``true`` / ``false`` in any case; an empty string means "not supplied"."""
    recorder = double(monkeypatch, Recorder())

    assert main(["--create-releases", written]) == 0
    assert recorder.kwargs["create_releases"] is expected


@pytest.mark.usefixtures("clean_environment")
def test_an_unrecognised_boolean_is_rejected_naming_the_option(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A typo is refused, not defaulted (design D3, ``openspec/GAPS.md`` ``CO-8``).

    GitHub itself coerces loosely. molt does not: a workflow writing ``yes`` and quietly getting
    ``false`` stops creating host releases and reports nothing about it. The message names the
    option, because the workflow author sees only the input's own spelling.
    """
    recorder = double(monkeypatch, Recorder())

    assert main(["--create-releases", "yes"]) == 1

    assert recorder.calls == [], "the loop must not run on an unreadable input"
    assert "--create-releases" in capsys.readouterr().err


#: A commit mode the entry point must refuse. The second row is the migration case and is the
#: reason the first one is not enough: ``github-api`` is upstream's own spelling, so it is the value
#: a workflow moving off ``changesets/action@v1`` is most likely to carry across unchanged.
REJECTED_COMMIT_MODES = [
    pytest.param("gitcli", id="a-typo"),
    pytest.param("github-api", id="upstreams-spelling-is-not-an-alias"),
]


@pytest.mark.parametrize("written", REJECTED_COMMIT_MODES)
@pytest.mark.usefixtures("clean_environment")
def test_an_unrecognised_commit_mode_is_refused_naming_both_spellings(
    written: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``CO-8``'s ratified rule, applied to the mode (api-commits design D8).

    Coercing a typo to the default would leave a repository failing its branch protection on every
    release with nothing in the log to explain why -- the mode is invisible in the output, so there
    is no other signal at all.

    ``github-api`` is refused **deliberately**, not by omission: accepting it as an alias would put
    a host name back into the surface the rename exists to clear. The message carries the migration
    instead, which is why both accepted spellings have to appear in it.
    """
    recorder = double(monkeypatch, Recorder())

    assert main(["--commit-mode", written]) == 1

    assert recorder.calls == [], "the loop must not run on an unreadable input"
    error = capsys.readouterr().err
    assert "--commit-mode" in error
    assert "git-cli" in error and "api" in error, "the message is the migration"


@pytest.mark.parametrize("written", ["", "   ", "git-cli", "GIT-CLI"])
@pytest.mark.usefixtures("clean_environment")
def test_the_default_mode_is_the_git_command_line(
    written: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absent, empty or the git command line: all three are today's behaviour, unchanged.

    An empty string is what a composite substitutes for an input the workflow left out (``CO-17``),
    and the spelling is read case-insensitively for the same reason booleans are -- GitHub itself
    coerces loosely, so a workflow that wrote ``GIT-CLI`` means the mode it named.
    """
    recorder = double(monkeypatch, Recorder())

    assert main(["--commit-mode", written]) == 0
    assert recorder.kwargs["commit_mode"] is CommitMode.GIT_CLI


# ======================================================================================
# 3. The token (index.ts:14-20)
# ======================================================================================


def test_a_missing_token_fails_before_the_release_loop_runs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No token is a hard failure, and it happens first (``index.ts:16-20``).

    "Before any other work" is the half that matters and the half an exit-code assertion alone
    would not catch: every phase of the loop ends in a forge call, so discovering the missing
    credential *after* the version script has rewritten the tree and pushed a branch is the worst
    possible moment to discover it. The recorder proves the ordering.
    """
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    recorder = double(monkeypatch, Recorder())

    assert main([]) == 1

    assert recorder.calls == []
    assert "GITHUB_TOKEN" in capsys.readouterr().err


# ======================================================================================
# 4. The output contract (design D4)
# ======================================================================================


@pytest.mark.usefixtures("clean_environment")
def test_the_outputs_are_written_to_the_output_file_in_the_delimiter_form(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A version run writes all four outputs, and they read back unchanged."""
    output = tmp_path / "outputs.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    double(monkeypatch, Recorder(ActionResult(has_changesets=True, pull_request_number=42)))

    assert main([]) == 0

    written = output.read_text(encoding="utf-8")
    assert "published<<" in written, "every value takes the heredoc form, never `name=value`"
    assert read_github_output(output) == {
        "published": "false",
        "published_packages": "[]",
        "has_changesets": "true",
        "pull_request_number": "42",
    }


def test_a_value_with_a_newline_or_an_equals_sign_round_trips(tmp_path: Path) -> None:
    """The delimiter form is what makes any value safe, and this is the row that proves it.

    ``name=value`` is silently wrong on the first newline -- the workflow reads the first line and
    carries on -- so the writer never emits it. ``published_packages`` is single-line JSON *today*;
    "happens to be" is not a contract, and one uniformly correct code path is cheaper than a rule
    about which values are safe.
    """
    output = tmp_path / "outputs.txt"

    write_github_output(
        {"multiline": "first\nsecond\nthird", "equals": "a=b=c", "blank": ""}, path=output
    )

    assert read_github_output(output) == {
        "multiline": "first\nsecond\nthird",
        "equals": "a=b=c",
        "blank": "",
    }


@pytest.mark.usefixtures("clean_environment")
def test_no_pull_request_means_no_pull_request_number_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The number is written only when there is one (``index.ts:167``).

    Upstream sets it inside the version branch alone. An output that is never written reads as an
    empty string in a workflow expression, which is what a downstream ``if:`` wants -- writing an
    empty value instead is indistinguishable to the workflow but is a claim molt did not make.
    """
    output = tmp_path / "outputs.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    double(monkeypatch, Recorder(published(("pkg-a", "1.2.0"))))

    assert main([]) == 0

    values = read_github_output(output)
    assert "pull_request_number" not in values
    assert set(values) == {"published", "published_packages", "has_changesets"}


def test_published_packages_is_a_json_array_of_name_and_version() -> None:
    """The documented payload shape, so a following step can ``fromJSON`` it.

    Order follows what ``run_publish`` reported; it is not re-derived from tags or from the
    workspace, so the array is the record of what actually went out.
    """
    payload = output_payload(published(("pkg-a", "1.2.0"), ("pkg-b", "0.4.1")))

    assert json.loads(payload["published_packages"]) == [
        {"name": "pkg-a", "version": "1.2.0"},
        {"name": "pkg-b", "version": "0.4.1"},
    ]
    assert payload["published"] == "true"


@pytest.mark.usefixtures("clean_environment")
def test_without_an_output_file_the_values_go_to_stdout_as_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Run by hand, the four values are a JSON document on stdout and nothing is written.

    molt's stream contract (``website/docs/cli/overview.md``): machine payloads to stdout, human
    output to stderr. It also makes the serialisation observable without a runner.
    ``pull_request_number`` keeps its key as ``null`` here -- a JSON document can say "no number"
    and a workflow output file cannot.
    """
    monkeypatch.chdir(tmp_path)
    double(monkeypatch, Recorder(published(("pkg-a", "1.2.0"))))

    assert main([]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == set(OUTPUT_NAMES)
    assert payload["published"] == "true"
    assert payload["pull_request_number"] is None
    assert list(tmp_path.iterdir()) == [], "no file is written when GITHUB_OUTPUT is unset"


# ======================================================================================
# 5. The exit-code contract (research doc 03 section 11.6)
# ======================================================================================

#: ``(raised, expected exit code, expected fragment on stderr)``.
EXIT_ROWS = [
    pytest.param(MoltError("the release branch is protected"), 1, "protected", id="molt-error"),
    pytest.param(ExitError(7), 7, "code: 7", id="exit-error-keeps-its-code"),
]


@pytest.mark.parametrize(("raised", "code", "fragment"), EXIT_ROWS)
@pytest.mark.usefixtures("clean_environment")
def test_a_failing_run_reports_molts_exit_code(
    raised: BaseException,
    code: int,
    fragment: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A ``MoltError`` is a sentence plus 1; an ``ExitError`` propagates a child's own status.

    The second row is the one CI depends on: when the publish command exits 7, the workflow step
    has to exit 7 too, or a half-failed release is reported as a pass.
    """
    double(monkeypatch, Recorder(error=raised))

    assert main([]) == code

    captured = capsys.readouterr()
    assert fragment in captured.err
    assert "Traceback" not in captured.err, "an expected failure is a sentence, not a stack"


# ======================================================================================
# 6. A failing run still writes its outputs (owner ruling 2026-08-01)
# ======================================================================================


@pytest.mark.usefixtures("clean_environment")
def test_a_failing_run_still_writes_its_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A half-published release hands the list of packages that went out to the next step.

    That is the run where reading ``published_packages`` matters most, and it is exactly the run
    that used to report nothing: the funnel returned the exit code before ``report()`` ever ran, so
    a workflow step guarded with ``if: always()`` read an empty file. ``ActionFailed`` carries the
    partial result out of the loop and the same one writer puts it on disk before the failure is
    reported.
    """
    output = tmp_path / "outputs.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    failure = ActionFailed(3, result=published(("pkg-a", "1.2.0")))
    double(monkeypatch, Recorder(error=failure))

    assert main([]) == 3, "the child's status is still what the step exits with"

    values = read_github_output(output)
    assert values["published"] == "true"
    assert json.loads(values["published_packages"]) == [{"name": "pkg-a", "version": "1.2.0"}]


@pytest.mark.usefixtures("clean_environment")
def test_a_failure_that_observed_nothing_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain ``MoltError`` writes no outputs, deliberately.

    There is no observed result to write, and synthesising one would report
    ``has_changesets = false`` about a repository molt never finished reading. An output file must
    not carry a value molt did not observe, so this hole is pinned rather than papered over.
    """
    output = tmp_path / "outputs.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    double(monkeypatch, Recorder(error=MoltError("the release branch is protected")))

    assert main([]) == 1
    assert not output.exists()
