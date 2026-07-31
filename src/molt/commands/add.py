"""`molt add` -- write a changeset, interactively or from flags, stdin or `--empty`.

Ports ``packages/cli/src/commands/add/index.ts`` and ``createChangeset.ts``; the behavioral source
of truth is research doc 03 section 2 (prompt primitives) plus section 9.2 (failure catalogue) and
section 10 (gotchas). The 41-row conformance suite is ``tests/cli/test_add.py``.

The widest branch matrix in the project, and one funnel through it (design D1). Four ways to say
*what* to release -- prompts, ``--package``/``--bump``, ``--major``/``--minor``/``--patch``, a JSON
payload on stdin -- collapse into one ordered ``[(package, bump)]`` list before anything is
validated or written, so "that package does not exist" is implemented once rather than four times.
Three ways to say *why* -- a prompt, ``$EDITOR``, ``--message`` -- collapse into one string.

**Non-interactive ``add`` is the differentiator** (research README section 5 item 4). changesets
cannot do it at all: its multiselect has no scripted equivalent, so a bot cannot participate in the
workflow. Every outcome here is reachable with no TTY, and ``--non-interactive`` is checked *before*
a prompt is constructed (design D4) -- not "prompt with a fallback", which is the difference between
a flag that works in CI and one that hangs a build for six hours.

Three upstream bugs are deliberately **not** ported (research doc 03 section 11.7, each pinned by a
row of the conformance suite):

- section 10.6 -- ``askWithEditor`` post-processes the edited file with
  ``replace(/^#.*\\n?/gm, "")`` and so deletes every Markdown heading the author typed. molt
  preserves them.
- section 10.8 -- declining the first-major confirmation *aborts with exit 1* in a single-package
  repository while the monorepo path falls through to the minor prompt. molt falls through in both.
- section 10.7 -- ``--major``/``--minor``/``--patch`` bypass the first-major guard entirely. molt
  applies it to flag-selected majors too; ``--non-interactive`` is the documented escape hatch.

And one deliberate divergence that is not a bug fix: the flag path does **no** changed-package
detection (design D7). Detection is a git call, a caller who has already named the packages should
not pay for it, and in a shallow CI clone it can fail outright. Upstream runs it unconditionally
(``add/index.ts:72-89``) and then ignores the answer in that flow.

**Owner ruling AC-3 (2026-07-30) narrows the previous paragraph's "molt falls through in both".**
That is still true of the *interactive* flow. On the *flag* path there is no minor prompt to fall
through to, so a declined flag-selected first major now **aborts the whole run** -- nothing is
written, exit code 0, one line reported -- rather than being silently downgraded to a minor. See
:func:`_confirm_first_majors` and :class:`_FirstMajorAborted`.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, Final

from molt.ui.prompts import is_cancel

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from molt.changeset import Changeset
    from molt.config.models import Config
    from molt.ecosystem import Package, Workspace
    from molt.versioning import BumpType

__all__ = ["run"]


# ======================================================================================
# Message constants -- the sentences a user or a bot log is going to search for
# ======================================================================================

#: ``ensureChangesetFolder`` (``commands/shared.ts:7-20``), with ``changeset init`` adapted. The
#: third line is why ``add`` does not silently create the folder: a deleted ``.changeset/`` is far
#: more often an accident than a fresh project, and scaffolding over it hides the accident.
_NO_CHANGESET_FOLDER: Final = (
    "There is no .changeset folder.\n"
    "If this is the first time molt is used in this project, run `molt init` to get set up.\n"
    "If you expected there to be changesets, check the git history for when the folder was "
    "removed so you do not lose any changesets."
)

#: ``add/index.ts:52-61``, with ``package.json`` adapted to ``pyproject.toml``.
_NO_VERSIONABLE_PACKAGES: Final = (
    "No versionable packages found.\n"
    "- Ensure the packages to version are not ignored by the config.\n"
    "- Ensure that the packages have a `version` field in their pyproject.toml."
)

_NO_SELECTION: Final = (
    "--non-interactive was given but no packages were selected, and molt may not prompt. "
    "Name the releases with --package/--bump, with --major/--minor/--patch, on --stdin, "
    "or record that nothing releases with --empty."
)

_NO_MESSAGE: Final = (
    "--non-interactive was given but no changeset summary was supplied, and molt may not prompt. "
    'Pass --message; use --message "" for a deliberately empty summary.'
)

#: The prompt wording. Upstream's questions restated in molt's voice; only the substrings the
#: conformance suite reads ("Which packages", the package name, its current version) are contracts.
_PACKAGES_PROMPT: Final = "Which packages would you like to include?"
_MAJOR_PROMPT: Final = "Which packages should have a major bump?"
_MINOR_PROMPT: Final = "Which packages should have a minor bump?"
_SUMMARY_PROMPT: Final = "Please enter a summary for this change (this will be in the changelogs)."
_EDITOR_PROMPT: Final = "Please enter a summary for this change."
_SUMMARY_RETRY_PROMPT: Final = (
    "Did not find a summary in the editor. Please enter a summary for this change:"
)
_CANCELED: Final = "Canceled"

#: Printed when a **flag-selected** first major is declined (owner ruling AC-3, 2026-07-30). A
#: distinct message from ``_CANCELED``: "you said no to a first major" and "you hit Ctrl-C" are
#: different events, and reporting the wrong one would tell a bot the wrong thing to search its log
#: for.
_FIRST_MAJOR_ABORTED: Final = "First major declined for {name}; nothing was written."

#: Group labels of the partitioned package multiselect (``createChangeset.ts:59-64``). Changed
#: packages come first, and an empty group is omitted rather than rendered as an empty box.
_CHANGED_GROUP: Final = "changed packages"
_UNCHANGED_GROUP: Final = "unchanged packages"

#: The release-type flags, in the order their errors and their releases are reported.
#: ``createChangeset.ts:128-138`` lists them this way whatever order the command line used.
_RELEASE_TYPE_FLAGS: Final[tuple[tuple[str, str], ...]] = (
    ("--major", "major"),
    ("--minor", "minor"),
    ("--patch", "patch"),
)

#: The source labels a ``--package``/``--bump`` pair and a stdin payload report themselves under.
#: One validator, three drive modes -- ``--stdin`` is another way to spell the same changeset, not
#: a second grammar with its own diagnostics.
_PACKAGE_FLAG: Final = "--package"
_STDIN_FLAG: Final = "--stdin"

#: Below this, a major release is the package's *first* major and is worth confirming.
_FIRST_MAJOR_BOUNDARY: Final = "1.0.0"


class _Canceled(Exception):
    """Internal control flow: a prompt was cancelled. Caught in :func:`run`, never escapes it.

    Cancelling exits **0** and writes nothing (design D5; research doc 03 section 11.6 -- a
    deliberate, documented divergence from POSIX 130): a user who aborted a prompt has not failed
    at anything. An exception rather than a sentinel threaded through six return types, because
    every prompt in the flow can produce one and all of them unwind to the same place.
    """


class _FirstMajorAborted(Exception):
    """Internal control flow: a **flag-selected** first major was declined. Caught in :func:`run`,
    never escapes it -- the same exit-0/writes-nothing contract as :class:`_Canceled` (owner ruling
    AC-3, 2026-07-30), but its own message: "nothing was written because a first major was
    declined" is a different event from "nothing was written because a prompt was cancelled", and
    conflating the two would misreport why to a user or a bot scanning the log.

    The **interactive** flow is unchanged by this ruling -- a declined confirmation there still
    falls through to the minor prompt (research doc 03 section 10.8; molt divergence item 2),
    because the interactive path always has a next question left to ask. The flag path never did,
    which is exactly why upstream skips the guard there entirely (research doc 03 section 10.7) and
    why silently downgrading to minor was the wrong fix: the caller asked for major and never
    agreed to minor.
    """

    def __init__(self, package_name: str) -> None:
        super().__init__(package_name)
        self.package_name = package_name


@dataclasses.dataclass(frozen=True)
class _Request:
    """One package-and-bump a non-interactive surface asked for, before validation.

    ``name`` is the spelling the caller wrote -- the error messages quote it back, and PEP 503
    matching happens at *lookup* time (design D3), never by rewriting what the user typed.
    """

    name: str
    bump: str
    source: str


@dataclasses.dataclass(frozen=True)
class _Input:
    """Everything the non-interactive surfaces supplied, before validation.

    ``summary`` is ``None`` when no surface carried one, which is a different answer from ``""``:
    a stdin payload with ``"summary": ""`` asked for an empty summary, and one with no ``summary``
    key asked for nothing and still needs ``--message`` or a prompt.
    """

    requests: tuple[_Request, ...]
    summary: str | None


# ======================================================================================
# The command
# ======================================================================================


def run(
    *,
    cwd: Path | None = None,
    console: Any = None,
    prompts: Any = None,
    git: Any = None,
    empty: bool = False,
    # `open` shadows the builtin in this signature only: the keyword is the CLI contract
    # (`--open` -> `open=`) and is not ours to rename. Nothing here calls the builtin.
    open: bool = False,
    since: str | Sequence[str] | None = None,
    message: str | None = None,
    major: Sequence[str] | None = None,
    minor: Sequence[str] | None = None,
    patch: Sequence[str] | None = None,
    package: Sequence[str] | None = None,
    bump: str | None = None,
    stdin: bool = False,
    dry_run: bool = False,
    non_interactive: bool = False,
    **options: Any,
) -> None:
    """Write one changeset into the workspace's ``.changeset/`` folder.

    ``console``/``prompts``/``git`` are injectable seams (:mod:`tests.cli.fake_cli`); a real
    invocation gets the module-level console, the concrete prompt implementation, and a
    :class:`molt.git.Git` bound to the workspace root. The git seam is built **lazily**, so a run
    that neither detects changed packages nor commits never shells out at all.

    Heavy imports happen here, not at module scope: ``tests/cli/test_cli.py``'s import-light
    assertion pins that for every ``molt.commands.*`` module.
    """
    del options

    from pathlib import Path

    from molt.changeset import CHANGESET_DIR
    from molt.config import load_config
    from molt.ecosystem import discover_workspace, find_workspace_root
    from molt.errors import ExitError

    if console is None:
        from molt.ui.console import console as _console

        console = _console
    if prompts is None:
        from molt.ui.prompts import QuestionaryPrompts

        prompts = QuestionaryPrompts()

    root = find_workspace_root(cwd if cwd is not None else Path.cwd())
    changeset_dir = root / CHANGESET_DIR
    if not changeset_dir.is_dir():
        # Before the package scan and before any prompt (`add/index.ts:33-42`): a missing folder is
        # a precondition failure, and reporting it after a five-question interview is rude.
        console.error(_NO_CHANGESET_FOLDER)
        raise ExitError(1)

    config = _load_config(root, console, load_config)
    workspace = discover_workspace(root, config.ecosystem)
    versionable = _versionable_packages(workspace, config)
    if not versionable:
        console.error(_NO_VERSIONABLE_PACKAGES)
        raise ExitError(1)

    try:
        selection, summary = _resolve(
            console=console,
            prompts=prompts,
            git=git,
            root=root,
            config=config,
            workspace=workspace,
            versionable=versionable,
            empty=empty,
            since=since,
            message=message,
            major=major,
            minor=minor,
            patch=patch,
            package=package,
            bump=bump,
            stdin=stdin,
            non_interactive=non_interactive,
        )
    except _Canceled:
        console.info(_CANCELED)
        return
    except _FirstMajorAborted as exc:
        # Owner ruling AC-3 (2026-07-30): a flag-selected first major that was declined aborts the
        # whole run, exactly like a cancelled prompt -- exit 0, nothing written -- but with its own
        # message naming which package and why.
        console.info(_FIRST_MAJOR_ABORTED.format(name=exc.package_name))
        return

    changeset = _build_changeset(changeset_dir, selection, summary)
    if dry_run:
        _report_dry_run(console, changeset)
        return

    path = _write(root, changeset, config)
    console.success(f"Changeset added: {path}")
    _commit(config, changeset, path, root=root, git=git)

    if open:
        # `add/index.ts:168-170`: last, and fire-and-forget. The file is on disk and reported
        # before the editor is launched, so a slow or broken editor cannot lose the changeset.
        prompts.editor(_EDITOR_PROMPT, file=str(path))


# ======================================================================================
# Configuration, discovery and the versionable set
# ======================================================================================


def _load_config(root: Path, console: Any, load_config: Any) -> Config:
    """Resolve the workspace configuration, rendering **both** channels of the result.

    ``load_config`` never raises, and ``config is None`` exactly when ``errors`` is non-empty.
    Warnings are printed even on the success path -- reporting one problem per run is the thing
    the three-part result shape exists to avoid.
    """
    from molt.errors import ExitError

    result = load_config(root)
    for warning in result.warnings:
        console.warn(str(warning))
    if result.config is None:
        console.error("\n".join(str(error) for error in result.errors))
        raise ExitError(1)
    return result.config


def _selectable_packages(workspace: Workspace) -> list[Package]:
    """Every package ``add`` may offer, before the versionable filters.

    The **root** of a uv workspace is excluded. ``website/docs/ecosystems/uv.md`` is explicit that
    ``[tool.uv.workspace] members`` is what ``molt add`` offers you, and upstream reaches the same
    place by its root ``package.json`` being private with no version. A single-package repository
    has no such distinction, so its one package stays.
    """
    root_package = workspace.root_package
    if workspace.backend != "uv" or root_package is None:
        return list(workspace.packages)
    return [pkg for pkg in workspace.packages if pkg.directory != root_package.directory]


def _versionable_packages(workspace: Workspace, config: Config) -> list[Package]:
    """The packages a changeset may name, sorted by normalized name.

    Ports ``shouldSkipPackage`` (``should-skip-package/src/index.ts:13-22``) minus its npm half:

    - **ignored** -- ``config.ignore`` arrives already expanded to concrete workspace names, so
      membership is literal, never a second glob match. Both sides still go through PEP 503
      normalization (research README section 4.5): one forgotten call site is a package that
      silently never matches its own entry.
    - **no version** -- a manifest with no ``[project].version``. In Python that also covers
      ``dynamic = ["version"]``, which :class:`molt.ecosystem.Package` represents as ``None``.
    - **private** -- the ``Private :: Do Not Upload`` classifier, withheld only when
      ``private_packages.version`` is off. It defaults to **on**, because a private application is
      still versioned so its internal pins stay correct.
    """
    from molt.names import normalize_name

    ignored = {normalize_name(name) for name in config.ignore}
    version_private = config.private_packages.version
    keep = [
        pkg
        for pkg in _selectable_packages(workspace)
        if pkg.version is not None
        and pkg.normalized_name not in ignored
        and (version_private or not pkg.private)
    ]
    return sorted(keep, key=lambda pkg: pkg.normalized_name)


# ======================================================================================
# The selection funnel (design D1)
# ======================================================================================


def _resolve(
    *,
    console: Any,
    prompts: Any,
    git: Any,
    root: Path,
    config: Config,
    workspace: Workspace,
    versionable: list[Package],
    empty: bool,
    since: str | Sequence[str] | None,
    message: str | None,
    major: Sequence[str] | None,
    minor: Sequence[str] | None,
    patch: Sequence[str] | None,
    package: Sequence[str] | None,
    bump: str | None,
    stdin: bool,
    non_interactive: bool,
) -> tuple[list[tuple[Package, BumpType]], str]:
    """Produce the release list and the summary, whichever surface supplied them.

    Everything downstream -- writing, committing, ``--open`` -- sees one shape, which is what keeps
    the four drive modes from growing four copies of every rule.
    """
    from molt.errors import ExitError

    if empty:
        # `add/index.ts:66-71`: `--empty` short-circuits *everything* -- no changed-package
        # detection, no prompts, no summary interview. It is the CI-safe way to record
        # "this change needs no release" and still satisfy the `status` gate.
        return [], message if message is not None else ""

    supplied = _gather_input(
        console=console,
        major=major,
        minor=minor,
        patch=patch,
        package=package,
        bump=bump,
        stdin=stdin,
    )

    if supplied is None and non_interactive:
        # Design D4: checked before a prompt is constructed, not after one is built and answered
        # from a default. The conformance suite asserts that *no prompt was issued*.
        console.error(_NO_SELECTION)
        raise ExitError(1)

    if supplied is not None:
        selection = _validate(
            supplied.requests, versionable, console, workspace=workspace, config=config
        )
        selection = _confirm_first_majors(selection, prompts, non_interactive=non_interactive)
    else:
        selection = _interactive_selection(
            prompts=prompts,
            git=git,
            root=root,
            config=config,
            versionable=versionable,
            since=since,
        )

    if message is not None:
        # `createChangeset.ts:273-278` returns early with the supplied message *after* the bump
        # prompts have run, so `-m` replaces the summary and nothing else. Only `None` -- the flag
        # being absent -- triggers prompting; `-m ""` is a provided, empty summary.
        return selection, message
    if supplied is not None and supplied.summary is not None:
        return selection, supplied.summary
    if non_interactive:
        console.error(_NO_MESSAGE)
        raise ExitError(1)
    return selection, _capture_summary(prompts)


def _gather_input(
    *,
    console: Any,
    major: Sequence[str] | None,
    minor: Sequence[str] | None,
    patch: Sequence[str] | None,
    package: Sequence[str] | None,
    bump: str | None,
    stdin: bool,
) -> _Input | None:
    """Collect every non-interactive input, or ``None`` when no surface supplied one.

    ``None`` and an empty :class:`_Input` are different answers: ``None`` means "nobody named a
    package, so ask", while an empty request list means "a surface was used and it selected
    nothing".
    """
    from molt.errors import ExitError

    by_flag: Mapping[str, Sequence[str] | None] = {
        "--major": major,
        "--minor": minor,
        "--patch": patch,
    }
    requests: list[_Request] = []
    summary: str | None = None
    used = False

    for flag, kind in _RELEASE_TYPE_FLAGS:
        names = by_flag[flag]
        if not names:
            continue
        used = True
        requests.extend(_Request(name, kind, flag) for name in _unique(names))

    if package:
        used = True
        if bump is None:
            console.error(
                "--package selects which packages to release; --bump says how much. "
                "Pass --bump major, minor, patch or none alongside it."
            )
            raise ExitError(1)
        kind = _bump_name(bump, console)
        requests.extend(_Request(name, kind, _PACKAGE_FLAG) for name in _unique(package))

    if stdin:
        used = True
        payload_requests, summary = _read_stdin_payload(console)
        requests.extend(payload_requests)

    return _Input(tuple(requests), summary) if used else None


def _unique(names: Sequence[str]) -> list[str]:
    """``names`` with PEP 503 duplicates removed, first spelling kept.

    Repeating one name under **one** flag is not the duplicate error: that rule is about a package
    carrying two different bump types (``createChangeset.ts:118-138``).
    """
    from molt.names import normalize_name

    seen: set[str] = set()
    kept: list[str] = []
    for name in names:
        key = normalize_name(name)
        if key in seen:
            continue
        seen.add(key)
        kept.append(name)
    return kept


def _bump_name(value: str, console: Any) -> str:
    """Validate a written bump type, reporting the accepted set rather than raising a bare error."""
    from molt.errors import ExitError
    from molt.versioning import BumpType

    try:
        return BumpType(value).value
    except ValueError:
        accepted = ", ".join(kind.value for kind in BumpType)
        console.error(f"`{value}` is not a release type. Use one of: {accepted}.")
        raise ExitError(1) from None


def _read_stdin_payload(console: Any) -> tuple[list[_Request], str | None]:
    """Parse the documented ``--stdin`` payload (``website/docs/cli/add.md``, "Non-interactive").

    ``{"releases": [{"name": ..., "bump": ...}], "summary": ...}`` -- the whole changeset in one
    JSON object, which is what makes ``--stdin`` a complete drive mode rather than half of one:
    a payload carrying a ``summary`` needs neither ``--message`` nor a TTY.

    Every rejection exits 1 rather than writing a partial changeset. ``--stdin`` is the bot-facing
    surface, so a malformed payload has to appear in the bot's log as a failure -- not as a silently
    empty changeset that fails much later, at ``molt version``.

    An absent ``summary`` key is **not** an error: it leaves the summary to ``--message`` or to the
    prompt, exactly as the flag path does.
    """
    from molt.errors import ExitError
    from molt.versioning import BumpType

    payload = _stdin_payload(console)
    summary = payload.get("summary")
    if summary is not None and not isinstance(summary, str):
        console.error(
            f"The --stdin payload's `summary` must be a string, not {type(summary).__name__}."
        )
        raise ExitError(1)
    releases = payload.get("releases")
    if not isinstance(releases, list):
        console.error(
            "The --stdin payload's `releases` must be a list of {name, bump} objects, "
            f"not {type(releases).__name__}."
        )
        raise ExitError(1)

    accepted = ", ".join(kind.value for kind in BumpType)
    requests: list[_Request] = []
    for index, entry in enumerate(releases):
        if not isinstance(entry, dict):
            console.error(f"`releases[{index}]` of the --stdin payload must be an object.")
            raise ExitError(1)
        name = entry.get("name")
        kind = entry.get("bump")
        if not isinstance(name, str) or not name.strip():
            console.error(f"`releases[{index}]` of the --stdin payload has no package `name`.")
            raise ExitError(1)
        if not isinstance(kind, str):
            console.error(
                f"`releases[{index}]` of the --stdin payload has no `bump`; "
                f"one of {accepted} is required."
            )
            raise ExitError(1)
        try:
            bump = BumpType(kind)
        except ValueError:
            console.error(
                f"`releases[{index}]` of the --stdin payload has bump `{kind}`; "
                f"one of {accepted} is required."
            )
            raise ExitError(1) from None
        requests.append(_Request(name, bump.value, _STDIN_FLAG))
    return requests, summary


def _stdin_payload(console: Any) -> dict[str, Any]:
    """Read and decode standard input as the ``--stdin`` JSON object.

    ``sys.stdin`` is looked up on the module at call time rather than bound at import: that is what
    lets a caller (or a test) replace the stream, and ``--stdin`` is explicitly not an injected
    seam.
    """
    import json
    import sys

    from molt.errors import ExitError

    raw = sys.stdin.read()
    if not raw.strip():
        console.error(
            "--stdin was given but standard input was empty. Pipe a JSON payload such as "
            '{"releases": [{"name": "acme-core", "bump": "patch"}], "summary": "..."}.'
        )
        raise ExitError(1)
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        console.error(f"Could not parse the --stdin payload as JSON: {exc}")
        raise ExitError(1) from exc
    if not isinstance(payload, dict):
        console.error("The --stdin payload must be a JSON object with a `releases` list.")
        raise ExitError(1)
    return payload


# ======================================================================================
# Validation (design D2 -- existence gates duplication)
# ======================================================================================


def _not_releasable_message(request: _Request, package: Package | None, config: Config) -> str:
    """The message for one request naming a package outside the versionable set.

    Owner ruling AC-4 (2026-07-30): a name matching **nothing** discovered in the project reports
    the pre-existing "not found" message; a name that resolves to a package the project *did*
    discover, but excludes from releasing, names the package and the reason class instead --
    reporting the second as though it were the first sends the user chasing a typo that is not
    there.
    """
    if package is None:
        return (
            f"The package {request.name} is passed to the `{request.source}` option "
            "but it is not found in the project."
        )
    return (
        f"The package {request.name} is passed to the `{request.source}` option "
        f"but it is {_unreleasable_reason(package, config)} and cannot be released."
    )


def _unreleasable_reason(package: Package, config: Config) -> str:
    """Which of :func:`_versionable_packages`'s three rules excludes ``package``.

    Re-applies the same three checks, in the same order, rather than inventing a second
    classification: a change to one rule changes its explanation for free. A package can fail more
    than one rule at once; the first is reported. The fallback string is defensive only -- every
    caller of this function already knows ``package`` is outside the versionable set, so one of the
    three checks always fires.
    """
    from molt.names import normalize_name

    if package.version is None:
        return "versionless (it declares no `version` in its pyproject.toml)"
    ignored = {normalize_name(name) for name in config.ignore}
    if package.normalized_name in ignored:
        return "ignored (listed in this project's `ignore` configuration)"
    if package.private and not config.private_packages.version:
        return "private, and `private_packages.version` is disabled for this project"
    return "not releasable"  # pragma: no cover - defensive; see docstring


def _validate(
    requests: Sequence[_Request],
    versionable: Sequence[Package],
    console: Any,
    *,
    workspace: Workspace,
    config: Config,
) -> list[tuple[Package, BumpType]]:
    """Resolve every request to a package, in a **fixed order: existence, then duplication**.

    ``validateDuplicatePackageNames`` intersects with the *known* names first
    (``createChangeset.ts:118-121``), so a name that is both unknown and repeated produces two
    "not found" errors and no duplicate error. Reporting both would tell the user to drop a flag
    when the real fix is to spell the name correctly.

    Every message from one pass is emitted as a **single** console error joined by newlines: a bot
    fixing one typo per run would otherwise need three runs to find three typos.

    **Owner ruling AC-4 (2026-07-30) distinguishes two failure classes** that used to share one
    message: a name matching **nothing** discovered in the project, and a name that resolves to a
    **discovered-but-excluded** package (ignored, private with private-package versioning off, or
    versionless). ``workspace`` and ``config`` exist on this signature only to answer that second
    question -- ``versionable`` alone cannot, because it has already dropped the excluded packages.
    """
    from molt.errors import ExitError
    from molt.names import normalize_name
    from molt.versioning import BumpType

    known = {pkg.normalized_name: pkg for pkg in versionable}
    discovered = {pkg.normalized_name: pkg for pkg in _selectable_packages(workspace)}

    unknown = [
        _not_releasable_message(request, discovered.get(normalize_name(request.name)), config)
        for request in requests
        if normalize_name(request.name) not in known
    ]
    if unknown:
        console.error("\n".join(unknown))
        raise ExitError(1)

    sources: dict[str, list[str]] = {}
    spellings: dict[str, str] = {}
    for request in requests:
        key = normalize_name(request.name)
        spellings.setdefault(key, request.name)
        bucket = sources.setdefault(key, [])
        if request.source not in bucket:
            bucket.append(request.source)

    order = [flag for flag, _ in _RELEASE_TYPE_FLAGS] + [_PACKAGE_FLAG, _STDIN_FLAG]
    duplicates = [
        f"The package {spellings[key]} is passed to multiple release type options: "
        + ", ".join(f"`{flag}`" for flag in sorted(flags, key=order.index))
        + ". Please select only one release type for this package."
        for key, flags in sources.items()
        if len(flags) > 1
    ]
    if duplicates:
        console.error("\n".join(duplicates))
        raise ExitError(1)

    return [(known[normalize_name(request.name)], BumpType(request.bump)) for request in requests]


# ======================================================================================
# The first-major confirmation (molt FIX of research doc 03 sections 10.7 and 10.8)
# ======================================================================================


def _is_first_major(package: Package) -> bool:
    """Whether a major release would be ``package``'s first (``createChangeset.ts:16``).

    ``semverLt(version, "1.0.0")`` upstream; PEP 440 comparison here. An unparseable version is
    treated as *not* pre-1.0: the guard exists to slow down an accidental 1.0.0, and stopping to
    ask about a version molt cannot even order would be a worse failure than skipping the question.
    """
    from packaging.version import InvalidVersion, Version

    if package.version is None:
        return False
    try:
        return Version(package.version) < Version(_FIRST_MAJOR_BOUNDARY)
    except InvalidVersion:
        return False


def _confirm_message(package: Package) -> str:
    """The confirmation's wording. Only the package name in it is a contract."""
    return (
        f"{package.name} is at {package.version}. Releasing a major version takes it to 1.0.0 "
        "and declares the API stable. Are you sure?"
    )


