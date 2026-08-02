"""The ``molt-action`` console script -- what the composite action actually runs.

Ports the thin shell of ``changesets/action`` v1.9.0 ``src/index.ts`` (fetched and read
2026-07-31): read the token, read the inputs, run the loop, report the outputs. Everything the
loop *does* is :func:`molt.action.run_action`, which is already shipped and pinned; nothing here
re-derives a release rule.

Why this is a second console script and not a ``molt`` verb
-----------------------------------------------------------
``tests/cli/test_cli.py`` asserts that the set of registered commands equals a frozen ``COMMANDS``
tuple -- "an undeclared command is an unspecified command" -- so a new verb means renegotiating a
pinned conformance row. ``molt --help`` is also a documented product surface
(``website/docs/cli/overview.md``); a verb only a CI runner types does not belong in it. A second
``[project.scripts]`` entry costs one line, keeps that surface unchanged, and ``uvx`` puts it on
``PATH`` beside ``molt`` so the version and publish commands still resolve to the *same* pinned
install (change design D2).

Two contracts this module owns
------------------------------
**The token never arrives as an option.** It is read from ``GITHUB_TOKEN`` in the environment, and
its absence fails before any other work (``index.ts:14-20``). A token on a command line lands in
the process table and in ``set -x`` output.

**Outputs are always written in the delimiter form** (design D4). ``name=value`` is silently wrong
the moment a value contains a newline -- the workflow reads a truncated value rather than failing
-- so :func:`write_github_output` writes ``name<<DELIMITER`` / value / ``DELIMITER`` for every
value, with a random delimiter per run, and refuses if a value contains it.

**Outputs are written for every run that observed a result -- success or failure -- and before the
failure is reported** (owner ruling 2026-08-01). A release that half-published is precisely the run
where a following ``if: always()`` step most needs to read ``published_packages``, so
:class:`molt.action.ActionFailed` carries the partial result out of the loop and
:func:`run_release_loop` hands it to the same :func:`report` the success path uses -- one writer,
so the delimiter form, the ``None``-drops and the stdout fallback cannot diverge between the two.

What that deliberately does **not** cover: a run that fails with a plain
:class:`~molt.errors.MoltError` writes nothing. There is no observed result to write, and
synthesising one would report ``has_changesets = false`` about a repository molt never finished
reading -- an output file must not carry a value molt did not observe.

The exit-code funnel below is a deliberate **narrow second copy** of :func:`molt.cli.main`'s: an
``ExitError`` keeps its code, a ``MoltError`` is its message plus 1, ``KeyboardInterrupt`` is 0,
anything else is 1 with a traceback (research doc 03 section 11.6). The original is welded to the
Typer app it drives, and extracting it would touch ``tests/cli/test_cli.py``'s pinned exit-code
rows. Extraction sketch, when somebody next does touch them: a ``molt.errors.exit_code_for(exc)``
helper both funnels call, leaving each one only its own console wiring
(``openspec/GAPS.md`` ``CO-5``).
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import traceback
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Final

import typer

from molt.errors import ExitError, MoltError
from molt.ui.console import console

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from molt.action.orchestrate import ActionResult

__all__ = [
    "ACTION_INPUTS",
    "OUTPUT_NAMES",
    "TOKEN_VARIABLE",
    "app",
    "main",
    "output_payload",
    "parse_boolean_input",
    "parse_commit_mode",
    "write_github_output",
]


# ======================================================================================
# The input and output surfaces -- the two names `action.yml` and this module share
# ======================================================================================

#: Every input ``action.yml`` declares -> the entry-point option it becomes, or ``None`` when the
#: composite consumes it itself and it never reaches this process.
#:
#: ``tests/action/test_action_yml.py`` compares this table against the real ``action.yml`` in both
#: directions and resolves each option against the Typer command's **actual** parameters, so an
#: input added to the YAML with no home here -- or renamed on either side -- fails a row rather
#: than reaching a user as a silently ignored setting.
ACTION_INPUTS: Final[dict[str, str | None]] = {
    # Consumed by the install step: it is the `==` pin in `uvx --from molt-release==<version>`, so
    # the process this table describes is a *different* molt than the one that read the input.
    "molt-version": None,
    "publish": "publish",
    # The version half of `publish`, added by owner ruling 2026-07-31 closing gap `CO-1`, which
    # reversed the earlier "not in v1" exclusion. Spelled `version-command` rather than upstream's
    # `version` because `molt-version` already exists and names a *distribution* pin: two inputs
    # differing by a prefix, one a CLI version and one a shell command, is a copy-paste waiting to
    # go wrong.
    "version-command": "version-command",
    "title": "title",
    # Upstream's spelling is `commit`; the option is `--commit-message` because a bare `--commit`
    # on a command line reads as a switch ("commit? yes") rather than as the message itself.
    "commit": "commit-message",
    "create-releases": "create-releases",
    "base-branch": "base-branch",
    # Upstream's `commitMode`, kebab-cased. Its `git-cli` default keeps upstream's spelling so a
    # migrating workflow does not have to change a value it already has; its other value is `api`
    # rather than `github-api`, because molt's forge seam means the host is not necessarily GitHub
    # (`CO-7`'s rename rule). `github-api` is deliberately NOT an alias -- the error message is the
    # migration (api-commits design D8).
    "commit-mode": "commit-mode",
    # The credential. It reaches this process as an environment variable and never as an option.
    "github-token": None,
}

#: The four outputs, snake_case (``website/docs/guides/ci-github-action.md``) -- a deliberate
#: divergence from upstream's camelCase ``action.yml``. Order is the order they are written in.
OUTPUT_NAMES: Final[tuple[str, ...]] = (
    "published",
    "published_packages",
    "has_changesets",
    "pull_request_number",
)

#: Where the host token comes from. Upstream reads the same variable first and falls back to its
#: input (``index.ts:14-20``); molt's composite does that fallback in the shell, so by the time
#: this process runs there is exactly one source.
TOKEN_VARIABLE: Final = "GITHUB_TOKEN"

#: The environment variable GitHub sets to the file a step appends its outputs to.
OUTPUT_FILE_VARIABLE: Final = "GITHUB_OUTPUT"

#: The two spellings a boolean input may take, case-insensitively. Anything else is refused rather
#: than guessed: a typo silently meaning ``false`` would stop creating releases with no signal.
_TRUE: Final = "true"
_FALSE: Final = "false"

#: Typer maps ``KeyboardInterrupt`` to this POSIX code before the funnel below can see it; molt's
#: contract is 0 (research doc 03 section 11.6), exactly as in :func:`molt.cli.main`.
_POSIX_SIGINT_EXIT: Final = 130


# ======================================================================================
# Input parsing
# ======================================================================================


def parse_boolean_input(value: str | None, *, option: str) -> bool | None:
    """``value`` as a boolean, ``None`` when it was not supplied.

    A composite action forwards ``${{ inputs.create-releases }}`` verbatim, so a boolean arrives
    as the *string* GitHub substituted -- and an input the workflow left out arrives as an empty
    string rather than as an absent option.

    ``true`` and ``false`` are accepted in any case. Everything else raises, naming the option and
    the accepted spellings (design D3): GitHub itself coerces loosely, but a workflow that writes
    ``yes`` and quietly gets ``false`` stops creating releases and reports nothing.
    """
    if value is None:
        return None
    text = value.strip().lower()
    if not text:
        return None
    if text == _TRUE:
        return True
    if text == _FALSE:
        return False
    raise MoltError(
        f"`{option}` must be `{_TRUE}` or `{_FALSE}` (any case); got `{value}`. "
        f"Leave it empty to use the default."
    )


def parse_commit_mode(value: str | None, *, option: str) -> Any:
    """``value`` as a :class:`molt.action.CommitMode`, ``None`` when it was not supplied.

    The same contract as :func:`parse_boolean_input`, for the same reasons: a composite forwards
    ``${{ inputs.commit-mode }}`` verbatim, so the value arrives as a string and an input the
    workflow left out arrives as an empty one (``openspec/GAPS.md`` ``CO-17``).

    An unrecognised value is a **hard error** naming the option and both accepted spellings (the
    rule ratified as ``CO-8``). That matters more here than anywhere else: a typo silently meaning
    ``git-cli`` would leave a repository failing its branch protection on every release with
    nothing in the log to explain why, and the one value a migrating workflow is most likely to
    carry across -- upstream's ``github-api`` -- is exactly such a typo. It is not accepted as an
    alias, deliberately: the error message carries the migration instead of putting a host name
    back into the surface the rename exists to clear (api-commits design D8).

    :class:`~molt.action.CommitMode` is imported inside the body, matching the lazy dispatch this
    module already uses for :mod:`molt.action.orchestrate`: it lives beside the version loop, which
    pulls the ecosystem layer, and ``molt-action --help`` must not pay for that.
    """
    from molt.action.run import CommitMode

    if value is None:
        return None
    text = value.strip().lower()
    if not text:
        return None
    for mode in CommitMode:
        if text == mode.value:
            return mode
    accepted = " or ".join(f"`{mode.value}`" for mode in CommitMode)
    raise MoltError(
        f"`{option}` must be {accepted}; got `{value}`. "
        f"Leave it empty to use the default (`{CommitMode.GIT_CLI.value}`)."
    )


def _argv(value: str | None) -> tuple[str, ...] | None:
    """A command-line string as argv, ``None`` when it was not supplied.

    Split with POSIX rules because the composite's shell is ``bash`` on the runner, so
    ``publish: molt publish --filter "pkg-*"`` means there what it means here.
    """
    if value is None or not value.strip():
        return None
    return tuple(shlex.split(value))


def _text(value: str | None) -> str | None:
    """A string input, ``None`` when it was not supplied.

    An empty string is "the workflow did not set this", not "set it to empty": a composite action
    substitutes an unset input as ``''``, and an empty pull-request title is not a thing anyone
    asks for.
    """
    if value is None or not value.strip():
        return None
    return value


def _token() -> str:
    """The host token, or a refusal naming the variable (``index.ts:16-20``).

    Read before anything else runs. Every phase of the loop ends in a forge call, so discovering a
    missing token after the version script has already rewritten the tree is the worst possible
    moment to discover it.
    """
    token = os.environ.get(TOKEN_VARIABLE, "").strip()
    if not token:
        raise MoltError(
            f"No host token available: set {TOKEN_VARIABLE} in the step's environment "
            f"(the action's `github-token` input defaults to the workflow's own token)."
        )
    return token


# ======================================================================================
# The output contract (design D4)
# ======================================================================================


def output_payload(result: ActionResult) -> dict[str, Any]:
    """The four outputs of a run, as the values a workflow reads.

    ``published_packages`` is a **compact** JSON array of ``{"name", "version"}`` objects -- the
    shape ``website/docs/guides/ci-github-action.md`` documents -- so a following step can
    ``fromJSON`` it. ``pull_request_number`` is ``None`` for a run that opened or updated no pull
    request, which is every publish run and every run that did nothing; the file writer drops it
    entirely in that case (``index.ts:167`` sets it inside the version branch alone), while the
    stdout document keeps the key as ``null`` because a JSON document can say "no number" and a
    workflow output file cannot.
    """
    return {
        "published": _bool_text(result.published),
        "published_packages": json.dumps(
            [
                {"name": package.name, "version": package.version}
                for package in result.published_packages
            ],
            separators=(",", ":"),
        ),
        "has_changesets": _bool_text(result.has_changesets),
        "pull_request_number": (
            None if result.pull_request_number is None else str(result.pull_request_number)
        ),
    }


def _bool_text(value: bool) -> str:
    """A boolean as the string a workflow expression compares against."""
    return _TRUE if value else _FALSE


def write_github_output(values: Mapping[str, Any], *, path: Path) -> None:
    """Append ``values`` to a ``GITHUB_OUTPUT`` file, always in the delimiter form.

    ::

        name<<DELIMITER
        ...value...
        DELIMITER

    Never ``name=value``. That form is wrong the moment a value contains a newline, and it is
    *silently* wrong -- the workflow reads a truncated value and carries on. One code path,
    uniformly correct, is cheaper than a rule about which values are safe today.

    The delimiter is random per run and every value is checked against it, which is what stops a
    value from being read as the end of its own output. A value of ``None`` is not written at all.

    The file is opened in **append** mode with LF newlines: GitHub appends every step's outputs to
    one file, and a CRLF line ending would leave the delimiter line unrecognised on the runner.
    """
    delimiter = f"ghadelimiter_{uuid.uuid4().hex}"
    lines: list[str] = []
    for name, value in values.items():
        if value is None:
            continue
        text = str(value)
        if delimiter in text:
            raise MoltError(
                f"The `{name}` output contains the generated delimiter and cannot be written "
                f"safely. Re-run the step; the delimiter is random per run."
            )
        lines.append(f"{name}<<{delimiter}\n{text}\n{delimiter}\n")
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(lines))


def report(result: ActionResult) -> None:
    """Write the run's outputs where the caller can read them.

    Inside a workflow that is the ``GITHUB_OUTPUT`` file. Outside one -- running the entry point by
    hand -- the four values go to **stdout** as one JSON document and no file is written, which
    follows molt's own stream contract (machine payloads to stdout, human output to stderr;
    ``website/docs/cli/overview.md``) and makes the serialisation observable without a runner.
    """
    payload = output_payload(result)
    destination = os.environ.get(OUTPUT_FILE_VARIABLE, "").strip()
    if not destination:
        sys.stdout.write(json.dumps(payload) + "\n")
        return
    write_github_output(payload, path=Path(destination))


# ======================================================================================
# The application
# ======================================================================================

app = typer.Typer(
    name="molt-action",
    help=(
        "Run molt's release loop for a CI workflow. Invoked by molt's composite GitHub Action; "
        "not part of the `molt` command table."
    ),
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@app.command()
def run_release_loop(
    publish: Annotated[
        str | None,
        typer.Option("--publish", help="The publish command to run in the publish phase."),
    ] = None,
    # Split through the same `_argv` helper `--publish` uses, so POSIX rules apply to both and
    # `CO-16` (POSIX splitting on every platform, Windows shells included) covers the pair rather
    # than growing a second answer to one question.
    version_command: Annotated[
        str | None,
        typer.Option(
            "--version-command", help="The command to run in the version phase. Defaults to molt."
        ),
    ] = None,
    title: Annotated[
        str | None, typer.Option("--title", help="Title of the release pull request.")
    ] = None,
    commit_message: Annotated[
        str | None,
        typer.Option("--commit-message", help="Commit message for the release branch."),
    ] = None,
    create_releases: Annotated[
        str | None,
        typer.Option("--create-releases", help="Create a host release per published package."),
    ] = None,
    base_branch: Annotated[
        str | None,
        typer.Option("--base-branch", help="The branch being released. Defaults to the checkout."),
    ] = None,
    commit_mode: Annotated[
        str | None,
        typer.Option(
            "--commit-mode",
            help="How the version commit reaches the remote: git-cli (default) or api.",
        ),
    ] = None,
    cwd: Annotated[
        Path | None,
        typer.Option("--cwd", help="Directory to run in. Defaults to the current directory."),
    ] = None,
) -> None:
    """Run one half of the release loop and report its four outputs."""
    # The import is deliberately inside the body, mirroring `molt.cli`'s lazy command dispatch:
    # `molt.action.orchestrate` pulls the ecosystem, publish and versioning layers, and `--help`
    # must not pay for them. It is looked up as a module attribute rather than bound at import,
    # which is what lets a test replace it.
    from molt.action import orchestrate
    from molt.action.run import CommitMode

    _token()
    releases = parse_boolean_input(create_releases, option="--create-releases")
    mode = parse_commit_mode(commit_mode, option="--commit-mode")

    try:
        result = orchestrate.run_action(
            cwd=Path.cwd() if cwd is None else cwd,
            publish=_argv(publish),
            version_script=_argv(version_command),
            title=_text(title),
            commit_message=_text(commit_message),
            base_branch=_text(base_branch),
            create_releases=True if releases is None else releases,
            commit_mode=CommitMode.GIT_CLI if mode is None else mode,
        )
    # The outputs are written for a failing run too, through the *same* `report` (owner ruling
    # 2026-08-01). This does not belong in `main()`'s funnel: that funnel maps exceptions to exit
    # codes and knows nothing about results.
    except orchestrate.ActionFailed as failure:
        report(failure.result)
        raise
    report(result)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the entry point and return its exit code. The ``molt-action`` console script.

    See the module docstring on why this funnel is a narrow second copy of
    :func:`molt.cli.main`'s rather than a shared helper (``openspec/GAPS.md`` ``CO-5``).
    """
    args = list(sys.argv[1:] if argv is None else argv)
    command = typer.main.get_command(app)

    try:
        command.main(args=args, prog_name="molt-action", standalone_mode=True)
    except SystemExit as exit_request:
        raw = exit_request.code
        code = 0 if raw is None else raw if isinstance(raw, int) else 1
        if code == _POSIX_SIGINT_EXIT:
            # An exit 0 with no message is indistinguishable from success, so cancellation says so.
            console.info("Canceled")
            return 0
        return code
    except KeyboardInterrupt:
        console.info("Canceled")
        return 0
    except ExitError as error:
        # A child process -- the version script or the publish command -- exited non-zero and molt
        # is propagating its status verbatim.
        console.error(str(error))
        return error.code
    except MoltError as error:
        console.error(str(error))
        return 1
    # A blanket `except Exception` is the funnel's entire job: catching what nothing else did.
    except Exception:
        console.error(traceback.format_exc())
        console.error("Exited with code 1")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
