"""The two CI loops: open a version branch, and publish what was merged.

Ports ``packages/release-utils/src/run.ts`` (research doc 04 sections 6.1 and 6.2). Both loops are
thin: they drive a **script the caller supplies** -- normally ``molt version`` or ``molt publish``
-- and own only the git choreography around it, which is the part a workflow author cannot express
in YAML without getting it subtly wrong.

What each loop owns
-------------------
:func:`run_version` switches to ``changeset-release/<branch>`` (``run.ts:112``), records every
package's version, runs the script, works out what actually moved, commits, and force-pushes
(``run.ts:146``). :func:`run_publish` runs the publish script, pushes the tags the script created,
and reports which packages went out -- from the script's machine-readable git-tag event stream, and
from its ``New tag:`` stdout lines when it wrote no stream.

**How the version commit reaches the remote is a mode**, :class:`CommitMode`. The default is the
git command line, exactly as above. Selecting :attr:`CommitMode.API` makes no local branch, no
local commit and no push: the working tree's changes against the base commit are sent to the host,
which authors the commit and therefore can sign it -- what a protected branch requiring signed
commits needs. Tags are unaffected in both modes and still go over git, because a tag ref carries
no signature either way (``openspec/GAPS.md`` ``ACM-1``).

Two deliberate divergences from upstream
-----------------------------------------
* **``script`` is an argv list, not a bare string** (design D1). ``run.ts:121`` splits a command
  string on whitespace and runs it with no arguments, so ``molt version --snapshot canary`` is not
  expressible at all (research doc 03 section 9.21). Taking argv also removes every shell-quoting
  question, which is the other half of that bug.
* **The changed set is derived from versions, not from touched files** (design D2). An ignored
  package's dependency pin can be rewritten without the package itself being released, and this
  answer becomes the body of a public pull request -- it has to mean "these were released". The
  before/after version diff gets that for free, which is why neither this module nor upstream's
  ``getChangedPackages`` (``utils.ts:16-31``) takes an ``ignore`` argument.
* **The published set is read from a machine channel, not from a log line.** ``run.ts:41-47``
  scrapes stdout because ``changeset publish`` writes ``New tag:`` there with ``console.log``.
  molt's console sends every human-facing level to **stderr**, so the same scrape found nothing on
  every real run -- three releases published, tagged, and reported nothing. :func:`run_publish`
  reads molt's NDJSON git-tag stream first and keeps the scrape for publish commands that are not
  molt. See its docstring.

Everything that touches the remote goes through the injected :class:`~molt.git.Git` seam, so a test
that does not hand one over pushes to whatever ``origin`` its own temporary clone has, and never
anywhere real.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from molt.action.api_commit import collect_file_changes
from molt.ecosystem import (
    discover_workspace,
    find_workspace_root,
    read_toml,
    resolve_workspace_versions,
)
from molt.errors import ExitError, MoltError
from molt.events import GIT_TAG_EVENT

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from molt.ecosystem import Package, Workspace

__all__ = [
    "DEFAULT_COMMIT_MESSAGE",
    "VERSION_BRANCH_PREFIX",
    "ChangedPackage",
    "CommitMode",
    "PublishedPackage",
    "RunPublishResult",
    "RunVersionResult",
    "run_publish",
    "run_version",
]

#: ``run.ts:112`` -- the version branch is always ``changeset-release/<the branch being released>``.
#: A fixed prefix rather than a configurable one: a workflow that has to guess the branch name
#: cannot find the pull request it opened last time.
VERSION_BRANCH_PREFIX = "changeset-release/"

#: ``run.ts:139`` -- the commit the version branch carries when the caller names no other.
DEFAULT_COMMIT_MESSAGE = "Version Packages"

#: ``run.ts:47`` -- a monorepo publish announces ``New tag: <name>@<version>``.
_MONOREPO_TAG_PATTERN = re.compile(r"New tag:\s+(\S+)@(\S+)")

#: ``run.ts:75`` -- a single-package publish announces ``New tag: v<version>``, with no name to
#: match against. The line is a marker, not a source of data: the version comes from the manifest,
#: exactly as it does on the monorepo path.
_SINGLE_TAG_PATTERN = re.compile(r"New tag:")

#: :attr:`molt.ecosystem.Workspace.backend` for a repository that declares no workspace -- the
#: analogue of ``@manypkg``'s ``tool === "root"`` test (``run.ts:44``).
_SINGLE_BACKEND = "single"

#: The environment variable that back-fills ``--output`` when a molt command is given no flag
#: (``molt.cli.normalize_options`` rule 6; upstream's ``CHANGESETS_OUTPUT``, ``index.ts:59-63``).
#: :func:`run_publish` names a destination through it rather than editing the caller's argv.
_OUTPUT_VARIABLE = "MOLT_OUTPUT"


class CommitMode(StrEnum):
    """How :func:`run_version` gets the release commit onto the remote.

    Ports ``changesets/action`` v1.9.0's ``commitMode`` input, with one value renamed:
    ``github-api`` becomes :attr:`API`, because molt's forge seam means the host is not necessarily
    GitHub and the same input has to keep meaning the right thing when a GitLab backend exists --
    the rename that produced ``create-releases`` and ``base-branch`` (``openspec/GAPS.md``
    ``CO-7``). :attr:`GIT_CLI` deliberately keeps upstream's spelling, so a workflow migrating from
    ``changesets/action`` does not have to change a value it already has.

    **An explicit mode, never inferred from "is there a forge"** (api-commits design D6). Upstream
    branches on ``if (this.octokit)``, so "the caller passed no client" and "the caller chose the
    git CLI" are one fact. In molt they are not: ``molt.action.orchestrate`` builds a real forge
    from the environment whenever none is injected, so a forge is essentially always present and
    inferring the mode from it would make :attr:`GIT_CLI` unreachable in production.

    A :class:`~enum.StrEnum` so the value a workflow writes and the value molt compares are the
    same string, which is what lets an unrecognised input be refused by name rather than coerced.
    """

    GIT_CLI = "git-cli"
    API = "api"


@dataclass(frozen=True, slots=True)
class ChangedPackage:
    """A package whose version moved during :func:`run_version`.

    ``manifest`` is the parsed ``pyproject.toml`` document -- the direct analogue of upstream's
    ``packageJson`` field -- read **after** the script ran, so it carries the new version and any
    dependency pin the release rewrote.
    """

    #: Absolute directory, spelled the way the caller spelled ``cwd``.
    dir: Path
    #: The same directory relative to the workspace root; what a pull-request body prints.
    relative_dir: Path
    #: The parsed manifest document.
    manifest: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RunVersionResult:
    """What :func:`run_version` pushed, and where it pushed it."""

    #: ``changeset-release/<branch>`` (``run.ts:112``).
    version_branch: str
    #: Only packages whose **version** changed, in workspace discovery order (design D2).
    changed_packages: tuple[ChangedPackage, ...] = ()


@dataclass(frozen=True, slots=True)
class PublishedPackage:
    """One package a publish script announced, through its event stream or a ``New tag:`` line."""

    name: str
    version: str
    #: The tag the publish command reported creating, when it reported one. Present on every
    #: event-stream path and ``None`` on the ``New tag:`` fallback, where no tag is parsed out --
    #: a caller with ``None`` re-derives the tag from molt's own tagging rule (design D3).
    #: Defaulted, so every construction site that predates the event stream is unchanged.
    tag: str | None = None


@dataclass(frozen=True, slots=True)
class RunPublishResult:
    """What :func:`run_publish` saw go out.

    ``published`` is ``False`` with an empty :attr:`published_packages` when the script announced
    no tags -- the normal "nothing to release" outcome, not a failure (``run.ts:88-97``).
    """

    published: bool
    published_packages: tuple[PublishedPackage, ...] = ()
    #: The publish command's own status. Non-zero means the caller must fail the run **after**
    #: reporting what did go out (owner ruling 2026-07-31, closing gap ``AP-14``). It defaults to
    #: ``0`` so every construction site that predates the ruling still reads as a clean publish.
    exit_status: int = 0


def run_version(
    *,
    cwd: Path | str,
    script: Sequence[str],
    commit_message: str = DEFAULT_COMMIT_MESSAGE,
    branch: str | None = None,
    git: Any = None,
    commit_mode: CommitMode = CommitMode.GIT_CLI,
    forge: Any = None,
) -> RunVersionResult:
    """Run the version script and publish the result to ``changeset-release/<branch>``.

    The order is upstream's (``run.ts:106-148``) and each step depends on the one before it:

    1. Switch to the version branch, creating it when it does not exist yet, and hard-reset it onto
       the commit being released. The reset is what makes a **re-run** idempotent: the branch left
       over from the previous run is discarded rather than built on, so the pull request always
       shows one release's worth of changes.
    2. Record every discovered package's version.
    3. Run ``script`` in ``cwd``. A non-zero exit raises :class:`~molt.errors.ExitError` rather
       than being logged and stepped over -- research README section 3.4's "silent CI failure" is
       the failure mode this whole tool exists to avoid.
    4. Re-discover, and keep the packages whose version differs (design D2).
    5. Commit everything, unless the script already committed it -- a project configured with
       ``commit = true`` runs ``molt version`` with its own commit step.
    6. Force-push. The version branch is molt's to own; a human who pushed to it will lose that
       edit, which is upstream's rule too (``run.ts:146``) and the only way a re-run converges.

    ``branch`` names the branch being released and defaults to whatever ``cwd`` currently has
    checked out, which is what a CI checkout leaves behind.

    ``commit_mode`` selects **how steps 1, 5 and 6 happen**, and nothing else (api-commits
    design D6). :attr:`CommitMode.GIT_CLI` is the default and is the six steps above, unchanged.
    :attr:`CommitMode.API` skips step 1 entirely -- upstream's ``git.ts::prepareBranch`` returns
    early with its own reason, *"Preparing a new local branch is not necessary when using the
    API"*, and a local branch switch would change which tree the changes are measured against --
    and replaces steps 5 and 6 with one :meth:`molt.forge.Forge.create_commit` call, so the host
    authors the commit and can sign it. Steps 2, 3 and 4 are identical in both modes: the changed
    set is still the before/after version diff, never what was staged.

    ``forge`` is required in API mode and unused in the other. It is injected rather than
    constructed here, because :mod:`molt.forge` costs ``httpx`` and the caller
    (``molt.action.orchestrate``) has already resolved one for the pull-request lifecycle -- and
    sharing that instance is what keeps its cache alive across the run.
    """
    from molt.git import Git

    root = Path(cwd)
    seam = Git(root) if git is None else git

    base_branch = branch if branch is not None else seam.current_branch()
    version_branch = f"{VERSION_BRANCH_PREFIX}{base_branch}"
    head = seam.get_current_commit_id()

    if commit_mode is CommitMode.GIT_CLI:
        seam.switch_to_maybe_existing_branch(version_branch)
        seam.reset(head)

    workspace_root = find_workspace_root(root)
    before = _versions_by_directory(discover_workspace(workspace_root))
    _require_success(_run_script(script, cwd=root))
    after = discover_workspace(workspace_root)
    after_versions = _versions_by_directory(after)

    changed = tuple(
        _changed_package(package, workspace_root)
        for package in after.packages
        if before.get(package.directory) != after_versions.get(package.directory)
    )

    if commit_mode is CommitMode.GIT_CLI:
        _publish_with_git(seam, root, branch=version_branch, message=commit_message)
    else:
        _publish_with_forge(seam, forge, branch=version_branch, base=head, message=commit_message)

    return RunVersionResult(version_branch=version_branch, changed_packages=changed)


def _publish_with_git(seam: Any, root: Path, *, branch: str, message: str) -> None:
    """Commit what the script left behind and force-push it (``run.ts:139-146``).

    The commit is conditional and the push is not, which is easy to misread as an oversight. It is
    upstream's shape and it is right: a version script that changed nothing has nothing to commit,
    but the release branch must still be reset onto the commit being released, or it keeps carrying
    the *previous* release. :func:`_publish_with_forge` reproduces exactly that asymmetry.
    """
    if not seam.is_clean():
        seam.add(root)
        seam.commit(message)
    seam.push(branch, force=True)


def _publish_with_forge(seam: Any, forge: Any, *, branch: str, base: str, message: str) -> None:
    """Send one API commit carrying everything the script changed (``git.ts::pushChanges``).

    The changes are measured **against ``base``**, the commit that was checked out before the
    script ran -- not against ``HEAD``. A project configured with ``commit = true`` runs a version
    script that commits its own work, and diffing against ``HEAD`` would drop exactly that work
    from the release (api-commits design D7; ``openspec/GAPS.md`` ``ACM-9`` records that the two
    modes therefore produce different commit *histories* for such a project, both correct).

    Nothing local is written: no branch, no commit, no push. The host makes the branch carry one
    commit on top of ``base``, and returns ``None`` when the script changed nothing -- which is
    still a branch reset onto ``base``, matching what the force-push above does unconditionally.
    """
    if forge is None:
        raise MoltError(
            f"commit-mode `{CommitMode.API.value}` needs a host connection and none was supplied. "
            f"Set GITHUB_TOKEN, or release with commit-mode `{CommitMode.GIT_CLI.value}`."
        )
    additions, deletions = collect_file_changes(seam, base=base)
    forge.create_commit(
        branch, base=base, message=message, additions=additions, deletions=deletions
    )


def run_publish(
    *,
    command: Sequence[str],
    cwd: Path | str,
    git: Any = None,
) -> RunPublishResult:
    """Run the publish script, push the tags it created, and report what went out.

    The script owns the upload **and** the tags (``run.ts:41``): molt's own ``molt publish`` writes
    them, and this loop only pushes what it finds, so a script that failed halfway leaves the tags
    it did create pushed and the rest absent -- which is the honest record of a partial publish.

    **A non-zero status is carried, not raised** (owner ruling 2026-07-31, closing gap ``AP-14``;
    upstream's ``src/index.ts:137-147`` reports the same partial publish). The tags are pushed, the
    output is scraped and the result is returned whatever the command's status was; that status
    travels on :attr:`RunPublishResult.exit_status` and it is the **caller** that fails the run,
    after it has created a host release for every package that did go out. Raising here would make
    the partial publish unobservable, which is exactly the information a half-failed release loses
    for good.

    Which packages went out comes from **two sources, in a fixed order** -- the script's
    machine-readable git-tag event stream first, its ``New tag:`` stdout lines second. Never both:
    a non-empty stream is the whole answer (design D1). Only the script knows which uploads the
    index actually accepted, which is why neither source is the tag list.

    **The stream is primary because stdout cannot detect a molt publish at all.** ``molt publish``
    announces each tag with ``console.success("New tag: ...")``, and :mod:`molt.ui.console`'s
    streams contract sends every human-facing level to **stderr** -- stdout carries machine-readable
    payloads only, so ``molt status --output json | jq`` is not poisoned by the banner. This loop
    scraped stdout, faithfully to ``run.ts:41-47``, where ``changeset publish`` writes those lines
    with ``console.log`` and stdout is the only channel. molt kept the consumer and reversed the
    producer's stream, so the scrape found nothing on every real run: ``molt-release`` 0.1.0, 0.1.1
    and 0.1.2 each published, tagged, reported an empty released set, created no host release and
    exited green.

    The destination for that stream is named through **``MOLT_OUTPUT``** rather than by appending
    ``--output`` to the command, because the command is the workflow author's argv and molt does not
    parse it. :func:`molt.cli.normalize_options` rule 6 already back-fills the option from that
    variable (upstream's ``withEnvOptions``, ``index.ts:59-63``), so it reaches every molt command
    in the child's process tree. A value the caller already set **wins**: a workflow that sets it is
    collecting the stream for a later step, and overriding it would break that step silently while
    giving molt the same events it can read from the caller's file anyway.

    The ``New tag:`` scrape is kept, and is reached whenever the stream is missing or empty, because
    a publish command that is **not** molt still follows upstream's contract -- a shell script, a
    ``twine`` wrapper. An empty stream falls through rather than meaning "nothing was published": an
    empty file is what ``molt publish`` writes when it published nothing *and* what a non-molt
    command leaves behind when this loop pre-creates the path, and the two cannot be told apart from
    the file. The fallback answers both correctly, because a molt run that published nothing has no
    ``New tag:`` lines either. A monorepo announces ``New tag: <name>@<version>`` (``run.ts:47``)
    and a single-package repository ``New tag: v<version>`` with no name (``run.ts:75``); the
    reported version comes from the manifest on both paths, so a tag spelled unusually cannot
    invent a release.
    """
    import tempfile

    from molt.git import Git

    root = Path(cwd)
    seam = Git(root) if git is None else git

    handle, name = tempfile.mkstemp(prefix="molt-publish-", suffix=".ndjson")
    os.close(handle)
    temporary = Path(name)
    stream, environment = _publish_environment(temporary)
    try:
        completed = _run_script(command, cwd=root, env=environment)
        events = _read_tag_events(stream)
    finally:
        # Only molt's own file. A destination the caller named belongs to the caller's later step,
        # and deleting it would be the silent breakage that honouring the value exists to avoid.
        temporary.unlink(missing_ok=True)

    seam.push_tags()

    workspace = discover_workspace(find_workspace_root(root))
    released = _event_releases(events, workspace)
    if not released:
        released = (
            _single_package_releases(completed.stdout, workspace)
            if workspace.backend == _SINGLE_BACKEND
            else _monorepo_releases(completed.stdout, workspace)
        )
    return RunPublishResult(
        published=bool(released),
        published_packages=released,
        exit_status=completed.returncode,
    )


# ======================================================================================
# Helpers
# ======================================================================================


def _run_script(
    argv: Sequence[str], *, cwd: Path, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run ``argv`` in ``cwd`` and return the completed process. **Never raises on a status.**

    ``argv`` is a list, never a string, and it is never handed to a shell (design D1). Upstream
    splits a command string on whitespace, which loses every quoted argument and makes
    ``--snapshot canary`` unexpressible. An empty ``argv`` is still a :class:`~molt.errors
    .MoltError`: that is a caller mistake, not a child's failure.

    Only stdout is captured -- the ``New tag:`` fallback in :func:`run_publish` reads it. **stderr
    is inherited on purpose**, so the script's own diagnostics reach the workflow log as it runs
    instead of being swallowed into an exception message nobody sees when the job is cancelled.
    That is also why :func:`_require_success`'s :class:`~molt.errors.ExitError` carries only the
    status: the operator already has the output. Capturing stderr *would* have made the old stdout
    scrape find molt's own ``New tag:`` lines, and it is the wrong fix twice over -- it hides a live
    upload log until the command exits, and it keeps a human sentence as a machine contract.

    ``env`` is the child's whole environment, or ``None`` to inherit this process's. Only
    :func:`run_publish` passes one, to name a ``MOLT_OUTPUT`` destination; ``None`` keeps every
    other call site byte-identical to before the seam existed.

    Deciding what a non-zero status *means* is the caller's, because the two callers disagree
    (owner ruling 2026-07-31, closing gap ``AP-14``). :func:`run_version` fails immediately through
    :func:`_require_success`; :func:`run_publish` carries the status and reports what went out
    first.
    """
    if not argv:
        raise MoltError("The script to run must name at least one argument")
    return subprocess.run(
        list(argv),
        cwd=cwd,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=None if env is None else dict(env),
    )


def _publish_environment(temporary: Path) -> tuple[Path, dict[str, str]]:
    """``(the stream to read, the child's environment)`` for a publish run.

    ``MOLT_OUTPUT`` is the seam :func:`molt.cli.normalize_options` rule 6 reads to back-fill
    ``--output`` when the flag is absent, so naming it here reaches every molt command the publish
    script spawns without this loop parsing an argv it does not own (design D2).

    **A value the caller already set wins, and then the loop reads _that_ file.** A workflow that
    exports ``MOLT_OUTPUT`` is collecting the stream for a later step; overriding it would break
    that step in silence. Honouring the variable but still reading ``temporary`` would be worse
    than either -- the command writes where it was told, molt reads an empty file it made itself,
    and the run reports nothing while looking like it tried. The path is returned rather than
    assumed for exactly that reason.

    Accepted consequence, recorded as a gap: a publish command that chains two molt commands has
    the second truncate the first's file, because :func:`molt.events.write_ndjson` overwrites.
    ``molt publish`` already tags, so the realistic chain does not arise.
    """
    environment = dict(os.environ)
    caller = environment.get(_OUTPUT_VARIABLE)
    stream = Path(caller) if caller else temporary
    environment[_OUTPUT_VARIABLE] = str(stream)
    return stream, environment


def _read_tag_events(stream: Path) -> tuple[dict[str, Any], ...]:
    """The git-tag events ``stream`` carries, or ``()`` for anything unreadable.

    **Never raises.** By the time this runs the publish command has already reached the index, and
    failing the run over a reporting artifact would turn a successful release into a red job for a
    reason nobody can act on (design D1, Risks). Every unreadable shape -- a missing file, an empty
    one, a truncated line, a line that is not an object -- resolves to "no events", which is the
    value that sends :func:`run_publish` to the stdout fallback.
    """
    try:
        text = stream.read_text(encoding="utf-8")
    except OSError:
        return ()
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return ()
        if isinstance(event, dict) and event.get("type") == GIT_TAG_EVENT:
            events.append(event)
    return tuple(events)


def _event_releases(
    events: Sequence[Mapping[str, Any]], workspace: Workspace
) -> tuple[PublishedPackage, ...]:
    """The published packages ``events`` name, resolved against the workspace.

    The event's ``package_name`` is matched **PEP 503-normalized** through
    :meth:`molt.ecosystem.Workspace.get`, exactly as :func:`_monorepo_releases` matches a scraped
    name, and a name that matches nothing is the same real error for the same reason: the publish
    command and molt disagree about what this repository contains, and guessing would report a
    release nobody can find.

    The event carries the **tag it created**, so it travels on the result and the caller stops
    re-deriving one from the package's name and version (design D3). That narrows
    ``openspec/GAPS.md`` ``AP-5`` -- the tag-shape branch survives only on the fallback path now.
    """
    released: list[PublishedPackage] = []
    for event in events:
        name = str(event.get("package_name", ""))
        package = workspace.get(name)
        if package is None:
            raise MoltError(f"Package {name} was published but is not in this workspace")
        tag = event.get("tag")
        released.append(
            PublishedPackage(
                name=package.name,
                version=package.version or "",
                tag=str(tag) if tag else None,
            )
        )
    return tuple(released)


def _require_success(completed: subprocess.CompletedProcess[str]) -> str:
    """``completed``'s stdout, or :class:`~molt.errors.ExitError` carrying its status.

    The half of the old ``_run_script`` that :func:`run_version` still wants: research README
    section 3.4's "silent CI failure" is the failure mode a version script that exits non-zero
    would otherwise become.
    """
    if completed.returncode != 0:
        raise ExitError(completed.returncode)
    return completed.stdout


def _versions_by_directory(workspace: Workspace) -> dict[Path, str | None]:
    """Every package's version, keyed on its directory (``utils.ts:16-31``).

    Keyed on the directory rather than the name because a release may **rename** nothing but a
    workspace can gain a package mid-run; a directory that was not there before has no previous
    version, so the package reads as changed, which is the answer a pull-request body wants.

    The version is read through :func:`molt.ecosystem.resolve_workspace_versions`, not off
    ``[project].version``. A package whose version lives in a ``__about__.py`` reads as ``None``
    from the manifest **both** before and after the script, so a manifest-only diff would report it
    as unchanged and it would be missing from the release pull request that just bumped it -- silent
    in exactly the case the version-source seam exists to make visible. Resolution never raises, so
    a member it cannot resolve simply keeps its manifest answer.
    """
    resolution = resolve_workspace_versions(workspace)
    return {
        package.directory: (
            package.version if package.version is not None else resolution.version_of(package.name)
        )
        for package in workspace.packages
    }


def _changed_package(package: Package, workspace_root: Path) -> ChangedPackage:
    """Read ``package``'s just-written manifest back off disk into a :class:`ChangedPackage`."""
    try:
        relative = package.directory.relative_to(workspace_root)
    except ValueError:  # pragma: no cover - a member outside its own workspace root
        relative = package.directory
    return ChangedPackage(
        dir=package.directory,
        relative_dir=relative,
        manifest=read_toml(package.manifest_path),
    )


def _monorepo_releases(stdout: str, workspace: Workspace) -> tuple[PublishedPackage, ...]:
    """``New tag: <name>@<version>`` lines, resolved against the workspace (``run.ts:44-64``).

    The announced name is matched **PEP 503-normalized** through
    :meth:`molt.ecosystem.Workspace.get`, so a script that prints ``Foo_Bar`` finds the package
    declaring ``foo-bar``. A name that matches nothing is a real error: it means the publish script
    and molt disagree about what this repository contains, and guessing would report a release
    nobody can find.
    """
    released: list[PublishedPackage] = []
    for line in stdout.splitlines():
        match = _MONOREPO_TAG_PATTERN.search(line)
        if match is None:
            continue
        package = workspace.get(match.group(1))
        if package is None:
            raise MoltError(f"Package {match.group(1)} was published but is not in this workspace")
        released.append(PublishedPackage(name=package.name, version=package.version or ""))
    return tuple(released)


def _single_package_releases(stdout: str, workspace: Workspace) -> tuple[PublishedPackage, ...]:
    """The one root package, if any ``New tag:`` line was printed at all (``run.ts:67-85``).

    A single-package tag carries no name to match on, so the first announcement is enough and the
    rest are the same release; upstream breaks out of the loop for the same reason.
    """
    if not workspace.packages:
        return ()
    if not any(_SINGLE_TAG_PATTERN.search(line) for line in stdout.splitlines()):
        return ()
    package = workspace.packages[0]
    return (PublishedPackage(name=package.name, version=package.version or ""),)