def _confirm_first_majors(
    selection: list[tuple[Package, BumpType]], prompts: Any, *, non_interactive: bool
) -> list[tuple[Package, BumpType]]:
    """Ask before a flag-selected first major, and **abort** rather than downgrade on a decline.

    Upstream never calls ``confirmMajorRelease`` on the flag path (``createChangeset.ts:148-170``),
    so ``changeset --major pkg-a`` silently bypasses the one safety net first majors have. molt
    applies the guard here too (research doc 03 section 11.7 item 3).

    **Owner ruling AC-3 (2026-07-30).** A declined confirmation used to be silently downgraded to a
    minor -- the flag path's only option, since it has no minor prompt to fall through to the way
    the interactive multiselect does. The owner ruled that silence is the wrong failure: the caller
    asked for a major and never agreed to a minor, so declining now raises
    :class:`_FirstMajorAborted`, which :func:`run` turns into a clean, nothing-written exit. This
    function is called **only** from the flag/``--package``/``--stdin`` path (:func:`_resolve`'s
    ``supplied is not None`` branch); the interactive flow's own confirmation logic lives in
    :func:`_monorepo_flow` and :func:`_single_package_flow` and is untouched by this ruling.

    ``--non-interactive`` proceeds without asking, which is what keeps the guard usable from a bot:
    without that escape hatch molt's improvement would make ``--major`` unusable in CI.
    """
    from molt.versioning import BumpType

    if non_interactive:
        return selection

    confirmed: list[tuple[Package, BumpType]] = []
    for package, bump in selection:
        if bump is not BumpType.MAJOR or not _is_first_major(package):
            confirmed.append((package, bump))
            continue
        answer = prompts.confirm(_confirm_message(package))
        if is_cancel(answer):
            raise _Canceled
        if not answer:
            raise _FirstMajorAborted(package.name)
        confirmed.append((package, BumpType.MAJOR))
    return confirmed


# ======================================================================================
# The interactive flow (createChangeset.ts:171-271)
# ======================================================================================


def _interactive_selection(
    *,
    prompts: Any,
    git: Any,
    root: Path,
    config: Config,
    versionable: list[Package],
    since: str | Sequence[str] | None,
) -> list[tuple[Package, BumpType]]:
    """Ask which packages release and by how much, in whichever shape the repository has.

    Changed-package detection is skipped in the single-package shape: there is no multiselect to
    partition, so the git round-trip would buy nothing.
    """
    if len(versionable) == 1:
        return _single_package_flow(versionable[0], prompts)
    changed = _changed_packages(git=git, root=root, config=config, since=since)
    return _monorepo_flow(versionable, changed, prompts)


def _changed_packages(
    *, git: Any, root: Path, config: Config, since: str | Sequence[str] | None
) -> set[str]:
    """Normalized names of the packages changed since ``--since`` or the configured base branch.

    Only ever an *ordering* input: it decides which multiselect group a package lands in, never
    what a changeset may contain (``website/docs/cli/add.md``, the ``--since`` row). So a git
    failure -- a fresh repository, a CI checkout that never fetched the base branch -- degrades to
    "nothing changed" rather than stopping a run that has nothing else to do with git.
    """
    from molt.errors import GitError
    from molt.names import normalize_name

    ref = _ref(since, config.base_branch)
    if git is None:
        from molt.git import Git

        git = Git(root)
    try:
        names = git.get_changed_packages_since_ref(
            ref,
            changed_file_patterns=list(config.changed_file_patterns),
            ecosystem=config.ecosystem,
        )
    except GitError:
        return set()
    return {normalize_name(str(name)) for name in names}


def _ref(since: str | Sequence[str] | None, base_branch: str) -> str:
    """``--since`` when given, else the configured ``base_branch``; a repeated flag takes the last.

    The CLI shell declares ``--since`` as repeatable and normalizes a repeated scalar to its last
    value, but a caller holding the list is accepted here too rather than being told to unwrap it.
    """
    if since is None:
        return base_branch
    if isinstance(since, str):
        return since or base_branch
    values = [str(value) for value in since if str(value)]
    return values[-1] if values else base_branch


def _monorepo_flow(
    versionable: list[Package], changed: set[str], prompts: Any
) -> list[tuple[Package, BumpType]]:
    """Flow A: a grouped package multiselect, then a major and a minor pass; the rest is a patch.

    The minor multiselect is skipped when the major pass already claimed everything -- asking for a
    bump on an empty set is a question with one possible answer.
    """
    from molt.versioning import BumpType

    groups = _partition(versionable, changed)
    chosen = _ask_multiselect(prompts, _PACKAGES_PROMPT, groups, versionable, required=True)
    if not chosen:
        return []

    bumps: dict[str, BumpType] = {}
    majors = _ask_multiselect(prompts, _MAJOR_PROMPT, [pkg.name for pkg in chosen], chosen)
    for package in majors:
        if _is_first_major(package):
            answer = prompts.confirm(_confirm_message(package))
            if is_cancel(answer):
                raise _Canceled
            if not answer:
                # `createChangeset.ts:197-209`: a declined package is **not** dropped. It stays in
                # `pkgsLeftToGetBumpTypeFor`, so the minor prompt offers it again and the user gets
                # a second choice instead of losing the changeset.
                continue
        bumps[package.normalized_name] = BumpType.MAJOR

    remaining = [pkg for pkg in chosen if pkg.normalized_name not in bumps]
    if remaining:
        names = [pkg.name for pkg in remaining]
        for package in _ask_multiselect(prompts, _MINOR_PROMPT, names, remaining):
            bumps[package.normalized_name] = BumpType.MINOR

    # `createChangeset.ts:234-253`: everything still unclaimed is a patch, with no prompt of
    # its own.
    return [(pkg, bumps.get(pkg.normalized_name, BumpType.PATCH)) for pkg in chosen]


def _partition(versionable: list[Package], changed: set[str]) -> list[tuple[str, list[str]]]:
    """Split the choices into changed and unchanged groups (``createChangeset.ts:41-64``).

    Changed packages come **first** -- upstream relies on object insertion order being display
    order -- each group keeps the discovery order, which is sorted by name, and an empty group is
    omitted so nothing renders an empty box.
    """
    buckets: list[tuple[str, list[str]]] = [
        (_CHANGED_GROUP, [pkg.name for pkg in versionable if pkg.normalized_name in changed]),
        (_UNCHANGED_GROUP, [pkg.name for pkg in versionable if pkg.normalized_name not in changed]),
    ]
    return [(label, names) for label, names in buckets if names]


def _ask_multiselect(
    prompts: Any,
    message: str,
    choices: Sequence[Any],
    pool: Sequence[Package],
    *,
    required: bool = False,
) -> list[Package]:
    """Ask a multiselect and map the answer back to packages, preserving ``pool``'s order.

    Mapping back through ``pool`` rather than trusting the answer's own order is what keeps the
    written changeset deterministic: a prompt implementation is free to return selections in click
    order, and two identical selections must not produce two different files.
    """
    from molt.names import normalize_name

    answer = prompts.multiselect(message, choices, required=required)
    if is_cancel(answer):
        raise _Canceled
    picked = {normalize_name(str(value)) for value in answer or ()}
    return [pkg for pkg in pool if pkg.normalized_name in picked]


def _single_package_flow(package: Package, prompts: Any) -> list[tuple[Package, BumpType]]:
    """Flow B (``createChangeset.ts:254-271``): one ``select``, never a package multiselect.

    The choice order is upstream's (patch, minor, major) and the message carries the package name
    and its current version -- both are what make the question answerable without leaving the
    terminal (research doc 03 section 2.5).

    Declining the first-major confirmation **re-asks** instead of aborting. Upstream throws
    ``ExitError(1)`` here while its own monorepo path falls through, so the same answer to the same
    question destroys the session in one repository shape and not the other (research doc 03
    section 10.8; molt divergence item 2).
    """
    from molt.versioning import BumpType

    choices = [BumpType.PATCH.value, BumpType.MINOR.value, BumpType.MAJOR.value]
    message = (
        f"What kind of change is this for {package.name}? (current version is {package.version})"
    )
    while True:
        answer = prompts.select(message, choices)
        if is_cancel(answer):
            raise _Canceled
        bump = BumpType(str(answer))
        if bump is not BumpType.MAJOR or not _is_first_major(package):
            return [(package, bump)]
        confirmed = prompts.confirm(_confirm_message(package))
        if is_cancel(confirmed):
            raise _Canceled
        if confirmed:
            return [(package, BumpType.MAJOR)]


# ======================================================================================
# Summary capture (research doc 03 section 2.4, the prompt-5b table)
# ======================================================================================


def _capture_summary(prompts: Any) -> str:
    """The console/editor fallback chain, exactly as the prompt-5b table specifies.

    A non-empty console answer is the summary and the editor never opens. An empty one opens
    ``$EDITOR`` and the editor's text wins. An empty *editor* result, or an editor that fails,
    falls back to the console prompt -- launching an editor must never be able to lose a summary.

    Nothing here strips ``#`` lines. Upstream's ``askWithEditor.ts:31`` runs
    ``replace(/^#.*\\n?/gm, "")`` over the edited file and so deletes every Markdown heading the
    author typed; molt preserves them (``website/docs/config/changeset-format.md``).
    """
    answer = prompts.text(_SUMMARY_PROMPT)
    if is_cancel(answer):
        raise _Canceled
    summary = str(answer or "").strip()
    if summary:
        return summary

    edited = _try_editor(prompts)
    if edited:
        return edited

    retry = prompts.text(_SUMMARY_RETRY_PROMPT)
    if is_cancel(retry):
        raise _Canceled
    return str(retry or "").strip()


def _try_editor(prompts: Any) -> str:
    """The editor leg of the chain: its text, or ``""`` when it produced none.

    An editor that raises is **non-fatal** -- no ``$EDITOR`` set, a spawn failure, a terminal that
    cannot be handed over -- so the failure is swallowed and the console prompt takes over.
    :class:`AssertionError` is re-raised: that is how a test double reports "you prompted more than
    the script allows", and swallowing it would turn an over-prompting bug into a pass.
    """
    try:
        edited = prompts.editor(_EDITOR_PROMPT)
    except AssertionError:
        raise
    except Exception:
        return ""
    if is_cancel(edited):
        raise _Canceled
    return str(edited or "").strip()


# ======================================================================================
# Writing, printing and committing
# ======================================================================================


def _build_changeset(
    changeset_dir: Path, selection: Sequence[tuple[Package, BumpType]], summary: str
) -> Changeset:
    """Assemble the changeset, including the id its filename will use.

    The id comes from the ``human-id`` port, looked up through :mod:`molt.changeset` at call time
    so the deterministic-id test seam can replace it. It carries no meaning
    (``website/docs/config/changeset-format.md``) but it is an identity: ``apply_release_plan``
    deletes ``<id>.md`` after versioning, so an id that does not round-trip double-bumps.
    """
    from molt.changeset import Changeset, Release, generate_changeset_id, unique_changeset_id

    releases = tuple(Release(name=package.name, type=bump) for package, bump in selection)
    changeset_id = unique_changeset_id(changeset_dir, generate_changeset_id)
    return Changeset(releases=releases, summary=summary.strip(), id=changeset_id)


def _report_dry_run(console: Any, changeset: Changeset) -> None:
    """Print the changeset that *would* be written, and touch nothing.

    The uniform plan-and-print behaviour research README section 5 item 3 requires of every
    mutating command. The preview has to be specific enough to check, so it is the real rendered
    bytes rather than a summary of them.
    """
    from molt.changeset import CHANGESET_DIR, render_changeset

    console.info(f"Would write {CHANGESET_DIR}/{changeset.id}.md:")
    console.info(render_changeset(changeset))


def _write(root: Path, changeset: Changeset, config: Config) -> Path:
    """Write the changeset, passing the configured formatter explicitly.

    ``format=`` is passed rather than left to its default, so the writer does not load the
    configuration a second time to answer a question this command already holds the answer to.
    """
    from molt.changeset import write_changeset

    return write_changeset(root, changeset, id=changeset.id, format=config.format)


def _commit(config: Config, changeset: Changeset, path: Path, *, root: Path, git: Any) -> None:
    """Stage and commit the new changeset when ``commit`` is configured; otherwise do nothing.

    ``config.commit`` is already normalized to ``(ref, options)`` or ``False`` -- ``commit = true``
    became the built-in provider plus its default options -- so this reads one shape, not three.
    ``getCommitFunctions`` returns no message function for a falsy setting
    (``add/index.ts:109-113``) and the git block at ``:117-125`` then never runs; committing by
    default would surprise anyone running ``molt add`` mid-review with unrelated staged work.

    Staging and committing are two calls, in that order, and the message comes from the configured
    provider rather than from a string built here: the convention is a plugin seam
    (``[project.entry-points."molt.commit"]``), not a constant.
    """
    from molt.commit import normalize_skip_ci

    setting = config.commit
    if setting is False:
        return
    ref, options = setting
    provider = _commit_provider(ref)
    # `skip_ci` is normalized here, never forwarded raw: `get_add_message` takes the concrete
    # `Literal["add", "version"] | False` and no longer interprets a bare `True` (gap `CM-2`).
    skip_ci = normalize_skip_ci((options or {}).get("skip_ci"), command="add")
    message = provider.get_add_message(changeset, skip_ci)
    if git is None:
        from molt.git import Git

        git = Git(root)
    git.add(str(path))
    git.commit(message)


def _commit_provider(ref: str) -> Any:
    """Load the commit-message provider ``ref`` names.

    Thin wrapper over :func:`molt.commit.load_provider`, which owns the resolution order shared
    with ``molt version`` -- entry point, entry point with the ``molt.commit.`` prefix stripped,
    then a dotted module path (gap ``AC-7``). Kept as a named function here because it is the seam
    a test would replace.
    """
    from molt.commit import load_provider

    return load_provider(ref, method="get_add_message")
